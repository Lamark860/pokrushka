"""Проект (канал) и его контент-ДНК: голос, кейсы, посты-эталоны, ключевые слова.

Контент-ДНК переехала из `kotlowoi/traff/knowledge.py` в БД: Артур должен править
кейсы и правила стиля через интерфейс, а не в исходнике.
"""
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TenantMixin, TimestampMixin

KEYWORD_SOURCE_MANUAL = "manual"
KEYWORD_SOURCE_WORDSTAT = "wordstat"


class Project(Base, TenantMixin, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Каналы назначения
    tg_channel_id: Mapped[str | None] = mapped_column(String(64))
    tg_channel_url: Mapped[str | None] = mapped_column(String(500))
    max_channel_url: Mapped[str | None] = mapped_column(String(500))
    site_url: Mapped[str | None] = mapped_column(String(500))

    # Демо-режим: витрина показывает засеянные псевдоцифры (Артур продаёт систему
    # своим заказчикам). Флаг виден в интерфейсе, чтобы демо не выдали за боевые данные.
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Вести CTA через свой редирект /r/<code> (считаем клики) или ставить прямую ссылку
    track_clicks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    voice: Mapped["VoiceProfile | None"] = relationship(
        back_populates="project", uselist=False, cascade="all, delete-orphan"
    )
    cases: Mapped[list["Case"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Case.ord"
    )
    examples: Mapped[list["Example"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    keywords: Mapped[list["Keyword"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class VoiceProfile(Base, TenantMixin, TimestampMixin):
    """Голос автора: позиционирование, персона, правила оформления, структура."""

    __tablename__ = "voice_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    brand: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    who: Mapped[str] = mapped_column(Text, nullable=False, default="")
    core_idea: Mapped[str] = mapped_column(Text, nullable=False, default="")
    audience: Mapped[str] = mapped_column(Text, nullable=False, default="")
    offer_cta: Mapped[str] = mapped_column(Text, nullable=False, default="")
    persona: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ["НЕ использовать длинные тире", ...]
    format_rules: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # {"Завалю клиентами": "Стабильный поток заявок", ...}
    avoid_map: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # ["Боль: ...", "Что такое система: ...", ...]
    structure: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # ["#маркетинг", "#таргет", ...]
    hashtags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Темник — идеи статей
    topics: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    project: Mapped[Project] = relationship(back_populates="voice")


class Case(Base, TenantMixin, TimestampMixin):
    """Реальный кейс с цифрами — доказательство внутри статьи."""

    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    niche: Mapped[str] = mapped_column(String(300), nullable=False)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    ord: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    project: Mapped[Project] = relationship(back_populates="cases")


class Example(Base, TenantMixin, TimestampMixin):
    """Пост-эталон для few-shot. Победители из дашборда попадают сюда автоматически (этап 4)."""

    __tablename__ = "examples"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_winner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL")
    )

    project: Mapped[Project] = relationship(back_populates="examples")


class Keyword(Base, TenantMixin, TimestampMixin):
    """Ключевой запрос. Источник: ручной ввод/CSV сейчас, Wordstat API — позже."""

    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    phrase: Mapped[str] = mapped_column(String(500), nullable=False)
    frequency: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=KEYWORD_SOURCE_MANUAL)

    project: Mapped[Project] = relationship(back_populates="keywords")
