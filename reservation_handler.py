"""
Gastino.ai - Reservation Handler
Verarbeitet Reservierungen UND Verfügbarkeitsanfragen über die ReservationEngine.
"""
import logging
from datetime import datetime, date, time, timedelta
import re

from models.database import db
from core.restaurant_engine import ReservationEngine

logger = logging.getLogger("gastino.reservations")


def process_availability(tenant, guest, analysis, config):
    """
    Prüft Verfügbarkeit über die ReservationEngine.
    Wenn alle Daten da: prüfe und biete Reservierung an.
    Wenn Daten fehlen: frage nach.
    """
    language = analysis.get("language", "de")
    entities = analysis.get("entities", {})

    res_date = entities.get("date")
    res_time = entities.get("time")
    party_size = entities.get("party_size")

    # Mindestens party_size und date brauchen wir
    if not party_size:
        return {"de": "Für wie viele Personen suchen Sie einen Tisch?",
                "it": "Per quante persone cerca un tavolo?",
                "en": "How many guests will be dining?"}.get(language, "Für wie viele Personen?")

    if not res_date:
        return {"de": "Für welchen Tag möchten Sie kommen?",
                "it": "Per quale giorno desidera venire?",
                "en": "For which day would you like to come?"}.get(language, "Für welchen Tag?")

    try:
        parsed_date = _parse_date(res_date)
    except (ValueError, TypeError):
        return {"de": "Entschuldigung, ich konnte das Datum nicht verstehen. Könnten Sie es nochmal angeben? (z.B. morgen, 28.02.2026)",
                "it": "Mi scusi, non ho capito la data. Puo ripeterla? (es. domani, 28.02.2026)",
                "en": "Sorry, I couldn't understand the date. Could you provide it again? (e.g. tomorrow, 28/02/2026)"}.get(language, "Datum nicht verstanden.")

    party_size = int(party_size)
    engine = ReservationEngine(tenant.id)

    # Wenn Uhrzeit angegeben: spezifischen Slot prüfen
    if res_time:
        try:
            parsed_time = _parse_time(res_time)
        except (ValueError, TypeError):
            parsed_time = None

        if parsed_time:
            result = engine.check_availability(parsed_date, parsed_time, party_size)

            if result["available"]:
                table = result["table"]
                return _availability_positive(language, parsed_date, parsed_time, party_size, table)
            else:
                reason = result.get("reason", "fully_booked")
                alternatives = result.get("alternatives", [])

                if reason == "closed":
                    return _closed_message(language, parsed_date)
                elif reason == "outside_hours":
                    return _outside_hours_message(language, parsed_date, tenant)
                else:
                    return _unavailable_message(language, alternatives, parsed_date, party_size)

    # Keine Uhrzeit: alle verfügbaren Slots zeigen
    slots = engine.get_available_slots(parsed_date, party_size)

    if not slots:
        # Pruefen ob geschlossen
        if engine._is_closed(parsed_date):
            return _closed_message(language, parsed_date)
        return _no_slots_message(language, parsed_date, party_size)

    return _show_available_slots(language, slots, parsed_date, party_size)


def process_reservation(tenant, guest, analysis, config):
    """
    Verarbeitet Reservierungsanfragen.
    Wenn alle Daten vorhanden: Reservierung erstellen.
    Wenn Daten fehlen: nachfragen.
    """
    language = analysis.get("language", "de")
    entities = analysis.get("entities", {})

    res_date = entities.get("date")
    res_time = entities.get("time")
    party_size = entities.get("party_size")
    guest_name = entities.get("guest_name") or guest.name

    missing = []
    if not res_date:
        missing.append("date")
    if not res_time:
        missing.append("time")
    if not party_size:
        missing.append("party_size")

    if missing:
        return _ask_missing_info(language, missing, entities)

    try:
        parsed_date = _parse_date(res_date)
        parsed_time = _parse_time(res_time)
    except (ValueError, TypeError) as e:
        logger.warning(f"Datum/Zeit Parse-Fehler: {e}")
        return _ask_missing_info(language, ["date", "time"], entities)

    engine = ReservationEngine(tenant.id)

    result = engine.create_reservation(
        target_date=parsed_date,
        target_time=parsed_time,
        party_size=int(party_size),
        guest_name=guest_name or "Gast",
        guest_phone=guest.whatsapp_id,
        language=language,
        zone_preference=entities.get("zone_preference"),
        notes=entities.get("notes"),
        special_requests=entities.get("special_requests"),
        source="whatsapp",
        guest_id=guest.id,
    )

    if result["success"]:
        res = result["reservation"]
        return _confirmation_message(
            language=language,
            res_date=parsed_date,
            res_time=parsed_time,
            party_size=int(party_size),
            guest_name=guest_name,
            table_name=res.get("table"),
            zone=res.get("zone"),
        )
    else:
        alternatives = result.get("alternatives", [])
        error = result.get("error", "fully_booked")
        if error == "closed":
            return _closed_message(language, parsed_date)
        return _unavailable_message(language, alternatives, parsed_date, int(party_size))


