import pytest

from app.core.formats import PLATFORM_DZEN, PLATFORM_VC, get_format
from app.core.validator import keyword_density, validate

GOOD_BODY = (
    "Системный маркетинг начинается с оцифровки. "
    "Заявки считаем на каждом шаге воронки. "
    "Напишите мне, разберём вашу ситуацию на диагностике без обязательств. #маркетинг"
)


def test_keyword_density_counts_phrase_occurrences():
    text = "воронка продаж и ещё раз воронка продаж, всего восемь слов тут"
    density = keyword_density(text, "воронка продаж")
    # 2 вхождения по 2 слова из 11 слов текста
    assert density == pytest.approx(36.36, abs=0.1)


def test_keyword_density_handles_punctuation_between_words():
    assert keyword_density("это воронка, продаж", "воронка продаж") > 0


def test_keyword_density_empty_inputs():
    assert keyword_density("", "заявки") == 0.0
    assert keyword_density("текст", "") == 0.0


def test_validate_flags_missing_cta_and_hashtags():
    report = validate("Просто текст без призыва", get_format(PLATFORM_DZEN))
    assert any("призыв" in w for w in report.warnings)
    assert any("хэштег" in w for w in report.warnings)


def test_validate_flags_long_dashes_and_hype():
    report = validate(GOOD_BODY + " — вот так 🔥", get_format(PLATFORM_DZEN))
    assert any("длинные тире" in w for w in report.warnings)
    assert any("Эмодзи-хайп" in w for w in report.warnings)


def test_validate_flags_cliches():
    report = validate(GOOD_BODY + " В современном мире это важно.", get_format(PLATFORM_DZEN))
    assert any("Штамп" in w for w in report.warnings)


def test_validate_flags_over_limit():
    fmt = get_format(PLATFORM_DZEN)
    report = validate("а" * (fmt.max_chars + 1) + " диагностика #тег", fmt)
    assert any("больше лимита" in w for w in report.warnings)


def test_validate_requires_subtitle_for_vc_only():
    fmt_vc, fmt_dzen = get_format(PLATFORM_VC), get_format(PLATFORM_DZEN)
    assert any("подзаголовок" in w for w in validate(GOOD_BODY, fmt_vc, subtitle="").warnings)
    assert not any("подзаголовок" in w for w in validate(GOOD_BODY, fmt_dzen, subtitle="").warnings)


def test_validate_reports_keyword_density():
    fmt = get_format(PLATFORM_DZEN)
    report = validate(GOOD_BODY, fmt, keywords=["заявки", "лендинг"])
    assert "заявки" in report.density
    assert report.density["лендинг"] == 0.0
    assert any("не встречается" in w for w in report.warnings)


def test_validate_flags_keyword_overuse():
    body = ("заявки " * 40) + " диагностика #тег"
    report = validate(body, get_format(PLATFORM_DZEN), keywords=["заявки"])
    assert any("переспам" in w for w in report.warnings)
