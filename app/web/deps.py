"""Зависимости веб-слоя: текущий пользователь, доступ к проекту, шаблоны."""
from pathlib import Path

from fastapi import Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.project import Project
from app.models.tenancy import User
from app.web.filters import FILTERS

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters.update(FILTERS)


class LoginRequired(HTTPException):
    """Отдельный тип, чтобы обработчик увёл на форму входа, а не показал 401."""

    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail="Нужен вход")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise LoginRequired()
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        raise LoginRequired()
    return user


def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Project:
    """Проект строго в пределах арендатора пользователя."""
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.tenant_id == user.tenant_id)
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден")
    return project
