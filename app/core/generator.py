"""Генератор статьи/поста в голосе автора.

Два режима, как и в MVP: есть ключ Anthropic — пишет Claude по промпту с few-shot;
нет ключа или API недоступен — шаблон-фолбэк в том же голосе (демо не ломается).

Формат ответа модели зафиксирован структурным выводом (title/subtitle/body_md) —
это надёжнее, чем парсить заголовок и подзаголовок из свободного текста, и сразу
закрывает требование vc.ru отдавать подзаголовок отдельным полем.
"""
import logging
from dataclasses import dataclass, field, replace

from app.config import Settings, get_settings
from app.integrations.llm import LLMError, LLMNoFunds, generate_json, provider
from app.core.formats import KIND_POST, PlatformFormat
from app.core.markdown import first_h1, md_to_html, slugify, strip_h1, strip_long_dashes
from app.core.validator import ValidationReport, validate

log = logging.getLogger(__name__)

VIA_LLM = "llm"
VIA_TEMPLATE = "template"

ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Заголовок H1, без хэштегов и кавычек"},
        "subtitle": {
            "type": "string",
            "description": "Одна строка, раскрывает суть заголовка, содержит ключевое слово",
        },
        "body_md": {
            "type": "string",
            "description": "Тело в Markdown, без заголовка H1 и без ссылок на каналы",
        },
    },
    "required": ["title", "subtitle", "body_md"],
    "additionalProperties": False,
}


@dataclass
class VoiceSpec:
    brand: str = ""
    who: str = ""
    core_idea: str = ""
    audience: str = ""
    offer_cta: str = ""
    persona: str = ""
    format_rules: list[str] = field(default_factory=list)
    avoid_map: dict[str, str] = field(default_factory=dict)
    structure: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)


@dataclass
class CaseSpec:
    niche: str
    metric: str


@dataclass
class GeneratedArticle:
    topic: str
    title: str
    subtitle: str
    slug: str
    body_md: str
    body_html: str
    chars: int
    via: str
    model: str | None
    report: ValidationReport
    error: str | None = None


# ------------------------------------------------------------------ промпт


def build_prompt(
    topic: str,
    fmt: PlatformFormat,
    voice: VoiceSpec,
    cases: list[CaseSpec],
    keywords: list[str],
    examples: list[str],
) -> str:
    cases_block = "\n".join(f"- {c.niche}: {c.metric}" for c in cases)
    avoid = "; ".join(f"«{k}» -> «{v}»" for k, v in voice.avoid_map.items())
    rules = "\n".join(f"- {r}" for r in [*voice.format_rules, *fmt.extra_rules])
    structure = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(voice.structure))
    hashtags = " ".join(voice.hashtags[:5])

    if fmt.wants_keywords and keywords:
        kw_block = (
            "Ключевые запросы. Каждый должен встретиться в тексте минимум дважды — "
            "дословно, в естественных формулировках, вразбивку по тексту, "
            "хотя бы один раз в подзаголовке. Не перечисляй их подряд и не повторяй "
            "в одном абзаце:\n" + "\n".join(f"- {k}" for k in keywords)
        )
    else:
        kw_block = "Ключевые запросы не используются: пиши естественным языком."

    shots = "\n\n===== ПРИМЕР =====\n".join(examples) if examples else ""
    fewshot = ("\n\n===== ПРИМЕРЫ ХОРОШИХ ТЕКСТОВ =====\n" + shots) if shots else ""

    kind_word = "пост" if fmt.kind == KIND_POST else "SEO-статью"

    return f"""Ты пишешь за автора канала «{voice.brand}». Он {voice.who}.
Главная идея всех текстов: {voice.core_idea}.
Аудитория: {voice.audience}.

Голос: {voice.persona}
Заменяй разговорный хайп деловым языком: {avoid}

ЖЁСТКИЕ ПРАВИЛА ОФОРМЛЕНИЯ:
{rules}

Напиши {kind_word} для площадки {fmt.title} на тему: «{topic}».
{fmt.notes}

ОБЪЁМ — ЖЁСТКОЕ ТРЕБОВАНИЕ ПЛОЩАДКИ: тело текста от {fmt.min_chars} до {fmt.max_chars}
символов, считая пробелы. Ближе к {(fmt.min_chars + fmt.max_chars) // 2}. Текст длиннее
{fmt.max_chars} площадка обрежет, поэтому лучше сказать меньше, но точнее. Перед ответом
прикинь длину и при необходимости сократи: убирай повторы и общие рассуждения, а цифры и
кейсы оставляй.

{kw_block}

Опорная структура (можно адаптировать):
{structure}

Используй 1-2 реальных кейса из списка, цифры не меняй:
{cases_block}

Заверши призывом: {voice.offer_cta}
НЕ добавляй ссылки на каналы и сайт — их подставит система. В самом конце тела поставь хэштеги: {hashtags}

Верни ТОЛЬКО JSON-объект, без пояснений до и после, с полями:
- "title": заголовок без символа # и без хэштегов;
- "subtitle": одна строка-подзаголовок, раскрывает суть заголовка;
- "body_md": тело в Markdown БЕЗ заголовка H1 (он уже в title) и без ссылок.{fewshot}
"""


