"""Дашборд и ручной учёт заявок."""
from datetime import date

from sqlalchemy import select

from app.models.content import Article
from app.models.tracking import STAGE_SALE, Deal, Subscriber


def login(client, email="artur@example.com"):
    return client.post(
        "/login", data={"email": email, "password": "secret123"}, follow_redirects=False
    )


def _article(db, project, title="Статья", slug="statya"):
    article = Article(
        tenant_id=project.tenant_id, project_id=project.id, topic=title, title=title,
        slug=slug, platform="dzen", format_kind="article",
        body_md="текст", body_html="<p>текст</p>", chars=5,
    )
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


def test_dashboard_opens_for_empty_project(client, tenant_with_user):
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    login(client)

    response = client.get(f"/projects/{project.id}/dashboard")

    assert response.status_code == 200
    assert "Воронка" in response.text


def test_deal_attributed_by_username(client, db_session, tenant_with_user):
    """Главная связка этапа 4: заявка → подписчик → статья, которая его привела."""
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    article = _article(db_session, project)
    db_session.add(
        Subscriber(
            tenant_id=project.tenant_id, project_id=project.id, platform="tg",
            external_user_id="777", username="vasya", article_id=article.id,
        )
    )
    db_session.commit()
    login(client)

    client.post(
        f"/projects/{project.id}/deals",
        data={"stage": STAGE_SALE, "username": "@vasya", "amount": "50000",
              "niche": "Ремонт котлов", "day": "2026-07-24"},
        follow_redirects=False,
    )

    deal = db_session.scalar(select(Deal))
    assert deal.article_id == article.id  # статью подставили сами
    assert deal.subscriber_id is not None
    assert deal.amount == 50000
    assert deal.day == date(2026, 7, 24)


def test_deal_with_explicit_article_wins_over_username(client, db_session, tenant_with_user):
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    chosen = _article(db_session, project, "Выбранная руками", "vybrannaya")
    other = _article(db_session, project, "Другая", "drugaya")
    db_session.add(
        Subscriber(tenant_id=project.tenant_id, project_id=project.id, platform="tg",
                   external_user_id="1", username="vasya", article_id=other.id)
    )
    db_session.commit()
    login(client)

    client.post(
        f"/projects/{project.id}/deals",
        data={"stage": "заявка", "username": "vasya", "article_id": str(chosen.id)},
        follow_redirects=False,
    )

    assert db_session.scalar(select(Deal)).article_id == chosen.id


def test_deal_without_match_is_still_saved(client, db_session, tenant_with_user):
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    login(client)

    client.post(
        f"/projects/{project.id}/deals",
        data={"stage": "заявка", "username": "nobody", "amount": "не число"},
        follow_redirects=False,
    )

    deal = db_session.scalar(select(Deal))
    assert deal is not None
    assert deal.article_id is None  # честно: связь неизвестна
    assert deal.amount == 0


def test_dashboard_shows_funnel_numbers(client, db_session, tenant_with_user):
    from app.services.metrics import save_manual

    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    article = _article(db_session, project)
    save_manual(db_session, article, {"views": 12000, "reads": 5000})
    db_session.add(
        Deal(tenant_id=project.tenant_id, project_id=project.id, article_id=article.id,
             day=date(2026, 7, 24), stage=STAGE_SALE, amount=90000)
    )
    db_session.commit()
    login(client)

    page = client.get(f"/projects/{project.id}/dashboard").text

    assert "12 000" in page  # показы с разделителем тысяч
    assert "90 000 ₽" in page
    assert "41.7%" in page  # дочитываемость


def test_deal_delete_scoped_to_tenant(client, db_session, tenant_with_user):
    _, _, foreign = tenant_with_user("chuzhoy@example.com", "Чужой")
    db_session.add(
        Deal(tenant_id=foreign.tenant_id, project_id=foreign.id, day=date(2026, 7, 24),
             stage="заявка", amount=0)
    )
    db_session.commit()
    foreign_deal = db_session.scalar(select(Deal))

    tenant_with_user("artur@example.com", "База Маркетинг")
    login(client)

    assert client.post(f"/deals/{foreign_deal.id}/delete",
                       follow_redirects=False).status_code == 404
