"""
Gastino.ai - Reservation Handler v2
AI-generierte Antworten statt Templates. Natürlich, flexibel, mehrsprachig.
"""
import logging
import re
from datetime import datetime, date, time, timedelta

from models.database import db
from core.restaurant_engine import ReservationEngine, ReservationExtended
from core.ai_client import chat_completion

logger = logging.getLogger("gastino.reservations")


# ─── AI RESPONSE GENERATION ─────────────────────────────

RESERVATION_AI_PROMPT = """Du bist Gastino, der freundliche KI-Reservierungsassistent für "{tenant_name}".
Heute ist {today} ({weekday}).

DEINE PERSÖNLICHKEIT:
- Warm, natürlich, wie ein aufmerksamer Mitarbeiter an der Rezeption
- Kurz und knackig (2-4 Sätze, WhatsApp-Stil)
- Sprache: {language}
- Sparsam mit Emojis (max 1-2)
- Verwende den Namen des Gastes wenn bekannt
- Sei NICHT robotisch oder formal

BETRIEBSINFO:
{tenant_context}

SITUATION:
{situation}

GESAMMELTE DATEN:
{entities_summary}

Antworte dem Gast natürlich und passend zur Situation. NUR die Antwort, keine Erklärungen."""


def _ai_response(tenant, language, situation, entities, config):
    """Generiert eine natürliche AI-Antwort für Reservierungssituationen."""
    today = date.today()
    weekday_names = {0: "Montag", 1: "Dienstag", 2: "Mittwoch", 3: "Donnerstag",
                     4: "Freitag", 5: "Samstag", 6: "Sonntag"}

    parts = []
    if entities.get("date"):
        parts.append(f"Datum: {entities['date']}")
    if entities.get("time"):
        parts.append(f"Uhrzeit: {entities['time']}")
    if entities.get("party_size"):
        parts.append(f"Personen: {entities['party_size']}")
    if entities.get("guest_name"):
        parts.append(f"Name: {entities['guest_name']}")
    entities_summary = ", ".join(parts) if parts else "Noch keine Daten gesammelt."

    system = RESERVATION_AI_PROMPT.format(
        tenant_name=tenant.name,
        today=today.strftime("%d.%m.%Y"),
        weekday=weekday_names.get(today.weekday(), ""),
        language={"de": "Deutsch", "it": "Italienisch", "en": "Englisch"}.get(language, "Deutsch"),
        tenant_context=tenant.get_full_context() if hasattr(tenant, 'get_full_context') else "",
        situation=situation,
        entities_summary=entities_summary,
    )

    try:
        return chat_completion(
            system_prompt=system,
            user_message=situation,
            config=config,
            temperature=0.7,
            max_tokens=250,
        )
    except Exception as e:
        logger.error(f"AI Response Fehler: {e}")
        return None


# ─── ENTITY ACCUMULATION ─────────────────────────────────

def _accumulate_entities(conversation, analysis):
    """Sammelt Entities über mehrere Nachrichten."""
    stored = {}
    if conversation and conversation.pending_entities:
        stored = dict(conversation.pending_entities)

    new_entities = analysis.get("entities", {})

    for key in ("date", "time", "party_size", "guest_name", "zone_preference", "notes", "special_requests"):
        new_val = new_entities.get(key)
        old_val = stored.get(key)

        if new_val is not None and new_val != "":
            if key == "date" and old_val and old_val != new_val:
                today_str = date.today().isoformat()
                if new_val == today_str and old_val != today_str:
                    logger.info(f"Ignoriere AI-generiertes 'heute' Datum, behalte: {old_val}")
                    continue
            stored[key] = new_val

    if conversation:
        conversation.pending_entities = stored
        db.session.commit()

    logger.info(f"Accumulated entities: {stored}")
    return stored


def _clear_pending_entities(conversation):
    if conversation:
        conversation.pending_entities = {}
        db.session.commit()


# ─── PROCESS AVAILABILITY ─────────────────────────────────

