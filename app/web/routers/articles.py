"""Генерация статей и работа с черновиками."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import case_specs, voice_spec
from app.core.formats import FORMATS, get_format
from app.core.generator import attach_cta, generate
from app.db import get_db
from app.models.content import STATUS_PUBLISHED, Article, TrackingLink
from app.models.project import Example, Keyword, Project
from app.models.tenancy import User
from app.models.tracking import MetricsSnapshot, Subscriber
from app.services.analytics import learning_examples, strip_cta
from app.services.links import LINK_LABELS, cta_links, create_tracking_links
from app.services.metrics import metrics_for
from app.web.deps import current_user, get_project, templates

router = APIRouter()


def _get_article(article_id: int, db: Session, user: User) -> Article:
    article = db.scalar(
        select(Article).where(Article.id == article_id, Article.tenant_id == user.tenant_id)
    )
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Статья не найдена")
    return article


@router.get("/projects/{project_id}/generate", response_class=HTMLResponse)
def generate_form(
    request: Request,
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    keywords = list(
        db.scalars(
            select(Keyword).where(Keyword.project_id == project.id).order_by(Keyword.phrase)
        )
    )
    topics = list(project.voice.topics or []) if project.voice else []
    return templates.TemplateResponse(
        request,
        "generate.html",
        {
            "project": project,
            "user": user,
            "formats": FORMATS,
            "topics": topics,
            "keywords": keywords,
        },
    )


@router.post("/projects/{project_id}/generate")
def generate_article(
    topic: str = Form(...),
    platform: str = Form(...),
    keyword_ids: list[int] = Form(default=[]),
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    fmt = get_format(platform)
    phrases: list[str] = []
    if keyword_ids:
        phrases = list(
            db.scalars(
                select(Keyword.phrase).where(
                    Keyword.project_id == project.id, Keyword.id.in_(keyword_ids)
                )
            )
        )

    voice = voice_spec(project)
    draft = generate(
        topic.strip(),
        fmt,
        voice,
        case_specs(project),
        keywords=phrases,
        # Петля обучения: сначала тексты статей-лидеров, и только потом эталоны из настроек
        examples=learning_examples(db, project),
    )

    article = Article(
        tenant_id=project.tenant_id,
        project_id=project.id,
        topic=draft.topic,
        title=draft.title,
        subtitle=draft.subtitle or None,
        slug=draft.slug,
        platform=fmt.key,
        format_kind=fmt.kind,
        body_md=draft.body_md,
        body_html=draft.body_html,
        chars=draft.chars,
        via=draft.via,
        model=draft.model,
        keywords_used=phrases,
        density=draft.report.density,
        warnings=draft.report.warnings + ([draft.error] if draft.error else []),
    )
    db.add(article)
    db.flush()  # нужен id: трекинг-ссылки создаются под конкретную статью

    # Ссылки дописываем после генерации: в UTM и в имя пригласительной ссылки
    # входит слаг, а он известен только когда готов заголовок. Повторно дёргать
    # модель ради этого нельзя.
    links = create_tracking_links(db, project, article, fmt)
    db.flush()
    final = attach_cta(draft, voice.offer_cta, cta_links(project, links))
    article.body_md = final.body_md
    article.body_html = final.body_html

    db.commit()
    db.refresh(article)
    return RedirectResponse(url=f"/articles/{article.id}", status_code=303)


@router.get("/projects/{project_id}/articles", response_class=HTMLResponse)
def article_list(
    request: Request,
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    articles = list(
        db.scalars(
            select(Article)
            .where(Article.project_id == project.id)
            .order_by(Article.created_at.desc())
        )
    )
    clicks = dict(
        db.execute(
            select(TrackingLink.article_id, func.sum(TrackingLink.clicks))
            .join(Article, Article.id == TrackingLink.article_id)
            .where(Article.project_id == project.id)
            .group_by(TrackingLink.article_id)
        ).all()
    )
    subscribers = dict(
        db.execute(
            select(Subscriber.article_id, func.count(Subscriber.id))
            .where(Subscriber.project_id == project.id, Subscriber.article_id.isnot(None))
            .group_by(Subscriber.article_id)
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "articles.html",
        {
            "project": project,
            "user": user,
            "articles": articles,
            "formats": FORMATS,
            "clicks": clicks,
            "subscribers": subscribers,
            "views": metrics_for(db, [a.id for a in articles]),
        },
    )


@router.get("/articles/{article_id}", response_class=HTMLResponse)
def article_page(
    request: Request,
    article_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    article = _get_article(article_id, db, user)
    subscribers = db.scalar(
        select(func.count(Subscriber.id)).where(Subscriber.article_id == article.id)
    )
    snapshots = list(
        db.scalars(
            select(MetricsSnapshot)
            .where(MetricsSnapshot.article_id == article.id)
            .order_by(MetricsSnapshot.collected_at.desc())
        )
    )
    return templates.TemplateResponse(
        request,
        "article.html",
        {
            "article": article,
            "project": db.get(Project, article.project_id),
            "user": user,
            "fmt": get_format(article.platform),
            "links": sorted(article.links, key=lambda link: link.kind),
            "subscribers": subscribers or 0,
            "link_labels": LINK_LABELS,
            "metrics": metrics_for(db, [article.id]).get(article.id),
            "snapshots": snapshots,
            "today": date.today().isoformat(),
        },
    )


@router.post("/articles/{article_id}/publish")
def mark_published(
    article_id: int,
    published_url: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    article = _get_article(article_id, db, user)
    article.status = STATUS_PUBLISHED
    article.published_url = published_url.strip() or None
    article.published_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url=f"/articles/{article.id}", status_code=303)


@router.post("/articles/{article_id}/make-example")
def make_example(
    article_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Пометить статью эталоном вручную — на случай, когда цифр ещё нет, а текст удачный."""
    article = _get_article(article_id, db, user)
    exists = db.scalar(
        select(Example).where(
            Example.project_id == article.project_id, Example.source_article_id == article.id
        )
    )
    if exists is None:
        db.add(
            Example(
                tenant_id=article.tenant_id,
                project_id=article.project_id,
                title=article.title,
                body=strip_cta(article.body_md),
                is_winner=True,
                source_article_id=article.id,
            )
        )
        db.commit()
    return RedirectResponse(url=f"/articles/{article.id}", status_code=303)


@router.post("/articles/{article_id}/delete")
def delete_article(
    article_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    article = _get_article(article_id, db, user)
    project_id = article.project_id
    db.delete(article)
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}/articles", status_code=303)
