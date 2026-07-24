"""Веб-слой: вход, генерация, изоляция арендаторов."""
from sqlalchemy import select

from app.models.content import Article


def login(client, email: str = "artur@example.com", password: str = "secret123"):
    return client.post(
        "/login", data={"email": email, "password": password}, follow_redirects=False
    )


def test_anonymous_redirected_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_rejects_wrong_password(client, tenant_with_user):
    tenant_with_user("artur@example.com", "База Маркетинг")
    response = login(client, password="wrong")
    assert response.status_code == 401
    assert "Неверная почта или пароль" in response.text


def test_login_then_single_project_opens_directly(client, tenant_with_user):
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    assert login(client).status_code == 303

    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == f"/projects/{project.id}"


def test_project_page_shows_seeded_content(client, tenant_with_user):
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    login(client)

    page = client.get(f"/projects/{project.id}").text
    assert "Остекление балконов и лоджий" in page  # кейс
    assert "Спокойный эксперт" in page  # персона
    assert "НЕ использовать длинные тире" in page  # правило Артура


def test_generate_article_creates_draft_within_limits(client, tenant_with_user, db_session):
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    login(client)

    response = client.post(
        f"/projects/{project.id}/generate",
        data={"topic": "Сколько заявок нужно для одной продажи", "platform": "dzen"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    article = db_session.scalar(select(Article))
    assert article is not None
    assert article.platform == "dzen"
    assert article.tenant_id == project.tenant_id
    assert 2000 <= article.chars <= 4000
    assert article.via == "template"  # в тестах ключа Anthropic нет


def test_cta_uses_own_redirect_and_utm_lives_in_link(client, tenant_with_user, db_session):
    """При включённом счётчике кликов в тексте стоит наш /r/<code>, а UTM — в самой ссылке."""
    from app.models.content import TrackingLink

    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    project.tg_channel_url = "https://t.me/artur_baza_marketingg"
    db_session.commit()
    login(client)

    client.post(
        f"/projects/{project.id}/generate",
        data={"topic": "Стабильный поток клиентов без выгорания", "platform": "vc"},
        follow_redirects=False,
    )
    article = db_session.scalar(select(Article))
    link = db_session.scalar(select(TrackingLink).where(TrackingLink.article_id == article.id))

    assert f"/r/{link.code}" in article.body_md
    assert "utm_source=vc" not in article.body_md  # метка не светится читателю
    assert "utm_source=vc" in link.url
    assert link.utm["utm_content"] == article.slug


def test_cta_uses_direct_link_when_clicks_not_tracked(client, tenant_with_user, db_session):
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    project.tg_channel_url = "https://t.me/artur_baza_marketingg"
    project.track_clicks = False
    db_session.commit()
    login(client)

    client.post(
        f"/projects/{project.id}/generate",
        data={"topic": "Стабильный поток клиентов без выгорания", "platform": "dzen"},
        follow_redirects=False,
    )
    article = db_session.scalar(select(Article))
    assert "https://t.me/artur_baza_marketingg" in article.body_md
    assert "utm_source=dzen" in article.body_md
    assert "/r/" not in article.body_md


def test_keywords_import_from_paste(client, tenant_with_user, db_session):
    from app.models.project import Keyword

    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    login(client)

    client.post(
        f"/projects/{project.id}/keywords",
        data={"raw": "Фраза;Частотность\nсистемный маркетинг;1200\nворонка продаж"},
        follow_redirects=False,
    )
    stored = db_session.scalars(select(Keyword).order_by(Keyword.id)).all()
    assert [(k.phrase, k.frequency) for k in stored] == [
        ("системный маркетинг", 1200),
        ("воронка продаж", None),
    ]


def test_tenant_cannot_reach_foreign_project(client, tenant_with_user):
    tenant_with_user("artur@example.com", "База Маркетинг")
    _, _, foreign_project = tenant_with_user("chuzhoy@example.com", "Чужой проект")

    login(client)  # входим как Артур
    assert client.get(f"/projects/{foreign_project.id}").status_code == 404


def test_tenant_cannot_reach_foreign_article(client, tenant_with_user, db_session):
    _, _, foreign = tenant_with_user("chuzhoy@example.com", "Чужой проект")
    login(client, email="chuzhoy@example.com")
    client.post(
        f"/projects/{foreign.id}/generate",
        data={"topic": "Чужая тема", "platform": "tg"},
        follow_redirects=False,
    )
    foreign_article = db_session.scalar(select(Article))
    client.post("/logout", follow_redirects=False)

    tenant_with_user("artur@example.com", "База Маркетинг")
    login(client)
    assert client.get(f"/articles/{foreign_article.id}").status_code == 404