# ------------------------------------------------------------------ LLM


def llm_generate(prompt: str, settings: Settings, *, short: bool = False) -> tuple[dict, str] | None:
    """Вернуть (данные статьи, имя модели) либо None — тогда сработает шаблон-фолбэк.

    Куда идёт запрос, решает `integrations.llm`: сперва прокси routerai.ru, потом
    прямой ключ провайдера. Ошибку не поднимаем выше — статья всё равно должна
    получиться, пусть и шаблонная.
    """
    if provider(settings) is None:
        return None
    try:
        return generate_json(prompt, settings, short=short)
    except LLMNoFunds as exc:
        log.warning("%s", exc)
        return None
    except LLMError as exc:
        log.warning("Модель недоступна: %s", exc)
        return None


# ------------------------------------------------------------------ шаблон-фолбэк


def template_article(
    topic: str,
    fmt: PlatformFormat,
    voice: VoiceSpec,
    cases: list[CaseSpec],
    keywords: list[str],
) -> dict:
    """Текст в голосе автора без LLM — собирается из контент-ДНК."""
    case = cases[0] if cases else CaseSpec("остекление балконов", "800 ₽ за заявку")
    case2 = cases[1] if len(cases) > 1 else case
    extra = ", ".join(
        f"{c.niche.lower()} ({c.metric.split(',')[0]})" for c in cases[2:6]
    )
    kw_line = (
        " Разберём по-простому: " + ", ".join(keywords[:4]) + "."
        if (fmt.wants_keywords and keywords)
        else ""
    )
    hashtags = " ".join(voice.hashtags[:5])

    head = f"""Многие предприниматели, с которыми я работаю, сталкиваются с одной проблемой: реклама то работает, то нет. Заявки приходят всплесками, потом тишина. Каждый запуск похож на лотерею.{kw_line}

Это не «плохой таргетолог» и не «не та аудитория». Это отсутствие системы."""

    system_block = f"""## Что такое системный маркетинг

Это когда вы точно знаете: сколько заявок нужно для одной продажи, сколько вложить в рекламу, что сказать клиенту на каждом этапе и как повторить результат в следующем месяце. Без догадок и без «почувствовал».

## Реальный пример с цифрами

Ниша: {case.niche.lower()}. Было: заявки приходили, но конверсия в замер низкая, менеджеры жаловались, что «лиды холодные». Сделали: убрали общие запросы, оставили целевые, добавили квиз с расчётом, оцифровали воронку. Результат: {case.metric}.

## Из чего состоит система

Кто ваш клиент. Не «всем подряд», а конкретно: какой оборот, есть ли отдел продаж, брал ли раньше платный трафик, какой средний чек. Если не подходит, не берём.

Предложение, которое фильтрует. Не «горячие заявки по 99 рублей», а точная формулировка под нужного клиента. Так приходят те, с кем комфортно работать.

Цепочка касаний. Заявка, квалификация, диагностика, предложение, оплата. На каждом этапе понятно, что говорить, без импровизации.

Оцифровка. Простая таблица: сколько заявок, сколько диагностик, сколько продаж, какой средний чек. Через месяц видны закономерности, через три можно прогнозировать доход.

## Почему это важно

Вы перестаёте зависеть от одного удачного месяца: если площадка проседает, понятно, какие цифры добрать в другом месте. Вы знаете, сколько стоит клиент, и планируете бюджет заранее, а не по ощущениям. Вы видите, на каком шаге теряются деньги, и чините конкретный этап вместо смены подрядчика раз в квартал.

## Простой расчёт

Хотите 300 000 рублей в месяц, средний чек 50 000, нужно 6 клиентов. Каждый третий на диагностике покупает, значит нужно 18 диагностик. 4 из 10 заявок доходят до диагностики, значит нужно 45 заявок. Заявка стоит 500 рублей, значит нужно 22 500 рублей на рекламу. Теперь это не мечта, а план.

## Мой опыт

За последний год запускал рекламу в десятках ниш: {case.niche.lower()} ({case.metric.split(',')[0]}), {case2.niche.lower()} ({case2.metric.split(',')[0]}), {extra}. Разные ниши, разные цифры, общий принцип один: система плюс оцифровка равно прогнозируемый результат."""

    if fmt.kind == KIND_POST:
        # Пост: без подзаголовков ## и без длинного перечня ниш
        system_block = f"""Что такое системный маркетинг. Это когда вы точно знаете: сколько заявок нужно для одной продажи, сколько вложить в рекламу, что сказать клиенту на каждом этапе и как повторить результат в следующем месяце.

Как это выглядит на практике. Ниша: {case.niche.lower()}. Убрали общие запросы, оставили целевые, добавили квиз с расчётом, оцифровали воронку. Результат: {case.metric}.

Простой расчёт. Хотите 300 000 рублей в месяц, средний чек 50 000, нужно 6 клиентов. Каждый третий на диагностике покупает, значит нужно 18 диагностик. 4 из 10 заявок доходят до диагностики, значит нужно 45 заявок. Заявка стоит 500 рублей, значит нужно 22 500 рублей на рекламу."""

    body = f"""{head}

{system_block}

Если хотите разобрать свою ситуацию, напишите мне. {voice.offer_cta}

{hashtags}"""

    return {
        "title": topic,
        "subtitle": "Разбор на цифрах: как система заменяет догадки в рекламе",
        "body_md": body,
    }


