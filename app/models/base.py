"""Общие примеси для моделей.

`tenant_id` стоит во всех таблицах данных сознательно (задел на мультиарендность):
сейчас арендатор один, но когда Артур начнёт продавать доступ клиентам, изоляция
включается фильтром/RLS-политикой по одной колонке — без миграции данных.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenantMixin:
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
