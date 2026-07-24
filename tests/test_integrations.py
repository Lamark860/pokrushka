"""Клиенты Telegram Bot API и TGTrack — на подменённом httpx, без сети."""
import httpx
import pytest

from app.integrations.telegram import INVITE_NAME_LIMIT, TelegramClient, TelegramError
from app.integrations.telegram import build_client as build_telegram
from app.integrations.tgtrack import MAX_ROOT, TG_ROOT, TGTrackClient
from app.integrations.tgtrack import build_client as build_tgtrack


class FakePost:
    """Подменяет httpx.post: запоминает вызовы и отдаёт заготовленный ответ."""

    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, json=None, timeout=None, **kwargs):
        self.calls.append((url, json or {}))
        return httpx.Response(self.status_code, json=self.payload)


# ------------------------------------------------------------------ Telegram


def test_build_client_without_token_returns_none():
    assert build_telegram("") is None
    assert build_telegram("123:ABC") is not None


def test_create_invite_link_truncates_name(monkeypatch):
    fake = FakePost({"ok": True, "result": {"invite_link": "https://t.me/+hash", "name": "x"}})
    monkeypatch.setattr(httpx, "post", fake)

    client = TelegramClient("123:ABC")
    client.create_invite_link("-100123", "a" * 50)

    _, payload = fake.calls[0]
    assert len(payload["name"]) == INVITE_NAME_LIMIT
    assert payload["chat_id"] == "-100123"
    assert payload["creates_join_request"] is False


def test_telegram_error_carries_description(monkeypatch):
    monkeypatch.setattr(
        httpx, "post",
        FakePost({"ok": False, "description": "not enough rights", "error_code": 400}),
    )
    with pytest.raises(TelegramError, match="not enough rights"):
        TelegramClient("123:ABC").create_invite_link("-100123", "тест")


def test_telegram_network_failure_becomes_domain_error(monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("нет сети")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(TelegramError, match="нет связи"):
        TelegramClient("123:ABC").get_me()


# ------------------------------------------------------------------ TGTrack


def test_tgtrack_uses_platform_specific_endpoint(monkeypatch):
    fake = FakePost({"status": "OK", "data": {"user_id": "1"}})
    monkeypatch.setattr(httpx, "post", fake)

    TGTrackClient("KEY", platform="tg").get_user_info("1")
    TGTrackClient("KEY", platform="max").get_user_info("1")

    assert fake.calls[0][0].startswith(TG_ROOT)
    assert fake.calls[1][0].startswith(MAX_ROOT)
    assert fake.calls[0][1] == {"user_id": "1"}


def test_tgtrack_parses_utm_and_invite_link(monkeypatch):
    monkeypatch.setattr(
        httpx, "post",
        FakePost({
            "status": "OK",
            "data": {
                "user_id": "123456789",
                "username": "vasya",
                "first_name": "Вася",
                "invite_link": "abc12345 Дзен slug",
                "utm_source": "dzen",
                "utm_medium": "article",
                "utm_campaign": "baza-marketing",
                "left_date": 0,
            },
        }),
    )
    info = TGTrackClient("KEY").get_user_info("123456789")

    assert info.username == "vasya"
    assert info.invite_link == "abc12345 Дзен slug"
    assert info.utm == {
        "utm_source": "dzen", "utm_medium": "article", "utm_campaign": "baza-marketing",
    }
    assert info.has_attribution
    assert info.raw["left_date"] == 0


def test_tgtrack_unknown_user_returns_none(monkeypatch):
    monkeypatch.setattr(httpx, "post", FakePost({"status": "ERROR"}))
    assert TGTrackClient("KEY").get_user_info("42") is None


def test_tgtrack_survives_broken_response(monkeypatch):
    def bad_json(*args, **kwargs):
        return httpx.Response(500, text="<html>502</html>")

    monkeypatch.setattr(httpx, "post", bad_json)
    assert TGTrackClient("KEY").get_user_info("42") is None


def test_build_tgtrack_without_key_returns_none():
    assert build_tgtrack("") is None
    assert build_tgtrack("KEY", "max") is not None


# ------------------------------------------------------------------ доступ к модели


def _llm_settings(**kwargs):
    from app.config import Settings

    base = {
        "router_api_key": "", "anthropic_api_key": "",
        "database_url": "postgresql+psycopg://x/x",
    }
    return Settings(**{**base, **kwargs})


def test_provider_prefers_router_over_direct_key():
    from app.integrations.llm import PROVIDER_ANTHROPIC, PROVIDER_ROUTER, provider

    assert provider(_llm_settings()) is None
    assert provider(_llm_settings(anthropic_api_key="sk-x")) == PROVIDER_ANTHROPIC
    assert provider(_llm_settings(router_api_key="r", anthropic_api_key="sk-x")) == PROVIDER_ROUTER


def test_router_call_sends_openai_shape(monkeypatch):
    from app.integrations.llm import generate_json

    fake = FakePost({
        "model": "anthropic/claude-haiku-4.5",
        "choices": [{"message": {"content": '{"title":"Т","subtitle":"П","body_md":"Тело"}'}}],
    })
    monkeypatch.setattr(httpx, "post", fake)

    data, model = generate_json("промпт", _llm_settings(router_api_key="key"))

    url, payload = fake.calls[0]
    assert url == "https://routerai.ru/api/v1/chat/completions"
    assert payload["messages"] == [{"role": "user", "content": "промпт"}]
    assert data["title"] == "Т"
    assert model == "anthropic/claude-haiku-4.5"


def test_router_unwraps_json_from_code_fence(monkeypatch):
    """Модель любит оборачивать ответ в ```json — это не должно ломать генерацию."""
    from app.integrations.llm import generate_json

    monkeypatch.setattr(httpx, "post", FakePost({
        "choices": [{"message": {"content": 'Вот результат:\n```json\n{"title":"Т"}\n```'}}],
    }))
    data, _ = generate_json("промпт", _llm_settings(router_api_key="key"))
    assert data["title"] == "Т"


def test_router_reports_empty_balance_separately(monkeypatch):
    from app.integrations.llm import LLMNoFunds, generate_json

    monkeypatch.setattr(httpx, "post", FakePost({"error": "no funds"}, status_code=402))
    with pytest.raises(LLMNoFunds, match="деньги"):
        generate_json("промпт", _llm_settings(router_api_key="key"))


def test_generator_falls_back_to_template_when_model_fails(monkeypatch, voice, cases):
    """Модель отвалилась — статья всё равно должна получиться."""
    from app.core.formats import get_format
    from app.core.generator import VIA_TEMPLATE, generate

    monkeypatch.setattr(
        httpx, "post", lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("нет сети"))
    )
    article = generate(
        "Тема", get_format("dzen"), voice, cases, settings=_llm_settings(router_api_key="key")
    )

    assert article.via == VIA_TEMPLATE
    assert article.error  # в интерфейсе честно сказано, что это фолбэк
