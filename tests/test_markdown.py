from app.core.markdown import (
    first_h1,
    has_long_dashes,
    md_to_html,
    slugify,
    strip_h1,
    strip_long_dashes,
)


def test_slugify_transliterates_and_trims():
    assert slugify("Почему дешёвые заявки дороже дорогих") == "pochemu-deshevye-zayavki-dorozhe-dorogih"
    assert slugify("") == "post"
    assert len(slugify("а" * 200)) <= 50


def test_strip_long_dashes_follows_artur_rule():
    text = "Заявки есть — продаж нет.\n— пункт списка\nдлинное–тире"
    out = strip_long_dashes(text)
    assert not has_long_dashes(out)
    assert "Заявки есть, продаж нет." in out
    assert out.splitlines()[1] == "пункт списка"


def test_md_to_html_renders_headings_lists_and_links():
    html = md_to_html("# Заголовок\n\n## Раздел\n\n- пункт\n- второй\n\n[ссылка](https://example.com)")
    assert "<h1>Заголовок</h1>" in html
    assert "<h2>Раздел</h2>" in html
    assert html.count("<li>") == 2
    assert "</ul>" in html
    assert '<a href="https://example.com" rel="nofollow">ссылка</a>' in html


def test_md_to_html_escapes_raw_html():
    assert "<script>" not in md_to_html("текст <script>alert(1)</script>")


def test_first_h1_and_strip_h1():
    md = "# Заголовок\n\nтело"
    assert first_h1(md) == "Заголовок"
    assert strip_h1(md) == "тело"
    assert first_h1("нет заголовка") is None
