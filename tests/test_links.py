"""Создание трекинг-ссылок под статью."""
import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.core.formats import get_format
from app.core.tracking import code_from_invite_name
from app.models.content import LINK_MAX, LINK_SITE, LINK_TG_INVITE, Article, TrackingLink
from app.services.links import cta_links, create_tracking_links, revoke_links


@pytest.fixture
def project_with_channels(db_session, tenant_with_user):
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    project.tg_channel_url = "https://t.me/artur_baza_marketingg"
    project.tg_channel_id = "-1001234567890"
    project.max_channel_url = "https://max.ru/id183308463787_biz"
    db_session.commit()
    return project


@pytest.fixture
def article(db_session, project_with_channels):
    art = Article(
        tenant_id=project_with_channels.tenant_id,
        project_id=project_with_channels.id,
        topic="Тема", title="Заголовок", slug="kak-vystroit-marketing",
        platform="dzen", format_kind="article",
        body_md="текст", body_html="<p>текст</p>", chars=5,
    )
    db_session.add(art)
    db_session.flush()
    return art


def _settings(**kwargs) -> Settings:
    base = {
        "router_api_key": "",
        "anthropic_api_key": "",
        "telegram_bot_token": "",
        "public_base_url": "https://traff.example.com",
        "database_url": "postgresql+psycopg://x/x",
    }
    return Settings(**{**base, **kwargs})


def test_without_bot_token_falls_back_to_direct_link(db_session, project_with_channels, article):
    links = create_tracking_links(
        db_session, project_with_channels, article, get_format("dzen"), settings=_settings()
    )
    db_session.commit()

    tg = next(link for link in links if link.kind == LINK_TG_INVITE)
    assert tg.invite_name is None
    assert tg.url.startswith("https://t.me/artur_baza_marketingg")
    assert "utm_source=dzen" in tg.url
    assert {link.kind for link in links} == {LINK_TG_INVITE, LINK_MAX}


def test_invite_name_contains_the_same_code_as_the_stored_link(
    db_session, project_with_channels, article, monkeypatch
):
    """Главный инвариант атрибуции: по коду из имени ссылки должна находиться эта же ссылка."""
    captured: dict = {}

    def fake_post(url, json=None, timeout=None):
        captured.update(json or {})
        return httpx.Response(
            200,
            json={"ok": True, "result": {"invite_link": "https://t.me/+hash", "name": json["name"]}},
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    links = create_tracking_links(
        db_session, project_with_channels, article, get_format("dzen"),
        settings=_settings(telegram_bot_token="123:ABC"),
    )
    db_session.commit()

    tg = next(link for link in links if link.kind == LINK_TG_INVITE)
    assert tg.url == "https://t.me/+hash"
    assert code_from_invite_name(captured["name"]) == tg.code
    assert db_session.scalar(
        select(TrackingLink).where(TrackingLink.code == code_from_invite_name(tg.invite_name))
    ).id == tg.id


def test_api_failure_degrades_to_direct_link(
    db_session, project_with_channels, article, monkeypatch
):
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **kw: httpx.Response(
            200, json={"ok": False, "description": "not enough rights", "error_code": 400}
        ),
    )
    links = create_tracking_links(
        db_session, project_with_channels, article, get_format("dzen"),
        settings=_settings(telegram_bot_token="123:ABC"),
    )
    db_session.commit()

    tg = next(link for link in links if link.kind == LINK_TG_INVITE)
    assert tg.invite_name is None
    assert tg.url.startswith("https://t.me/artur_baza_marketingg")


def test_codes_are_unique_across_links(db_session, project_with_channels, article):
    links = create_tracking_links(
        db_session, project_with_channels, article, get_format("dzen"), settings=_settings()
    )
    db_session.commit()
    assert len({link.code for link in links}) == len(links)


def test_cta_order_and_redirect_switch(db_session, project_with_channels, article):
    links = create_tracking_links(
        db_session, project_with_channels, article, get_format("dzen"), settings=_settings()
    )
    db_session.commit()
    settings = _settings()

    tracked = cta_links(project_with_channels, links, settings=settings)
    assert [label for label, _ in tracked] == ["Канал в MAX", "Telegram"]
    assert all(url.startswith("https://traff.example.com/r/") for _, url in tracked)

    project_with_channels.track_clicks = False
    direct = cta_links(project_with_channels, links, settings=settings)
    assert all("/r/" not in url for _, url in direct)


def test_site_link_added_when_configured(db_session, project_with_channels, article):
    project_with_channels.site_url = "https://example-marketing.ru"
    db_session.commit()

    links = create_tracking_links(
        db_session, project_with_channels, article, get_format("vc"), settings=_settings()
    )
    db_session.commit()
    site = next(link for link in links if link.kind == LINK_SITE)
    assert "utm_source=vc" in site.url


def test_revoke_marks_invite_links(db_session, project_with_channels, article, monkeypatch):
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: httpx.Response(
            200, json={"ok": True, "result": {"invite_link": "https://t.me/+hash", "name": json.get("name", "")}}
        ),
    )
    settings = _settings(telegram_bot_token="123:ABC")
    create_tracking_links(
        db_session, project_with_channels, article, get_format("dzen"), settings=settings
    )
    db_session.commit()
    db_session.refresh(article)

    assert revoke_links(db_session, article, settings=settings) == 1
    db_session.commit()

    tg = next(link for link in article.links if link.kind == LINK_TG_INVITE)
    assert tg.revoked_at is not None
    # повторный вызов ничего не отзывает
    assert revoke_links(db_session, article, settings=settings) == 0
