"""Коды трекинг-ссылок и имена пригласительных ссылок.

Чистые функции без БД и сети — вся работа с моделями лежит в `app/services/links.py`.
"""
import re
import secrets

from app.integrations.telegram import INVITE_NAME_LIMIT

CODE_LENGTH = 8
_CODE_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # без похожих символов: 0/o, 1/l
_CODE_RE = re.compile(rf"^[{_CODE_ALPHABET}]{{{CODE_LENGTH}}}$")


def new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


def build_invite_name(code: str, platform_title: str, slug: str) -> str:
    """Имя пригласительной ссылки: «код площадка слаг», обрезанное до лимита Telegram.

    Код идёт первым и обрезаться не может — по нему в апдейте `chat_member` находится
    статья. Остальное нужно, чтобы Артур узнавал ссылку глазами в интерфейсе Telegram.
    """
    return f"{code} {platform_title} {slug}".strip()[:INVITE_NAME_LIMIT]


def code_from_invite_name(name: str | None) -> str | None:
    """Достать код из имени ссылки, пришедшего в апдейте `chat_member`."""
    if not name:
        return None
    candidate = name.strip().split(" ", 1)[0]
    return candidate if _CODE_RE.match(candidate) else None


def redirect_url(base_url: str, code: str) -> str:
    return f"{base_url.rstrip('/')}/r/{code}"
