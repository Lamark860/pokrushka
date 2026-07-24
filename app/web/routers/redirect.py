"""Свой редирект `/r/<code>` — единственный способ честно считать переходы.

Telegram не отдаёт клики по пригласительной ссылке (только факт вступления), у MAX это
зависит от TGTrack. Поэтому в CTA статьи ставится ссылка на нас, а мы ведём человека
дальше и увеличиваем счётчик.

Открыт без авторизации: по нему ходят читатели статьи.
"""
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.content import TrackingLink

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/r/{code}", include_in_schema=False)
def follow(code: str, db: Session = Depends(get_db)) -> RedirectResponse:
    link = db.scalar(select(TrackingLink).where(TrackingLink.code == code))

    if link is None:
        log.info("Переход по неизвестному коду %s", code)
        return RedirectResponse(url="/", status_code=302)

    # Отозванная ссылка ведёт туда же (канал никуда не делся), но клик не считаем:
    # иначе статистика снятой статьи продолжит расти.
    if link.revoked_at is None:
        db.execute(
            update(TrackingLink)
            .where(TrackingLink.id == link.id)
            .values(clicks=TrackingLink.clicks + 1)
        )
        db.commit()

    return RedirectResponse(url=link.url, status_code=302)