def process_availability(tenant, guest, conversation, analysis, config):
    language = analysis.get("language", "de")
    entities = _accumulate_entities(conversation, analysis)

    res_date = entities.get("date")
    res_time = entities.get("time")
    party_size = entities.get("party_size")

    if not party_size:
        resp = _ai_response(tenant, language,
            "Gast fragt nach Verfügbarkeit aber hat die Personenanzahl nicht genannt. Frage natürlich nach.",
            entities, config)
        return resp or "Für wie viele Personen suchen Sie einen Tisch?"

    if not res_date:
        resp = _ai_response(tenant, language,
            "Gast fragt nach Verfügbarkeit für {} Personen aber hat kein Datum genannt. Frage natürlich nach dem Tag.".format(party_size),
            entities, config)
        return resp or "Für welchen Tag möchten Sie kommen?"

    try:
        parsed_date = _parse_date(res_date)
    except (ValueError, TypeError):
        resp = _ai_response(tenant, language,
            "Gast hat ein Datum genannt das ich nicht verstanden habe: '{}'. Bitte freundlich um Wiederholung.".format(res_date),
            entities, config)
        return resp or "Entschuldigung, ich konnte das Datum nicht verstehen."

    party_size = int(party_size)
    engine = ReservationEngine(tenant.id)

    if res_time:
        try:
            parsed_time = _parse_time(res_time)
        except (ValueError, TypeError):
            parsed_time = None

        if parsed_time:
            result = engine.check_availability(parsed_date, parsed_time, party_size)

            if result["available"]:
                table = result["table"]
                zone_name = table.get("zone", "") if isinstance(table, dict) else getattr(table, "zone", "")
                table_name = table.get("name", "") if isinstance(table, dict) else getattr(table, "name", "")
                resp = _ai_response(tenant, language,
                    "Verfügbarkeit geprüft: JA, es ist frei am {} um {} für {} Personen. "
                    "Tisch: {} ({}). Frage ob der Gast reservieren möchte.".format(
                        parsed_date.strftime('%d.%m.%Y'), parsed_time.strftime('%H:%M'),
                        party_size, table_name, zone_name),
                    entities, config)
                return resp or "Am {} um {} ist für {} Personen frei!".format(
                    parsed_date.strftime('%d.%m.%Y'), parsed_time.strftime('%H:%M'), party_size)
            else:
                reason = result.get("reason", "fully_booked")
                alternatives = result.get("alternatives", [])

                if reason == "closed":
                    day_names = {0:"Montag",1:"Dienstag",2:"Mittwoch",3:"Donnerstag",4:"Freitag",5:"Samstag",6:"Sonntag"}
                    resp = _ai_response(tenant, language,
                        "Am {} ({}) ist Ruhetag. Schlage freundlich einen anderen Tag vor.".format(
                            parsed_date.strftime('%d.%m.%Y'), day_names.get(parsed_date.weekday(), '')),
                        entities, config)
                    return resp or "Am {} haben wir leider Ruhetag.".format(parsed_date.strftime('%d.%m.%Y'))
                elif reason == "outside_hours":
                    resp = _ai_response(tenant, language,
                        "Die gewünschte Uhrzeit {} liegt außerhalb der Servicezeiten. Nenne die Servicezeiten und frage nach einer anderen Uhrzeit.".format(
                            parsed_time.strftime('%H:%M')),
                        entities, config)
                    return resp or "Zu dieser Uhrzeit nehmen wir leider keine Reservierungen an."
                else:
                    alt_text = ""
                    if alternatives:
                        alt_text = "Alternativen: " + ", ".join([a['time'] + " Uhr" for a in alternatives[:3]])
                    resp = _ai_response(tenant, language,
                        "Am {} um {} ist für {} Personen leider ausgebucht. {}".format(
                            parsed_date.strftime('%d.%m.%Y'), parsed_time.strftime('%H:%M'),
                            party_size, alt_text),
                        entities, config)
                    return resp or "Leider ist dieser Zeitpunkt ausgebucht."

    slots = engine.get_available_slots(parsed_date, party_size)

    if not slots:
        if engine._is_closed(parsed_date):
            day_names = {0:"Montag",1:"Dienstag",2:"Mittwoch",3:"Donnerstag",4:"Freitag",5:"Samstag",6:"Sonntag"}
            resp = _ai_response(tenant, language,
                "Am {} ({}) ist Ruhetag.".format(
                    parsed_date.strftime('%d.%m.%Y'), day_names.get(parsed_date.weekday(), '')),
                entities, config)
            return resp or "Am {} haben wir leider Ruhetag.".format(parsed_date.strftime('%d.%m.%Y'))
        resp = _ai_response(tenant, language,
            "Am {} sind für {} Personen keine Plätze mehr frei.".format(
                parsed_date.strftime('%d.%m.%Y'), party_size),
            entities, config)
        return resp or "Leider keine Plätze mehr frei."

    slots_text = ", ".join([s['time'] + " Uhr" for s in slots[:6]])
    resp = _ai_response(tenant, language,
        "Am {} sind für {} Personen folgende Zeiten frei: {}. Frage welche Zeit passt.".format(
            parsed_date.strftime('%d.%m.%Y'), party_size, slots_text),
        entities, config)
    return resp or "Verfügbare Zeiten: {}".format(slots_text)


