import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.core.generator import CaseSpec, VoiceSpec
from app.core.seed_knowledge import CASES, VOICE

_VOICE_FIELDS = (
    "brand", "who", "core_idea", "audience", "offer_cta",
    "persona", "format_rules", "avoid_map", "structure", "hashtags",
)

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://traff:traff@localhost:5436/traff_test"
)


@pytest.fixture(autouse=True)
def offline_env(monkeypatch):
    """Тесты не ходят в интернет.

    В `.env` разработчика лежат боевые ключи, а роутеры зовут `get_settings()` сами —
    без этой заглушки веб-тесты начали бы генерировать статьи через платный API:
    медленно, дорого и с плавающим результатом. Переменные окружения имеют приоритет
    над `.env`, поэтому достаточно занулить их и сбросить кэш настроек.
    """
    from app.config import get_settings

    for name in ("ROUTER_API_KEY", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN",
                 "TGTRACK_TG_API_KEY", "TGTRACK_MAX_API_KEY", "NOTIFY_CHAT_ID"):
        monkeypatch.setenv(name, "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def voice() -> VoiceSpec:
    return VoiceSpec(**{k: VOICE[k] for k in _VOICE_FIELDS})


@pytest.fixture
def cases() -> list[CaseSpec]:
    return [CaseSpec(niche, metric) for niche, metric in CASES]


@pytest.fixture
def offline_settings() -> Settings:
    """Без единого ключа модели — генератор обязан уйти в шаблон-фолбэк.

    Зануляем оба ключа явно: `.env` разработчика содержит боевой ключ роутера, и без
    этого тесты молча начнут ходить в сеть — станут медленными и негерметичными.
    """
    return Settings(
        router_api_key="", anthropic_api_key="", database_url="postgresql+psycopg://x/x"
    )


# --------------------------------------------------------------- БД для веб-тестов


@pytest.fixture(scope="session")
def db_engine():
    """Отдельная тестовая БД. Если Postgres не поднят — веб-тесты пропускаются."""
    from app.db import Base
    import app.models  # noqa: F401 — регистрирует таблицы

    admin_url = TEST_DB_URL.rsplit("/", 1)[0] + "/postgres"
    db_name = TEST_DB_URL.rsplit("/", 1)[1]
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    except OperationalError:
        pytest.skip("Postgres недоступен — веб-тесты пропущены")

    engine = create_engine(TEST_DB_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    from app.db import Base

    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)
    session_factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.web.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def tenant_with_user(db_session):
    """Арендатор + пользователь + проект с контент-ДНК."""
    from app.core.seed_knowledge import BRAND, seed_project
    from app.models.project import Project
    from app.models.tenancy import Tenant, User
    from app.security import hash_password

    def _make(email: str, name: str) -> tuple[Tenant, User, Project]:
        tenant = Tenant(name=name)
        db_session.add(tenant)
        db_session.flush()
        user = User(
            tenant_id=tenant.id, email=email, password_hash=hash_password("secret123")
        )
        project = Project(tenant_id=tenant.id, name=name)
        db_session.add_all([user, project])
        db_session.commit()
        db_session.refresh(project)
        seed_project(db_session, project)
        # expire_on_commit=False, поэтому связи (cases/voice) остались бы пустыми
        db_session.expire_all()
        return tenant, user, project

    return _make
