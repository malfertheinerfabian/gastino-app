"""
Gastino.ai - Response Generator
Generiert natuerliche AI-Antworten fuer Gaesteanfragen.
"""
import logging
from core.ai_client import chat_completion

logger = logging.getLogger("gastino.response")

RESPONSE_SYSTEM_PROMPT = """Du bist Gastino, der freundliche KI-Assistent fuer "{tenant_name}".
Du beantwortest Gaesteanfragen natuerlich, hoeflich und hilfreich.

DEINE REGELN:
1. Antworte IMMER in der Sprache des Gastes ({language})
2. Sei warm und gastfreundlich, aber professionell
3. Halte dich kurz - WhatsApp-Nachrichten sollten nicht laenger als 3-4 Saetze sein
4. Nutze passende Emojis, aber sparsam (max 2 pro Nachricht)
5. Wenn du eine Information NICHT weisst, sag es ehrlich und biete an weiterzuleiten
6. Erfinde NIEMALS Informationen (Preise, Oeffnungszeiten, etc.)
7. Wenn der Gast einen Namen genannt hat, nutze ihn gelegentlich

BETRIEBSINFORMATIONEN:
{tenant_context}

GAST:
{guest_context}

AKTUELLER INTENT: {intent}"""


def generate_response(tenant, guest, analysis, history, config):
    """Generiert eine AI-Antwort basierend auf Intent und Knowledge Base."""
    language = analysis.get("language", guest.language or "de")
    intent = analysis.get("intent", "general_question")

    guest_parts = []
    if guest.name:
        guest_parts.append(f"Name: {guest.name}")
    if guest.room_number:
        guest_parts.append(f"Zimmer: {guest.room_number}")
    guest_context = "\n".join(guest_parts) if guest_parts else "Keine Details bekannt."

    system = RESPONSE_SYSTEM_PROMPT.format(
        tenant_name=tenant.name,
        language=language,
        tenant_context=tenant.get_full_context(),
        guest_context=guest_context,
        intent=intent,
    )

    # Letzte Nachricht als User-Message
    last_msg = "Hallo"
    for msg in reversed(history):
        if msg["role"] == "user":
            last_msg = msg["content"]
            break

    try:
        text = chat_completion(
            system_prompt=system,
            user_message=last_msg,
            config=config,
            temperature=0.7,
            max_tokens=300,
        )
        logger.info(f"Response generiert ({len(text)} chars)")
        return text

    except Exception as e:
        logger.error(f"Response-Generator Fehler: {e}", exc_info=True)
        fallbacks = {
            "de": "Entschuldigung, ich habe gerade ein technisches Problem. Bitte versuchen Sie es erneut.",
            "it": "Mi scusi, sto riscontrando un problema tecnico. Per favore riprovi.",
            "en": "Sorry, I'm experiencing a technical issue. Please try again.",
        }
        return fallbacks.get(language, fallbacks["de"])
