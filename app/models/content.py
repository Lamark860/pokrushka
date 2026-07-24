"""Статьи и трекинг-ссылки под них."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TenantMixin, TimestampMixin

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"

VIA_LLM = "llm"
VIA_TEMPLATE = "template"

LINK_TG_INVITE = "tg_invite"
LINK_MAX = "max"
LINK_SITE = "site"


class Article(Base, TenantMixin, TimestampMixin):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    topic: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # Подзаголовок нужен vc.ru отдельным полем при публикации (правка Артура п.5)
    subtitle: Mapped[str | None] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # dzen | vc | tg | max
    format_kind: Mapped[str] = mapped_column(String(20), nullable=False)  # article | post

    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    via: Mapped[str] = mapped_column(String(20), nullable=False, default=VIA_TEMPLATE)
    model: Mapped[str | None] = mapped_column(String(80))

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_DRAFT)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_url: Mapped[str | None] = mapped_column(String(1000))

    # ["системный маркетинг", ...] и {"системный маркетинг": 1.8} — отчёт валидатора
    keywords_used: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    density: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Замечания пост-валидатора: длина вне лимита, нет CTA и т.п.
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    links: Mapped[list["TrackingLink"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class TrackingLink(Base, TenantMixin, TimestampMixin):
    """Ссылка из CTA статьи.

    `code` — короткий идентификатор для своего редиректа /r/<code>; он же ложится
    в имя пригласительной ссылки Telegram (лимит `name` — 32 символа, поэтому
    полный utm_campaign живёт в `utm`, а не в имени).
    """

    __tablename__ = "tracking_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # tg_invite | max | site
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    invite_name: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    utm: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    article: Mapped[Article] = relationship(back_populates="links")
