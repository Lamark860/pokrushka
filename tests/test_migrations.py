"""Миграции должны накатываться и откатываться на чистой БД."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from tests.conftest import TEST_DB_URL

ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    return cfg


def test_upgrade_then_downgrade(db_engine):
    from app.db import Base

    Base.metadata.drop_all(db_engine)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    tables = set(inspect(db_engine).get_table_names())
    assert {"projects", "articles", "subscribers", "metrics_snapshots"} <= tables

    command.downgrade(cfg, "base")
    left = set(inspect(db_engine).get_table_names()) - {"alembic_version"}
    assert left == set()
