import pytest

from app.core.formats import FORMATS, PLATFORM_DZEN, PLATFORM_MAX, PLATFORM_TG, PLATFORM_VC, get_format
from app.core.generator import VIA_TEMPLATE, build_prompt, generate
from app.core.markdown import has_long_dashes

TOPIC = "Как выстроить маркетинг, который не зависит от удачи"


@pytest.mark.parametrize("platform", list(FORMATS))
def test_fallback_fits_platform_limits(platform, voice, cases, offline_settings):
    """Без ключа Anthropic шаблон-фолбэк обязан укладываться в лимиты каждой площадки."""
    fmt = get_format(platform)
    art = generate(TOPIC, fmt, voice, cases, settings=offline_settings)

    assert art.via == VIA_TEMPLATE
    assert art.model is None
    assert fmt.min_chars <= art.chars <= fmt.max_chars
    assert not any("лимита" in w or "ориентира" in w for w in art.report.warnings)


@pytest.mark.parametrize("platform", list(FORMATS))
def test_fallback_follows_style_rules(platform, voice, cases, offline_settings):
    art = generate(TOPIC, get_format(platform), voice, cases, settings=offline_settings)

    assert not has_long_dashes(art.body_md)
    assert "#" in art.body_md  # хэштеги на месте
    assert "диагностик" in art.body_md.lower()  # призыв к действию


def test_posts_have_no_subheadings(voice, cases, offline_settings):
    """Telegram и MAX — простые абзацы: текст должен копироваться с телефона."""
    for platform in (PLATFORM_TG, PLATFORM_MAX):
        art = generate(TOPIC, get_format(platform), voice, cases, settings=offline_settings)
        assert "## " not in art.body_md


def test_articles_have_subheadings(voice, cases, offline_settings):
    art = generate(TOPIC, get_format(PLATFORM_DZEN), voice, cases, settings=offline_settings)
    assert "## " in art.body_md


def test_subtitle_present_for_vc(voice, cases, offline_settings):
    art = generate(TOPIC, get_format(PLATFORM_VC), voice, cases, settings=offline_settings)
    assert art.subtitle.strip()


def test_cta_links_appended(voice, cases, offline_settings):
    art = generate(
        TOPIC,
        get_format(PLATFORM_DZEN),
        voice,
        cases,
        cta_links=[("Telegram", "https://t.me/artur_baza_marketingg")],
        settings=offline_settings,
    )
    assert "https://t.me/artur_baza_marketingg" in art.body_md
    assert "Что дальше" in art.body_md
    assert 'rel="nofollow"' in art.body_html


def test_generate_reports_density_for_seo_platforms(voice, cases, offline_settings):
    art = generate(
        TOPIC, get_format(PLATFORM_DZEN), voice, cases,
        keywords=["системный маркетинг"], settings=offline_settings,
    )
    assert "системный маркетинг" in art.report.density


def test_prompt_carries_voice_rules_and_limits(voice, cases):
    prompt = build_prompt(TOPIC, get_format(PLATFORM_VC), voice, cases, ["заявки"], [])

    assert "НЕ использовать длинные тире" in prompt
    assert "от 2500 до 5000" in prompt  # лимит площадки требуем жёстко
    assert "Завалю клиентами" in prompt  # карта замен хайпа
    assert "800 ₽ за заявку" in prompt  # кейсы с цифрами
    assert "подзаголовок" in prompt.lower()


def test_prompt_omits_keywords_for_posts(voice, cases):
    prompt = build_prompt(TOPIC, get_format(PLATFORM_TG), voice, cases, ["заявки"], [])
    assert "Ключевые запросы не используются" in prompt
