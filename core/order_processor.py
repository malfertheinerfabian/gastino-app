"""
Gastino.ai — Order Processor
Verarbeitet Bestellungen (Roomservice, Tischbestellungen) und routet sie
an die richtige Abteilung (Küche, Bar) via WhatsApp-Gruppe.
"""
import logging
from datetime import datetime, timezone

from models.database import db, Order, Department, Guest
from integrations.whatsapp import send_text_message
from core.formatters import format_order_for_staff, format_order_confirmation_for_guest

logger = logging.getLogger("gastino.orders")

# Mapping: Intent → Department-Name
INTENT_TO_DEPARTMENT = {
    "roomservice_food": "küche",
    "roomservice_drink": "bar",
    "roomservice_mixed": None,  # Split: Food → Küche, Drinks → Bar
    "order_at_table": "küche",  # Default, wird ggf. gesplittet
}

# Getränke-Keywords für automatisches Splitting
DRINK_KEYWORDS = {
    "bier", "wein", "prosecco", "aperol", "spritz", "cocktail", "gin",
    "tonic", "whiskey", "vodka", "rum", "hugo", "negroni", "campari",
    "cola", "fanta", "sprite", "saft", "juice", "wasser", "acqua",
    "birra", "vino", "caffè", "kaffee", "espresso", "cappuccino",
    "tee", "tea", "tè", "limo", "limonade", "beer", "wine",
    "mojito", "margarita", "caipirinha", "daiquiri", "martini",
    "grappa", "limoncello", "amaretto", "sambuca",
}


def process_order(tenant, guest, conversation, analysis: dict, config: dict) -> str:
    """
    Verarbeitet eine Bestellung und routet sie an die richtige Abteilung.
    """
    intent = analysis.get("intent")
    language = analysis.get("language", "de")
    entities = analysis.get("entities", {})
    items = entities.get("items", [])
    room = entities.get("room") or guest.room_number
    table = entities.get("table") or guest.table_number

    if not items:
        # Keine Items erkannt — nachfragen
        return _ask_for_details(language, intent)

    # Location (Zimmer oder Tisch) bestimmen
    location_type = "room" if "roomservice" in intent else "table"
    location = room if location_type == "room" else table

    if location_type == "room" and not location:
        return _ask_for_room(language)

    # ─── Bestellung splitten wenn nötig ───
    if intent == "roomservice_mixed" or _has_mixed_items(items):
        food_items, drink_items = _split_items(items)
    elif intent == "roomservice_drink" or _all_drinks(items):
        food_items, drink_items = [], items
    else:
        food_items, drink_items = items, []

    orders_created = []

    # ─── Food-Bestellung → Küche ───
    if food_items:
        order = _create_and_route_order(
            tenant=tenant,
            guest=guest,
            items=food_items,
            dept_name="küche",
            order_type="roomservice" if "roomservice" in intent else "table_order",
            room=room,
            table=table,
            config=config,
        )
        if order:
            orders_created.append(("küche", order))

    # ─── Drink-Bestellung → Bar ───
    if drink_items:
        order = _create_and_route_order(
            tenant=tenant,
            guest=guest,
            items=drink_items,
            dept_name="bar",
            order_type="roomservice" if "roomservice" in intent else "table_order",
            room=room,
            table=table,
            config=config,
        )
        if order:
            orders_created.append(("bar", order))

    # ─── Alles an Default-Abteilung wenn kein Split ───
    if not orders_created and items:
        dept_name = INTENT_TO_DEPARTMENT.get(intent, "küche")
        order = _create_and_route_order(
            tenant=tenant,
            guest=guest,
            items=items,
            dept_name=dept_name or "küche",
            order_type="roomservice" if "roomservice" in intent else "table_order",
            room=room,
            table=table,
            config=config,
        )
        if order:
            orders_created.append((dept_name, order))

    if not orders_created:
        return _fallback_response(language)

    # ─── Bestätigung an Gast ───
    return format_order_confirmation_for_guest(
        language=language,
        items=items,
        room=room,
        table=table,
        location_type=location_type,
    )


