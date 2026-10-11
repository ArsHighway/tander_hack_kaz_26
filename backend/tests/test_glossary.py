from app.glossary import Glossary, mine


def test_seed_loads_and_expands_first_mention_only() -> None:
    g = Glossary.load()
    text = "Мешки ПВД 60 мкм, ПВД плотный"
    assert g.expand(text) == "Мешки ПВД (полиэтилен высокого давления) 60 мкм, ПВД плотный"


def test_no_expansion_inside_words() -> None:
    g = Glossary.load()
    assert (
        g.expand("СПГЗ подобран") == "СПГЗ (справочник предметов государственного заказа) подобран"
    )
    assert g.find("ПВДшка") == []


def test_ambiguous_abbreviation_lists_all_meanings() -> None:
    expanded = Glossary.load().expand("Пакет ПП 30 мкм")
    assert "постановление правительства" in expanded
    assert "полипропилен" in expanded


def test_case_insensitive_medical_abbreviations() -> None:
    g = Glossary.load()
    assert g.find("антиген (пса) ивд, набор") == ["ПСА", "ИВД"]


def test_longest_match_first() -> None:
    assert Glossary.load().find("соответствует ТР ТС 017/2011") == ["ТР ТС"]


def test_prompt_block_only_for_found_abbreviations() -> None:
    block = Glossary.load().prompt_block("Картридж для МФУ")
    assert block == "- МФУ — многофункциональное устройство (принтер, сканер, копир)"


def test_mine_finds_new_abbreviation_with_definition() -> None:
    texts = [
        "Реагент для определения аланинаминотрансферазы (АЛТ)",
        "Набор реагентов АЛТ, 100 тестов",
        "Тест-система общий простатический специфический антиген (пса) ивд",
        "ФЛАКОН стеклянный",
        "стеклянный флакон 10 мл",
        "Контейнер III класса, 10 л",
    ]
    cands = {c.abbr: c for c in mine(texts, Glossary.load())}
    assert cands["АЛТ"].count == 2
    assert cands["АЛТ"].expansions.most_common(1)[0][0] == "аланинаминотрансферазы"
    assert "простатический специфический антиген" in cands["ПСА"].expansions
    assert "ФЛАКОН" not in cands  # обычное слово заглавными
    assert "III" not in cands  # римская цифра
    assert cands["АЛТ"].known


def test_portal_abbreviations_are_in_the_seed() -> None:
    # Сокращения из наименований и характеристик 2 549 СТЕ портала (mine_abbreviations, 02.10.2026).
    g = Glossary.load()
    text = g.expand(
        "Полуботинки, подошва КЩС, МБС, верх ТПУ. Реактив ЧДА. Признак ЖНВЛП. Замок для СКУД"
    )
    for full in [
        "кислотно-щелочестойкая",
        "маслобензостойкая",
        "термопластичный полиуретан",
        "чистый для анализа",
        "жизненно необходимые",
        "контроля и управления доступом",
    ]:
        assert full in text, full


def test_english_wheels_is_explained_as_wheel_rims() -> None:
    # «power wheels» без русского «колёсный» уводил поиск к жёстким дискам
    expanded = Glossary.load().expand("Кованые диски Power Wheels, 32 диаметр")
    assert "Wheels (колесные)" in expanded
    # без «диски»: «Электромобиль Power Wheels» и «Пенал Hot Wheels» — игрушки и канцелярия,
    # расшифровка с «диски» уводила их к жёстким дискам
    assert "диски" not in Glossary.load().expand("Электромобиль детский Power Wheels")
