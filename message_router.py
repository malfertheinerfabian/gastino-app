"""
Gastino.ai - Message Router
Routet analysierte Nachrichten an den richtigen Handler.
"""
import logging
from core.response_generator import generate_response
from core.order_processor import process_order
from core.reservation_handler import process_reservation, process_availability

logger = logging.getLogger("gastino.router")

AUTO_REPLY_INTENTS = {"menu_question", "price_question", "checkin_info", "local_tips", "general_question", "thank_you", "greeting"}
ORDER_INTENTS = {"roomservice_food", "roomservice_drink", "roomservice_mixed", "order_at_table"}
RESERVATION_INTENTS = {"reservation"}
AVAILABILITY_INTENTS = {"availability"}
ESCALATION_INTENTS = {"complaint", "human_needed"}


def route_message(tenant, guest, conversation, analysis, history, config):
    intent = analysis.get("intent", "general_question")
    language = analysis.get("language", guest.language or "de")
    needs_human = analysis.get("needs_human", False)

    logger.info(f"Routing: intent={intent}, lang={language}, needs_human={needs_human}")

    if needs_human or intent in ESCALATION_INTENTS:
        return handle_escalation(tenant, guest, analysis, history, config)

    if intent in ORDER_INTENTS:
        return process_order(tenant, guest, conversation, analysis, config)

    if intent in RESERVATION_INTENTS:
        return process_reservation(tenant, guest, analysis, config)

    if intent in AVAILABILITY_INTENTS:
        return process_availability(tenant, guest, analysis, config)

    if intent == "cancel_order":
        return handle_cancellation(tenant, guest, language, config)

    if intent == "housekeeping":
        return handle_housekeeping(tenant, guest, analysis, config)

    if intent == "checkout":
        return handle_checkout(tenant, guest, analysis, config)

    if intent in AUTO_REPLY_INTENTS:
        return generate_response(tenant, guest, analysis, history, config)

    return generate_response(tenant, guest, analysis, history, config)


def handle_escalation(tenant, guest, analysis, history, config):
    language = analysis.get("language", "de")
    try:
        from models.database import Department
        dept = Department.query.filter_by(tenant_id=tenant.id, is_escalation=True, active=True).first()
        if dept and dept.whatsapp_group_id:
            from integrations.whatsapp import send_text_message
            from core.formatters import format_escalation_for_staff
            send_text_message(phone_number_id=tenant.whatsapp_phone_id, to=dept.whatsapp_group_id,
                text=format_escalation_for_staff(guest, analysis, history), token=config.get("WHATSAPP_TOKEN", ""))
    except Exception as e:
        logger.warning(f"Staff-Benachrichtigung fehlgeschlagen: {e}")

    return {"de": "Ich habe Ihre Anfrage an unser Team weitergeleitet. Jemand wird sich in Kürze bei Ihnen melden.",
            "it": "Ho inoltrato la sua richiesta al nostro team. Qualcuno la contattera a breve.",
            "en": "I've forwarded your request to our team. Someone will get back to you shortly."}.get(language, "Ich habe Ihre Anfrage an unser Team weitergeleitet.")


def handle_housekeeping(tenant, guest, analysis, config):
    language = analysis.get("language", "de")
    room = analysis.get("entities", {}).get("room") or guest.room_number
    r = f" (Zimmer {room})" if room else ""
    return {"de": f"Anfrage ans Housekeeping weitergeleitet{r}. Wir kümmern uns darum!",
            "it": f"Richiesta inoltrata al team pulizie{r}. Ce ne occuperemo!",
            "en": f"Request forwarded to housekeeping{r}. We'll take care of it!"}.get(language, f"Housekeeping informiert{r}.")


def handle_checkout(tenant, guest, analysis, config):
    language = analysis.get("language", "de")
    return {"de": "Rezeption informiert. Ihre Rechnung wird vorbereitet!",
            "it": "Reception informata. Il suo conto viene preparato!",
            "en": "Reception notified. Your bill is being prepared!"}.get(language, "Rezeption informiert.")


def handle_cancellation(tenant, guest, language, config):
    return {"de": "Stornierungsanfrage weitergeleitet. Wir melden uns!",
            "it": "Richiesta di cancellazione inoltrata. La contatteremo!",
            "en": "Cancellation request forwarded. We'll get back to you!"}.get(language, "Stornierung weitergeleitet.")
