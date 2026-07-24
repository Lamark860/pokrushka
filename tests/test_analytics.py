"""Воронка, топ статей, петля обучения."""
from datetime import date

import pytest
from sqlalchemy import select

from app.models.content import LINK_MAX, STATUS_PUBLISHED, Article, TrackingLink
from app.models.project import Example
from app.models.tracking import STAGE_LEAD, STAGE_SALE, Deal, Subscriber
from app.services.analytics import (
    article_stats,
    funnel,
    learning_examples,
    platform_stats,
    strip_cta,
    top_articles,
)
from app.services.metrics import save_manual


def _article(db, project, title, slug, platform="dzen", body="Текст статьи"):
    article = Article(
        tenant_id=project.tenant_id, project_id=project.id, topic=title, title=title,
        slug=slug, platform=platform, format_kind="article",
        body_md=body, body_html=f"<p>{body}</p>", chars=len(body),
    )
    db.add(article)
    db.flush()
    return article


@pytest.fixture
def project_with_funnel(db_session, tenant_with_user):
    """Одна статья с полной воронкой, вторая — только с показами."""
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")

    winner = _article(db_session, project, "Как выстроить маркетинг", "kak-vystroit")
    winner.status = STATUS_PUBLISHED
    winner.published_at = date(2026, 7, 1)
    loser = _article(db_session, project, "Почему дешёвые заявки дороже", "pochemu", platform="vc")

    db_session.add_all([
        TrackingLink(tenant_id=project.tenant_id, article_id=winner.id, kind=LINK_MAX,
                     code="aaaaaaaa", url="https://max.ru/x", utm={}, clicks=40),
        TrackingLink(tenant_id=project.tenant_id, article_id=loser.id, kind=LINK_MAX,
                     code="bbbbbbbb", url="https://max.ru/y", utm={}, clicks=5),
    ])
    db_session.add_all([
        Subscriber(tenant_id=project.tenant_id, project_id=project.id, platform="tg",
                   external_user_id="1", username="vasya", article_id=winner.id),
        Subscriber(tenant_id=project.tenant_id, project_id=project.id, platform="tg",
                   external_user_id="2", article_id=winner.id),
        # пришёл мимо статей
        Subscriber(tenant_id=project.tenant_id, project_id=project.id, platform="tg",
                   external_user_id="3"),
    ])
    db_session.add_all([
        Deal(tenant_id=project.tenant_id, project_id=project.id, article_id=winner.id,
             day=date(2026, 7, 10), stage=STAGE_LEAD, amount=0),
        Deal(tenant_id=project.tenant_id, project_id=project.id, article_id=winner.id,
             day=date(2026, 7, 12), stage=STAGE_SALE, amount=50000),
    ])
    db_session.commit()

    save_manual(db_session, winner, {"views": 10000, "reads": 4000},
                collected_at=date(2026, 7, 20))
    save_manual(db_session, winner, {"views": 14000, "reads": 6000},
                collected_at=date(2026, 7, 27))
    save_manual(db_session, loser, {"views": 2000, "reads": 400}, collected_at=date(2026, 7, 27))
    return project, winner, loser


def test_funnel_uses_latest_snapshot_not_sum(db_session, project_with_funnel):
    """Три среза одной статьи не должны утроить показы."""
    project, _, _ = project_with_funnel

    result = funnel(db_session, project)

    assert result.get("views").count == 16000  # 14000 (последний срез) + 2000
    assert result.get("reads").count == 6400
    assert result.get("clicks").count == 45
    assert result.get("subscribers").count == 3  # включая пришедшего мимо статей
    assert result.get("leads").count == 1
    assert result.get("sales").count == 1
    assert result.revenue == 50000


def test_funnel_conversions_and_ctr(db_session, project_with_funnel):
    project, _, _ = project_with_funnel

    result = funnel(db_session, project)

    assert result.get("views").conversion == 40.0  # 6400 / 16000
    assert result.ctr == pytest.approx(0.28, abs=0.01)  # 45 / 16000
    assert result.revenue_per_article == 50000  # одна опубликованная статья


def test_funnel_empty_project_does_not_crash(db_session, tenant_with_user):
    _, _, project = tenant_with_user("empty@example.com", "Пустой")
    result = funnel(db_session, project)

    assert result.get("views").count == 0
    assert result.get("views").conversion is None
    assert result.ctr is None
    assert result.max_count == 1  # деление на ноль в шаблоне исключено


