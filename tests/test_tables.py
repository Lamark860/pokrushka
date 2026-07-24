"""Чтение выгрузок: кодировки, разделители, русские числа, угадывание колонок."""
import io

import pytest

from app.core.tables import (
    TableError,
    guess_mapping,
    normalize_header,
    parse_date,
    parse_number,
    parse_ratio,
    read_csv,
    read_table,
    read_xlsx,
)

CSV_SEMICOLON = (
    "Заголовок;Ссылка;Показы;Дочитывания;Дочитываемость\n"
    "Как выстроить маркетинг;https://dzen.ru/a/abc;12 500;5 200;41,6%\n"
)


def test_parse_number_handles_russian_formatting():
    assert parse_number("12 500") == 12500
    assert parse_number("12 500") == 12500  # неразрывный пробел
    assert parse_number("1 234,7") == 1235
    assert parse_number(4200) == 4200
    assert parse_number("") is None
    assert parse_number("нет данных") is None
    assert parse_number(None) is None


def test_parse_ratio_percent_and_fraction():
    assert parse_ratio("41,6%") == pytest.approx(41.6)
    assert parse_ratio("41.6") == pytest.approx(41.6)
    assert parse_ratio("0,416") == pytest.approx(41.6)  # доля без знака процента
    assert parse_ratio("мусор") is None


def test_parse_date_formats_and_period():
    assert parse_date("2026-07-24").isoformat() == "2026-07-24"
    assert parse_date("24.07.2026").isoformat() == "2026-07-24"
    # период — берём его конец: срез описывает состояние на эту дату
    assert parse_date("01.07.2026 - 07.07.2026").isoformat() == "2026-07-07"
    assert parse_date("") is None


def test_normalize_header_strips_punctuation():
    assert normalize_header("  Дочитывания, %  ") == "дочитывания %"


def test_guess_mapping_finds_expected_columns():
    mapping = guess_mapping(["Заголовок", "Ссылка", "Показы", "Дочитывания", "Дочитываемость"])
    assert mapping["title"] == "Заголовок"
    assert mapping["url"] == "Ссылка"
    assert mapping["views"] == "Показы"
    assert mapping["reads"] == "Дочитывания"
    assert mapping["read_ratio"] == "Дочитываемость"


def test_guess_mapping_does_not_reuse_one_column_twice():
    mapping = guess_mapping(["Публикация", "Просмотров", "Дочитываний"])
    assert len(set(mapping.values())) == len(mapping)


def test_read_csv_semicolon_utf8():
    table = read_csv(CSV_SEMICOLON.encode("utf-8"))
    assert table.headers[0] == "Заголовок"
    assert len(table.rows) == 1
    assert table.rows[0]["Показы"] == "12 500"
    assert table.mapping["views"] == "Показы"


def test_read_csv_windows_1251_with_commas():
    payload = "Заголовок,Показы\nТест,900\n".encode("cp1251")
    table = read_csv(payload)
    assert table.rows[0]["Показы"] == "900"


def test_read_csv_tab_separated():
    table = read_csv("Заголовок\tПоказы\nТест\t10\n".encode("utf-8"))
    assert table.headers == ["Заголовок", "Показы"]


def test_read_csv_rejects_empty_file():
    with pytest.raises(TableError):
        read_csv(b"   ")


def test_read_xlsx_skips_empty_leading_rows():
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append([])
    sheet.append(["Заголовок", "Показы", "Дочитывания"])
    sheet.append(["Как выстроить маркетинг", 12500, 5200])
    buffer = io.BytesIO()
    workbook.save(buffer)

    table = read_xlsx(buffer.getvalue())
    assert table.headers == ["Заголовок", "Показы", "Дочитывания"]
    assert table.rows[0]["Показы"] == "12500"
    assert table.mapping["reads"] == "Дочитывания"


def test_read_table_dispatches_by_extension():
    table = read_table("выгрузка.csv", CSV_SEMICOLON.encode("utf-8"))
    assert table.headers[0] == "Заголовок"
