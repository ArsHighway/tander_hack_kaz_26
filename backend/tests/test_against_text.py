"""Подсказка против текста поставщика: текст говорит о поле иное — подсказку не ставим."""

from app.catalog import CharacteristicSpec
from app.extract.model import Extracted
from app.schemas import Span
from app.search.against_text import keep_unsaid, said_otherwise, said_same
from app.search.similar import Similar


def q(name: str, unit: str | None, values: list[str] | None = None) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="quantitative", unit=unit, values=values or [])


def t(name: str, values: list[str]) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="qualitative", values=values)


def said(spec: CharacteristicSpec, value: str, name: str, description: str = "", **kw):
    texts = [("name", name), ("description", description)]
    return said_otherwise(spec, value, texts, kw.get("extracted", []), kw.get("specs", [spec]))


CURRENT = q("Номинальный ток", "А")
CORES = q("Число жил", None)


def test_number_of_same_quantity_contradicts_hint() -> None:
    assert said(CURRENT, "10", "Автоматический выключатель", "Ток 16 А, 1 полюс") == "16 А"
    # тот же ток в другой записи — не довод против: «0,01 кА» = 10 А
    assert said(CURRENT, "10", "Автомат 0,01 кА") is None


def test_number_next_to_field_word_contradicts_hint() -> None:
    assert said(CORES, "5", "Кабель ВВГнг 3 жилы") == "3 жилы"
    assert said(CORES, "3", "Кабель ВВГнг 3 жилы") is None


def test_unlabelled_count_and_bare_number_are_not_arguments() -> None:
    # «10 шт» — счёт чего угодно, голое число без подписи — тоже не довод
    count = q("Количество листов", "шт")
    assert said(count, "500", "Бумага А4", "В коробке 5 пачек, 10 шт") is None
    assert said(CURRENT, "10", "Автомат ВА47-29 16") is None


def test_number_labelled_with_other_field_is_not_an_argument() -> None:
    load = q("Максимальная нагрузка", "кг")
    weight = q("Вес", "кг")
    specs = [load, weight]
    assert said(weight, "8", "Стул", "Нагрузка до 120 кг", specs=specs) is None
    assert said(load, "100", "Стул", "Нагрузка до 120 кг", specs=specs) == "120 кг"


def test_number_already_taken_by_rules_is_not_an_argument() -> None:
    taken = Extracted(
        name="Высота",
        raw_value="1860",
        raw_unit="мм",
        source=Span(field="description", start=7, end=14, text="1860 мм"),
        method="rule",
    )
    depth = q("Глубина полки", "мм")
    assert said(depth, "300", "Шкаф", "Высота 1860 мм", extracted=[taken]) is None
    assert said(depth, "300", "Шкаф", "Высота 1860 мм") == "1860 мм"


def test_bucket_hint_agrees_with_number_inside() -> None:
    density = q("Плотность", "г/м2", [">=80 ... <90", ">=90"])
    assert said(density, ">=80 ... <90", "Бумага 80 г/м2") is None
    assert said(density, ">=80 ... <90", "Бумага 120 г/м2") == "120 г/м2"


def test_code_of_same_shape_contradicts_hint() -> None:
    socket = t("Тип цоколя", ["E27", "E14", "G9"])
    assert said(socket, "E27", "Лампа светодиодная E28") == "E28"
    assert said(socket, "E27", "Лампа светодиодная e27 10 Вт") is None


def test_listed_word_in_free_text_is_not_an_argument() -> None:
    # слово перечня в наименовании или в метке соседнего поля — не довод (отложенная выборка)
    kind = t("Тип", ["Сварочный инвертор", "Аппарат"])
    assert said(kind, "Сварочный инвертор", "Аппарат сварочный MIG-500") is None


def test_negation_contradicts_yes_and_mention_contradicts_no() -> None:
    wifi = t("Наличие Wi-Fi", ["Да", "Нет"])
    assert said(wifi, "Да", "МФУ лазерное без Wi-Fi") is not None
    assert said(wifi, "Нет", "МФУ лазерное с Wi-Fi") is not None
    assert said(wifi, "Да", "МФУ лазерное") is None
    assert said(wifi, "Нет", "МФУ лазерное без Wi-Fi") is None