# ─── RESPONSE MESSAGES ─────────────────────────────────

def _availability_positive(language, d, t, party_size, table):
    date_str = d.strftime("%d.%m.%Y")
    time_str = t.strftime("%H:%M")
    zone_de = {"innen": "Innenbereich", "terrasse": "Terrasse", "stube": "Stube", "garten": "Garten"}
    zone_it = {"innen": "sala interna", "terrasse": "terrazza", "stube": "stube", "garten": "giardino"}

    zone_info_de = f" ({zone_de.get(table.get('zone', ''), table.get('zone', ''))})" if table.get("zone") else ""
    zone_info_it = f" ({zone_it.get(table.get('zone', ''), table.get('zone', ''))})" if table.get("zone") else ""

    msgs = {
        "de": (f"Ja, am {date_str} um {time_str} Uhr haben wir noch Platz für {party_size} Personen!\n"
               f"Tisch: {table.get('name', '?')}{zone_info_de}\n\n"
               f"Soll ich direkt für Sie reservieren?"),
        "it": (f"Si, il {date_str} alle {time_str} abbiamo ancora posto per {party_size} persone!\n"
               f"Tavolo: {table.get('name', '?')}{zone_info_it}\n\n"
               f"Desidera che prenoti subito?"),
        "en": (f"Yes, on {date_str} at {time_str} we have a table for {party_size} guests!\n"
               f"Table: {table.get('name', '?')}\n\n"
               f"Shall I book it for you?"),
    }
    return msgs.get(language, msgs["de"])


def _show_available_slots(language, slots, d, party_size):
    date_str = d.strftime("%d.%m.%Y")
    slots_limited = slots[:6]

    slots_de = "\n".join([f"  {s['time']} Uhr ({s.get('best_table', '')})" for s in slots_limited])
    slots_it = "\n".join([f"  ore {s['time']} ({s.get('best_table', '')})" for s in slots_limited])
    slots_en = "\n".join([f"  {s['time']} ({s.get('best_table', '')})" for s in slots_limited])

    msgs = {
        "de": (f"Am {date_str} haben wir folgende freie Zeiten für {party_size} Personen:\n\n"
               f"{slots_de}\n\n"
               f"Welche Zeit passt Ihnen am besten?"),
        "it": (f"Il {date_str} abbiamo questi orari liberi per {party_size} persone:\n\n"
               f"{slots_it}\n\n"
               f"Quale orario preferisce?"),
        "en": (f"On {date_str} we have these available times for {party_size} guests:\n\n"
               f"{slots_en}\n\n"
               f"Which time works best for you?"),
    }
    return msgs.get(language, msgs["de"])


def _closed_message(language, d):
    date_str = d.strftime("%d.%m.%Y")
    day_names_de = {0:"Montag",1:"Dienstag",2:"Mittwoch",3:"Donnerstag",4:"Freitag",5:"Samstag",6:"Sonntag"}
    day_name = day_names_de.get(d.weekday(), "")

    msgs = {
        "de": f"Am {date_str} ({day_name}) haben wir leider Ruhetag. Möchten Sie einen anderen Tag versuchen?",
        "it": f"Il {date_str} siamo chiusi. Desidera provare un altro giorno?",
        "en": f"We're closed on {date_str}. Would you like to try another day?",
    }
    return msgs.get(language, msgs["de"])


def _outside_hours_message(language, d, tenant):
    msgs = {
        "de": "Zu dieser Uhrzeit nehmen wir leider keine Reservierungen an. Unsere Servicezeiten: Mittag 11:30-14:00, Abend 18:00-22:00. Möchten Sie eine andere Uhrzeit?",
        "it": "A quest'ora non accettiamo prenotazioni. I nostri orari: pranzo 11:30-14:00, cena 18:00-22:00. Desidera un altro orario?",
        "en": "We don't accept reservations at this time. Our service hours: lunch 11:30-14:00, dinner 18:00-22:00. Would you like another time?",
    }
    return msgs.get(language, msgs["de"])


def _no_slots_message(language, d, party_size):
    date_str = d.strftime("%d.%m.%Y")
    msgs = {
        "de": f"Leider sind am {date_str} für {party_size} Personen keine Plätze mehr frei. Möchten Sie einen anderen Tag versuchen?",
        "it": f"Purtroppo il {date_str} non ci sono posti disponibili per {party_size} persone. Desidera provare un altro giorno?",
        "en": f"Unfortunately, we're fully booked on {date_str} for {party_size} guests. Would you like to try another day?",
    }
    return msgs.get(language, msgs["de"])


