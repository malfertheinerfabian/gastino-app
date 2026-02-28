"""
Gastino.ai — WhatsApp Webhook Handler
Empfängt alle eingehenden WhatsApp-Nachrichten und orchestriert die Verarbeitung.
"""
import logging
from flask import Blueprint, request, current_app, jsonify

from models.database import db, Tenant, Guest, Conversation, Message
from core.intent_engine import analyze_message
from core.message_router import route_message

logger = logging.getLogger("gastino.webhook")
webhook_bp = Blueprint("webhook", __name__)


@webhook_bp.route("/webhook", methods=["GET"])
def verify_webhook():
    """Meta Webhook-Verifizierung (einmalig bei Setup)."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == current_app.config["WHATSAPP_VERIFY_TOKEN"]:
        logger.info("Webhook verifiziert!")
        return challenge, 200

    logger.warning(f"Webhook-Verifizierung fehlgeschlagen: mode={mode}, token={token}")
    return "Forbidden", 403


@webhook_bp.route("/webhook", methods=["POST"])
def receive_message():
    """Haupteingang für alle WhatsApp-Nachrichten."""
    data = request.json

    if not data:
        return "OK", 200

    try:
        # Meta sendet verschiedene Event-Typen
        entry = data.get("entry", [])
        for e in entry:
            changes = e.get("changes", [])
            for change in changes:
                value = change.get("value", {})

                # Status-Updates ignorieren (delivered, read, etc.)
                if "statuses" in value:
                    continue

                messages = value.get("messages", [])
                if not messages:
                    continue

                # Metadata: Welche WhatsApp-Nummer wurde kontaktiert?
                metadata = value.get("metadata", {})
                phone_number_id = metadata.get("phone_number_id")

                for msg in messages:
                    process_incoming_message(phone_number_id, msg, value)

    except Exception as ex:
        logger.error(f"Webhook-Fehler: {ex}", exc_info=True)

    # Immer 200 zurückgeben — Meta wiederholt sonst
    return "OK", 200


def process_incoming_message(phone_number_id: str, msg: dict, value: dict):
    """Verarbeitet eine einzelne eingehende Nachricht."""

    msg_type = msg.get("type")
    sender_wa_id = msg.get("from")  # Gast WhatsApp-ID

    # Nur Textnachrichten verarbeiten (Bilder, Audio etc. → v2)
    if msg_type != "text":
        logger.info(f"Nicht-Text-Nachricht ignoriert: type={msg_type}")
        return

    text = msg.get("text", {}).get("body", "").strip()
    if not text:
        return

    logger.info(f"Nachricht empfangen von {sender_wa_id}: {text[:80]}...")

    # ─── 1. Tenant identifizieren ───
    tenant = Tenant.query.filter_by(
        whatsapp_phone_id=phone_number_id,
        active=True
    ).first()

    if not tenant:
        logger.warning(f"Kein Tenant für phone_id={phone_number_id}")
        return

    # ─── 2. Prüfen ob es eine Gruppen-Nachricht ist (Staff-Antwort) ───
    # Gruppen-Nachrichten haben ein "group" Feld — das ist z.B. die Bar die ✅ antwortet
    if is_group_message(value):
        handle_group_reply(tenant, msg, value)
        return

    # ─── 3. Gast identifizieren oder anlegen ───
    guest = get_or_create_guest(tenant, sender_wa_id, value)

    # ─── 4. Conversation holen oder erstellen ───
    conversation = get_active_conversation(tenant, guest)

    # ─── 5. Nachricht speichern ───
    save_message(conversation, text, "inbound", "guest")

    # ─── 6. Konversationshistorie laden ───
    history = get_conversation_history(conversation)

    # ─── 7. Intent analysieren (Claude) ───
    analysis = analyze_message(
        tenant=tenant,
        guest=guest,
        text=text,
        history=history,
        model=current_app.config["CLAUDE_MODEL"],
        api_key=current_app.config["ANTHROPIC_API_KEY"]
    )

    logger.info(f"Intent: {analysis.get('intent')} (confidence: {analysis.get('confidence', 0):.2f})")

    # ─── 8. Nachricht routen ───
    response_text = route_message(
        tenant=tenant,
        guest=guest,
        conversation=conversation,
        analysis=analysis,
        history=history,
        config=current_app.config
    )

    # ─── 9. Antwort speichern und senden ───
    if response_text:
        save_message(conversation, response_text, "outbound", "ai",
                     metadata={"intent": analysis.get("intent"),
                               "confidence": analysis.get("confidence")})

        from integrations.whatsapp import send_text_message
        send_text_message(
            phone_number_id=phone_number_id,
            to=sender_wa_id,
            text=response_text,
            token=current_app.config["WHATSAPP_TOKEN"]
        )

    # ─── 10. Conversation updaten ───
    conversation.last_intent = analysis.get("intent")
    db.session.commit()


# ─── HELPER FUNCTIONS ───────────────────────────────────

def get_or_create_guest(tenant: Tenant, wa_id: str, value: dict) -> Guest:
    """Gast finden oder neu anlegen."""
    guest = Guest.query.filter_by(
        tenant_id=tenant.id,
        whatsapp_id=wa_id
    ).first()

    if not guest:
        # Name aus WhatsApp-Profil extrahieren (wenn verfügbar)
        contacts = value.get("contacts", [])
        name = None
        if contacts:
            profile = contacts[0].get("profile", {})
            name = profile.get("name")

        guest = Guest(
            tenant_id=tenant.id,
            whatsapp_id=wa_id,
            name=name,
            language="de"  # Default, wird beim ersten Intent-Check aktualisiert
        )
        db.session.add(guest)
        db.session.commit()
        logger.info(f"Neuer Gast angelegt: {name or wa_id}")

    return guest


def get_active_conversation(tenant: Tenant, guest: Guest) -> Conversation:
    """Aktive Konversation finden oder neue erstellen."""
    conv = Conversation.query.filter_by(
        tenant_id=tenant.id,
        guest_id=guest.id,
        status="active"
    ).first()

    if not conv:
        conv = Conversation(
            tenant_id=tenant.id,
            guest_id=guest.id,
            status="active"
        )
        db.session.add(conv)
        db.session.commit()

    return conv


def save_message(conversation: Conversation, content: str, direction: str,
                 sender_type: str, metadata: dict = None):
    """Nachricht in DB speichern."""
    msg = Message(
        conversation_id=conversation.id,
        direction=direction,
        sender_type=sender_type,
        content=content,
        metadata_json=metadata
    )
    db.session.add(msg)
    db.session.commit()
    return msg


def get_conversation_history(conversation: Conversation, limit: int = 20) -> list:
    """Letzte N Nachrichten der Konversation als Chat-History für Claude."""
    messages = (
        Message.query
        .filter_by(conversation_id=conversation.id)
        .order_by(Message.created_at.desc())
        .limit(limit)
        .all()
    )
    messages.reverse()  # Chronologische Reihenfolge

    history = []
    for m in messages:
        role = "user" if m.direction == "inbound" else "assistant"
        history.append({"role": role, "content": m.content})

    return history


def is_group_message(value: dict) -> bool:
    """Prüft ob die Nachricht aus einer WhatsApp-Gruppe kommt."""
    messages = value.get("messages", [])
    if messages:
        # Gruppen-Nachrichten haben ein zusätzliches "context" oder "group_id" Feld
        # In der Meta Cloud API: Prüfe ob es ein "group" context gibt
        return messages[0].get("context", {}).get("group_id") is not None
    return False


def handle_group_reply(tenant: Tenant, msg: dict, value: dict):
    """
    Verarbeitet Antworten aus Staff-WhatsApp-Gruppen.
    z.B. Bar antwortet mit ✅ auf eine Bestellung.
    """
    text = msg.get("text", {}).get("body", "").strip()

    if current_app.config["ORDER_CONFIRMATION_EMOJI"] in text:
        # Letzte unbestätigte Bestellung für diese Gruppe finden
        from core.order_processor import confirm_latest_order
        group_id = msg.get("context", {}).get("group_id")
        confirm_latest_order(tenant, group_id)

    logger.info(f"Gruppen-Antwort verarbeitet: {text[:50]}")