def test_keep_unsaid_drops_only_contradicted_hints() -> None:
    hints = {
        "Номинальный ток": Similar(value="10", unit="А", votes=3, of=4),
        "Число жил": Similar(value="3", votes=3, of=3),
    }
    texts = [("name", "Кабель 3 жилы, 16 А"), ("description", "")]
    kept = keep_unsaid(hints, [CURRENT, CORES], texts, [])
    assert list(kept) == ["Число жил"]


def test_number_belongs_to_field_named_fully_by_its_label() -> None:
    width, shelf = q("Ширина", "мм"), q("Ширина полки", "мм")
    specs = [width, shelf]
    # «ширина 600 мм» — про «Ширину», а не про «Ширину полки»
    assert said(shelf, "550", "Шкаф", "ширина 600 мм", specs=specs) is None
    assert said(shelf, "550", "Шкаф", "ширина полки 500 мм", specs=specs) == "500 мм"


def test_label_minus_and_next_number() -> None:
    level = q("Минимальный уровень мощности", "дБм")
    assert said(level, "-50", "Анализатор", "Минимальный уровень: -50 дБм") is None
    # «р. 54 рост 182»: «рост» подписывает 182, а не 54
    height = q("Рост", "см")
    assert said(height, "182", "Рубашка р. 54 рост 182") is None
    # тире диапазона — не минус
    volt = q("Напряжение", "В")
    assert said(volt, "265", "Нагреватель", "Питание: 85-265 В") is None


def test_unlabelled_number_needs_the_only_field_of_its_quantity() -> None:
    height, thick = q("Высота", "мм"), q("Толщина столешницы", "мм")
    specs = [height, thick]
    # «600 мм» у стола — о высоте, ширине или толщине: не довод против «Толщины 16 мм»
    assert said(thick, "16", "Столик журнальный 600*520*600 мм", specs=specs) is None


def test_hint_words_anywhere_in_text_agree() -> None:
    kind = t("Тип", ["Накидной ударный", "Ударный", "Накидной"])
    assert said(kind, "Накидной ударный", "Ключ накидной 46мм 12-ти гранный ударный") is None
    shield = t("Уровень защиты", ["C1", "C2", "C3"])
    assert said(shield, "C3", "Костюм", "Воздухопроницаемость, дм3/м2с: 20") is None


def test_word_right_after_a_number_labels_that_number() -> None:
    boxes = q("Количество ящиков", "шт")
    # «6 ящиков 50х80х100 см»: «ящиков» — подпись 6, а не 50
    assert said(boxes, "6", "Комод 6 ящиков 50х80х100 см") is None
    holes = q("Количество проколов", "шт")
    assert said(holes, "4", "Пуговица с 4 проколами Gamma GEC 0009") is None


def test_bounds_articles_and_hyphen() -> None:
    far = q("Максимальная дальность", "м")
    # «Минимальная дальность: 0,5 м» — не о максимальной
    assert said(far, "130", "Сканер", "Минимальная дальность: 0,5 м", specs=[far]) is None
    assert said(far, "130", "Сканер", "Максимальная дальность: 70 м", specs=[far]) == "70 м"
    tools = q("Количество отверток", "шт")
    assert said(tools, "5", "Набор отверток BAHCO BE-9882S") is None
    assert said(tools, "6", "Набор отверток WITTE 653867") is None
    step = q("Шаг зубьев", "мм")
    assert said(step, "1,2", "Рашпиль 200*10мм, шаг-1.2мм") is None


def test_plain_label_prefers_field_without_bounds() -> None:
    height, top = q("Высота", "мм"), q("Максимальная высота", "мм")
    specs = [height, top]
    assert said(height, "7500", "Стол", "Высота 750 мм", specs=specs) == "750 мм"
    assert said(top, "900", "Стол", "Высота 750 мм", specs=specs) is None


