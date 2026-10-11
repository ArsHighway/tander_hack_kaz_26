"""Опечатки в наименовании и описании: исправление поиска — замечание."""

from app.catalog import Catalog, CharacteristicSpec, Position
from app.checks.typos import layout_fix, layout_issues, typo_issues
from app.search import build_bm25
from app.search.exemplars import ExemplarIndex

TACHEO = Position(
    code="T-1",
    name="Тахеометры электронные",
    okpd2="",
    source="test",
    characteristics=[
        CharacteristicSpec(name="Наличие триггера", kind="qualitative", values=["Да", "Нет"])
    ],
)
GAUGE = Position(code="G-1", name="Шумомеры", okpd2="", source="test", characteristics=[])
CATALOG = Catalog([TACHEO, GAUGE], version="test")
BM25 = build_bm25(CATALOG)
PORTAL = ExemplarIndex(
    [("T-1", "Тахеометр с триггером")] * 3 + [("G-1", "Шумомер цифровой AMTAST")] * 3
)


def issues(name: str, description: str = "", position: Position = TACHEO, portal=PORTAL):
    return typo_issues(name, description, position, BM25, portal)


def test_typo_in_name_is_warning_with_fixed_name() -> None:
    [issue] = issues("Шуммомер цифровой AMF004", position=GAUGE)
    assert (issue.field, issue.kind, issue.severity) == ("name", "typo", "warn")
    assert "«Шуммомер» — «Шумомер»" in issue.message
    assert issue.fix is not None and issue.fix.value == "Шумомер цифровой AMF004"


def test_typo_in_description_is_info_and_keeps_ending() -> None:
    [issue] = issues("Тахеометр электронный", "Наличие треггера: да")
    assert (issue.field, issue.severity) == ("description", "info")
    assert "«треггера» — «триггера»" in issue.message


def test_brands_forms_and_frequent_words_are_not_typos() -> None:
    # бренд в кавычках, латиница, капс, заглавная внутри фразы — не опечатка
    assert issues('Тахеометр "Тригер" GeoMax ТРИГЕР Трегер') == []
    # слово, которое на портале пишут часто, — так пишут, хоть справочник пишет иначе
    portal = ExemplarIndex([*[("T-1", "Тахеометр с треггером")] * 3, ("T-1", "триггер")] * 3)
    assert issues("Тахеометр", "с треггером", portal=portal) == []


JACKET = Position(code="J-1", name="Куртки женские", okpd2="", source="test", characteristics=[])
SAFE = Position(
    code="S-1", name="Шкафы металлические для документов", okpd2="", source="test",
    characteristics=[],
)  # fmt: skip
CLOTHES_BM25 = build_bm25(Catalog([JACKET, SAFE], version="test"))
CLOTHES_PORTAL = ExemplarIndex(
    [("J-1", "Куртка зимняя женская")] * 3
    + [("S-1", "Шкаф металлический")] * 100
    + [("S-1", "Шкаф металический")] * 3
)


def test_doubled_letter_is_a_typo_not_another_form() -> None:
    # «курткка» — не форма «куртк(а)», а лишняя буква
    [issue] = typo_issues("Курткка зимняя", "", JACKET, CLOTHES_BM25, CLOTHES_PORTAL)
    assert issue.severity == "warn" and "«Курткка» — «Куртка»" in issue.message
    assert issue.fix is not None and issue.fix.value == "Куртка зимняя"


def test_frequent_portal_misspelling_is_a_typo_when_the_right_form_is_far_more_frequent() -> None:
    # «металический» на портале пишут, но «металлический» — в 20+ раз чаще
    [issue] = typo_issues(
        "шкаф металический для документов", "", SAFE, CLOTHES_BM25, CLOTHES_PORTAL
    )
    assert "«металический» — «металлический»" in issue.message


CHAIRS = Position(code="C-1", name="Стулья офисные", okpd2="", source="test", characteristics=[])
LAYOUT_BM25 = build_bm25(Catalog([CHAIRS, GAUGE], version="test"))


def test_wrong_layout_is_translated_when_translation_is_known() -> None:
    # Набрано в английской раскладке: «cnek jabcysq» — «стул офисный».
    assert layout_fix("cnek jabcysq", LAYOUT_BM25) == ("стул офисный", ["cnek", "jabcysq"])
    assert layout_fix("Cnek Bosch", LAYOUT_BM25) == ("Стул Bosch", ["Cnek"])
    [issue] = layout_issues("cnek jabcysq", "", LAYOUT_BM25)
    assert (issue.field, issue.kind, issue.severity) == ("name", "typo", "warn")
    assert "«cnek jabcysq» — «стул офисный»" in issue.message
    assert (
        issue.fix is not None and issue.fix.value == "Стул офисный"
    )  # с заглавной, как на портале


def test_service_word_next_to_translated_word_is_translated_too() -> None:
    # «lkz» — «для»: короче порога, но рядом переведённое слово
    assert layout_fix("cnek lkz jabcysq", LAYOUT_BM25) == (
        "стул для офисный",
        ["cnek", "lkz", "jabcysq"],
    )
    assert layout_fix("lkz", LAYOUT_BM25) == ("lkz", [])  # одно служебное слово — не раскладка
    assert layout_fix("Fast-Cath", LAYOUT_BM25) == ("Fast-Cath", [])  # дефис — составное имя


def test_brands_and_models_in_latin_stay() -> None:
    # перевод не даёт слов справочника — бренд и модель остаются как есть
    for text in ("Bosch", "Makita DF333DWYE", "HR2460", "LED", "A4", "BOSCH GLL 2", "Ferro Vest"):
        assert layout_fix(text, LAYOUT_BM25) == (text, [])
    assert layout_issues("Шумомер Makita", "", LAYOUT_BM25) == []


def test_without_portal_index_correction_still_needs_the_position_word() -> None:
    # два различия («Штангельциркуль») — уже не опечатка в одну букву, не трогаем
    assert issues("Штангельциркуль", position=GAUGE, portal=None) == []
    assert issues("Тахеометр", "треггер", portal=None) != []
    assert issues("Шумомер", "треггер", position=GAUGE, portal=None) == []
