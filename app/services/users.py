"""Учётные записи.

Веб-интерфейса для пользователей нет и пока не нужно: доступ выдаётся руками командой
`python3 -m app.cli adduser`. Логика лежит здесь, а не в `app/cli.py`, чтобы её можно
было проверить тестом на обычной сессии, без подмены `SessionLocal`.
"""
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenancy import ROLE_MEMBER, ROLE_OWNER, Tenant, User
from app.security import hash_password

ROLES = (ROLE_OWNER, ROLE_MEMBER)

# Без похожих символов: пароль диктуют в мессенджере и вводят руками
_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PASSWORD_LENGTH = 14


class UserError(RuntimeError):
    """Причина, по которой пользователя не создать, — в формулировке для человека."""


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def create_user(
    db: Session,
    email: str,
    *,
    role: str = ROLE_MEMBER,
    password: str | None = None,
) -> tuple[User, str]:
    """Завести пользователя в существующем арендаторе. Возвращает его и пароль.

    Пароль показывается вызывающему один раз: в базе лежит только argon2-хеш.
    """
    email = (email or "").strip().lower()
    if not email:
        raise UserError("Не указана почта")
    if role not in ROLES:
        raise UserError(f"Неизвестная роль «{role}», допустимы: {', '.join(ROLES)}")
    if db.scalar(select(User).where(User.email == email)):
        raise UserError(f"Пользователь {email} уже есть")

    tenant = db.scalar(select(Tenant).order_by(Tenant.id).limit(1))
    if tenant is None:
        raise UserError("Нет ни одного арендатора — сначала выполните `bootstrap`")

    password = password or generate_password()
    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, password