def test_number_taken_by_field_outside_position_still_counts() -> None:
    # Правила взяли «5 м» в поле КТРУ «Высота до», которого в позиции нет, —
    # число всё равно о «Высоте», подсказка «750 мм» с ним спорит.
    height, width, depth = q("Высота", "мм"), q("Ширина", "мм"), q("Глубина", "мм")
    specs = [height, width, depth]
    text = "ЛДСП, высота 5 метров, ширина 1400 мм, глубина 700 мм"
    elsewhere = Extracted(
        name="Высота до",
        raw_value="5",
        raw_unit="м",
        source=Span(field="description", start=13, end=14, text="5"),
        method="rule",
    )
    assert (
        said(height, "750", "Стол офисный", text, extracted=[elsewhere], specs=specs) == "5 метров"
    )
    hints = {"Высота": Similar(value="750", unit="мм", votes=6, of=10)}
    texts = [("name", "Стол офисный"), ("description", text)]
    assert keep_unsaid(hints, specs, texts, [elsewhere]) == {}
    # взято полем самой позиции — по-прежнему не довод (оно о другом)
    taken = elsewhere.model_copy(update={"name": "Ширина"})
    assert said(height, "750", "Стол офисный", text, extracted=[taken], specs=specs) is None


def test_size_names_the_dimensions_it_is_labelled_with() -> None:
    # «1900х800 мм» — длина и ширина спального места, подсказки 1860/700 с ним спорят.
    length, width = q("Длина спального места", "мм"), q("Ширина спального места", "мм")
    legs, places = q("Высота ножек/опор", "мм"), q("Количество спальных мест", "шт")
    specs = [length, width, legs, places]
    name, text = "Кровать металлическая", "Размер спального места 1900х800 мм, каркас металлический"
    assert said(length, "1860", name, text, specs=specs) == "1900х800 мм"
    assert said(width, "700", name, text, specs=specs) == "1900х800 мм"
    assert said(length, "1900", name, text, specs=specs) is None
    assert said(width, "800", name, "Размер спального места 190х80 см", specs=specs) is None
    # высота ножек размером спального места не названа
    assert said(legs, "400", name, text, specs=specs) is None
    assert said(places, "2", name, text, specs=specs) is None


def test_size_labelled_only_by_item_name_is_not_about_qualified_fields() -> None:
    # Ложные «расходится с текстом» из holdout_full: уточнение поля совпало со
    # словом наименования («Длина электрода», «Электрод-петля 10 x 10 мм») — не подпись размера.
    electrode, rod = q("Длина электрода", "мм"), q("Диаметр стержня", "мм")
    name = "Электрод-петля O 10 x 10 мм, с изолированным стержнем, 128 мм"
    assert said(electrode, "128", name, specs=[electrode, rod]) is None
    net, length = q("Ширина сетки", "м"), q("Длина", "мм")
    net_name = "Сетка штукатурная оцинкованная 10х10 мм 0.6 мм 1х15 м"
    assert said(net, "1", net_name, specs=[net, length]) is None
    # с «размер» в подписи — о поле с уточнением
    sized = "Сетка штукатурная, размер сетки 900х15000 мм"
    assert said(net, "1", sized, specs=[net, length]) == "900х15000 мм"
    # поле без уточнения, а у позиции то же измерение с уточнением («Высота грузового отсека»):
    # размер в наименовании может быть и отсека — не довод
    dims = [q(n, "мм") for n in ("Высота", "Длина", "Ширина")]
    box = [q("Высота грузового отсека", "мм"), q("Длина грузового отсека", "мм")]
    van = "Фургон промтоварный МЗСА 2445х1348х1520 мм"
    about = "Высота грузового отсека: 1520 м; Длина грузового отсека: 2445 м; Ширина: 1852 мм"
    assert said(dims[0], "2119", van, about, specs=dims + box) is None
    assert said(dims[1], "3804", van, about, specs=dims + box) is None
    # одно совпадение — не довод: длина кровати и спального места одна
    places = [q("Длина спального места", "мм"), q("Ширина спального места", "мм")]
    bed = "Кровать раскладная Лидер 2, 370х800х1900 мм черный с матрасом"
    said_bed = "Вид товара: Раскладушка с матрасом; Длина спального места: 1900 мм"
    assert said(dims[2], "700", bed, said_bed, specs=dims + places) == "370х800х1900 мм"


