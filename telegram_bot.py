"""
Gastino.ai - Telegram Bot Integration

SETUP:
1. Telegram @BotFather -> /newbot -> Token kopieren
2. Token in .env als TELEGRAM_TOKEN eintragen
3. python app.py
4. Webhook setzen (Browser): https://api.telegram.org/bot{TOKEN}/setWebhook?url={APP_URL}/telegram/webhook
"""
import logging
import requests
from flask import Blueprint, request, current_app

from models.database import db, Tenant, Guest, Conversation, Message
from core.intent_engine import analyze_message
from core.message_router import route_message

logger = logging.getLogger("gastino.telegram")
telegram_bp = Blueprint("telegram", __name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"


@telegram_bp.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    """Empfaengt alle Telegram-Nachrichten."""
    data = request.json
    if not data or "message" not in data:
        return "OK", 200

    msg = data["message"]
    chat_id = str(msg["chat"]["id"])
    text = msg.get("text", "").strip()

    if not text:
        return "OK", 200

    try:
        # Kommandos abfangen
        if text == "/start":
            send_telegram(chat_id, WELCOME_MSG)
            return "OK", 200

        if text == "/help":
            send_telegram(chat_id, HELP_MSG)
            return "OK", 200

        if text.startswith("/setroom"):
            parts = text.split(maxsplit=1)
            if len(parts) >= 2:
                _set_guest_field(chat_id, "room_number", parts[1])
                send_telegram(chat_id, f"Zimmer {parts[1]} gespeichert.")
            else:
                send_telegram(chat_id, "Bitte Zimmernummer angeben: /setroom 13")
            return "OK", 200

        if text.startswith("/settable"):
            parts = text.split(maxsplit=1)
            if len(parts) >= 2:
                _set_guest_field(chat_id, "table_number", parts[1])
                send_telegram(chat_id, f"Tisch {parts[1]} gespeichert.")
            else:
                send_telegram(chat_id, "Bitte Tischnummer angeben: /settable 5")
            return "OK", 200

        if text == "/status":
            send_telegram(chat_id, _get_status(chat_id))
            return "OK", 200

        if text == "/debug":
            current = current_app.config.get("TELEGRAM_DEBUG", True)
            current_app.config["TELEGRAM_DEBUG"] = not current
            status = "AN" if not current else "AUS"
            send_telegram(chat_id, f"Debug-Modus: {status}")
            return "OK", 200

        # Normale Nachricht -> AI Pipeline
        process_message(chat_id, text, msg)

    except Exception as e:
        logger.error(f"Telegram-Fehler: {e}", exc_info=True)
        send_telegram(chat_id, "Ein Fehler ist aufgetreten. Bitte versuchen Sie es erneut.")

    return "OK", 200


def process_message(chat_id, text, raw_msg):
    """Verarbeitet eine Nachricht durch die Gastino-Pipeline."""
    config = current_app.config

    # 1. Tenant
    tenant = Tenant.query.filter_by(active=True).first()
    if not tenant:
        send_telegram(chat_id, "Kein Betrieb konfiguriert. Bitte python seed.py ausführen.")
        return

    # 2. Gast
    tg_id = f"tg_{chat_id}"
    guest = Guest.query.filter_by(tenant_id=tenant.id, whatsapp_id=tg_id).first()
    if not guest:
        from_user = raw_msg.get("from", {})
        name = f"{from_user.get('first_name', '')} {from_user.get('last_name', '')}".strip() or None
        guest = Guest(tenant_id=tenant.id, whatsapp_id=tg_id, name=name, language="de")
        db.session.add(guest)
        db.session.commit()

    # 3. Conversation
    conv = Conversation.query.filter_by(tenant_id=tenant.id, guest_id=guest.id, status="active").first()
    if not conv:
        conv = Conversation(tenant_id=tenant.id, guest_id=guest.id, status="active")
        db.session.add(conv)
        db.session.commit()

    # 4. Nachricht speichern
    db.session.add(Message(conversation_id=conv.id, direction="inbound", sender_type="guest", content=text))
    db.session.commit()

    # 5. History
    messages = Message.query.filter_by(conversation_id=conv.id).order_by(Message.created_at.desc()).limit(20).all()
    messages.reverse()
    history = [{"role": "user" if m.direction == "inbound" else "assistant", "content": m.content} for m in messages]

    # 6. AI Config zusammenbauen
    ai_config = {
        "AI_PROVIDER": config.get("AI_PROVIDER", "anthropic"),
        "AI_API_KEY": config.get("AI_API_KEY") or config.get("ANTHROPIC_API_KEY"),
        "AI_MODEL": config.get("AI_MODEL") or config.get("CLAUDE_MODEL"),
        "AI_BASE_URL": config.get("AI_BASE_URL"),
    }

    # 7. Intent analysieren
    analysis = analyze_message(tenant=tenant, guest=guest, text=text, history=history, config=ai_config)

    # 8. Debug senden
    if config.get("TELEGRAM_DEBUG", True):
        debug = (
            f"--- DEBUG ---\n"
            f"Intent: {analysis.get('intent')}\n"
            f"Sprache: {analysis.get('language')}\n"
            f"Confidence: {analysis.get('confidence', 0):.0%}\n"
            f"Entities: {analysis.get('entities', {})}\n"
            f"Zimmer: {guest.room_number or '-'} | Tisch: {guest.table_number or '-'}"
        )
        send_telegram(chat_id, debug)

    # 9. Routen - config mit AI settings anreichern
    full_config = dict(config)
    full_config.update(ai_config)
    response_text = route_message(
        tenant=tenant, guest=guest, conversation=conv,
        analysis=analysis, history=history, config=full_config,
    )

    # 10. Antwort speichern + senden
    if response_text:
        db.session.add(Message(
            conversation_id=conv.id, direction="outbound", sender_type="ai",
            content=response_text, metadata_json={"intent": analysis.get("intent")}
        ))
        conv.last_intent = analysis.get("intent")
        db.session.commit()
        send_telegram(chat_id, response_text)


def send_telegram(chat_id, text):
    """Sendet eine Telegram-Nachricht."""
    token = current_app.config.get("TELEGRAM_TOKEN")
    if not token:
        logger.error("TELEGRAM_TOKEN nicht gesetzt!")
        return
    url = f"{TELEGRAM_API.format(token=token)}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        logger.error(f"Telegram senden fehlgeschlagen: {e}")


def _set_guest_field(chat_id, field, value):
    """Setzt ein Feld beim Gast-Profil."""
    tenant = Tenant.query.filter_by(active=True).first()
    if not tenant:
        return
    guest = Guest.query.filter_by(tenant_id=tenant.id, whatsapp_id=f"tg_{chat_id}").first()
    if guest:
        setattr(guest, field, value)
        db.session.commit()


def _get_status(chat_id):
    """Debug-Status."""
    tenant = Tenant.query.filter_by(active=True).first()
    if not tenant:
        return "Kein Tenant konfiguriert."
    guest = Guest.query.filter_by(tenant_id=tenant.id, whatsapp_id=f"tg_{chat_id}").first()
    if not guest:
        return "Kein Gast-Profil. Schreibe eine Nachricht um eins zu erstellen."
    conv = Conversation.query.filter_by(tenant_id=tenant.id, guest_id=guest.id, status="active").first()
    msg_count = Message.query.filter_by(conversation_id=conv.id).count() if conv else 0

    provider = current_app.config.get("AI_PROVIDER", "anthropic")
    model = current_app.config.get("AI_MODEL") or current_app.config.get("CLAUDE_MODEL", "?")

    return (
        f"--- Gastino Status ---\n"
        f"Betrieb: {tenant.name}\n"
        f"Name: {guest.name or '-'}\n"
        f"Sprache: {guest.language or '-'}\n"
        f"Zimmer: {guest.room_number or '-'}\n"
        f"Tisch: {guest.table_number or '-'}\n"
        f"Nachrichten: {msg_count}\n"
        f"Letzter Intent: {conv.last_intent or '-'}\n"
        f"AI: {provider} / {model}"
    )


WELCOME_MSG = """Willkommen bei Gastino!

Ich bin Ihr KI-Assistent. So kann ich helfen:

Tisch reservieren:
  "Tisch für 4 morgen um 20 Uhr"

Roomservice bestellen:
  /setroom 13
  "2 Aperol Spritz bitte"

Fragen stellen:
  "Was für Hauptgerichte habt ihr?"
  "Wann ist das Fruehstueck?"

Kommandos:
  /setroom 13 - Zimmernummer setzen
  /settable 5 - Tischnummer setzen
  /status - Profil anzeigen
  /debug - Debug an/aus
  /help - Diese Hilfe"""


HELP_MSG = """Test-Szenarien:

1. Reservierung (Deutsch):
   "Haben Sie morgen einen Tisch für 4?"

2. Reservierung (Italienisch):
   "Avete un tavolo per 2 stasera?"

3. Roomservice:
   /setroom 13
   "2 Aperol Spritz und Oliven bitte"

4. Speisekarte:
   "Was kostet das Schnitzel?"

5. Beschwerde:
   "Die Heizung funktioniert nicht"

6. Allgemein:
   "Gibt es Parkplätze?"
   "Habt ihr WLAN?"

Debug zeigt Intent, Sprache und Confidence.
/debug zum An-/Ausschalten."""
