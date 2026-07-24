"""Добор атрибуции из TGTrack по подписчикам, которых мы уже знаем.

Бот-слушатель ловит вступления в Telegram и в большинстве случаев сразу знает статью
(по имени пригласительной ссылки). Этот проход закрывает остаток:
  • подписчик пришёл по основной ссылке канала — вдруг TGTrack видел его источник;
  • MAX, где своего слушателя у нас нет.

Запуск: `python3 -m app.cli tgtrack-sync` (позже — по расписанию раз в сутки).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.tracking import code_from_invite_name
from app.integrations.tgtrack import PLATFORM_MAX, PLATFORM_TG, TGTrackClient, build_client
from app.models.content import Article, TrackingLink
from app.models.tracking import JobRun, Subscriber

log = logging.getLogger(__name__)

JOB_KIND = "tgtrack-sync"


@dataclass
class SyncResult:
    checked: int = 0
    enriched: int = 0
    attributed: int = 0

    def as_log(self) -> str:
        return (
            f"проверено {self.checked}, метки добавлены {self.enriched}, "
            f"привязано к статьям {self.attributed}"
        )


def _resolve_article_id(db: Session, subscriber: Subscriber, info) -> int | None:
    """Найти статью по имени пригласительной ссылки, а если его нет — по utm_content."""
    code = code_from_invite_name(info.invite_link)
    if code:
        link = db.scalar(select(TrackingLink).where(TrackingLink.code == code))
        if link is not None:
            return link.article_id

    slug = info.utm.get("utm_content")
    if slug:
        return db.scalar(
            select(Article.id).where(
                Article.project_id == subscriber.project_id, Article.slug == slug
            )
        )
    return None


def sync_platform(db: Session, client: TGTrackClient, platform: str) -> SyncResult:
    result = SyncResult()
    pending = db.scalars(
        select(Subscriber).where(
            Subscriber.platform == platform,
            or_(Subscriber.article_id.is_(None), Subscriber.tgtrack_raw == {}),
        )
    ).all()

    for subscriber in pending:
        result.checked += 1
        info = client.get_user_info(subscriber.external_user_id)
        if info is None:
            continue

        subscriber.tgtrack_raw = info.raw
        subscriber.username = subscriber.username or info.username
        subscriber.first_name = subscriber.first_name or info.first_name
        if info.utm and not subscriber.utm:
            subscriber.utm = info.utm
        if info.has_attribution:
            result.enriched += 1

        if subscriber.article_id is None:
            article_id = _resolve_article_id(db, subscriber, info)
            if article_id is not None:
                subscriber.article_id = article_id
                result.attributed += 1

    db.commit()
    return result


def run_sync(db: Session, *, settings: Settings | None = None) -> SyncResult:
    settings = settings or get_settings()
    total = SyncResult()

    job = JobRun(kind=JOB_KIND, started_at=datetime.now(timezone.utc), status="running")
    db.add(job)
    db.commit()

    for api_key, platform in (
        (settings.tgtrack_tg_api_key, PLATFORM_TG),
        (settings.tgtrack_max_api_key, PLATFORM_MAX),
    ):
        client = build_client(api_key, platform)
        if client is None:
            log.info("Ключ TGTrack для %s не задан — пропускаю", platform)
            continue
        part = sync_platform(db, client, platform)
        total.checked += part.checked
        total.enriched += part.enriched
        total.attributed += part.attributed

    job.finished_at = datetime.now(timezone.utc)
    job.status = "ok"
    job.log = total.as_log()
    db.commit()
    return total
