"""
Gastino.ai — Formatters
Formatiert Nachrichten für Staff-WhatsApp-Gruppen und Gäste-Bestätigungen.
"""
from datetime import datetime, timezone


def format_order_for_staff(order, guest, department) -> str:
    """Formatiert eine Bestellung für die Staff-WhatsApp-Gruppe."""
    now = datetime.now(timezone.utc).strftime("%H:%M")

    # Items formatieren
    items_lines = []
    for item in order.items:
        qty = item.get("qty", 1)
        name = item.get("name", "?")
        notes = item.get("notes", "")
        line = f"  {qty}x {name}"
        if notes:
            line += f" ({notes})"
        items_lines.append(line)

    items_str = "\n".join(items_lines)

    # Location
    if order.room_number:
        location = f"📍 Zimmer {order.room_number}"
    elif order.table_number:
        location = f"📍 Tisch {order.table_number}"
    else:
        location = "📍 Unbekannt"

    # Gast-Info
    guest_name = guest.name or guest.whatsapp_id[-4:]  # Letzte 4 Ziffern als Fallback

    msg = (
        f"🔔 {'ROOMSERVICE' if order.type == 'roomservice' else 'BESTELLUNG'}\n"
        f"━━━━━━━━━━━━━━\n"
        f"{location}\n"
        f"👤 {guest_name}\n"
        f"\n"
        f"{items_str}\n"
        f"\n"
        f"⏰ Bestellt: {now}\n"
        f"📱 Order-ID: {order.id[:8]}\n"
        f"\n"
        f"✅ Zum Bestätigen antworten"
    )

    return msg


def format_order_confirmation_for_guest(language: str, items: list,
                                        room: str = None, table: str = None,
                                        location_type: str = "room") -> str:
    """Formatiert die Bestellbestätigung für den Gast."""
    items_str = ", ".join([
        f"{i.get('qty', 1)}x {i.get('name', '?')}" for i in items
    ])

    if location_type == "room" and room:
        loc_de = f"aufs Zimmer {room}"
        loc_it = f"in camera {room}"
        loc_en = f"to room {room}"
    elif table:
        loc_de = f"an Tisch {table}"
        loc_it = f"al tavolo {table}"
        loc_en = f"to table {table}"
    else:
        loc_de = ""
        loc_it = ""
        loc_en = ""

    msgs = {
        "de": f"Perfekt! Ihre Bestellung ({items_str}) kommt {loc_de}. Geschätzte Wartezeit: ca. 10-15 Minuten. 🍹",
        "it": f"Perfetto! Il suo ordine ({items_str}) arriva {loc_it}. Tempo di attesa stimato: circa 10-15 minuti. 🍹",
        "en": f"Perfect! Your order ({items_str}) is on its way {loc_en}. Estimated wait: about 10-15 minutes. 🍹",
    }

    return msgs.get(language, msgs["de"])


def format_escalation_for_staff(guest, analysis: dict, history: list) -> str:
    """Formatiert eine Eskalation für die Staff-Gruppe."""
    intent = analysis.get("intent", "unknown")
    language = analysis.get("language", "?")

    # Letzte Gast-Nachricht
    last_msg = ""
    for msg in reversed(history):
        if msg["role"] == "user":
            last_msg = msg["content"]
            break

    guest_name = guest.name or guest.whatsapp_id
    room = guest.room_number or "-"

    msg = (
        f"⚠️ WEITERLEITUNG\n"
        f"━━━━━━━━━━━━━━\n"
        f"👤 {guest_name}\n"
        f"📍 Zimmer {room}\n"
        f"🏷️ Intent: {intent}\n"
        f"🌍 Sprache: {language.upper()}\n"
        f"\n"
        f"💬 Nachricht:\n"
        f'"{last_msg[:300]}"\n'
        f"\n"
        f"Bitte direkt antworten via WhatsApp."
    )

    return msg


def format_housekeeping_for_staff(guest, analysis: dict) -> str:
    """Formatiert eine Housekeeping-Anfrage."""
    entities = analysis.get("entities", {})
    room = entities.get("room") or guest.room_number or "?"

    # Versuche die spezifische Anfrage zu extrahieren
    msg = (
        f"🧹 HOUSEKEEPING\n"
        f"━━━━━━━━━━━━━━\n"
        f"📍 Zimmer {room}\n"
        f"👤 {guest.name or guest.whatsapp_id}\n"
        f"\n"
        f"✅ Zum Bestätigen antworten"
    )

    return msg