# ─── PROCESS RESERVATION ─────────────────────────────────

def process_reservation(tenant, guest, conversation, analysis, config):
    language = analysis.get("language", "de")
    entities = _accumulate_entities(conversation, analysis)

    res_date = entities.get("date")
    res_time = entities.get("time")
    party_size = entities.get("party_size")
    guest_name = entities.get("guest_name")

    missing = []
    if not res_date:
        missing.append("date")
    if not res_time:
        missing.append("time")
    if not party_size:
        missing.append("party_size")
    if not guest_name:
        missing.append("guest_name")

    if missing:
        missing_text = {
            "date": "Datum",
            "time": "Uhrzeit",
            "party_size": "Personenanzahl",
            "guest_name": "Name für die Reservierung",
        }
        missing_str = ", ".join([missing_text.get(m, m) for m in missing])

        known_parts = []
        if res_date:
            known_parts.append("Datum: {}".format(res_date))
        if res_time:
            known_parts.append("Uhrzeit: {}".format(res_time))
        if party_size:
            known_parts.append("Personen: {}".format(party_size))
        if guest_name:
            known_parts.append("Name: {}".format(guest_name))
        known_str = ", ".join(known_parts) if known_parts else "noch nichts"

        resp = _ai_response(tenant, language,
            "Gast möchte reservieren. Bereits bekannt: {}. "
            "Noch fehlend: {}. Frage natürlich nach den fehlenden Infos in EINER Nachricht.".format(
                known_str, missing_str),
            entities, config)
        return resp or _ask_missing_fallback(language, missing)

    try:
        parsed_date = _parse_date(res_date)
        parsed_time = _parse_time(res_time)
    except (ValueError, TypeError) as e:
        logger.warning("Datum/Zeit Parse-Fehler: {}".format(e))
        resp = _ai_response(tenant, language,
            "Konnte Datum '{}' oder Zeit '{}' nicht verstehen. Bitte freundlich um Wiederholung.".format(
                res_date, res_time),
            entities, config)
        return resp or "Entschuldigung, ich konnte die Angaben nicht richtig verstehen."

    engine = ReservationEngine(tenant.id)

    result = engine.create_reservation(
        target_date=parsed_date,
        target_time=parsed_time,
        party_size=int(party_size),
        guest_name=guest_name,
        guest_phone=guest.whatsapp_id,
        language=language,
        zone_preference=entities.get("zone_preference"),
        notes=entities.get("notes"),
        special_requests=entities.get("special_requests"),
        source="telegram",
        guest_id=guest.id,
    )

    if result["success"]:
        _clear_pending_entities(conversation)
        res = result["reservation"]
        table_name = res.get("table", "")
        zone = res.get("zone", "")

        resp = _ai_response(tenant, language,
            "Reservierung ERFOLGREICH erstellt! Details: {} um {} Uhr, "
            "{} Personen auf den Namen {}, Tisch: {} ({}). "
            "Bestätige die Reservierung freundlich und wünsche einen schönen Abend.".format(
                parsed_date.strftime('%d.%m.%Y'), parsed_time.strftime('%H:%M'),
                party_size, guest_name, table_name, zone),
            entities, config)
        return resp or "Reservierung bestätigt! {} um {}, {} Personen, {}. Wir freuen uns auf Sie!".format(
            parsed_date.strftime('%d.%m.%Y'), parsed_time.strftime('%H:%M'), party_size, guest_name)
    else:
        alternatives = result.get("alternatives", [])
        error = result.get("error", "fully_booked")

        if error == "closed":
            day_names = {0:"Montag",1:"Dienstag",2:"Mittwoch",3:"Donnerstag",4:"Freitag",5:"Samstag",6:"Sonntag"}
            resp = _ai_response(tenant, language,
                "Am {} ({}) ist Ruhetag.".format(
                    parsed_date.strftime('%d.%m.%Y'), day_names.get(parsed_date.weekday(), '')),
                entities, config)
            return resp or "Am {} haben wir leider Ruhetag.".format(parsed_date.strftime('%d.%m.%Y'))

        alt_text = ""
        if alternatives:
            alt_text = "Alternativen: " + ", ".join([a['time'] + " Uhr" for a in alternatives[:3]])
        resp = _ai_response(tenant, language,
            "Reservierung NICHT möglich am {} um {} für {} Personen. {}".format(
                parsed_date.strftime('%d.%m.%Y'), parsed_time.strftime('%H:%M'),
                party_size, alt_text),
            entities, config)
        return resp or "Leider ist dieser Zeitpunkt ausgebucht."


