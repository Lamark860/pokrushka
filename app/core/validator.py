"""Пост-валидатор сгенерированного текста.

LLM не соблюдает лимиты символов сама — проверяем после генерации и показываем
замечания рядом со статьёй, а не молча публикуем.
"""
import re
from dataclasses import dataclass, field

from app.core.formats import PlatformFormat
from app.core.markdown import has_long_dashes

# Ключ считается «в норме», если его плотность попадает в этот коридор (правка Артура п.4)
DENSITY_MIN = 2.0
DENSITY_MAX = 3.0

_HYPE_EMOJI = "🔥💰🔑🚀💵🤑❤️👺😩"
_CLICHES = ("в современном мире", "не секрет, что", "в наше время")


@dataclass
class ValidationReport:
    chars: int
    density: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.warnings


def _words(text: str) -> list[str]:
    return re.findall(r"[\w-]+", text.lower(), flags=re.UNICODE)


def keyword_density(text: str, phrase: str) -> float:
    """Доля слов текста, занятых вхождениями фразы, в процентах."""
    total = len(_words(text))
    if not total:
        return 0.0
    phrase_words = _words(phrase)
    if not phrase_words:
        return 0.0
    pattern = r"\b" + r"[\s,.;:!?()-]+".join(re.escape(w) for w in phrase_words) + r"\b"
    hits = len(re.findall(pattern, text.lower(), flags=re.UNICODE))
    return round(hits * len(phrase_words) / total * 100, 2)


def validate(
    body: str,
    fmt: PlatformFormat,
    *,
    keywords: list[str] | None = None,
    cta_markers: tuple[str, ...] = ("диагностик", "напишите мне"),
    require_hashtags: bool = True,
    subtitle: str | None = None,
) -> ValidationReport:
    report = ValidationReport(chars=len(body))

    if report.chars > fmt.max_chars:
        report.warnings.append(
            f"Длина {report.chars} символов — больше лимита {fmt.title} ({fmt.max_chars})"
        )
    elif report.chars < fmt.min_chars:
        report.warnings.append(
            f"Длина {report.chars} символов — меньше ориентира {fmt.title} ({fmt.min_chars})"
        )

    if has_long_dashes(body):
        report.warnings.append("В тексте остались длинные тире — правило Артура нарушено")

    hype = {ch for ch in body if ch in _HYPE_EMOJI}
    if hype:
        report.warnings.append("Эмодзи-хайп в тексте: " + " ".join(sorted(hype)))

    lowered = body.lower()
    for cliche in _CLICHES:
        if cliche in lowered:
            report.warnings.append(f"Штамп в тексте: «{cliche}»")

    if not any(marker in lowered for marker in cta_markers):
        report.warnings.append("Не найден призыв к действию (диагностика без обязательств)")

    if require_hashtags and "#" not in body:
        report.warnings.append("Нет хэштегов в конце")

    if fmt.wants_subtitle and not (subtitle or "").strip():
        report.warnings.append(f"{fmt.title} требует отдельный подзаголовок — он пустой")

    if fmt.wants_keywords and keywords:
        for phrase in keywords:
            density = keyword_density(body, phrase)
            report.density[phrase] = density
            if density == 0.0:
                report.warnings.append(f"Ключ «{phrase}» не встречается в тексте")
            elif density < DENSITY_MIN:
                report.warnings.append(
                    f"Ключ «{phrase}»: плотность {density}% ниже {DENSITY_MIN}% — недобор"
                )
            elif density > DENSITY_MAX:
                report.warnings.append(
                    f"Ключ «{phrase}»: плотность {density}% выше {DENSITY_MAX}% — переспам"
                )

    return report
