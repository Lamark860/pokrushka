"""Редирект /r/<code>: ведёт по назначению и считает переходы."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.tracking import new_code
from app.models.content import LINK_MAX, Article, TrackingLink


@pytest.fixture
def link(db_session, tenant_with_user):
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    article = Article(
        tenant_id=project.tenant_id, project_id=project.id, topic="Тема", title="Заголовок",
        slug="tema", platform="dzen", format_kind="article",
        body_md="текст", body_html="<p>текст</p>", chars=5,
    )
    db_session.add(article)
    db_session.flush()
    tracking_link = TrackingLink(
        tenant_id=project.tenant_id, article_id=article.id, kind=LINK_MAX,
        code=new_code(), url="https://max.ru/id183308463787_biz?utm_source=dzen", utm={},
    )
    db_session.add(tracking_link)
    db_session.commit()
    return tracking_link


def test_redirect_leads_to_target_and_counts_click(client, db_session, link):
    response = client.get(f"/r/{link.code}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == link.url
    db_session.expire_all()
    assert db_session.scalar(select(TrackingLink.clicks).where(TrackingLink.id == link.id)) == 1


def test_clicks_accumulate(client, db_session, link):
    for _ in range(3):
        client.get(f"/r/{link.code}", follow_redirects=False)

    db_session.expire_all()
    assert db_session.scalar(select(TrackingLink.clicks).where(TrackingLink.id == link.id)) == 3


def test_unknown_code_goes_home_without_error(client):
    response = client.get("/r/nosuchcd", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/"


def test_revoked_link_still_works_but_click_not_counted(client, db_session, link):
    """Ссылку сняли вместе со статьёй: человека пропускаем, статистику не растим."""
    link.revoked_at = datetime.now(timezone.utc)
    db_session.commit()

    response = client.get(f"/r/{link.code}", follow_redirects=False)

    assert response.headers["location"] == link.url
    db_session.expire_all()
    assert db_session.scalar(select(TrackingLink.clicks).where(TrackingLink.id == link.id)) == 0


def test_redirect_is_public(client, link):
    """По ссылке ходят читатели статьи — авторизации быть не должно."""
    client.post("/logout", follow_redirects=False)
    assert client.get(f"/r/{link.code}", follow_redirects=False).status_code == 302