def test_size_of_two_numbers_leaves_height_out() -> None:
    length, width, height = q("Длина", "мм"), q("Ширина", "мм"), q("Высота", "мм")
    specs = [length, width, height]
    # «размер 1200х600» у стола — длина и ширина: высоту не называет
    sized = "Стол письменный, размер 1200х600 мм"
    assert said(height, "750", sized, specs=specs) is None
    assert said(width, "800", sized, specs=specs) == "1200х600 мм"
    # три числа — и высота названа
    assert said(height, "700", "Стол письменный 1200х600х750 мм", specs=specs) == "1200х600х750 мм"
    assert said(height, "750", "Стол письменный 1200х600х750 мм", specs=specs) is None
    # два числа без «размер» — не габариты («Ключ 22х24 мм», «Сверло 12х120 мм»)
    assert said(length, "245", "Ключ накидной 22х24 мм", specs=specs) is None
    # размер не самого изделия
    assert said(height, "380", "Кукла", "Размер упаковки: 32х20х10 см", specs=specs) is None
    # без единицы длины — не размер изделия («3х2,5» у кабеля — жилы и сечение)
    assert said(length, "100000", "Кабель ВВГнг 3х2,5", specs=specs) is None
    # размер разобран правилами по полям — остальные поля он не называет
    split = [
        Extracted(
            name=n,
            raw_value=v,
            raw_unit="мм",
            source=Span(field="name", start=s, end=e, text=v),
            method="rule",
        )
        for n, v, s, e in (("Длина", "1200", 16, 20), ("Ширина", "600", 21, 24))
    ]
    assert (
        said(height, "700", "Стол письменный 1200х600х750 мм", extracted=split, specs=specs) is None
    )


def test_size_hint_against_size_in_text() -> None:
    # «Спальное место 2000х900 мм», подсказка двойника — размер 1900х800
    size = q("Размер спального места", "мм")
    whole = q("Габаритные размеры", "мм")
    name = "Кровать металлическая"
    assert said(size, "1900х800", name, "Спальное место 2000х900 мм") == "2000х900 мм"
    assert said(size, "1900х800", name, "Спальное место 1900х800 мм") is None
    assert said(size, "1900х800", name, "Спальное место 190х80 см") is None
    assert said(size, "1900х800", name, "Размер упаковки: 2000х900 мм") is None
    bigger = "Кровать 2000х900х450 мм"
    assert said(whole, "1950х850х400", name, bigger, specs=[whole]) == "2000х900х450 мм"
    assert said(whole, "1950х850х400", name, "Кровать 1950х850х400 мм", specs=[whole]) is None
    # на стенде это поле — перечень с единицей, а «2000» правила взяли в «Количество спальных мест»
    listed = CharacteristicSpec(
        name="Размер спального места", kind="qualitative", unit="мм", values=["1900х800"]
    )
    places = Extracted(
        name="Количество спальных мест",
        raw_value="2000",
        source=Span(field="description", start=15, end=19, text="2000"),
        method="rule",
    )
    text = "Спальное место 2000х900 мм, каркас металлический"
    assert said(listed, "1900х800", name, text, extracted=[places]) == "2000х900 мм"
    # тот же размер в другом порядке и с латинской «x» — не довод
    assert said(listed, "900x2000", name, text, extracted=[places]) is None


def test_unit_word_names_the_field() -> None:
    # «шаг 7 TPI» — 7 зубьев на дюйм; подсказка «11» с ним спорит, хотя поле качественное.
    step, tpi = t("Шаг зубьев", []), t("Количество зубьев на дюйм", [])
    specs = [step, tpi]
    name, text = "Ножовка по дереву 450мм", "Ножовка по дереву, длина полотна 450 мм, шаг 7 TPI"
    assert said(tpi, "11", name, text, specs=specs) == "7 TPI"
    assert said(tpi, "7", name, text, specs=specs) is None
    # число в качественном поле сверяется с подписанным им числом текста
    size = t("Размер", [])
    assert said(size, "54", "Брюки рабочие", "Размер 52, рост 182", specs=[size]) == "52"
    assert said(size, "52", "Брюки рабочие", "Размер 52, рост 182", specs=[size]) is None


# Подсказка, которую подтверждает текст, — значение из текста с фрагментом.
def same(spec: CharacteristicSpec, value: str, name: str, description: str = "", **kw):
    texts = [("name", name), ("description", description)]
    found = said_same(spec, value, texts, kw.get("extracted", []), kw.get("specs", [spec]))
    return found.text if found else None


MIXER = (
    "Смеситель для кухни однорычажный с выдвижной лейкой, хром, латунь, керамический картридж 35 мм"
)


