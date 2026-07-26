"""Дашборд: воронка от показов до денег, и ручной учёт заявок."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.content import Article
from app.models.project import Project
from app.models.tenancy import User
from app.models.tracking import STAGE_LEAD, STAGE_SALE, Deal, Subscriber
from app.services.analytics import (
    article_stats,
    funnel,
    platform_stats,
    recent_deals,
    timeline,
    top_articles,
)
from app.web.deps import current_user, get_project, templates

router = APIRouter()

STAGES = (STAGE_LEAD, "диагностика", STAGE_SALE)


@router.get("/projects/{project_id}/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    stats = article_stats(db, project)
    articles = list(
        db.scalars(
            select(Article)
            .where(Article.project_id == project.id)
            .order_by(Article.created_at.desc())
        )
    )
    orphan_subscribers = db.scalar(
        select(func.count(Subscriber.id)).where(
            Subscriber.project_id == project.id, Subscriber.article_id.is_(None)
        )
    ) or 0

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "project": project,
            "user": user,
            "funnel": funnel(db, project, stats),
            "top": top_articles(db, project, stats=stats),
            "platforms": platform_stats(db, project, stats),
            "timeline": timeline(db, project),
            "stats": sorted(stats, key=lambda s: s.score, reverse=True),
            "deals": recent_deals(db, project),
            "articles": articles,
            "orphan_subscribers": orphan_subscribers,
            "stages": STAGES,
            "today": date.today().isoformat(),
        },
    )


@router.post("/projects/{project_id}/deals")
def add_deal(
    stage: str = Form(STAGE_LEAD),
    article_id: str = Form(""),
    username: str = Form(""),
    day: str = Form(""),
    niche: str = Form(""),
    service: str = Form(""),
    amount: str = Form("0"),
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    """Заявка или продажа.

    Статью можно выбрать руками, а можно указать Telegram-username: если человек
    подписан по нашей ссылке, система сама поймёт, какая статья его привела. Это и есть
    та связка «заявка → статья», без которой доход по статьям не посчитать.
    """
    resolved_article_id: int | None = None
    if article_id.strip().isdigit():
        candidate = db.scalar(
            select(Article).where(
                Article.id == int(article_id), Article.project_id == project.id
            )
        )
        resolved_article_id = candidate.id if candidate else None

    subscriber = None
    handle = username.strip().lstrip("@")
    if handle:
        subscriber = db.scalar(
            select(Subscriber).where(
                Subscriber.project_id == project.id,
                func.lower(Subscriber.username) == handle.lower(),
            )
        )
        if subscriber is not None and resolved_article_id is None:
            resolved_article_id = subscriber.article_id

    try:
        day_value = datetime.strptime(day.strip(), "%Y-%m-%d").date() if day.strip() else date.today()
    except ValueError:
        day_value = date.today()

    try:
        amount_value = max(int(amount.strip() or 0), 0)
    except ValueError:
        amount_value = 0

    db.add(
        Deal(
            tenant_id=project.tenant_id,
            project_id=project.id,
            article_id=resolved_article_id,
            subscriber_id=subscriber.id if subscriber else None,
            day=day_value,
            niche=niche.strip() or None,
            service=service.strip() or None,
            amount=amount_value,
            stage=stage if stage in STAGES else STAGE_LEAD,
        )
    )
    db.commit()
    return RedirectResponse(url=f"/projects/{project.id}/dashboard", status_code=303)


@router.post("/deals/{deal_id}/delete")
def delete_deal(
    deal_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    deal = db.scalar(
        select(Deal).where(Deal.id == deal_id, Deal.tenant_id == user.tenant_id)
    )
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Запись не найдена")
    project_id = deal.project_id
    db.delete(deal)
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}/dashboard", status_code=303)
