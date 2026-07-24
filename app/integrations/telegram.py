"""Клиент Telegram Bot API — ровно те методы, что нужны для атрибуции подписок.

Почему именно так: у канала нет параметра `?start=` (его понимают только боты), а Bot API
не отдаёт список подписчиков. Единственный рабочий способ узнать, из какой статьи пришёл
человек, — завести на каждую статью **именованную пригласительную ссылку** и поймать
апдейт `chat_member`, в котором Telegram присылает имя этой ссылки.

Ограничение имени — 32 символа, поэтому в него кладётся короткий код, а полный набор
UTM живёт в `tracking_links.utm`.
"""
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"
INVITE_NAME_LIMIT = 32
DEFAULT_TIMEOUT = 15.0


class TelegramError(RuntimeError):
    """Bot API ответил ошибкой (ok=false) или не ответил вовсе."""


@dataclass(frozen=True)
class InviteLink:
    url: str
    name: str


class TelegramClient:
    def __init__(self, token: str, *, timeout: float = DEFAULT_TIMEOUT, api_root: str = API_ROOT):
        if not token:
            raise ValueError("Нужен токен бота")
        self._token = token
        self._timeout = timeout
        self._api_root = api_root.rstrip("/")

    # ------------------------------------------------------------------ низкий уровень

    def _call(self, method: str, payload: dict | None = None) -> dict:
        url = f"{self._api_root}/bot{self._token}/{method}"
        try:
            response = httpx.post(url, json=payload or {}, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise TelegramError(f"{method}: нет связи с Telegram ({exc})") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramError(f"{method}: ответ не разобрался как JSON") from exc

        if not data.get("ok"):
            raise TelegramError(
                f"{method}: {data.get('description', 'неизвестная ошибка')} "
                f"(код {data.get('error_code')})"
            )
        return data.get("result", {})

    # ------------------------------------------------------------------ методы

    def get_me(self) -> dict:
        return self._call("getMe")

    def create_invite_link(self, chat_id: str, name: str) -> InviteLink:
        """Именованная пригласительная ссылка. Имя режется до лимита Telegram."""
        safe_name = name[:INVITE_NAME_LIMIT]
        result = self._call(
            "createChatInviteLink",
            {"chat_id": chat_id, "name": safe_name, "creates_join_request": False},
        )
        return InviteLink(url=result["invite_link"], name=result.get("name", safe_name))

    def revoke_invite_link(self, chat_id: str, invite_link: str) -> None:
        self._call("revokeChatInviteLink", {"chat_id": chat_id, "invite_link": invite_link})

    def send_message(self, chat_id: str, text: str) -> None:
        """Служебное уведомление (например, напоминание обновить статистику)."""
        self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        )


def build_client(token: str) -> TelegramClient | None:
    """Клиент или None, если токена нет: без бота система работает на прямых ссылках."""
    if not token:
        return None
    return TelegramClient(token)
