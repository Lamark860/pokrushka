"""Клиент сервиса «Откуда подписки» (tgtrack.ru).

Важно понимать границы сервиса: у него **нет** метода «дай подписчиков за период».
Все три метода API (`on_telegram_webhook`, `send_reach_goal`, `get_user_info`) требуют,
чтобы `user_id` мы уже знали. Поэтому TGTrack не заменяет бота-слушателя, а дополняет
его: по известным подписчикам добирает UTM-метки, а для MAX служит основным источником
атрибуции, потому что там своего слушателя у нас нет.

Данные о последней подписке сервис хранит 30 дней — опрашивать нужно регулярно.
"""
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

TG_ROOT = "https://bot-api.tgtrack.ru/v1"
MAX_ROOT = "https://max.tgtrack.ru/API/bot-api/v1"
DEFAULT_TIMEOUT = 15.0

PLATFORM_TG = "tg"
PLATFORM_MAX = "max"


@dataclass(frozen=True)
class UserInfo:
    user_id: str
    username: str | None
    first_name: str | None
    invite_link: str | None
    utm: dict[str, str]
    raw: dict

    @property
    def has_attribution(self) -> bool:
        return bool(self.invite_link or self.utm)


class TGTrackClient:
    def __init__(
        self,
        api_key: str,
        *,
        platform: str = PLATFORM_TG,
        timeout: float = DEFAULT_TIMEOUT,
        root: str | None = None,
    ):
        if not api_key:
            raise ValueError("Нужен ключ TGTrack")
        self._api_key = api_key
        self._platform = platform
        self._timeout = timeout
        self._root = (root or (TG_ROOT if platform == PLATFORM_TG else MAX_ROOT)).rstrip("/")

    def get_user_info(self, user_id: str) -> UserInfo | None:
        """Карточка подписчика или None, если сервис его не знает / не ответил."""
        url = f"{self._root}/{self._api_key}/get_user_info"
        try:
            response = httpx.post(url, json={"user_id": str(user_id)}, timeout=self._timeout)
        except httpx.HTTPError as exc:
            log.warning("TGTrack недоступен: %s", exc)
            return None

        try:
            payload = response.json()
        except ValueError:
            log.warning("TGTrack вернул не JSON (код %s)", response.status_code)
            return None

        if str(payload.get("status", "")).upper() != "OK":
            log.info("TGTrack не знает пользователя %s: %s", user_id, payload.get("status"))
            return None

        data = payload.get("data") or {}
        utm = {
            key: str(data[key])
            for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
            if data.get(key)
        }
        return UserInfo(
            user_id=str(data.get("user_id", user_id)),
            username=data.get("username"),
            first_name=data.get("first_name"),
            invite_link=data.get("invite_link"),
            utm=utm,
            raw=data,
        )


def build_client(api_key: str, platform: str = PLATFORM_TG) -> TGTrackClient | None:
    """Клиент или None, если ключа нет: добор UTM просто не выполняется."""
    if not api_key:
        return None
    return TGTrackClient(api_key, platform=platform)
