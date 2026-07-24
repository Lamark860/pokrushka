"""Коды трекинг-ссылок и имена пригласительных ссылок Telegram."""
from app.core.tracking import CODE_LENGTH, build_invite_name, code_from_invite_name, new_code, redirect_url
from app.integrations.telegram import INVITE_NAME_LIMIT


def test_new_code_length_and_alphabet():
    codes = {new_code() for _ in range(200)}
    assert all(len(code) == CODE_LENGTH for code in codes)
    # без похожих символов: их легко перепутать глазами в имени ссылки
    assert not any(set(code) & {"0", "o", "1", "l"} for code in codes)
    assert len(codes) > 190  # коллизии не должны сыпаться на первых же двух сотнях


def test_invite_name_fits_telegram_limit():
    name = build_invite_name(new_code(), "Яндекс.Дзен", "kak-vystroit-marketing-kotoryy-ne-zavisit")
    assert len(name) <= INVITE_NAME_LIMIT


def test_code_survives_truncation_of_invite_name():
    """Имя режется до 32 символов, но код стоит первым и остаётся целым."""
    code = new_code()
    name = build_invite_name(code, "Яндекс.Дзен", "очень-длинный-слаг-который-точно-не-влезет")
    assert code_from_invite_name(name) == code


def test_code_from_invite_name_rejects_foreign_names():
    assert code_from_invite_name("Ссылка из рекламы") is None
    assert code_from_invite_name("") is None
    assert code_from_invite_name(None) is None
    assert code_from_invite_name("Яндекс.Директ") is None


def test_redirect_url_normalises_slashes():
    assert redirect_url("https://traff.example.com/", "abc12345") == (
        "https://traff.example.com/r/abc12345"
    )
