"""Атрибуция подписок: апдейт `chat_member` → запись в `subscribers`.

Здесь намеренно нет aiogram: на вход приходит обычный словарь апдейта, как его отдаёт
Telegram. Так логику можно проверить на фикстурах, без бота и без сети.

Как это работает: у каждой статьи своя именованная пригласительная ссылка, её имя
начинается с кода трекинг-ссылки. Telegram присылает это имя в апдейте — по нему
находится статья. Пришёл без ссылки (по основной ссылке канала, из поиска) — подписчик
всё равно записывается, но без привязки к статье.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tracking import code_from_invite_name
from app.models.content import TrackingLink
from app.models.project import Project
from app.models.tracking import Subscriber

log = logging.getLogger(__name__)

PLATFORM_TG = "tg"

# Статусы, при которых человек состоит в канале
JOINED_STATUSES = frozenset({"member", "administrator", "creator", "restricted"})
LEFT_STATUSES = frozenset({"left", "kicked"})


@dataclass(frozen=True)
class ChatMemberEvent:
    chat_id: str
    user_id: str
    username: str | None
    first_name: str | None
    old_status: str
    new_status: str
    invite_name: str | None
    at: datetime

    @property
    def is_join(self) -> bool:
        return self.old_status in LEFT_STATUSES and self.new_status in JOINED_STATUSES

    @property
    def is_leave(self) -> bool:
        return self.old_status in JOINED_STATUSES and self.new_status in LEFT_STATUSES


def parse_chat_member(update: dict) -> ChatMemberEvent | None:
    """Достать из апдейта то, что нам нужно. None — если это не про членство в канале."""
    payload = update.get("chat_member") or update
    chat = payload.get("chat") or {}
    old = payload.get("old_chat_member") or {}
    new = payload.get("new_chat_member") or {}
    user = new.get("user") or payload.get("from") or {}

    if not chat.get("id") or not user.get("id") or not new.get("status"):
        return None

    timestamp = payload.get("date")
    at = (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        if isinstance(timestamp, (int, float))
        else datetime.now(timezone.utc)
    )

    return ChatMemberEvent(
        chat_id=str(chat["id"]),
        user_id=str(user["id"]),
        username=user.get("username"),
        first_name=user.get("first_name"),
        old_status=old.get("status", "left"),
        new_status=new["status"],
        invite_name=(payload.get("invite_link") or {}).get("name"),
        at=at,
    )


def _find_project(db: Session, chat_id: str) -> Project | None:
    return db.scalar(select(Project).where(Project.tg_channel_id == chat_id))


def _find_article_id(db: Session, project: Project, invite_name: str | None) -> tuple[int | None, str | None]:
    code = code_from_invite_name(invite_name)
    if code is None:
        return None, None
    link = db.scalar(select(TrackingLink).where(TrackingLink.code == code))
    if link is None:
        log.info("Пришёл подписчик по неизвестному коду ссылки %s", code)
        return None, code
    return link.article_id, code


def handle_chat_member(db: Session, update: dict) -> Subscriber | None:
    """Обработать апдейт. Возвращает затронутого подписчика либо None."""
    event = parse_chat_member(update)
    if event is None or not (event.is_join or event.is_leave):
        return None

    project = _find_project(db, event.chat_id)
    if project is None:
        log.warning("Событие из канала %s — такого проекта нет", event.chat_id)
        return None

    subscriber = db.scalar(
        select(Subscriber).where(
            Subscriber.project_id == project.id,
            Subscriber.platform == PLATFORM_TG,
            Subscriber.external_user_id == event.user_id,
        )
    )

    if event.is_leave:
        if subscriber is None:
            return None
        subscriber.left_at = event.at
        db.commit()
        return subscriber

    article_id, code = _find_article_id(db, project, event.invite_name)

    if subscriber is None:
        subscriber = Subscriber(
            tenant_id=project.tenant_id,
            project_id=project.id,
            platform=PLATFORM_TG,
            external_user_id=event.user_id,
            username=event.username,
            first_name=event.first_name,
            joined_at=event.at,
            invite_name=event.invite_name,
            article_id=article_id,
            utm={"code": code} if code else {},
        )
        db.add(subscriber)
    else:
        # Вернулся после отписки: снимаем left_at, но источник подписки не переписываем —
        # первая атрибуция и есть та статья, которая привела человека.
        subscriber.left_at = None
        subscriber.username = event.username or subscriber.username
        subscriber.first_name = event.first_name or subscriber.first_name
        if subscriber.joined_at is None:
            subscriber.joined_at = event.at
        if subscriber.article_id is None and article_id is not None:
            subscriber.article_id = article_id
            subscriber.invite_name = event.invite_name

    db.commit()
    return subscriber
