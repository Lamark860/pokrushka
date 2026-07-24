"""Jinja-фильтры форматирования.

Одно место на весь интерфейс: раньше `'{:,}'.format(x).replace(',', ' ')` был размазан
по шаблонам, и каждый раз можно было ошибиться с разделителем или знаком рубля.
"""
from datetime import date, datetime

NBSP = " "  # неразрывный пробел: «60 000 ₽» не должно переноситься


def ru_num(value: int | float | None) -> str:
    """12500 → «12 500». Пусто и None → «—»."""
    if value is None or value == "":
        return "—"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,}".replace(",", NBSP)


def ru_money(value: int | float | None) -> str:
    if not value:
        return "—"
    return f"{ru_num(value)}{NBSP}₽"


def ru_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{round(value, digits)}%"


def ru_delta(value: int | None) -> str:
    """Прирост к прошлому срезу: «+6 400», «-120», «—»."""
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{ru_num(value)}" if value else "0"


def ru_date(value: date | datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%d.%m.%Y")


def ru_plural(value: int, one: str, few: str, many: str) -> str:
    """«1 подписчик», «4 подписчика», «11 подписчиков» — с самим числом."""
    number = abs(int(value or 0))
    if number % 10 == 1 and number % 100 != 11:
        word = one
    elif 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        word = few
    else:
        word = many
    return f"{ru_num(value)}{NBSP}{word}"


FILTERS = {
    "ru_num": ru_num,
    "ru_plural": ru_plural,
    "ru_money": ru_money,
    "ru_pct": ru_pct,
    "ru_delta": ru_delta,
    "ru_date": ru_date,
}
