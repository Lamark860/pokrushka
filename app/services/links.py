"""Трекинг-ссылки статьи: создание, публичный адрес для CTA, отзыв.

Схема одной статьи:
  Telegram — именованная пригласительная ссылка (единственный способ узнать, из какой
             статьи пришёл подписчик: UTM в `t.me/+hash` не передаётся);
  MAX      — ссылка на канал с UTM (атрибуцию там даёт TGTrack);
  Сайт     — ссылка с UTM.

Ничего из этого не обязательно: нет токена бота — будет прямая ссылка на канал, нет
ссылок в проекте — не будет и трекинг-ссылок. Система должна работать в любом случае.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.context import add_utm
from app.core.formats import PlatformFormat
from app.core.tracking import build_invite_name, new_code, redirect_url
from app.integrations.telegram import TelegramError, build_client
from app.models.content import LINK_MAX, LINK_SITE, LINK_TG_INVITE, Article, TrackingLink
from app.models.project import Project

log = logging.getLogger(__name__)

LINK_LABELS = {
    LINK_MAX: "Канал в MAX",
    LINK_TG_INVITE: "Telegram",
    LINK_SITE: "Сайт",
}
# Порядок в CTA-блоке: MAX первым — это основной канал Артура
LINK_ORDER = (LINK_MAX, LINK_TG_INVITE, LINK_SITE)


def _unique_code(db: Session) -> str:
    for _ in range(10):
        code = new_code()
        if db.scalar(select(TrackingLink.id).where(TrackingLink.code == code)) is None:
            return code
    raise RuntimeError("Не удалось подобрать свободный код ссылки")


def _utm(fmt: PlatformFormat, slug: str) -> dict[str, str]:
    return {
        "utm_source": fmt.key,
        "utm_medium": "post" if fmt.kind == "post" else "article",
        "utm_campaign": "baza-marketing",
        "utm_content": slug,
    }


def create_tracking_links(
    db: Session,
    project: Project,
    article: Article,
    fmt: PlatformFormat,
    *,
    settings: Settings | None = None,
) -> list[TrackingLink]:
    """Создать ссылки под статью. Коммит остаётся за вызывающим кодом."""
    settings = settings or get_settings()
    utm = _utm(fmt, article.slug)
    links: list[TrackingLink] = []

    def add(kind: str, url: str, *, code: str | None = None, invite_name: str | None = None) -> None:
        links.append(
            TrackingLink(
                tenant_id=article.tenant_id,
                article_id=article.id,
                kind=kind,
                code=code or _unique_code(db),
                invite_name=invite_name,
                url=url,
                utm=utm,
            )
        )

    # Telegram: пробуем именную пригласительную ссылку, иначе прямая.
    # Код подбирается ДО обращения к API — он же уходит в имя ссылки, по нему потом
    # находится статья в апдейте chat_member.
    if project.tg_channel_url or project.tg_channel_id:
        code = _unique_code(db)
        invite_url, invite_name = None, None
        client = build_client(settings.telegram_bot_token)
        if client is not None and project.tg_channel_id:
            try:
                invite = client.create_invite_link(
                    project.tg_channel_id,
                    build_invite_name(code, fmt.title, article.slug),
                )
                invite_url, invite_name = invite.url, invite.name
            except TelegramError as exc:
                log.warning("Не удалось создать пригласительную ссылку: %s", exc)
        if invite_url:
            add(LINK_TG_INVITE, invite_url, code=code, invite_name=invite_name)
        elif project.tg_channel_url:
            add(
                LINK_TG_INVITE,
                add_utm(project.tg_channel_url, platform=fmt.key, kind=fmt.kind,
                        content=article.slug),
                code=code,
            )

    if project.max_channel_url:
        add(LINK_MAX, add_utm(project.max_channel_url, platform=fmt.key,
                              kind=fmt.kind, content=article.slug))

    if project.site_url:
        add(LINK_SITE, add_utm(project.site_url, platform=fmt.key,
                               kind=fmt.kind, content=article.slug))

    db.add_all(links)
    return links


def cta_links(
    project: Project, links: list[TrackingLink], *, settings: Settings | None = None
) -> list[tuple[str, str]]:
    """Пары «подпись → адрес» для CTA-блока статьи.

    `project.track_clicks` включает свой редирект — только так считаются переходы:
    Telegram кликов по ссылке не отдаёт вообще.
    """
    settings = settings or get_settings()
    ordered = sorted(links, key=lambda link: LINK_ORDER.index(link.kind)
                     if link.kind in LINK_ORDER else len(LINK_ORDER))
    return [
        (
            LINK_LABELS.get(link.kind, link.kind),
            redirect_url(settings.public_base_url, link.code) if project.track_clicks else link.url,
        )
        for link in ordered
    ]


def revoke_links(db: Session, article: Article, *, settings: Settings | None = None) -> int:
    """Отозвать пригласительные ссылки статьи (например, когда её сняли с публикации)."""
    settings = settings or get_settings()
    client = build_client(settings.telegram_bot_token)
    project = db.get(Project, article.project_id)
    revoked = 0

    for link in article.links:
        if link.kind != LINK_TG_INVITE or link.invite_name is None or link.revoked_at:
            continue
        if client is not None and project is not None and project.tg_channel_id:
            try:
                client.revoke_invite_link(project.tg_channel_id, link.url)
            except TelegramError as exc:
                log.warning("Не удалось отозвать ссылку %s: %s", link.code, exc)
                continue
        link.revoked_at = datetime.now(timezone.utc)
        revoked += 1
    return revoked