def _unavailable_message(language, alternatives, target_date, party_size):
    date_str = target_date.strftime("%d.%m.%Y")

    if alternatives:
        alt_de = "\n".join([f"  {a['time']} Uhr" for a in alternatives[:3]])
        alt_it = "\n".join([f"  ore {a['time']}" for a in alternatives[:3]])
        alt_en = "\n".join([f"  {a['time']}" for a in alternatives[:3]])

        msgs = {
            "de": (f"Leider ist dieser Zeitpunkt am {date_str} für {party_size} Personen ausgebucht.\n\n"
                   f"Folgende Zeiten wären noch verfügbar:\n{alt_de}\n\n"
                   f"Soll ich einen dieser Termine reservieren?"),
            "it": (f"Purtroppo questo orario per il {date_str} per {party_size} persone e al completo.\n\n"
                   f"Questi orari sono ancora disponibili:\n{alt_it}\n\n"
                   f"Desidera prenotare uno di questi?"),
            "en": (f"Unfortunately this time on {date_str} for {party_size} guests is fully booked.\n\n"
                   f"These times are still available:\n{alt_en}\n\n"
                   f"Would you like me to book one of these?"),
        }
    else:
        msgs = {
            "de": f"Am {date_str} sind wir für {party_size} Personen komplett ausgebucht. Möchten Sie einen anderen Tag versuchen?",
            "it": f"Il {date_str} siamo al completo per {party_size} persone. Desidera provare un altro giorno?",
            "en": f"We're fully booked on {date_str} for {party_size} guests. Would you like to try a different day?",
        }

    return msgs.get(language, msgs["de"])


def _ask_missing_info(language, missing, entities):
    if "date" in missing and "time" in missing and "party_size" in missing:
        msgs = {"de": "Gerne reserviere ich einen Tisch! Für wann (Datum und Uhrzeit) und für wie viele Personen?",
                "it": "Con piacere le riservo un tavolo! Per quando (data e ora) e per quante persone?",
                "en": "I'd be happy to reserve a table! For when (date and time) and how many guests?"}
    elif "date" in missing:
        msgs = {"de": "Für welches Datum möchten Sie reservieren?",
                "it": "Per quale data desidera prenotare?",
                "en": "For which date would you like to reserve?"}
    elif "time" in missing:
        msgs = {"de": "Um welche Uhrzeit möchten Sie kommen?",
                "it": "A che ora desidera venire?",
                "en": "What time would you like to come?"}
    elif "party_size" in missing:
        msgs = {"de": "Für wie viele Personen soll ich reservieren?",
                "it": "Per quante persone devo prenotare?",
                "en": "How many guests will be dining?"}
    else:
        msgs = {"de": "Könnten Sie mir bitte die Details für Ihre Reservierung geben?",
                "it": "Puo darmi i dettagli per la sua prenotazione?",
                "en": "Could you give me the details for your reservation?"}
    return msgs.get(language, msgs["de"])


def _confirmation_message(language, res_date, res_time, party_size, guest_name=None, table_name=None, zone=None):
    date_str = res_date.strftime("%d.%m.%Y")
    time_str = res_time.strftime("%H:%M")
    zone_de = {"innen": "Innenbereich", "terrasse": "Terrasse", "stube": "Stube", "garten": "Garten"}

    name_de = f" auf den Namen {guest_name}" if guest_name else ""
    name_it = f" a nome {guest_name}" if guest_name else ""
    name_en = f" under the name {guest_name}" if guest_name else ""
    table_de = f"\nTisch: {table_name}" + (f" ({zone_de.get(zone, zone)})" if zone else "") if table_name else ""

    msgs = {
        "de": (f"Reservierung bestaetigt!\n\n"
               f"{date_str}\n"
               f"{time_str} Uhr\n"
               f"{party_size} Personen{name_de}{table_de}\n\n"
               f"Wir freuen uns auf Sie!"),
        "it": (f"Prenotazione confermata!\n\n"
               f"{date_str}\n"
               f"ore {time_str}\n"
               f"{party_size} persone{name_it}\n\n"
               f"Vi aspettiamo!"),
        "en": (f"Reservation confirmed!\n\n"
               f"{date_str}\n"
               f"{time_str}\n"
               f"{party_size} guests{name_en}\n\n"
               f"We look forward to seeing you!"),
    }
    return msgs.get(language, msgs["de"])


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
    if lower in ("uebermorgen", "dopodomani", "day after tomorrow"):
        return today + timedelta(days=2)

    # "naechsten Freitag" etc.
    day_map = {"montag":0,"dienstag":1,"mittwoch":2,"donnerstag":3,"freitag":4,"samstag":5,"sonntag":6,
               "lunedi":0,"martedi":1,"mercoledi":2,"giovedi":3,"venerdi":4,"sabato":5,"domenica":6,
               "monday":0,"tuesday":1,"wednesday":2,"thursday":3,"friday":4,"saturday":5,"sunday":6}
    for day_name, day_num in day_map.items():
        if day_name in lower:
            days_ahead = day_num - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            return today + timedelta(days=days_ahead)

    raise ValueError(f"Unbekanntes Datum: {date_str}")


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

    raise ValueError(f"Unbekannte Uhrzeit: {time_str}")
