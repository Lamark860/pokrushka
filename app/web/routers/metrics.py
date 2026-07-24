"""Статистика площадок: загрузка выгрузки и ручной ввод."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tables import FIELD_SYNONYMS, NUMERIC_FIELDS, TableError, read_table
from app.db import get_db
from app.models.content import Article
from app.models.project import Project
from app.models.tenancy import User
from app.models.tracking import JobRun, MetricsSnapshot
from app.services.metrics import import_metrics, metrics_for, save_manual
from app.services.reminders import stale_projects
from app.web.deps import current_user, get_project, templates

router = APIRouter()

# Поля, которые пользователь может сопоставить руками
MAPPABLE_FIELDS = {
    "title": "Заголовок",
    "url": "Ссылка на публикацию",
    "views": "Показы",
    "reads": "Дочитывания",
    "read_ratio": "Дочитываемость, %",
    "likes": "Лайки",
    "comments": "Комментарии",
    "reposts": "Репосты",
    "collected_at": "Дата",
}


def _staleness(db: Session, project: Project):
    """Плашка «цифры устарели» — та же проверка, что и у недельного напоминания."""
    return next((item for item in stale_projects(db) if item.project.id == project.id), None)


@router.get("/projects/{project_id}/metrics", response_class=HTMLResponse)
def metrics_page(
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
    return templates.TemplateResponse(
        request,
        "metrics.html",
        {
            "project": project,
            "user": user,
            "articles": articles,
            "views": metrics_for(db, [a.id for a in articles]),
            "fields": MAPPABLE_FIELDS,
            "report": None,
            "table_headers": [],
            "detected": {},
            "today": date.today().isoformat(),
            "stale": _staleness(db, project),
            "jobs": list(
                db.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(5))
            ),
        },
    )


@router.post("/projects/{project_id}/metrics/import", response_class=HTMLResponse)
async def import_file(
    request: Request,
    file: UploadFile = File(...),
    collected_at: str = Form(""),
    dry_run: bool = Form(False),
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    payload = await file.read()
    try:
        table = read_table(file.filename or "", payload)
    except TableError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Ручное сопоставление колонок: приходит полями вида mapping_views=<заголовок>
    form = await request.form()
    mapping = dict(table.mapping)
    for field_name in FIELD_SYNONYMS:
        chosen = (form.get(f"mapping_{field_name}") or "").strip()
        if chosen:
            mapping[field_name] = chosen
        elif f"mapping_{field_name}" in form:
            mapping.pop(field_name, None)

    day = None
    if collected_at.strip():
        try:
            day = datetime.strptime(collected_at.strip(), "%Y-%m-%d").date()
        except ValueError:
            day = None

    report = import_metrics(
        db, project.id, table, mapping=mapping, collected_at=day, dry_run=dry_run
    )

    articles = list(
        db.scalars(
            select(Article)
            .where(Article.project_id == project.id)
            .order_by(Article.created_at.desc())
        )
    )
    return templates.TemplateResponse(
        request,
        "metrics.html",
        {
            "project": project,
            "user": user,
            "articles": articles,
            "views": metrics_for(db, [a.id for a in articles]),
            "fields": MAPPABLE_FIELDS,
            "report": report,
            "table_headers": table.headers,
            "detected": mapping,
            "today": (day or date.today()).isoformat(),
            "filename": file.filename,
            "stale": _staleness(db, project),
            "jobs": list(db.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(5))),
        },
    )


@router.post("/articles/{article_id}/metrics")
async def save_article_metrics(
    request: Request,
    article_id: int,
    collected_at: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    article = db.scalar(
        select(Article).where(Article.id == article_id, Article.tenant_id == user.tenant_id)
    )
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Статья не найдена")

    form = await request.form()
    values: dict[str, int] = {}
    for name in NUMERIC_FIELDS:
        raw = (form.get(name) or "").strip()
        if raw:
            try:
                values[name] = max(int(raw), 0)
            except ValueError:
                continue

    if values:
        day = None
        if collected_at.strip():
            try:
                day = datetime.strptime(collected_at.strip(), "%Y-%m-%d").date()
            except ValueError:
                day = None
        save_manual(db, article, values, collected_at=day)

    return RedirectResponse(url=f"/articles/{article.id}", status_code=303)


@router.post("/articles/{article_id}/metrics/{snapshot_id}/delete")
def delete_snapshot(
    article_id: int,
    snapshot_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    snapshot = db.scalar(
        select(MetricsSnapshot).where(
            MetricsSnapshot.id == snapshot_id,
            MetricsSnapshot.article_id == article_id,
            MetricsSnapshot.tenant_id == user.tenant_id,
        )
    )
    if snapshot is not None:
        db.delete(snapshot)
        db.commit()
    return RedirectResponse(url=f"/articles/{article_id}", status_code=303)
