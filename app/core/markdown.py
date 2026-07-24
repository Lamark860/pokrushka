"""Текстовые утилиты: slug, правила оформления Артура, markdown → HTML.

Перенесено из `kotlowoi/traff/gen_article.py` практически без изменений — логика
уже обкатана на боевых статьях.
"""
import html
import re

_TR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", " ": "-", "_": "-",
}


def slugify(text: str) -> str:
    text = (text or "").lower().strip()
    out = "".join(_TR.get(ch, ch if ch.isalnum() else "-") for ch in text)
    return re.sub(r"-{2,}", "-", out).strip("-")[:50] or "post"


def strip_long_dashes(text: str) -> str:
    """Правило Артура: никаких длинных тире между абзацами и как разделитель.

    Порядок важен: сначала снимаем тире в начале строки, потом внутристрочные —
    и только по горизонтальным пробелам. В MVP шаблон `\\s+[—–]\\s+` захватывал
    перенос строки и склеивал соседние абзацы через запятую.
    """
    text = re.sub(r"^[^\S\n]*[—–][^\S\n]*", "", text, flags=re.M)
    text = re.sub(r"[^\S\n]+[—–][^\S\n]+", ", ", text)
    return text.replace("—", "-").replace("–", "-")


def has_long_dashes(text: str) -> bool:
    return "—" in text or "–" in text


_INLINE = [
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r'<a href="\2" rel="nofollow">\1</a>'),
]


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    for rx, rep in _INLINE:
        s = rx.sub(rep, s)
    return s


def md_to_html(md: str) -> str:
    """Мини-конвертер под то подмножество markdown, которое пишет генератор."""
    out: list[str] = []
    buf: list[str] = []
    in_list = False

    def flush_p() -> None:
        if buf:
            out.append("<p>" + " ".join(buf) + "</p>")
            buf.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush_p()
            close_list()
            continue
        if line.startswith(("### ", "## ", "# ")):
            flush_p()
            close_list()
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>" + _inline(line[level + 1:]) + f"</h{level}>")
        elif line.lstrip().startswith(("- ", "* ")):
            flush_p()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>" + _inline(line.lstrip()[2:]) + "</li>")
        else:
            close_list()
            buf.append(_inline(line))

    flush_p()
    close_list()
    return "\n".join(out)


def first_h1(md: str) -> str | None:
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def strip_h1(md: str) -> str:
    """Убрать первый H1 из тела — заголовок хранится отдельным полем."""
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join(lines[:i] + lines[i + 1:]).lstrip("\n")
    return md