# ─── PROCESS CANCELLATION ─────────────────────────────────

def process_cancellation(tenant, guest, conversation, analysis, config):
    language = analysis.get("language", "de")
    entities = analysis.get("entities", {})
    target_date_str = entities.get("date")

    # Letzte User-Nachricht für Nummernauswahl
    last_msg = ""
    if conversation:
        from models.database import Message
        last = (Message.query
                .filter_by(conversation_id=conversation.id, direction="inbound")
                .order_by(Message.created_at.desc())
                .first())
        if last:
            last_msg = last.content or ""

    number_match = re.search(r'(\d+)', last_msg)
    wants_all = any(w in last_msg.lower() for w in ["alle", "tutti", "all", "alles"])

    # Finde Reservierungen
    query = (
        ReservationExtended.query
        .filter_by(tenant_id=tenant.id, status="confirmed")
        .filter(
            db.or_(
                ReservationExtended.guest_id == guest.id,
                ReservationExtended.guest_phone == guest.whatsapp_id,
            )
        )
        .order_by(ReservationExtended.date, ReservationExtended.time)
    )

    if target_date_str and not wants_all and not number_match:
        try:
            target_date = _parse_date(target_date_str)
            query = query.filter_by(date=target_date)
        except (ValueError, TypeError):
            pass

    reservations = query.all()

    if not reservations:
        resp = _ai_response(tenant, language,
            "Gast möchte stornieren, aber keine offenen Reservierungen gefunden. Frage freundlich ob unter einem anderen Namen reserviert wurde.",
            entities, config)
        return resp or "Ich konnte keine offene Reservierung für Sie finden."

    engine = ReservationEngine(tenant.id)

    # Alle stornieren
    if wants_all:
        count = len(reservations)
        for res in reservations:
            engine.cancel_reservation(res.id)
        _clear_pending_entities(conversation)

        resp = _ai_response(tenant, language,
            "ALLE {} Reservierungen wurden erfolgreich storniert. Bestätige freundlich.".format(count),
            entities, config)
        return resp or "Alle {} Reservierungen wurden storniert.".format(count)

    # Genau eine → direkt stornieren
    if len(reservations) == 1:
        res = reservations[0]
        engine.cancel_reservation(res.id)
        _clear_pending_entities(conversation)

        resp = _ai_response(tenant, language,
            "Reservierung storniert: {} um {}, {} Personen, {}. Bestätige freundlich.".format(
                res.date.strftime('%d.%m.%Y'), res.time.strftime('%H:%M'),
                res.party_size, res.guest_name),
            entities, config)
        return resp or "Ihre Reservierung wurde storniert."

    # Nummer gewählt
    if number_match:
        idx = int(number_match.group(1))
        if 1 <= idx <= len(reservations):
            res = reservations[idx - 1]
            engine.cancel_reservation(res.id)
            _clear_pending_entities(conversation)

            resp = _ai_response(tenant, language,
                "Reservierung #{} storniert: {} um {}, {} Personen, {}. Bestätige freundlich.".format(
                    idx, res.date.strftime('%d.%m.%Y'), res.time.strftime('%H:%M'),
                    res.party_size, res.guest_name),
                entities, config)
            return resp or "Reservierung storniert."

    # Mehrere → Liste zeigen
    lines = []
    for i, res in enumerate(reservations[:5], 1):
        d = res.date.strftime("%d.%m.%Y")
        t = res.time.strftime("%H:%M")
        lines.append("  {}. {} um {} Uhr - {} Pers. ({})".format(i, d, t, res.party_size, res.guest_name))

    list_text = "\n".join(lines)

    resp = _ai_response(tenant, language,
        "Gast möchte stornieren und hat mehrere Reservierungen:\n{}\n"
        "Frage natürlich welche storniert werden soll. Der Gast kann die Nummer nennen oder 'alle' sagen.".format(list_text),
        entities, config)
    if resp:
        return resp

    return "Ich habe mehrere Reservierungen gefunden:\n\n{}\n\nWelche möchten Sie stornieren? (Nummer oder 'alle')".format(list_text)