# ------------------------------------------------------------------ CTA


def cta_markdown(offer_cta: str, links: list[tuple[str, str]]) -> str:
    if not links:
        return ""
    lines = ["", "## Что дальше", "", offer_cta, ""]
    # Подпись, а не голый URL: длинная ссылка с метками в тексте статьи выглядит мусорно
    # и переносится по строкам прямо в опубликованном виде.
    lines += [f"- [{label}]({url})" for label, url in links]
    return "\n".join(lines) + "\n"


def attach_cta(
    article: "GeneratedArticle", offer_cta: str, links: list[tuple[str, str]]
) -> "GeneratedArticle":
    """Дописать блок ссылок к готовому тексту.

    Нужен потому, что UTM-метка содержит слаг, а слаг известен только после
    генерации заголовка. Повторно дёргать модель ради этого нельзя.
    """
    if not links:
        return article
    body_md = article.body_md.rstrip() + "\n" + cta_markdown(offer_cta, links)
    return replace(article, body_md=body_md, body_html=md_to_html(body_md))


# ------------------------------------------------------------------ сборка


def generate(
    topic: str,
    fmt: PlatformFormat,
    voice: VoiceSpec,
    cases: list[CaseSpec],
    *,
    keywords: list[str] | None = None,
    examples: list[str] | None = None,
    cta_links: list[tuple[str, str]] | None = None,
    settings: Settings | None = None,
) -> GeneratedArticle:
    settings = settings or get_settings()
    keywords = keywords or []
    examples = examples or []

    prompt = build_prompt(topic, fmt, voice, cases, keywords, examples)
    result = llm_generate(prompt, settings, short=fmt.kind == KIND_POST)

    error: str | None = None
    if result is None:
        data, model, via = template_article(topic, fmt, voice, cases, keywords), None, VIA_TEMPLATE
        # Ключ есть, а текста нет — значит модель отвалилась. Об этом надо сказать прямо,
        # иначе шаблонная статья молча выдаётся за написанную моделью.
        if provider(settings) is not None:
            error = "Модель недоступна, собран шаблон-фолбэк"
    else:
        data, model = result
        via = VIA_LLM

    title = (data.get("title") or topic).strip()
    subtitle = (data.get("subtitle") or "").strip()
    body = strip_long_dashes(data.get("body_md") or "").strip()

    # Модель иногда всё равно кладёт H1 в тело — заголовок хранится отдельно
    if (h1 := first_h1(body)) is not None:
        title = title or h1
        body = strip_h1(body)

    report = validate(body, fmt, keywords=keywords, subtitle=subtitle)

    full_md = body + "\n" + cta_markdown(voice.offer_cta, cta_links or [])
    return GeneratedArticle(
        topic=topic,
        title=title,
        subtitle=subtitle,
        slug=slugify(title or topic),
        body_md=full_md,
        body_html=md_to_html(full_md),
        chars=report.chars,
        via=via,
        model=model,
        report=report,
        error=error,
    )
