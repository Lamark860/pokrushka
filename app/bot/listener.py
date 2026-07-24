"""Бот-слушатель: ловит вступления и отписки в канале.

Запуск: `python3 -m app.bot.listener`

Почему это отдельный постоянно работающий процесс, а не задача по расписанию: Telegram
отдаёт события членства только апдейтом `chat_member` в реальном времени. Списка
подписчиков в Bot API нет, история апдейтов живёт ~24 часа — пропустил, значит потерял.

Long-polling выбран вместо вебхука сознательно: не нужен публичный HTTPS и домен,
поэтому бота можно поднять и проверить до деплоя.

Требования к боту: он должен быть **администратором канала** с правом приглашать —
иначе апдейты `chat_member` не приходят вообще.
"""
import asyncio
import logging

from app.config import get_settings
from app.db import SessionLocal
from app.services.subscribers import handle_chat_member

log = logging.getLogger(__name__)

ALLOWED_UPDATES = ["chat_member"]


async def run() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit(
            "Нет TELEGRAM_BOT_TOKEN в .env — без токена слушать нечего.\n"
            "Артур должен создать бота, добавить его администратором канала "
            "и дать право приглашать пользователей."
        )

    try:
        from aiogram import Bot, Dispatcher
        from aiogram.types import ChatMemberUpdated
    except ImportError as exc:  # pragma: no cover - зависимость объявлена в requirements
        raise SystemExit("Не установлен aiogram: pip install -r requirements.txt") from exc

    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()

    @dispatcher.chat_member()
    async def on_chat_member(event: ChatMemberUpdated) -> None:
        update = {"chat_member": event.model_dump(mode="json")}
        # Сервис синхронный (SQLAlchemy), поэтому уводим его в отдельный поток
        subscriber = await asyncio.to_thread(_handle, update)
        if subscriber is not None:
            log.info(
                "Подписчик %s: статья %s, ссылка %s",
                subscriber.external_user_id, subscriber.article_id, subscriber.invite_name,
            )

    me = await bot.get_me()
    log.info("Слушаю события от имени @%s", me.username)
    await dispatcher.start_polling(bot, allowed_updates=ALLOWED_UPDATES)


def _handle(update: dict):
    with SessionLocal() as db:
        return handle_chat_member(db, update)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
