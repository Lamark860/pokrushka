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
    timeline,
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

    assert result.step("views").conversion == 40.0  # 6400 / 16000
    assert result.ctr == pytest.approx(0.28, abs=0.01)  # 45 / 16000
    assert result.ctr_articles == 2  # обе статьи с обоими приборами
    assert result.revenue_per_article == 50000  # одна опубликованная статья


def test_funnel_empty_project_does_not_crash(db_session, tenant_with_user):
    _, _, project = tenant_with_user("empty@example.com", "Пустой")
    result = funnel(db_session, project)

    assert result.get("views").count == 0
    assert result.step("views").conversion is None
    assert not result.step("views").measured
    assert result.ctr is None
    assert result.worst_step is None


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


def test_step_measured_only_where_both_instruments_stand(db_session, project_with_funnel):
    """Статья без трекинг-ссылки не должна занижать конверсию в переходы.

    Её дочитывания попали бы в знаменатель, а переходы в числитель попасть не могут:
    их физически никто не считает. Ровно на этом дашборд объявлял провальным шаг,
    который на измеренных статьях идёт нормально.
    """
    project, _, _ = project_with_funnel
    blind = _article(db_session, project, "Без трекинга", "bez-trekinga")
    db_session.commit()
    save_manual(db_session, blind, {"views": 50000, "reads": 20000},
                collected_at=date(2026, 7, 27))

    step = funnel(db_session, project).step("reads")

    assert step.from_count == 6400  # 20 000 дочитываний слепой статьи не в счёт
    assert step.to_count == 45
    assert step.conversion == pytest.approx(0.70, abs=0.01)
    assert step.partial  # прибор стоит не на всех статьях — это видно на экране
    assert (step.articles, step.total_articles) == (2, 3)


def test_step_ratio_uses_own_benchmark(db_session, project_with_funnel):
    """Единого порога нет: 40% с показов — норма, 0.7% с дочитываний — провал."""
    project, _, _ = project_with_funnel

    steps = {s.key: s for s in funnel(db_session, project).steps}

    assert steps["views"].ratio == pytest.approx(1.6, abs=0.01)  # 40% при ориентире 25%
    assert not steps["views"].is_bottleneck
    assert steps["reads"].ratio == pytest.approx(0.35, abs=0.01)  # 0.7% при ориентире 2%
    assert steps["reads"].is_bottleneck
    assert steps["reads"].lost == 6355  # 6400 дочитываний → 45 переходов


def test_worst_step_is_the_biggest_lag_not_the_first(db_session, project_with_funnel):
    """Ниже ориентира могут быть несколько шагов — чинить надо тот, что отстал сильнее."""
    project, _, _ = project_with_funnel

    result = funnel(db_session, project)
    steps = {s.key: s for s in result.steps}

    assert steps["reads"].is_bottleneck  # 0.70% при 2% → 0.35 от ориентира
    assert steps["clicks"].is_bottleneck  # 4.44% при 20% → 0.22 от ориентира
    assert result.worst_step.key == "clicks"  # раньше был бы выбран первый по порядку


def test_timeline_takes_latest_snapshot_per_article(db_session, project_with_funnel):
    """Накопительная линия, а не сумма всех срезов: три замера ≠ утроенные показы."""
    project, _, _ = project_with_funnel

    tl = timeline(db_session, project)

    assert [(p.day, p.views) for p in tl.points] == [
        (date(2026, 7, 20), 10000),  # только победитель, первый срез
        (date(2026, 7, 27), 16000),  # 14 000 (его же новый срез) + 2 000 второй статьи
    ]
    assert bool(tl)


def test_timeline_marks_money_event(db_session, project_with_funnel):
    project, _, _ = project_with_funnel

    events = timeline(db_session, project).events

    assert [e.day for e in events] == [date(2026, 7, 10), date(2026, 7, 12)]
    assert events[0].title == "заявка" and not events[0].is_money
    assert events[1].is_money and events[1].title == "продажа"
    assert events[1].amount == 50000  # форматирует шаблон, не сервис


def test_timeline_needs_two_points(db_session, tenant_with_user):
    """По одному замеру линию не построить — блок просто не показывается."""
    _, _, project = tenant_with_user("empty@example.com", "Пустой")

    tl = timeline(db_session, project)

    assert not tl
    assert tl.plot().line == ""


def test_timeline_plot_geometry(db_session, project_with_funnel):
    project, _, _ = project_with_funnel

    plot = timeline(db_session, project).plot()

    assert plot.line.startswith("M ") and len(plot.dots) == 2
    assert plot.dots[1]["x"] > plot.dots[0]["x"]  # позже по времени — правее
    assert plot.dots[1]["y"] < plot.dots[0]["y"]  # больше показов — выше
    assert plot.ticks[0]["label"] == "10.07"  # шкала начинается с первого события
    assert all(0 <= e["x"] <= 1000 for e in plot.events)


def test_empty_article_separated_from_working_ones(db_session, project_with_funnel):
    """Статья без единого измерения помечается отдельно, а не строкой нулей."""
    project, winner, _ = project_with_funnel
    fresh = _article(db_session, project, "Свежая, ещё не опубликована", "svezhaya")
    db_session.commit()

    stats = {item.article.id: item for item in article_stats(db_session, project)}

    assert stats[fresh.id].is_empty
    assert not stats[fresh.id].has_metrics and not stats[fresh.id].has_links
    assert not stats[winner.id].is_empty
    assert stats[winner.id].has_metrics and stats[winner.id].has_links


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