def _create_and_route_order(tenant, guest, items: list, dept_name: str,
                            order_type: str, room: str, table: str,
                            config: dict) -> Order:
    """Erstellt eine Bestellung in der DB und sendet sie an die WhatsApp-Gruppe."""

    # Abteilung finden
    dept = Department.query.filter_by(
        tenant_id=tenant.id,
        name=dept_name,
        active=True
    ).first()

    # Fallback wenn Abteilung nicht existiert
    if not dept:
        dept = Department.query.filter_by(
            tenant_id=tenant.id,
            is_escalation=True,
            active=True
        ).first()

    if not dept:
        logger.warning(f"Keine Abteilung '{dept_name}' für Tenant {tenant.name}")
        return None

    # Prüfen ob Abteilung geöffnet ist
    target_dept = dept
    if not dept.is_open_now() and dept.fallback_dept_id:
        fallback = Department.query.get(dept.fallback_dept_id)
        if fallback:
            target_dept = fallback
            logger.info(f"{dept_name} geschlossen → Fallback auf {fallback.name}")

    # Order in DB speichern
    order = Order(
        tenant_id=tenant.id,
        guest_id=guest.id,
        department_id=target_dept.id,
        type=order_type,
        items=[{"name": i.get("name", "?"), "qty": i.get("qty", 1),
                "notes": i.get("notes", "")} for i in items],
        room_number=room,
        table_number=table,
        status="pending",
    )
    db.session.add(order)
    db.session.commit()

    logger.info(f"Order erstellt: {order.id} → {target_dept.name} "
                f"({len(items)} Items, Zimmer {room or '-'}, Tisch {table or '-'})")

    # An WhatsApp-Gruppe senden
    if target_dept.whatsapp_group_id:
        staff_msg = format_order_for_staff(order, guest, target_dept)
        send_text_message(
            phone_number_id=tenant.whatsapp_phone_id,
            to=target_dept.whatsapp_group_id,
            text=staff_msg,
            token=config["WHATSAPP_TOKEN"],
        )
    else:
        logger.warning(f"Keine WhatsApp-Gruppe für Abteilung {target_dept.name}")

    return order


def confirm_latest_order(tenant, group_id: str):
    """
    Bestätigt die letzte offene Bestellung für eine WhatsApp-Gruppe.
    Wird aufgerufen wenn ein Mitarbeiter ✅ in der Gruppe sendet.
    """
    dept = Department.query.filter_by(
        tenant_id=tenant.id,
        whatsapp_group_id=group_id,
        active=True
    ).first()

    if not dept:
        return

    order = (
        Order.query
        .filter_by(tenant_id=tenant.id, department_id=dept.id, status="pending")
        .order_by(Order.created_at.desc())
        .first()
    )

    if order:
        order.status = "confirmed"
        order.confirmed_at = datetime.now(timezone.utc)
        db.session.commit()

        logger.info(f"Order {order.id} bestätigt von {dept.name}")

        # Optional: Gast benachrichtigen
        guest = Guest.query.get(order.guest_id)
        if guest:
            lang = guest.language or "de"
            msgs = {
                "de": "Ihre Bestellung wird gerade zubereitet! 👨‍🍳",
                "it": "Il suo ordine è in preparazione! 👨‍🍳",
                "en": "Your order is being prepared! 👨‍🍳",
            }
            send_text_message(
                phone_number_id=tenant.whatsapp_phone_id,
                to=guest.whatsapp_id,
                text=msgs.get(lang, msgs["de"]),
                token="",  # TODO: aus Config holen
            )


# ─── HELPER FUNCTIONS ───────────────────────────────────

def _split_items(items: list) -> tuple:
    """Splittet Items in Food und Drinks."""
    food, drinks = [], []
    for item in items:
        name_lower = item.get("name", "").lower()
        if any(kw in name_lower for kw in DRINK_KEYWORDS):
            drinks.append(item)
        else:
            food.append(item)
    return food, drinks


def _has_mixed_items(items: list) -> bool:
    """Prüft ob sowohl Food als auch Drinks bestellt wurden."""
    food, drinks = _split_items(items)
    return bool(food) and bool(drinks)


def _all_drinks(items: list) -> bool:
    """Prüft ob alle Items Getränke sind."""
    _, drinks = _split_items(items)
    return len(drinks) == len(items) and len(items) > 0


def _ask_for_details(language: str, intent: str) -> str:
    """Fragt nach Details wenn keine Items erkannt wurden."""
    msgs = {
        "de": "Gerne! Was genau darf ich für Sie bestellen? 📋",
        "it": "Con piacere! Cosa posso ordinarle esattamente? 📋",
        "en": "Of course! What exactly can I order for you? 📋",
    }
    return msgs.get(language, msgs["de"])


def _ask_for_room(language: str) -> str:
    """Fragt nach Zimmernummer."""
    msgs = {
        "de": "Gerne! Aus welchem Zimmer schreiben Sie? 🏨",
        "it": "Con piacere! Da quale camera ci scrive? 🏨",
        "en": "Of course! Which room are you writing from? 🏨",
    }
    return msgs.get(language, msgs["de"])


def _fallback_response(language: str) -> str:
    msgs = {
        "de": "Entschuldigung, ich konnte Ihre Bestellung nicht verarbeiten. Bitte versuchen Sie es erneut oder kontaktieren Sie die Rezeption. 🙏",
        "it": "Mi scusi, non sono riuscito a elaborare il suo ordine. Per favore riprovi o contatti la reception. 🙏",
        "en": "Sorry, I couldn't process your order. Please try again or contact the reception. 🙏",
    }
    return msgs.get(language, msgs["de"])