def test_hint_word_in_text_is_said_by_text() -> None:
    material = t("Материал", ["латунь", "сталь"])
    control = t("Тип управления", ["Однорычажный", "Двухвентильный"])
    assert same(material, "латунь", MIXER) == "латунь"
    assert same(control, "Однорычажный", MIXER) == "однорычажный"
    assert same(material, "сталь", MIXER) is None
    # все слова значения подряд: «для кухни (мойки)» в тексте только наполовину
    purpose = t("Назначение", ["для кухни (мойки)"])
    assert same(purpose, "для кухни (мойки)", MIXER) is None
    assert same(t("Тип", ["рожковый"]), "рожковый", "Ключ гаечный рожковый 22х24 мм") == "рожковый"
    # отрицание — не подтверждение
    assert same(material, "латунь", "Смеситель без латуни") is None
    # «Да/Нет» словом не подтверждаются
    assert same(t("Наличие лейки", ["Да", "Нет"]), "Да", "Смеситель с лейкой") is None


def test_hint_number_said_by_text_like_said_otherwise() -> None:
    weight = q("Вес", "г")
    assert same(weight, "500", "Молоток слесарный 500 г") == "500 г"
    assert same(weight, "0,5", "Молоток слесарный", "масса 500 г", specs=[weight]) is None
    height = q("Рост", "см")
    assert same(height, "176", "Куртка мужская зимняя", "размер 52, рост 176") == "176"
    assert same(height, "182", "Куртка мужская зимняя", "размер 52, рост 176") is None
    # число без подписи у поля, которое не одно своей величины, — не подтверждение
    width, depth = q("Ширина", "мм"), q("Глубина", "мм")
    assert same(width, "600", "Стол 600 мм", specs=[width, depth]) is None
    # размер: подсказка «1900» подтверждена числом размера
    length = q("Длина спального места", "мм")
    bed = "Спальное место 1900х800 мм, каркас металлический"
    assert same(length, "1900", "Кровать металлическая", bed, specs=[length]) == "1900"
    # размер в наименовании без подписи: довода против нет, а то же число измерения — подтверждение
    width, legs = q("Ширина спального места", "мм"), q("Высота ножек", "мм")
    bed_name = "Кровать металлическая одноярусная 1900х800 мм"
    specs = [length, width, legs]
    assert same(length, "1900", bed_name, specs=specs) == "1900"
    assert same(width, "800", bed_name, specs=specs) == "800"
    assert same(legs, "400", bed_name, specs=specs) is None
    # размер, взятый правилами в «Размер спального места», — тот же размер: длина из него
    whole = q("Размер спального места", "мм")
    taken = Extracted(
        name="Размер спального места",
        raw_value="1900х800",
        raw_unit="мм",
        source=Span(field="name", start=34, end=42, text="1900х800"),
        method="rule",
    )
    assert same(length, "1900", bed_name, extracted=[taken], specs=[*specs, whole]) == "1900"
    # а число, взятое другим полем («Количество спальных мест: 1900»), — не её
    places = q("Количество спальных мест", "шт")
    counted = taken.model_copy(update={"name": "Количество спальных мест", "raw_value": "1900"})
    assert same(length, "1900", bed_name, extracted=[counted], specs=[*specs, places]) is None
    # размер без единицы — в единице поля («Кровать 1900х800»)
    assert same(length, "1900", "Кровать 1900х800", "металлическая", specs=specs) == "1900"
    # число той же величины, равное подсказке, — подтверждение и без подписи, когда полей массы
    # несколько: против него оно не довод, а то же значение — довод за
    mass, head = q("Вес", "г"), q("Масса головки", "кг")
    assert same(mass, "500", "Молоток слесарный 500 г", specs=[mass, head]) == "500 г"
    assert same(mass, "450", "Молоток слесарный 500 г", specs=[mass, head]) is None
    # качественное поле с единицей: значения перечня к ней не приведены, сверяем подписанное число
    tall = CharacteristicSpec(name="Рост", kind="qualitative", unit="см", values=[])
    assert same(tall, "176", "Куртка мужская зимняя", "размер 52, рост 176", specs=[tall]) == "176"


def test_hint_maker_in_name_is_said_by_text() -> None:
    maker = CharacteristicSpec(name="Производитель", kind="qualitative", values=[], group="general")
    assert same(maker, "Bosch", "Болгарка Bosch GWS 750-125") == "Bosch"
    assert same(maker, "Makita", "Болгарка Bosch GWS 750-125") is None


