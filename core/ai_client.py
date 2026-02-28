"""
Gastino.ai - AI Client Abstraction
Unterstuetzt Anthropic (Claude), OpenAI, Groq, und jede OpenAI-kompatible API.

Konfiguration in .env:
  AI_PROVIDER=anthropic|openai|groq|openrouter
  AI_API_KEY=dein-key
  AI_MODEL=model-name
  AI_BASE_URL=https://custom-endpoint (optional, fuer OpenAI-kompatible APIs)
"""
import json
import logging
import requests

logger = logging.getLogger("gastino.ai")


def chat_completion(system_prompt: str, user_message: str, config: dict,
                    temperature: float = 0.1, max_tokens: int = 500) -> str:
    """
    Universeller Chat-Completion Call.
    Unterstuetzt Anthropic und OpenAI-kompatible APIs.
    Returns: Raw text response from the AI model.
    """
    provider = config.get("AI_PROVIDER", "anthropic")
    api_key = config.get("AI_API_KEY") or config.get("ANTHROPIC_API_KEY")
    model = config.get("AI_MODEL") or config.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250514")

    if not api_key:
        logger.error("Kein AI API Key konfiguriert!")
        raise ValueError("AI_API_KEY oder ANTHROPIC_API_KEY fehlt in .env")

    if provider == "anthropic":
        return _call_anthropic(system_prompt, user_message, api_key, model, temperature, max_tokens)
    else:
        # OpenAI, Groq, OpenRouter, oder jede OpenAI-kompatible API
        base_url = config.get("AI_BASE_URL", _default_base_url(provider))
        return _call_openai_compatible(system_prompt, user_message, api_key, model, base_url, temperature, max_tokens)


def _call_anthropic(system_prompt, user_message, api_key, model, temperature, max_tokens):
    """Anthropic Claude API Call."""
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        temperature=temperature,
    )
    return response.content[0].text.strip()


def _call_openai_compatible(system_prompt, user_message, api_key, model, base_url, temperature, max_tokens):
    """OpenAI-kompatible API Call (OpenAI, Groq, OpenRouter, etc.)."""
    url = f"{base_url}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except requests.exceptions.HTTPError as e:
        error_body = ""
        try:
            error_body = e.response.text
        except Exception:
            pass
        logger.error(f"AI API Fehler ({base_url}): {e}\n{error_body}")
        raise
    except Exception as e:
        logger.error(f"AI API Verbindungsfehler: {e}")
        raise


def _default_base_url(provider: str) -> str:
    """Standard-URLs fuer bekannte Provider."""
    urls = {
        "openai": "https://api.openai.com/v1",
        "groq": "https://api.groq.com/openai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "together": "https://api.together.xyz/v1",
        "mistral": "https://api.mistral.ai/v1",
    }
    return urls.get(provider, "https://api.openai.com/v1")
