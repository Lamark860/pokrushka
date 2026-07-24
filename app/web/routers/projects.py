"""Проекты: настройки канала, голос, кейсы, эталоны, ключевые слова."""
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.keywords import parse_keywords
from app.core.seed_knowledge import seed_project
from app.db import get_db
from app.models.project import Case, Keyword, Project
from app.models.tenancy import User
from app.web.deps import current_user, get_project, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    projects = list(
        db.scalars(select(Project).where(Project.tenant_id == user.tenant_id).order_by(Project.id))
    )
    if len(projects) == 1:
        return RedirectResponse(url=f"/projects/{projects[0].id}", status_code=303)
    return templates.TemplateResponse(request, "projects.html", {"projects": projects, "user": user})


@router.post("/projects")
def create_project(
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    project = Project(tenant_id=user.tenant_id, name=name.strip() or "Новый проект")
    db.add(project)
    db.commit()
    db.refresh(project)
    seed_project(db, project)
    return RedirectResponse(url=f"/projects/{project.id}", status_code=303)


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(
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
    return templates.TemplateResponse(
        request,
        "project.html",
        {"project": project, "user": user, "keywords": keywords},
    )


@router.post("/projects/{project_id}/settings")
def save_settings(
    tg_channel_url: str = Form(""),
    tg_channel_id: str = Form(""),
    max_channel_url: str = Form(""),
    site_url: str = Form(""),
    is_demo: bool = Form(False),
    track_clicks: bool = Form(False),
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    project.tg_channel_url = tg_channel_url.strip() or None
    project.tg_channel_id = tg_channel_id.strip() or None
    project.max_channel_url = max_channel_url.strip() or None
    project.site_url = site_url.strip() or None
    project.is_demo = is_demo
    project.track_clicks = track_clicks
    db.commit()
    return RedirectResponse(url=f"/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/voice")
def save_voice(
    brand: str = Form(""),
    who: str = Form(""),
    core_idea: str = Form(""),
    audience: str = Form(""),
    offer_cta: str = Form(""),
    persona: str = Form(""),
    format_rules: str = Form(""),
    structure: str = Form(""),
    hashtags: str = Form(""),
    topics: str = Form(""),
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    voice = project.voice
    if voice is None:
        seed_project(db, project)
        db.refresh(project)
        voice = project.voice

    voice.brand = brand.strip()
    voice.who = who.strip()
    voice.core_idea = core_idea.strip()
    voice.audience = audience.strip()
    voice.offer_cta = offer_cta.strip()
    voice.persona = persona.strip()
    voice.format_rules = [line.strip() for line in format_rules.splitlines() if line.strip()]
    voice.structure = [line.strip() for line in structure.splitlines() if line.strip()]
    voice.hashtags = [tag.strip() for tag in hashtags.split() if tag.strip()]
    voice.topics = [line.strip() for line in topics.splitlines() if line.strip()]
    db.commit()
    return RedirectResponse(url=f"/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/cases")
def add_case(
    niche: str = Form(...),
    metric: str = Form(...),
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    if niche.strip() and metric.strip():
        db.add(
            Case(
                tenant_id=project.tenant_id,
                project_id=project.id,
                niche=niche.strip(),
                metric=metric.strip(),
                ord=len(project.cases),
            )
        )
        db.commit()
    return RedirectResponse(url=f"/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/cases/{case_id}/delete")
def delete_case(
    case_id: int,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    case = db.scalar(select(Case).where(Case.id == case_id, Case.project_id == project.id))
    if case is not None:
        db.delete(case)
        db.commit()
    return RedirectResponse(url=f"/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/keywords")
async def add_keywords(
    raw: str = Form(""),
    file: UploadFile | None = File(None),
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    text = raw
    if file is not None and file.filename:
        content = await file.read()
        text += "\n" + content.decode("utf-8-sig", errors="replace")

    existing = {
        phrase.lower()
        for phrase in db.scalars(select(Keyword.phrase).where(Keyword.project_id == project.id))
    }
    for parsed in parse_keywords(text):
        if parsed.phrase.lower() in existing:
            continue
        existing.add(parsed.phrase.lower())
        db.add(
            Keyword(
                tenant_id=project.tenant_id,
                project_id=project.id,
                phrase=parsed.phrase,
                frequency=parsed.frequency,
            )
        )
    db.commit()
    return RedirectResponse(url=f"/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/keywords/{keyword_id}/delete")
def delete_keyword(
    keyword_id: int,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    keyword = db.scalar(
        select(Keyword).where(Keyword.id == keyword_id, Keyword.project_id == project.id)
    )
    if keyword is not None:
        db.delete(keyword)
        db.commit()
    return RedirectResponse(url=f"/projects/{project.id}", status_code=303)
