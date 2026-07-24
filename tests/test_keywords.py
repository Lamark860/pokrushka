from app.core.keywords import parse_keywords


def test_plain_lines():
    result = parse_keywords("системный маркетинг\nворонка продаж\n")
    assert [k.phrase for k in result] == ["системный маркетинг", "воронка продаж"]
    assert all(k.frequency is None for k in result)


def test_csv_with_frequency():
    result = parse_keywords("системный маркетинг,1200\nворонка продаж;340\nтаргет\t85")
    assert [(k.phrase, k.frequency) for k in result] == [
        ("системный маркетинг", 1200),
        ("воронка продаж", 340),
        ("таргет", 85),
    ]


def test_frequency_without_separator_and_with_spaces():
    result = parse_keywords("настройка рекламы 12 500")
    assert result[0].phrase == "настройка рекламы"
    assert result[0].frequency == 12500


def test_quoted_csv_cells():
    result = parse_keywords('"маркетинг, система";900')
    assert result[0].phrase == "маркетинг, система"
    assert result[0].frequency == 900


def test_header_and_blank_lines_skipped():
    result = parse_keywords("Фраза;Частотность\n\nзаявки;100\n   \n")
    assert [(k.phrase, k.frequency) for k in result] == [("заявки", 100)]


def test_duplicates_collapsed_case_insensitive():
    result = parse_keywords("Заявки\nзаявки,50\nЗАЯВКИ")
    assert len(result) == 1
    assert result[0].phrase == "Заявки"
