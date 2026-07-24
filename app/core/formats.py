"""Форматы контента по площадкам (правка Артура п.3).

Лимиты: Telegram — 4096 символов на сообщение; MAX — Артур называл 3979 в переписке
и 4000 в правках, берём меньшее как безопасное; Dzen и vc.ru — длинная форма.
"""
from dataclasses import dataclass, field

PLATFORM_DZEN = "dzen"
PLATFORM_VC = "vc"
PLATFORM_TG = "tg"
PLATFORM_MAX = "max"

KIND_ARTICLE = "article"
KIND_POST = "post"

COPY_HTML = "html"
COPY_MARKDOWN = "markdown"


@dataclass(frozen=True)
class PlatformFormat:
    key: str
    title: str
    kind: str
    min_chars: int
    max_chars: int
    wants_keywords: bool
    wants_subtitle: bool
    copy_as: str
    notes: str = ""
    extra_rules: list[str] = field(default_factory=list)


FORMATS: dict[str, PlatformFormat] = {
    PLATFORM_DZEN: PlatformFormat(
        key=PLATFORM_DZEN,
        title="Яндекс.Дзен",
        kind=KIND_ARTICLE,
        min_chars=2000,
        max_chars=4000,
        wants_keywords=True,
        wants_subtitle=False,
        copy_as=COPY_HTML,
        notes="SEO-статья: H1, подзаголовки H2-H3, ключевые запросы, CTA, хэштеги.",
        extra_rules=[
            "Заголовок H1 через #, подзаголовки через ##",
            "Ключевые запросы вплетать естественно, без перечислений подряд",
        ],
    ),
    PLATFORM_VC: PlatformFormat(
        key=PLATFORM_VC,
        title="vc.ru",
        kind=KIND_ARTICLE,
        min_chars=2500,
        max_chars=5000,
        wants_keywords=True,
        wants_subtitle=True,
        copy_as=COPY_HTML,
        notes="Как Дзен, плюс отдельный подзаголовок: vc.ru просит его отдельным полем.",
        extra_rules=[
            "Подзаголовок раскрывает суть заголовка и содержит ключевое слово",
            "Подзаголовок — одна строка, без точки в конце",
        ],
    ),
    PLATFORM_TG: PlatformFormat(
        key=PLATFORM_TG,
        title="Telegram",
        kind=KIND_POST,
        min_chars=800,
        max_chars=4096,
        wants_keywords=False,
        wants_subtitle=False,
        copy_as=COPY_MARKDOWN,
        notes="Пост: короткий экспертный вывод, 1-2 кейса, CTA, хэштеги. Без SEO-ключей.",
        extra_rules=[
            "Естественный язык, ключевые запросы НЕ вставлять",
            "Без подзаголовков ## — только абзацы",
        ],
    ),
    PLATFORM_MAX: PlatformFormat(
        key=PLATFORM_MAX,
        title="MAX",
        kind=KIND_POST,
        min_chars=800,
        max_chars=3979,
        wants_keywords=False,
        wants_subtitle=False,
        copy_as=COPY_MARKDOWN,
        notes="Как Telegram, лимит жёстче (3979 символов).",
        extra_rules=[
            "Естественный язык, ключевые запросы НЕ вставлять",
            "Без подзаголовков ## — только абзацы",
        ],
    ),
}


def get_format(platform: str) -> PlatformFormat:
    try:
        return FORMATS[platform]
    except KeyError:
        raise ValueError(f"Неизвестная площадка: {platform}") from None
