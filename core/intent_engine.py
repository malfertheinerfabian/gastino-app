"""
Gastino.ai - Intent Engine
Analysiert eingehende Nachrichten mit AI und extrahiert Intent, Sprache, Entitaeten.
"""
import json
import logging
from core.ai_client import chat_completion

logger = logging.getLogger("gastino.intent")

SYSTEM_PROMPT = """Du bist der Intent-Classifier für Gastino.ai, einen KI-Assistenten für Gastgeber.
Analysiere die Nachricht des Gastes und antworte NUR mit einem JSON-Objekt. Kein anderer Text.

HEUTIGES DATUM: {today}
AKTUELLER WOCHENTAG: {weekday}

Extrahiere:
1. "intent" - Einer der definierten Intents (siehe unten)
2. "language" - Erkannte Sprache: "de", "it", oder "en"
3. "entities" - Relevante Informationen als Objekt:
   - "room" (Zimmernummer, falls erwähnt)
   - "table" (Tischnummer, falls erwähnt)
   - "items" (Bestellte Items als Array: [{{"name": "...", "qty": 1, "notes": ""}}])
   - "date" (Datum im ISO-Format YYYY-MM-DD. "heute"={today}, "morgen"={tomorrow}. IMMER relatives Datum korrekt berechnen!)
   - "time" (Uhrzeit, HH:MM)
   - "party_size" (Personenanzahl)
   - "guest_name" (Name des Gastes, falls genannt)
4. "confidence" - Deine Sicherheit von 0.0 bis 1.0
5. "needs_human" - true wenn du dir unsicher bist oder die Anfrage komplex ist

INTENTS:
- roomservice_food: Essen aufs Zimmer bestellen
- roomservice_drink: Getraenke aufs Zimmer bestellen
- roomservice_mixed: Essen UND Getraenke aufs Zimmer
- housekeeping: Reinigung, Handtuecher, Bettwaesche, Minibar
- checkout: Auschecken, Rechnung
- complaint: Beschwerde, Problem, Reklamation
- reservation: Tisch- oder Zimmerreservierung, AUCH wenn der Gast auf ein Verfügbarkeitsangebot antwortet (z.B. "ja bitte", "19:30 Uhr", "den um 20 Uhr")
- menu_question: Frage zur Speise-/Getraenkekarte
- order_at_table: Bestellung am Tisch (Restaurant/Bar)
- price_question: Preisanfrage für Zimmer/Angebot
- availability: Verfügbarkeit prüfen (Zimmer oder Tisch)
- checkin_info: Check-in Informationen, Anfahrt, Schluessel
- local_tips: Lokale Empfehlungen, Ausfluege, Wetter
- general_question: Allgemeine Frage über den Betrieb
- thank_you: Dank, Lob, positives Feedback
- greeting: Begruessung ohne konkrete Anfrage
- human_needed: Braucht definitiv menschliche Hilfe
- cancel_order: Möchte eine Bestellung/Reservierung stornieren

BETRIEBSKONTEXT:
{tenant_context}

GAST-KONTEXT:
{guest_context}

LETZTER KONVERSATIONSVERLAUF:
{history_context}

WICHTIG:
- DATUM: Nutze IMMER {today} als heutiges Datum. "heute Abend" = {today}. "morgen" = {tomorrow}.
- KONTEXT BEIBEHALTEN: Wenn im Konversationsverlauf bereits ein Datum, eine Uhrzeit oder Personenanzahl erwähnt wurde und der Gast diese nicht explizit ändert, übernimm die Werte aus dem Verlauf! Beispiel: Bot fragte "Für wie viele Personen?" nach Datum 2026-03-01 -> Gast antwortet "3" -> Datum bleibt 2026-03-01. Gast antwortet dann "20 Uhr" -> Datum bleibt 2026-03-01, party_size bleibt 3.
- Wenn der Gast eine Zimmernummer erwähnt, extrahiere sie IMMER
- Wenn Items bestellt werden, parse sie so genau wie moeglich mit Menge
- Bei gemischten Bestellungen (Essen + Trinken) nutze "roomservice_mixed"
- Bei Beschwerden IMMER needs_human=true setzen
- Wenn der Gast auf Zeitslots antwortet (z.B. "13:30", "ja den um 20 Uhr", "den ersten"), nutze intent "reservation" mit der genannten Zeit UND dem Datum aus dem Verlauf
- Antworte NUR mit validem JSON, kein Markdown, keine Erklaerung"""


def analyze_message(tenant, guest, text, history, config=None, **kwargs):
    """Analysiert eine Gastnachricht mit AI."""
    from datetime import date as date_cls, timedelta as td

    if config is None:
        config = {
            "AI_PROVIDER": "anthropic",
            "AI_API_KEY": kwargs.get("api_key"),
            "AI_MODEL": kwargs.get("model"),
        }

    today = date_cls.today()
    tomorrow = today + td(days=1)
    weekdays_de = ["Montag","Dienstag","Mittwoch","Donnerstag","Freitag","Samstag","Sonntag"]

    guest_parts = []
    if guest.name:
        guest_parts.append(f"Name: {guest.name}")
    if guest.room_number:
        guest_parts.append(f"Zimmer: {guest.room_number}")
    if guest.language:
        guest_parts.append(f"Bevorzugte Sprache: {guest.language}")
    guest_context = "\n".join(guest_parts) if guest_parts else "Neuer Gast."

    # Letzte 4 Nachrichten als Kontext fuer Follow-ups
    history_lines = []
    for msg in history[-4:]:
        role = "Gast" if msg["role"] == "user" else "Bot"
        history_lines.append(f"{role}: {msg['content'][:150]}")
    history_context = "\n".join(history_lines) if history_lines else "Keine vorherige Konversation."

    system = SYSTEM_PROMPT.format(
        today=today.isoformat(),
        tomorrow=tomorrow.isoformat(),
        weekday=weekdays_de[today.weekday()],
        tenant_context=tenant.get_full_context(),
        guest_context=guest_context,
        history_context=history_context,
    )

    try:
        raw = chat_completion(
            system_prompt=system,
            user_message=text,
            config=config,
            temperature=0.1,
            max_tokens=500,
        )

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        analysis = json.loads(raw)

        detected_lang = analysis.get("language", "de")
        if detected_lang != guest.language:
            guest.language = detected_lang
            from models.database import db
            db.session.commit()

        entities = analysis.get("entities", {})
        if entities.get("room") and not guest.room_number:
            guest.room_number = str(entities["room"])
            from models.database import db
            db.session.commit()

        logger.info(f"Intent: {analysis.get('intent')} (lang={detected_lang}, conf={analysis.get('confidence', 0):.2f})")
        return analysis

    except json.JSONDecodeError as e:
        logger.error(f"JSON-Parse-Fehler: {e}")
        return {"intent": "general_question", "language": guest.language or "de", "entities": {}, "confidence": 0.3, "needs_human": False}
    except Exception as e:
        logger.error(f"Intent-Engine Fehler: {e}", exc_info=True)
        return {"intent": "human_needed", "language": guest.language or "de", "entities": {}, "confidence": 0.0, "needs_human": True}
