"""Выдача доступа: команда `adduser`."""
import pytest
from sqlalchemy import select

from app.models.tenancy import ROLE_MEMBER, ROLE_OWNER, Tenant, User
from app.security import verify_password
from app.services.users import UserError, create_user, generate_password


def test_creates_member_in_existing_tenant(db_session, tenant_with_user):
    tenant, _, _ = tenant_with_user("artur@example.com", "База Маркетинг")

    user, password = create_user(db_session, "  Novyi@Example.COM ")

    assert user.email == "novyi@example.com"  # почта нормализуется: вход по ней же
    assert user.tenant_id == tenant.id
    assert user.role == ROLE_MEMBER
    assert verify_password(password, user.password_hash)


def test_password_is_not_stored_as_is(db_session, tenant_with_user):
    tenant_with_user("artur@example.com", "База Маркетинг")

    _, password = create_user(db_session, "novyi@example.com")

    stored = db_session.scalar(select(User).where(User.email == "novyi@example.com"))
    assert password not in stored.password_hash


def test_owner_role_can_be_asked_for(db_session, tenant_with_user):
    tenant_with_user("artur@example.com", "База Маркетинг")

    user, _ = create_user(db_session, "vtoroy@example.com", role=ROLE_OWNER)

    assert user.role == ROLE_OWNER


def test_rejects_duplicate_email(db_session, tenant_with_user):
    tenant_with_user("artur@example.com", "База Маркетинг")

    with pytest.raises(UserError, match="уже есть"):
        create_user(db_session, "ARTUR@example.com")


def test_rejects_unknown_role(db_session, tenant_with_user):
    tenant_with_user("artur@example.com", "База Маркетинг")

    with pytest.raises(UserError, match="роль"):
        create_user(db_session, "novyi@example.com", role="admin")


def test_rejects_empty_email(db_session, tenant_with_user):
    tenant_with_user("artur@example.com", "База Маркетинг")

    with pytest.raises(UserError, match="почта"):
        create_user(db_session, "   ")


def test_requires_bootstrap_first(db_session):
    assert db_session.scalar(select(Tenant).limit(1)) is None

    with pytest.raises(UserError, match="bootstrap"):
        create_user(db_session, "novyi@example.com")


def test_generated_password_avoids_lookalike_characters():
    """Пароль диктуют в мессенджере: 0/O и 1/l/I путают при вводе."""
    passwords = "".join(generate_password() for _ in range(50))

    assert not set(passwords) & set("0O1lI")
    assert len(generate_password()) == 14
