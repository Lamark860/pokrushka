"""Подписчики, срезы метрик площадок, заявки/продажи и журнал фоновых задач."""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TenantMixin, TimestampMixin

METRICS_SOURCE_MANUAL = "manual"
METRICS_SOURCE_IMPORT = "import"
METRICS_SOURCE_PARSER = "parser"
METRICS_SOURCE_TGTRACK = "tgtrack"

STAGE_LEAD = "заявка"
STAGE_DIAGNOSTIC = "диагностика"
STAGE_SALE = "продажа"


class Subscriber(Base, TenantMixin, TimestampMixin):
    """Подписчик канала с привязкой к статье-источнику.

    Заполняется ботом-слушателем из апдейта `chat_member` (этап 2): в нём приходит
    `invite_link.name`, по которому находится tracking_link → article. Пустая ссылка
    значит «пришёл мимо статей» — article_id остаётся NULL.
    """

    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )

    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # tg | max
    external_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(200))
    first_name: Mapped[str | None] = mapped_column(String(200))

    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    invite_name: Mapped[str | None] = mapped_column(String(32), index=True)
    utm: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    tgtrack_raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class MetricsSnapshot(Base, TenantMixin, TimestampMixin):
    """Недельный срез метрик статьи.

    Именно история, а не одна строка на статью (в старом MVP `metrics` имела
    PRIMARY KEY article_id и перезаписывалась) — Артуру нужна динамика.

    Срез привязан ко дню: повторная загрузка той же выгрузки обновляет строку, а не
    добавляет вторую.
    """

    __tablename__ = "metrics_snapshots"
    __table_args__ = (
        UniqueConstraint("article_id", "collected_at", "source", name="uq_snapshot_day_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reads: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reposts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    source: Mapped[str] = mapped_column(String(20), nullable=False, default=METRICS_SOURCE_MANUAL)


class Deal(Base, TenantMixin, TimestampMixin):
    """Заявка или продажа. Связь со статьёй — через подписчика (username из amoCRM, этап 4)."""

    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id", ondelete="SET NULL"))
    subscriber_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscribers.id", ondelete="SET NULL")
    )
    amo_lead_id: Mapped[str | None] = mapped_column(String(64), index=True)

    day: Mapped[date] = mapped_column(Date, nullable=False)
    niche: Mapped[str | None] = mapped_column(String(300))
    service: Mapped[str | None] = mapped_column(String(300))
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str] = mapped_column(String(30), nullable=False, default=STAGE_LEAD)


class JobRun(Base, TimestampMixin):
    """Журнал фоновых задач: сбор метрик, опрос TGTrack, синк amoCRM.

    `tenant_id` здесь необязателен: часть задач (например, опрос TGTrack) идёт разом
    по всем арендаторам, и привязывать такой прогон к одному из них было бы враньём.
    """

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    log: Mapped[str | None] = mapped_column(Text)
