"""Служебные команды.

    python3 -m app.cli bootstrap          — арендатор + пользователь + проект с контент-ДНК
    python3 -m app.cli adduser <email> [owner|member]
                                          — выдать доступ ещё одному человеку, пароль сгенерируется
    python3 -m app.cli passwd <email>     — сменить пароль (спросит новый)
    python3 -m app.cli tgtrack-sync       — добрать UTM подписчиков из TGTrack
    python3 -m app.cli check-bot          — проверить токен бота и доступ к каналу
    python3 -m app.cli metrics-reminder   — проверить свежесть статистики (и уведомить)
"""
import getpass
import sys

from sqlalchemy import select

from app.config import get_settings
from app.core.seed_knowledge import BRAND, seed_project
from app.db import SessionLocal
from app.models.project import Project
from app.models.tenancy import ROLE_OWNER, Tenant, User
from app.security import hash_password


def bootstrap() -> None:
    settings = get_settings()
    email = (settings.bootstrap_email or "").strip().lower()
    password = settings.bootstrap_password

    if not email or not password:
        print("Заполните BOOTSTRAP_EMAIL и BOOTSTRAP_PASSWORD в .env", file=sys.stderr)
        raise SystemExit(1)

    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            print(f"Пользователь {email} уже есть — ничего не делаю.")
            return

        tenant = db.scalar(select(Tenant).limit(1))
        if tenant is None:
            tenant = Tenant(name=BRAND)
            db.add(tenant)
            db.flush()

        db.add(
            User(
                tenant_id=tenant.id,
                email=email,
                password_hash=hash_password(password),
                role=ROLE_OWNER,
            )
        )

        project = db.scalar(select(Project).where(Project.tenant_id == tenant.id).limit(1))
        if project is None:
            project = Project(tenant_id=tenant.id, name=BRAND)
            db.add(project)
            db.flush()

        db.commit()
        db.refresh(project)
        seed_project(db, project)

    print(f"Готово: арендатор «{BRAND}», пользователь {email}, проект «{project.name}».")


def adduser(email: str, role: str) -> None:
    """Доступ второму и следующим: `bootstrap` заводит только первого."""
    from app.models.tenancy import ROLE_MEMBER
    from app.services.users import UserError, create_user

    with SessionLocal() as db:
        try:
            user, password = create_user(db, email, role=role or ROLE_MEMBER)
        except UserError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from exc

    print(f"Пользователь {user.email} создан, роль: {user.role}")
    print(f"Пароль (показывается один раз): {password}")


def passwd(email: str) -> None:
    new_password = getpass.getpass("Новый пароль: ")
    if not new_password:
        print("Пустой пароль не годится", file=sys.stderr)
        raise SystemExit(1)

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            print(f"Пользователь {email} не найден", file=sys.stderr)
            raise SystemExit(1)
        user.password_hash = hash_password(new_password)
        db.commit()
    print("Пароль обновлён.")


def tgtrack_sync() -> None:
    from app.services.tgtrack_sync import run_sync

    settings = get_settings()
    if not settings.tgtrack_tg_api_key and not settings.tgtrack_max_api_key:
        print("Нет ключей TGTrack в .env — добирать нечем.", file=sys.stderr)
        raise SystemExit(1)

    with SessionLocal() as db:
        result = run_sync(db, settings=settings)
    print(f"TGTrack: {result.as_log()}")


def check_bot() -> None:
    """Проверка перед подключением канала: жив ли токен и видит ли бот канал."""
    from app.integrations.telegram import TelegramError, build_client

    settings = get_settings()
    client = build_client(settings.telegram_bot_token)
    if client is None:
        print("Нет TELEGRAM_BOT_TOKEN в .env", file=sys.stderr)
        raise SystemExit(1)

    try:
        me = client.get_me()
    except TelegramError as exc:
        print(f"Токен не работает: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Бот: @{me.get('username')} (id {me.get('id')})")

    with SessionLocal() as db:
        projects = db.scalars(select(Project).where(Project.tg_channel_id.isnot(None))).all()

    if not projects:
        print("Ни у одного проекта не задан ID Telegram-канала — задайте его в интерфейсе.")
        return

    for project in projects:
        try:
            link = client.create_invite_link(project.tg_channel_id, "проверка доступа")
            client.revoke_invite_link(project.tg_channel_id, link.url)
            print(f"«{project.name}»: канал {project.tg_channel_id} доступен, ссылки создаются")
        except TelegramError as exc:
            print(f"«{project.name}»: {exc}", file=sys.stderr)
            print("  Бот должен быть админом канала с правом приглашать пользователей.")


def metrics_reminder() -> None:
    from app.services.reminders import run_reminder

    with SessionLocal() as db:
        stale = run_reminder(db)
    if not stale:
        print("Статистика свежая, напоминать не о чем.")
        return
    for item in stale:
        print(item.as_text())
    if not get_settings().notify_chat_id:
        print("\nNOTIFY_CHAT_ID не задан — напоминание видно только здесь и в интерфейсе.")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(0)

    command = sys.argv[1]
    if command == "bootstrap":
        bootstrap()
    elif command == "adduser" and len(sys.argv) > 2:
        adduser(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    elif command == "passwd" and len(sys.argv) > 2:
        passwd(sys.argv[2])
    elif command == "tgtrack-sync":
        tgtrack_sync()
    elif command == "check-bot":
        check_bot()
    elif command == "metrics-reminder":
        metrics_reminder()
    else:
        print(__doc__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
