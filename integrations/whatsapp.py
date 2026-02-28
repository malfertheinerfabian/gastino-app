"""
Gastino.ai — WhatsApp Cloud API Integration
Wrapper für die Meta WhatsApp Business Cloud API.
"""
import logging
import requests

logger = logging.getLogger("gastino.whatsapp")

BASE_URL = "https://graph.facebook.com/v21.0"


def send_text_message(phone_number_id: str, to: str, text: str, token: str) -> dict:
    """
    Sendet eine Textnachricht über die WhatsApp Cloud API.

    Args:
        phone_number_id: Die Phone Number ID des Business-Accounts
        to: Empfänger WhatsApp-ID (z.B. "4917612345678")
        text: Nachrichtentext
        token: WhatsApp API Access Token

    Returns:
        API Response als dict
    """
    url = f"{BASE_URL}/{phone_number_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Nachricht gesendet an {to[-4:]}: {text[:50]}...")
        return data

    except requests.exceptions.HTTPError as e:
        error_data = {}
        try:
            error_data = e.response.json()
        except Exception:
            pass
        logger.error(f"WhatsApp API Fehler: {e}\nResponse: {error_data}")
        return {"error": str(e), "details": error_data}

    except requests.exceptions.RequestException as e:
        logger.error(f"WhatsApp Verbindungsfehler: {e}")
        return {"error": str(e)}


def send_template_message(phone_number_id: str, to: str, template_name: str,
                          language_code: str, token: str,
                          components: list = None) -> dict:
    """
    Sendet eine Template-Nachricht (für erste Kontaktaufnahme / Marketing).
    WhatsApp erlaubt nur Template-Nachrichten für die erste Nachricht an einen Nutzer.
    """
    url = f"{BASE_URL}/{phone_number_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
        }
    }

    if components:
        payload["template"]["components"] = components

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        logger.error(f"Template-Nachricht Fehler: {e}")
        return {"error": str(e)}


def mark_as_read(phone_number_id: str, message_id: str, token: str) -> dict:
    """Markiert eine Nachricht als gelesen (blaue Häkchen)."""
    url = f"{BASE_URL}/{phone_number_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        logger.warning(f"Mark-as-read Fehler: {e}")
        return {"error": str(e)}


def get_media_url(media_id: str, token: str) -> str:
    """Holt die Download-URL für ein Media-Objekt (Bild, Audio, etc.)."""
    url = f"{BASE_URL}/{media_id}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get("url")
    except Exception as e:
        logger.error(f"Media URL Fehler: {e}")
        return None
