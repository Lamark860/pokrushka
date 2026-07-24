"""Настройки сервиса. Всё читается из .env (см. .env.example)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # БД
    database_url: str = "postgresql+psycopg://traff:traff@localhost:5436/traff"

    # Веб
    secret_key: str = "dev-only-change-me"
    public_base_url: str = "http://localhost:8099"

    # Генерация статей. Основной путь — OpenAI-совместимый прокси routerai.ru (как в
    # соседних проектах владельца), запасной — прямой ключ провайдера.
    # Без обоих работает шаблон-фолбэк: демо не ломается.
    router_api_key: str = ""
    router_base_url: str = "https://routerai.ru/api/v1"
    # Выбрано сравнением 21 модели каталога на реальной задаче, с перепроверкой финалистов
    # на трёх темах (подробности — _local_docs/work_reports). qwen-plus: единственная, кто
    # уложился в лимит площадки во всех прогонах, самый маленький разброс и самая низкая
    # цена. Соблюдение лимита здесь важнее пикового качества: Дзен режет длинный текст,
    # а Артур публикует руками и не должен каждый раз это ловить.
    router_model: str = "qwen/qwen-plus"
    # Посты в Telegram и MAX. Отдельно не замерялись: там короче и без SEO-ключей,
    # поэтому пока та же модель. Появится статистика по постам — можно развести.
    router_model_short: str = "qwen/qwen-plus"

    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-4-8"
    llm_max_tokens: int = 8000
    llm_effort: str = "high"

    # Трекинг подписок (этап 2). Пусто — система работает на прямых ссылках без атрибуции.
    telegram_bot_token: str = ""
    tgtrack_tg_api_key: str = ""
    tgtrack_max_api_key: str = ""

    # Куда слать напоминания планировщика (личка Артура или служебный чат).
    # Пусто — напоминание останется только в интерфейсе и журнале задач.
    notify_chat_id: str = ""
    # Статистика считается устаревшей, если срезов не было столько дней
    metrics_stale_days: int = 8

    # Первый пользователь (команда `python3 -m app.cli bootstrap`)
    bootstrap_email: str = ""
    bootstrap_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
