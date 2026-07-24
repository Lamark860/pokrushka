"""Напоминание обновить статистику.

Публикует Артур руками, цифры приносит тоже он — значит система должна сама замечать,
что данных давно не было, и говорить об этом. Иначе воронка тихо устаревает.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.integrations.telegram import TelegramError, build_client
from app.models.content import STATUS_PUBLISHED, Article
from app.models.project import Project
from app.models.tracking import JobRun, MetricsSnapshot

log = logging.getLogger(__name__)

JOB_KIND = "metrics-reminder"


@dataclass
class StaleProject:
    project: Project
    published: int
    last_collected_at: datetime | None

    @property
    def days_since(self) -> int | None:
        if self.last_collected_at is None:
            return None
        delta = datetime.now(timezone.utc) - self.last_collected_at
        return max(delta.days, 0)

    def as_text(self) -> str:
        if self.last_collected_at is None:
            return (
                f"«{self.project.name}»: {self.published} опубликованных статей, "
                "статистику ещё ни разу не заносили"
            )
        return (
            f"«{self.project.name}»: статистику не обновляли {self.days_since} дн. "
            f"(последний срез {self.last_collected_at.strftime('%d.%m.%Y')})"
        )


def stale_projects(db: Session, *, settings: Settings | None = None) -> list[StaleProject]:
    """Проекты, где есть что мерить, но цифры давно не приносили."""
    settings = settings or get_settings()
    threshold = datetime.now(timezone.utc) - timedelta(days=settings.metrics_stale_days)
    stale: list[StaleProject] = []

    for project in db.scalars(select(Project)).all():
        published = db.scalar(
            select(func.count(Article.id)).where(
                Article.project_id == project.id, Article.status == STATUS_PUBLISHED
            )
        )
        if not published:
            continue  # публиковать ещё нечего — напоминать не о чем

        last = db.scalar(
            select(func.max(MetricsSnapshot.collected_at))
            .join(Article, Article.id == MetricsSnapshot.article_id)
            .where(Article.project_id == project.id)
        )
        if last is None or last < threshold:
            stale.append(StaleProject(project=project, published=published, last_collected_at=last))
    return stale


def run_reminder(db: Session, *, settings: Settings | None = None) -> list[StaleProject]:
    """Проверить свежесть данных, записать прогон и попробовать уведомить в Telegram."""
    settings = settings or get_settings()
    job = JobRun(kind=JOB_KIND, started_at=datetime.now(timezone.utc), status="running")
    db.add(job)
    db.commit()

    stale = stale_projects(db, settings=settings)

    if stale and settings.notify_chat_id:
        client = build_client(settings.telegram_bot_token)
        if client is not None:
            lines = ["Пора обновить статистику по статьям:", ""]
            lines += [f"• {item.as_text()}" for item in stale]
            lines += ["", f"Загрузить выгрузку: {settings.public_base_url.rstrip('/')}/"]
            try:
                client.send_message(settings.notify_chat_id, "\n".join(lines))
            except TelegramError as exc:
                log.warning("Не отправилось напоминание: %s", exc)

    job.finished_at = datetime.now(timezone.utc)
    job.status = "ok"
    job.log = (
        "всё свежее" if not stale else "; ".join(item.as_text() for item in stale)
    )
    db.commit()
    return stale