def test_article_stats_and_score(db_session, project_with_funnel):
    project, winner, loser = project_with_funnel

    stats = {item.article.id: item for item in article_stats(db_session, project)}

    best = stats[winner.id]
    assert best.views == 14000
    assert best.subscribers == 2
    assert best.leads == 1
    assert best.sales == 1
    assert best.revenue == 50000
    assert best.read_ratio == pytest.approx(42.9, abs=0.1)
    assert best.sub_conversion == pytest.approx(0.03, abs=0.01)
    assert best.score > stats[loser.id].score


def test_funnel_shape_narrows_monotonically(db_session, project_with_funnel):
    """Воронка должна сужаться: это её единственное сообщение, читаемое без цифр."""
    project, _, _ = project_with_funnel

    shape = funnel(db_session, project).shape()

    assert len(shape.segments) == 6
    widths = [seg.width_top for seg in shape.segments]
    assert widths == sorted(widths, reverse=True)
    assert widths[0] == 100.0  # первая ступень — во всю ширину
    assert all(w >= 14.0 for w in widths)  # хвост остаётся различимым
    assert widths[1] > widths[2]  # обрыв на узком месте виден
    assert shape.outline.startswith("M ") and shape.outline.endswith("Z")
    assert shape.height == 6 * 46


def test_funnel_shape_empty_when_no_traffic(db_session, tenant_with_user):
    _, _, project = tenant_with_user("empty@example.com", "Пустой")
    shape = funnel(db_session, project).shape()

    assert not shape
    assert shape.segments == []


def test_bottleneck_detected_by_stage_benchmark(db_session, project_with_funnel):
    """Единого порога нет: 41% с показов — норма, 0.2% с дочитываний — провал."""
    project, _, _ = project_with_funnel

    stages = {s.key: s for s in funnel(db_session, project).stages}

    assert not stages["views"].is_bottleneck  # 40% при ориентире 25%
    assert stages["reads"].is_bottleneck  # 0.7% при ориентире 2%
    assert stages["reads"].lost == 6355  # 6400 дочитываний → 45 переходов


def test_top_articles_sorted_and_filtered(db_session, project_with_funnel):
    project, winner, _ = project_with_funnel

    top = top_articles(db_session, project)

    assert top[0].article.id == winner.id
    assert all(item.score > 0 for item in top)


def test_platform_stats_grouped(db_session, project_with_funnel):
    project, _, _ = project_with_funnel

    platforms = {p.key: p for p in platform_stats(db_session, project)}

    assert platforms["dzen"].views == 14000
    assert platforms["vc"].articles == 1
    assert platforms["dzen"].read_ratio == pytest.approx(42.9, abs=0.1)


# ------------------------------------------------------------------ петля обучения


def test_strip_cta_removes_links_block():
    body = "Текст статьи\n\n## Что дальше\n\n- Telegram: [x](x)\n"
    assert strip_cta(body) == "Текст статьи"


def test_learning_examples_prefer_winning_articles(db_session, project_with_funnel):
    project, winner, _ = project_with_funnel

    examples = learning_examples(db_session, project, limit=2)

    assert examples[0].startswith("Текст статьи")  # тело статьи-лидера
    assert len(examples) == 2  # добито эталонами из сида


def test_learning_examples_fall_back_to_seeded(db_session, tenant_with_user):
    """Пока метрик нет, генератор работает на посевных эталонах Артура."""
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")

    examples = learning_examples(db_session, project, limit=2)

    assert len(examples) == 2
    assert "системный маркетинг" in " ".join(examples).lower()


def test_make_example_button_adds_winner(client, db_session, tenant_with_user):
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    article = _article(db_session, project, "Ручной эталон", "ruchnoy",
                       body="Тело статьи\n\n## Что дальше\n\n- Telegram: [x](x)\n")
    db_session.commit()
    client.post("/login", data={"email": "artur@example.com", "password": "secret123"},
                follow_redirects=False)

    client.post(f"/articles/{article.id}/make-example", follow_redirects=False)

    example = db_session.scalar(
        select(Example).where(Example.source_article_id == article.id)
    )
    assert example is not None
    assert example.is_winner
    assert "## Что дальше" not in example.body  # CTA в примере не нужен

    # повторное нажатие не плодит дубли
    client.post(f"/articles/{article.id}/make-example", follow_redirects=False)
    assert len(db_session.scalars(select(Example).where(
        Example.source_article_id == article.id)).all()) == 1
