"""Атрибуция подписок: апдейт `chat_member` → запись в subscribers."""
import pytest
from sqlalchemy import select

from app.core.tracking import build_invite_name, new_code
from app.models.content import LINK_TG_INVITE, Article, TrackingLink
from app.models.tracking import Subscriber
from app.services.subscribers import handle_chat_member, parse_chat_member

CHAT_ID = "-1001234567890"


@pytest.fixture
def channel(db_session, tenant_with_user):
    """Проект с подключённым каналом, статья и её пригласительная ссылка."""
    _, _, project = tenant_with_user("artur@example.com", "База Маркетинг")
    project.tg_channel_id = CHAT_ID
    db_session.commit()

    article = Article(
        tenant_id=project.tenant_id,
        project_id=project.id,
        topic="Тема",
        title="Заголовок",
        slug="kak-vystroit-marketing",
        platform="dzen",
        format_kind="article",
        body_md="текст",
        body_html="<p>текст</p>",
        chars=5,
    )
    db_session.add(article)
    db_session.flush()

    code = new_code()
    link = TrackingLink(
        tenant_id=project.tenant_id,
        article_id=article.id,
        kind=LINK_TG_INVITE,
        code=code,
        invite_name=build_invite_name(code, "Дзен", article.slug),
        url="https://t.me/+abcdef",
        utm={"utm_content": article.slug},
    )
    db_session.add(link)
    db_session.commit()
    return project, article, link


def make_update(
    *, status: str = "member", old_status: str = "left", invite_name: str | None = None,
    user_id: str = "555", chat_id: str = CHAT_ID, date: int = 1_780_000_000,
) -> dict:
    payload = {
        "chat": {"id": int(chat_id), "type": "channel", "title": "База Маркетинг"},
        "from": {"id": int(user_id), "is_bot": False, "first_name": "Вася"},
        "date": date,
        "old_chat_member": {"user": {"id": int(user_id)}, "status": old_status},
        "new_chat_member": {
            "user": {"id": int(user_id), "first_name": "Вася", "username": "vasya"},
            "status": status,
        },
    }
    if invite_name is not None:
        payload["invite_link"] = {"invite_link": "https://t.me/+abcdef", "name": invite_name}
    return {"update_id": 1, "chat_member": payload}


def test_parse_reads_fields_and_direction():
    event = parse_chat_member(make_update(invite_name="abc Дзен slug"))
    assert event.user_id == "555"
    assert event.username == "vasya"
    assert event.invite_name == "abc Дзен slug"
    assert event.is_join and not event.is_leave


def test_parse_ignores_unrelated_payload():
    assert parse_chat_member({"update_id": 1, "message": {"text": "привет"}}) is None


def test_join_via_named_link_attributes_article(db_session, channel):
    project, article, link = channel

    subscriber = handle_chat_member(db_session, make_update(invite_name=link.invite_name))

    assert subscriber is not None
    assert subscriber.article_id == article.id
    assert subscriber.project_id == project.id
    assert subscriber.tenant_id == project.tenant_id
    assert subscriber.utm["code"] == link.code
    assert subscriber.left_at is None


def test_join_without_link_is_recorded_without_article(db_session, channel):
    subscriber = handle_chat_member(db_session, make_update(invite_name=None))

    assert subscriber is not None
    assert subscriber.article_id is None  # органика вне статей — тоже подписчик


def test_join_via_unknown_code_does_not_crash(db_session, channel):
    subscriber = handle_chat_member(db_session, make_update(invite_name="zzzzzzzz Дзен nope"))
    assert subscriber is not None
    assert subscriber.article_id is None


def test_leave_sets_left_at(db_session, channel):
    _, _, link = channel
    handle_chat_member(db_session, make_update(invite_name=link.invite_name))

    subscriber = handle_chat_member(
        db_session, make_update(status="left", old_status="member")
    )
    assert subscriber.left_at is not None


def test_return_keeps_first_attribution(db_session, channel):
    """Вернулся по другой ссылке — источником считается первая статья."""
    project, article, link = channel
    handle_chat_member(db_session, make_update(invite_name=link.invite_name))
    handle_chat_member(db_session, make_update(status="left", old_status="member"))

    other = Article(
        tenant_id=project.tenant_id, project_id=project.id, topic="Другая", title="Другая",
        slug="drugaya", platform="tg", format_kind="post", body_md="x", body_html="x", chars=1,
    )
    db_session.add(other)
    db_session.flush()
    other_code = new_code()
    db_session.add(
        TrackingLink(
            tenant_id=project.tenant_id, article_id=other.id, kind=LINK_TG_INVITE,
            code=other_code, invite_name=build_invite_name(other_code, "Telegram", other.slug),
            url="https://t.me/+other", utm={},
        )
    )
    db_session.commit()

    subscriber = handle_chat_member(
        db_session,
        make_update(invite_name=build_invite_name(other_code, "Telegram", other.slug)),
    )

    assert subscriber.article_id == article.id  # не перезаписали
    assert subscriber.left_at is None
    assert db_session.scalars(select(Subscriber)).all().__len__() == 1  # без дублей


def test_orphan_join_attributes_article_on_return(db_session, channel):
    """Пришёл без ссылки, потом вернулся по ссылке — тогда атрибуцию проставляем."""
    _, article, link = channel
    handle_chat_member(db_session, make_update(invite_name=None))
    handle_chat_member(db_session, make_update(status="left", old_status="member"))

    subscriber = handle_chat_member(db_session, make_update(invite_name=link.invite_name))
    assert subscriber.article_id == article.id


def test_event_from_unknown_channel_ignored(db_session, channel):
    assert handle_chat_member(db_session, make_update(chat_id="-100999")) is None
    assert db_session.scalars(select(Subscriber)).all() == []


def test_promotion_inside_channel_is_not_a_new_subscription(db_session, channel):
    """member → administrator: человек уже был в канале, новой подписки нет."""
    assert handle_chat_member(
        db_session, make_update(status="administrator", old_status="member")
    ) is None