# ─── FALLBACK RESPONSES ─────────────────────────────────

def _ask_missing_fallback(language, missing):
    if "date" in missing and "time" in missing:
        return "Gerne! Für wann (Datum und Uhrzeit), wie viele Personen und auf welchen Namen?"
    if "guest_name" in missing:
        return "Auf welchen Namen darf ich die Reservierung eintragen?"
    if "party_size" in missing:
        return "Für wie viele Personen soll ich reservieren?"
    if "time" in missing:
        return "Um welche Uhrzeit möchten Sie kommen?"
    if "date" in missing:
        return "Für welches Datum möchten Sie reservieren?"
    return "Könnten Sie mir bitte die Details geben?"


# ─── PARSING HELPERS ────────────────────────────────────

def _parse_date(date_str):
    if not date_str:
        raise ValueError("Kein Datum")

    for fmt in ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"]:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    lower = date_str.lower().strip()
    today = date.today()
    if lower in ("heute", "oggi", "today"):
        return today
    if lower in ("morgen", "domani", "tomorrow"):
        return today + timedelta(days=1)
    if lower in ("übermorgen", "dopodomani", "day after tomorrow"):
        return today + timedelta(days=2)

    day_map = {"montag":0,"dienstag":1,"mittwoch":2,"donnerstag":3,"freitag":4,"samstag":5,"sonntag":6,
               "lunedi":0,"martedi":1,"mercoledi":2,"giovedi":3,"venerdi":4,"sabato":5,"domenica":6,
               "monday":0,"tuesday":1,"wednesday":2,"thursday":3,"friday":4,"saturday":5,"sunday":6}
    for day_name, day_num in day_map.items():
        if day_name in lower:
            days_ahead = day_num - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            return today + timedelta(days=days_ahead)

    raise ValueError("Unbekanntes Datum: {}".format(date_str))


def _parse_time(time_str):
    if not time_str:
        raise ValueError("Keine Uhrzeit")

    for fmt in ["%H:%M", "%H.%M", "%H:%M:%S"]:
        try:
            return datetime.strptime(time_str, fmt).time()
        except ValueError:
            continue

    match = re.search(r"(\d{1,2})\s*(uhr|ore|h|pm|am|oclock)?", time_str.lower())
    if match:
        hour = int(match.group(1))
        suffix = match.group(2) or ""
        if "pm" in suffix and hour < 12:
            hour += 12
        return time(hour, 0)

    raise ValueError("Unbekannte Uhrzeit: {}".format(time_str))
