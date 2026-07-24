"""Доступ к модели через OpenAI-совместимый прокси routerai.ru.

Так же, как в соседних проектах владельца (ceramic, Vlad): у него там team-ключ с
контролем бюджета, прямой биллинг у провайдера не настроен. Прямой вызов Anthropic
оставлен запасным путём — он включается, только если задан свой ключ и не задан
роутерный.

Ответ модели ждём в JSON: генератору нужны заголовок, подзаголовок и тело отдельно.
"""
import json
import logging
import re

import httpx

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://routerai.ru/api/v1"
TIMEOUT = 120.0

PROVIDER_ROUTER = "router"
PROVIDER_ANTHROPIC = "anthropic"


class LLMError(RuntimeError):
    """Модель недоступна или ответила ошибкой — генератор уйдёт в шаблон-фолбэк."""


class LLMNoFunds(LLMError):
    """На ключе кончились деньги: отдельный случай, о нём стоит сказать прямо."""


def _extract_json(text: str) -> dict | None:
    """Достать объект из ответа: модель иногда оборачивает JSON в ```json.``` или текст."""
    text = text.strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _call_router(prompt: str, settings, model: str | None = None) -> tuple[dict, str]:
    url = f"{settings.router_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model or settings.router_model,
        "max_tokens": settings.llm_max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        response = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {settings.router_api_key}"},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise LLMError(f"Нет связи с routerai.ru: {exc}") from exc

    if response.status_code == 402:
        raise LLMNoFunds("На ключе routerai.ru закончились деньги — пополните баланс.")
    if response.status_code >= 400:
        raise LLMError(f"routerai.ru ответил {response.status_code}: {response.text[:200]}")

    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError) as exc:
        raise LLMError("Не разобрал ответ routerai.ru") from exc

    parsed = _extract_json(content)
    if parsed is None:
        raise LLMError("Модель вернула не JSON")

    usage = data.get("usage") or {}
    if usage:
        log.info(
            "routerai %s: вход %s, выход %s токенов",
            data.get("model"), usage.get("prompt_tokens"), usage.get("completion_tokens"),
        )
    return parsed, data.get("model", model or settings.router_model)


def _call_anthropic(prompt: str, settings) -> tuple[dict, str]:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - пакет объявлен в requirements
        raise LLMError("Пакет anthropic не установлен") from exc

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        message = client.messages.create(
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.llm_effort},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.RateLimitError as exc:
        raise LLMError(f"Лимит запросов: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Ошибка API ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError(f"Нет связи: {exc}") from exc

    if message.stop_reason == "refusal":
        raise LLMError("Модель отказалась генерировать текст")

    text = next((block.text for block in message.content if block.type == "text"), "")
    parsed = _extract_json(text)
    if parsed is None:
        raise LLMError("Модель вернула не JSON")
    return parsed, message.model


def provider(settings) -> str | None:
    """Какой путь используем: роутер приоритетнее, как в соседних проектах."""
    if settings.router_api_key:
        return PROVIDER_ROUTER
    if settings.anthropic_api_key:
        return PROVIDER_ANTHROPIC
    return None


def generate_json(prompt: str, settings, *, short: bool = False) -> tuple[dict, str]:
    """Ответ модели как словарь плюс имя модели. Бросает LLMError, если не вышло.

    `short=True` — для постов в мессенджеры: там нет SEO-ключей и объём меньше,
    поэтому берётся модель побыстрее и подешевле.
    """
    chosen = provider(settings)
    if chosen == PROVIDER_ROUTER:
        model = settings.router_model_short if short else settings.router_model
        return _call_router(prompt, settings, model)
    if chosen == PROVIDER_ANTHROPIC:
        return _call_anthropic(prompt, settings)
    raise LLMError("Ключ модели не задан")
