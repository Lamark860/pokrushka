"""Разбор списка ключевых запросов.

Модуль Вордстата (правка Артура п.4) на первом этапе — это ручной ввод: Артур
приносит список из Вордстата сам. Принимаем и вставку в поле, и выгрузку файлом:
строки вида «фраза», «фраза,1200», «фраза;1200», «фраза<tab>1200».
"""
import csv
import io
import re
from dataclasses import dataclass

# Порядок важен: запятая часто встречается внутри самой фразы, поэтому пробуем её последней
_SEPARATORS = ";\t,"
_HEADERS = {"фраза", "запрос", "ключевое слово", "ключевой запрос", "keyword",
            "частотность", "частота", "показов", "показы"}


@dataclass(frozen=True)
class ParsedKeyword:
    phrase: str
    frequency: int | None


def _clean(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip().strip('"').strip("'")).strip()


def _as_frequency(raw: str) -> int | None:
    digits = re.sub(r"[\s ]", "", raw.strip())
    return int(digits) if digits.isdigit() else None


def _split_cells(line: str) -> list[str]:
    if not any(sep in line for sep in _SEPARATORS):
        return [line]
    delimiter = next(sep for sep in _SEPARATORS if sep in line)
    try:
        return next(csv.reader(io.StringIO(line), delimiter=delimiter))
    except (csv.Error, StopIteration):
        return line.split(delimiter)


def _parse_line(line: str) -> ParsedKeyword | None:
    cells = [_clean(cell) for cell in _split_cells(line.strip()) if _clean(cell)]
    if not cells or all(cell.lower() in _HEADERS for cell in cells):
        return None

    frequency = _as_frequency(cells[-1]) if len(cells) > 1 else None
    phrase_cells = cells[:-1] if frequency is not None else cells
    phrase = " ".join(phrase_cells).strip()

    # Строка без разделителей вида «монтаж отопления 1200» — хвостовое число это частота
    if frequency is None and len(cells) == 1:
        if match := re.match(r"^(?P<phrase>.+?)[\s ]+(?P<freq>[\d\s ]+)$", phrase):
            candidate = _as_frequency(match.group("freq"))
            if candidate is not None:
                phrase, frequency = _clean(match.group("phrase")), candidate

    if not phrase or phrase.lower() in _HEADERS:
        return None
    return ParsedKeyword(phrase, frequency)


def parse_keywords(text: str) -> list[ParsedKeyword]:
    """Разобрать текст (вставка или содержимое CSV) в список ключей без дублей."""
    out: list[ParsedKeyword] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        parsed = _parse_line(raw_line)
        if parsed is None:
            continue
        key = parsed.phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(parsed)

    return out
