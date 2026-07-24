"""Импорт метрик: сопоставление со статьями, срезы, идемпотентность, дельты."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.tables import read_csv
from app.models.content import STATUS_PUBLISHED, Article
from app.models.tracking import METRICS_SOURCE_IMPORT, MetricsSnapshot
from app.services.metrics import (
    import_metrics,
    metrics_for,
    normalize_title,
    normalize_url,
    save_manual,
)
from app.services.reminders import stale_projects


@pytest.fixture
def project_with_articles(db_session, tenant_with_user):
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")

    published = Article(
        tenant_id=project.tenant_id, project_id=project.id,
        topic="Тема", title="Как выстроить маркетинг", slug="kak-vystroit-marketing",
        platform="dzen", format_kind="article", body_md="x", body_html="x", chars=1,
        status=STATUS_PUBLISHED, published_url="https://dzen.ru/a/abc123",
    )
    without_url = Article(
        tenant_id=project.tenant_id, project_id=project.id,
        topic="Вторая", title="Почему дешёвые заявки дороже дорогих", slug="pochemu",
        platform="vc", format_kind="article", body_md="x", body_html="x", chars=1,
    )
    db_session.add_all([published, without_url])
    db_session.commit()
    return project, published, without_url


def csv_table(text: str):
    return read_csv(text.encode("utf-8"))


def test_normalizers():
    assert normalize_url("https://www.dzen.ru/a/abc123/") == "dzen.ru/a/abc123"
    assert normalize_url("dzen.ru/a/abc123") == "dzen.ru/a/abc123"
    assert normalize_title("  Как  выстроить,  маркетинг! ") == "как выстроить маркетинг"


def test_import_matches_by_url_and_title(db_session, project_with_articles):
    project, published, without_url = project_with_articles
    table = csv_table(
        "Заголовок;Ссылка;Показы;Дочитывания\n"
        "Совсем другое имя;https://dzen.ru/a/abc123;12 500;5 200\n"
        "Почему дешёвые заявки дороже дорогих;;3 100;900\n"
    )

    report = import_metrics(db_session, project.id, table)

    assert report.total == 2
    assert report.matched_by_url == 1
    assert report.matched_by_title == 1
    assert report.created == 2

    snapshot = db_session.scalar(
        select(MetricsSnapshot).where(MetricsSnapshot.article_id == published.id)
    )
    assert snapshot.views == 12500
    assert snapshot.reads == 5200
    assert snapshot.source == METRICS_SOURCE_IMPORT


def test_reads_computed_from_percentage(db_session, project_with_articles):
    project, published, _ = project_with_articles
    table = csv_table(
        "Ссылка;Показы;Дочитываемость\nhttps://dzen.ru/a/abc123;10 000;41,6%\n"
    )

    import_metrics(db_session, project.id, table)

    snapshot = db_session.scalar(
        select(MetricsSnapshot).where(MetricsSnapshot.article_id == published.id)
    )
    assert snapshot.views == 10000
    assert snapshot.reads == 4160


def test_repeated_import_updates_instead_of_duplicating(db_session, project_with_articles):
    project, published, _ = project_with_articles
    day = date(2026, 7, 20)

    import_metrics(db_session, project.id,
                   csv_table("Ссылка;Показы\nhttps://dzen.ru/a/abc123;1 000\n"),
                   collected_at=day)
    report = import_metrics(db_session, project.id,
                            csv_table("Ссылка;Показы\nhttps://dzen.ru/a/abc123;1 400\n"),
                            collected_at=day)

    assert report.created == 0
    assert report.updated == 1
    snapshots = db_session.scalars(
        select(MetricsSnapshot).where(MetricsSnapshot.article_id == published.id)
    ).all()
    assert len(snapshots) == 1
    assert snapshots[0].views == 1400


def test_different_days_make_separate_snapshots(db_session, project_with_articles):
    project, published, _ = project_with_articles
    import_metrics(db_session, project.id,
                   csv_table("Ссылка;Показы\nhttps://dzen.ru/a/abc123;1 000\n"),
                   collected_at=date(2026, 7, 20))
    import_metrics(db_session, project.id,
                   csv_table("Ссылка;Показы\nhttps://dzen.ru/a/abc123;1 800\n"),
                   collected_at=date(2026, 7, 27))

    snapshots = db_session.scalars(
        select(MetricsSnapshot).where(MetricsSnapshot.article_id == published.id)
    ).all()
    assert len(snapshots) == 2


def test_date_column_wins_over_default(db_session, project_with_articles):
    project, published, _ = project_with_articles
    table = csv_table("Ссылка;Дата;Показы\nhttps://dzen.ru/a/abc123;20.07.2026;500\n")

    import_metrics(db_session, project.id, table, collected_at=date(2026, 7, 27))

    snapshot = db_session.scalar(
        select(MetricsSnapshot).where(MetricsSnapshot.article_id == published.id)
    )
    assert snapshot.collected_at.date() == date(2026, 7, 20)


def test_dry_run_changes_nothing(db_session, project_with_articles):
    project, _, _ = project_with_articles
    table = csv_table("Ссылка;Показы\nhttps://dzen.ru/a/abc123;1 000\n")

    report = import_metrics(db_session, project.id, table, dry_run=True)

    assert report.matched == 1
    assert report.created == 1  # столько появилось бы
    assert db_session.scalars(select(MetricsSnapshot)).all() == []


def test_unmatched_rows_reported(db_session, project_with_articles):
    project, _, _ = project_with_articles
    table = csv_table(
        "Заголовок;Ссылка;Показы\nЧужая статья;https://dzen.ru/a/zzz;100\n"
    )

    report = import_metrics(db_session, project.id, table)

    assert report.matched == 0
    assert report.unmatched[0]["title"] == "Чужая статья"
    assert any("сопоставить" in w for w in report.warnings)


def test_file_without_numbers_is_rejected_with_hint(db_session, project_with_articles):
    project, _, _ = project_with_articles
    report = import_metrics(db_session, project.id, csv_table("Заголовок;Ссылка\nа;б\n"))

    assert report.total == 0
    assert any("колонк" in w for w in report.warnings)


def test_manual_entry_and_delta(db_session, project_with_articles):
    _, published, _ = project_with_articles
    save_manual(db_session, published, {"views": 1000, "reads": 400},
                collected_at=date(2026, 7, 20))
    save_manual(db_session, published, {"views": 1500, "reads": 700},
                collected_at=date(2026, 7, 27))

    view = metrics_for(db_session, [published.id])[published.id]

    assert view.views == 1500
    assert view.delta("views") == 500
    assert view.delta("reads") == 300
    assert view.read_ratio == pytest.approx(46.7, abs=0.1)


def test_delta_is_none_for_single_snapshot(db_session, project_with_articles):
    _, published, _ = project_with_articles
    save_manual(db_session, published, {"views": 100})

    view = metrics_for(db_session, [published.id])[published.id]
    assert view.delta("views") is None


# ------------------------------------------------------------------ напоминание


def test_stale_project_detected_when_no_metrics(db_session, project_with_articles):
    project, _, _ = project_with_articles

    stale = stale_projects(db_session)

    assert [item.project.id for item in stale] == [project.id]
    assert stale[0].published == 1
    assert stale[0].last_collected_at is None


def test_fresh_metrics_are_not_reported(db_session, project_with_articles):
    _, published, _ = project_with_articles
    save_manual(db_session, published, {"views": 10}, collected_at=datetime.now(timezone.utc))

    assert stale_projects(db_session) == []


def test_old_metrics_are_reported(db_session, project_with_articles):
    _, published, _ = project_with_articles
    save_manual(
        db_session, published, {"views": 10},
        collected_at=datetime.now(timezone.utc) - timedelta(days=30),
    )

    stale = stale_projects(db_session)
    assert len(stale) == 1
    assert stale[0].days_since >= 29


def test_project_without_published_articles_is_ignored(db_session, tenant_with_user):
    tenant_with_user("nobody@example.com", "Пустой проект")
    assert stale_projects(db_session) == []