def test_keep_unsaid_marks_hints_said_by_text() -> None:
    material = t("Материал", ["латунь", "сталь"])
    mount = t("Монтаж", ["на горизонтальную поверхность"])
    hints = {
        "Материал": Similar(value="латунь", votes=5, of=6),
        "Монтаж": Similar(value="на горизонтальную поверхность", votes=7, of=9),
    }
    kept = keep_unsaid(hints, [material, mount], [("name", MIXER), ("description", "")], [])
    assert kept["Материал"].source is not None
    assert kept["Материал"].source.text == "латунь"
    assert kept["Материал"].source.field == "name"
    assert MIXER[kept["Материал"].source.start : kept["Материал"].source.end] == "латунь"
    assert kept["Монтаж"].source is None


def test_word_under_another_fields_label_or_after_negation_is_not_said() -> None:
    upper = t("Материал верха", ["натуральная кожа", "искусственная кожа"])
    lining = t("Материал подкладки", ["натуральная кожа"])
    shoes = [upper, lining]
    lined = "Подкладка: натуральная кожа"
    assert same(upper, "натуральная кожа", "Ботинки", lined, specs=shoes) is None
    own = "Материал верха: натуральная кожа"
    assert same(upper, "натуральная кожа", "Ботинки", own, specs=shoes) == "натуральная кожа"
    maker = CharacteristicSpec(name="Производитель", kind="qualitative", values=[], group="general")
    assert same(maker, "ЭВА", "Сапоги", "Материал верха обуви: ЭВА", specs=[maker, upper]) is None
    material = t("Материал", ["латунь", "силумин"])
    negated = "хром, корпус не из латуни, а из силумина"
    assert same(material, "латунь", "Смеситель для кухни однорычажный", negated) is None
    # своя подпись и свободный текст — подтверждение
    assert same(material, "латунь", "Смеситель", "Материал: латунь") == "латунь"
    assert same(material, "латунь", MIXER) == "латунь"


def test_said_number_must_equal_the_hint_not_be_close_to_it() -> None:
    height = q("Высота", "мм")
    dims = [q("Ширина", "мм"), q("Глубина", "мм"), height]
    table = "Стол письменный 800х700х747 мм"
    assert same(height, "750", table, specs=dims) is None
    assert same(height, "747", table, specs=dims) == "747"
    assert same(height, "825", "Стол", "высота 83 см", specs=[height]) is None
    assert same(height, "830", "Стол", "высота 83 см", specs=[height]) == "83 см"
    weight = q("Вес", "кг")
    assert same(weight, "0,383", "Ножовка", "вес 383 г", specs=[weight]) == "383 г"
    # целая запись подсказки — не повод округлять: «1500 гр» не «2 кг»
    assert same(weight, "2", "Кирка Gigant PFH1500-1", "вес 1500 гр", specs=[weight]) is None
    # против подсказки близкое число по-прежнему не довод: это округление
    assert said(height, "750", table, specs=dims) is None


def test_one_number_of_a_size_is_said_for_one_field_only() -> None:
    width, length, depth = q("Ширина", "мм"), q("Длина", "мм"), q("Глубина", "мм")
    specs = [width, length, depth, q("Высота", "мм")]
    hints = {
        "Ширина": Similar(value="600", unit="мм", votes=3, of=4),
        "Глубина": Similar(value="600", unit="мм", votes=3, of=4),
        "Длина": Similar(value="1200", unit="мм", votes=3, of=4),
    }
    texts = [("name", "Стол обеденный 600х1200 мм"), ("description", "")]
    kept = keep_unsaid(hints, specs, texts, [])
    said_by = {n: h.source.text if h.source else None for n, h in kept.items()}
    assert said_by == {"Ширина": "600", "Глубина": None, "Длина": "1200"}


def test_label_after_its_number() -> None:
    # «120 см ширина 75 см высота»: подпись после числа — «75 см» не о ширине.
    height, width = q("Высота", "мм"), q("Ширина", "мм")
    text = "120 см ширина 75 см высота"
    assert said(width, "1200", "Стол", text, specs=[height, width]) is None
    assert said(height, "750", "Стол", text, specs=[height, width]) is None
    assert said(width, "750", "Стол", text, specs=[height, width]) == "120 см"
