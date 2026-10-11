from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.demo import get_photos, get_submissions
from app.api.v1 import get_deps
from app.catalog import Catalog
from app.demo import ACCEPTED_CLEAN_NOTE, MemorySubmissionRepo
from app.demo.photos import MemoryPhotoRepo
from app.glossary import Glossary
from app.main import create_app
from app.pipeline import Deps
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

FAKE = Path(__file__).parent / "fixtures" / "fake"
CATALOG = Catalog.load([FAKE / "ktru.json"])
COLOR = "17.12.14.129-00000102"

# Карточка без блокирующих замечаний: с ними стенд заявку не принимает (test_input_hardening).
CARD = {
    "name": "Бумага цветная А4",
    "ktru_code": COLOR,
    "characteristics": [
        {"name": "Формат", "value": "A4"},
        {"name": "Цветность", "value": "Цветная"},
        {"name": "Интенсивность цвета", "value": "Неон"},
        {"name": "Количество листов в пачке", "value": "500", "unit": "шт"},
    ],
}

CONFLICT = {
    "name": "Бумага офисная цветная А4",
    "ktru_code": COLOR,
    "characteristics": [
        {"name": "Формат", "value": "A4"},
        {"name": "Цветность", "value": "Цветная"},
        {"name": "Интенсивность цвета", "value": "Белый"},
        {"name": "Количество листов в пачке", "value": "500", "unit": "шт"},
    ],
}


def _client() -> TestClient:
    deps = Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=FakeLLM([]),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
    )
    repo = MemorySubmissionRepo()
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    app.dependency_overrides[get_submissions] = lambda: repo
    photos = MemoryPhotoRepo(repo)
    app.dependency_overrides[get_photos] = lambda: photos
    return TestClient(app)


def _with_photo(client: TestClient, card: dict) -> dict:
    """Карточка с загруженным фото: без него /check предупреждает, заявка не «чистая» (п. 7)."""
    png = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"png body"
    r = client.post("/api/demo/photos", files={"file": ("p.png", png, "application/octet-stream")})
    return {**card, "photo_id": r.json()["photo_id"]}


def test_submitted_card_appears_in_queue_as_pending() -> None:
    client = _client()
    r = client.post("/api/demo/submissions", json={"card": CARD})
    assert r.status_code == 201
    queue = client.get("/api/demo/submissions").json()
    assert [(s["id"], s["status"]) for s in queue] == [(r.json()["id"], "pending")]


def test_queue_is_newest_first() -> None:
    client = _client()
    first = client.post("/api/demo/submissions", json={"card": CARD}).json()["id"]
    second = client.post("/api/demo/submissions", json={"card": CARD}).json()["id"]
    assert [s["id"] for s in client.get("/api/demo/submissions").json()] == [second, first]


def test_submission_view_carries_check() -> None:
    client = _client()
    sid = client.post("/api/demo/submissions", json={"card": CARD}).json()["id"]
    view = client.get(f"/api/demo/submissions/{sid}").json()
    assert view["check"]["verdict"] == "ready"
    assert not any(i["severity"] == "block" for i in view["check"]["issues"])


def test_ktru_conflict_is_rejected_and_not_queued() -> None:
    client = _client()
    r = client.post("/api/demo/submissions", json={"card": CONFLICT})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "Интенсивность цвета" in detail
    assert "Белый" in detail
    assert client.get("/api/demo/submissions").json() == []


def test_missing_ktru_is_rejected() -> None:
    client = _client()
    card = {**CARD, "ktru_code": None}
    r = client.post("/api/demo/submissions", json={"card": card})
    assert r.status_code == 422
    assert "категория товара" in r.json()["detail"]


def test_unknown_ktru_is_rejected() -> None:
    client = _client()
    card = {**CARD, "ktru_code": "00.00.00.000-99999999"}
    r = client.post("/api/demo/submissions", json={"card": card})
    assert r.status_code == 422
    assert "нет в справочнике" in r.json()["detail"]


def test_removed_required_characteristic_is_rejected() -> None:
    client = _client()
    chars = [c for c in CARD["characteristics"] if c["name"] != "Формат"]
    r = client.post("/api/demo/submissions", json={"card": {**CARD, "characteristics": chars}})
    assert r.status_code == 422
    assert "Формат" in r.json()["detail"]


def test_value_outside_range_is_rejected() -> None:
    client = _client()
    bags = "22.22.11.000-00000201"
    card = {
        "name": "Мешки для мусора",
        "ktru_code": bags,
        "characteristics": [
            {"name": "Объем", "value": "60", "unit": "л"},
            {"name": "Толщина", "value": "200", "unit": "мкм"},
            {"name": "Материал", "value": "ПВД"},
        ],
    }
    r = client.post("/api/demo/submissions", json={"card": card})
    assert r.status_code == 422
    assert "Толщина" in r.json()["detail"]
    assert "200" in r.json()["detail"]


def test_fixed_value_can_be_submitted() -> None:
    client = _client()
    rejected = client.post("/api/demo/submissions", json={"card": CONFLICT})
    assert rejected.status_code == 422
    accepted = client.post("/api/demo/submissions", json={"card": CARD})
    assert accepted.status_code == 201


def test_return_with_note() -> None:
    client = _client()
    sid = client.post("/api/demo/submissions", json={"card": CARD}).json()["id"]
    r = client.post(
        f"/api/demo/submissions/{sid}/decision",
        json={"decision": "return", "note": "Интенсивность цвета: значения нет в перечне"},
    )
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["decision_note"]) == (
        "returned",
        "Интенсивность цвета: значения нет в перечне",
    )


def test_second_decision_is_409() -> None:
    client = _client()
    sid = client.post("/api/demo/submissions", json={"card": CARD}).json()["id"]
    client.post(f"/api/demo/submissions/{sid}/decision", json={"decision": "accept"})
    again = {"decision": "return", "note": "Проверьте характеристики."}
    r = client.post(f"/api/demo/submissions/{sid}/decision", json=again)
    assert r.status_code == 409


def test_unknown_submission_is_404() -> None:
    client = _client()
    assert client.get("/api/demo/submissions/999").status_code == 404
    r = client.post("/api/demo/submissions/999/decision", json={"decision": "accept"})
    assert r.status_code == 404


def _returned(client: TestClient) -> int:
    sid = client.post("/api/demo/submissions", json={"card": CARD}).json()["id"]
    note = {"decision": "return", "note": "Уточните интенсивность цвета."}
    client.post(f"/api/demo/submissions/{sid}/decision", json=note)
    return sid


def test_supplier_fixes_returned_submission_and_it_goes_back_to_the_queue() -> None:
    client = _client()
    sid = _returned(client)
    fixed = {**CARD, "description": "Неоновая бумага, 500 листов в пачке"}
    r = client.put(f"/api/demo/submissions/{sid}", json={"card": fixed})
    assert r.status_code == 200
    body = r.json()
    assert (body["id"], body["status"]) == (sid, "pending")
    assert body["card"]["description"] == "Неоновая бумага, 500 листов в пачке"
    # Прежний текст возврата остаётся: модератор видит, что заявку исправляли и по какому замечанию.
    assert body["decision_note"] == "Уточните интенсивность цвета."
    assert [s["id"] for s in client.get("/api/demo/submissions").json()] == [sid]


def test_fixed_submission_can_be_decided_again() -> None:
    client = _client()
    sid = _returned(client)
    client.put(f"/api/demo/submissions/{sid}", json={"card": CARD})
    r = client.post(f"/api/demo/submissions/{sid}/decision", json={"decision": "accept"})
    assert (r.status_code, r.json()["status"]) == (200, "accepted")


def test_only_returned_submission_can_be_fixed() -> None:
    client = _client()
    sid = client.post("/api/demo/submissions", json={"card": CARD}).json()["id"]
    assert client.put(f"/api/demo/submissions/{sid}", json={"card": CARD}).status_code == 409
    client.post(f"/api/demo/submissions/{sid}/decision", json={"decision": "accept"})
    assert client.put(f"/api/demo/submissions/{sid}", json={"card": CARD}).status_code == 409
    assert client.put("/api/demo/submissions/999", json={"card": CARD}).status_code == 404


def test_fix_with_blocking_issues_is_rejected() -> None:
    client = _client()
    sid = _returned(client)
    broken = {**CARD, "characteristics": [{"name": "Формат", "value": "A4"}]}
    r = client.put(f"/api/demo/submissions/{sid}", json={"card": broken})
    assert r.status_code == 422
    assert client.get(f"/api/demo/submissions/{sid}").json()["status"] == "returned"


# Приёмка в один клик: чистая заявка — без блокирующих замечаний и предупреждений модуля;
# подсказки СТЕ портала без правки не мешают, их только считают: явного «Принять подсказку» пока
# нет, а подсказки есть у всех карточек мастера.
HINT = {
    "name": "Формат", "value": "A4", "unit": None, "source": None, "status": "filled",
    "required": False, "method": "similar", "note": "как у 3 из 3 похожих СТЕ портала с этим полем",
}  # fmt: skip


WARNED = {**CARD, "name": "Бумага цветная А4 80 г/м2 500 листов"}  # предупреждение о наименовании


def test_warning_needs_a_look_and_an_accepted_hint_is_only_counted() -> None:
    client = _client()
    card = _with_photo(client, CARD)
    clean = client.post("/api/demo/submissions", json={"card": card}).json()["id"]
    hinted = client.post("/api/demo/submissions", json={"card": card, "accepted": [HINT]}).json()
    # Подсказка была «A3», поставщик написал «A4»: в карточке его значение, подсказку не считаем.
    replaced = {**HINT, "value": "A3"}
    own = client.post("/api/demo/submissions", json={"card": card, "accepted": [replaced]}).json()
    warned_card = _with_photo(client, WARNED)
    warned = client.post("/api/demo/submissions", json={"card": warned_card}).json()["id"]
    triage = {s["id"]: s["triage"] for s in client.get("/api/demo/submissions").json()}
    assert triage[clean] == {"clean": True, "reasons": [], "hinted": 0}
    assert triage[hinted["id"]] == {"clean": True, "reasons": [], "hinted": 1}
    assert triage[own["id"]] == {"clean": True, "reasons": [], "hinted": 0}
    assert triage[warned] == {
        "clean": False,
        "reasons": ["В наименовании «80 г/м2», «500 листов»."],
        "hinted": 0,
    }
    view = client.get(f"/api/demo/submissions/{warned}").json()
    assert view["triage"]["clean"] is False


def test_submission_without_photo_stays_clean() -> None:
    # Фото необязательно: заявка без снимка чистая наравне с заявкой с фото.
    client = _client()
    bare = client.post("/api/demo/submissions", json={"card": CARD}).json()["id"]
    shot = client.post("/api/demo/submissions", json={"card": _with_photo(client, CARD)}).json()
    triage = {s["id"]: s["triage"] for s in client.get("/api/demo/submissions").json()}
    assert triage[bare] == {"clean": True, "reasons": [], "hinted": 0}
    assert triage[shot["id"]]["clean"] is True
    assert set(client.post("/api/demo/submissions/accept-clean").json()["accepted"]) == {
        bare,
        shot["id"],
    }


def test_accept_clean_accepts_only_clean_pending_with_a_note() -> None:
    client = _client()
    card = _with_photo(client, CARD)
    clean = client.post("/api/demo/submissions", json={"card": card}).json()["id"]
    warned_card = _with_photo(client, WARNED)
    warned = client.post("/api/demo/submissions", json={"card": warned_card}).json()["id"]
    r = client.post("/api/demo/submissions/accept-clean")
    assert (r.status_code, r.json()) == (200, {"accepted": [clean]})
    queue = {s["id"]: s for s in client.get("/api/demo/submissions").json()}
    assert queue[clean]["status"] == "accepted"
    assert queue[clean]["decision_note"] == ACCEPTED_CLEAN_NOTE
    assert queue[warned]["status"] == "pending"
    assert client.post("/api/demo/submissions/accept-clean").json() == {"accepted": []}


def test_accept_clean_takes_only_the_ids_the_moderator_confirmed() -> None:
    # Модератор подтверждает список в диалоге: пачкой уходят ровно те заявки, что он видел, и
    # только пока они чистые; заявка, пришедшая после открытия диалога, ждёт его.
    client = _client()
    card = _with_photo(client, CARD)
    seen = client.post("/api/demo/submissions", json={"card": card}).json()["id"]
    warned = client.post("/api/demo/submissions", json={"card": WARNED}).json()["id"]
    later = client.post("/api/demo/submissions", json={"card": card}).json()["id"]
    r = client.post("/api/demo/submissions/accept-clean", json={"ids": [seen, warned]})
    assert (r.status_code, r.json()) == (200, {"accepted": [seen]})
    queue = {s["id"]: s["status"] for s in client.get("/api/demo/submissions").json()}
    assert queue == {seen: "accepted", warned: "pending", later: "pending"}


def test_triage_names_blocking_count_and_warnings_word_for_word() -> None:
    from app.demo import triage
    from app.schemas import CardIn, CheckOut, Issue

    def issue(severity: str, message: str) -> Issue:
        return Issue(field="name", kind="moderation_rule", severity=severity, message=message,  # type: ignore[arg-type]
                     why="", rule="r")  # fmt: skip

    result = CheckOut(
        issues=[issue("block", "мат"), issue("warn", "Такая СТЕ уже есть на портале: №1"),
                issue("info", "можно короче")],
        verdict="needs_fix",
    )  # fmt: skip
    got = triage(CardIn(name="Бумага", characteristics=[]), [], result)
    assert (got.clean, got.reasons) == (
        False,
        ["нарушения правил модерации: 1", "Такая СТЕ уже есть на портале: №1"],
    )


def test_ktru_warning_is_not_clean_and_is_named_first() -> None:
    # значение вне перечня итоговой позиции КТРУ — warn, verdict «ready»;
    # пачкой такую заявку не одобрять, а причину КТРУ показывать, даже если предупреждений много.
    from app.demo import triage
    from app.pipeline import check
    from app.schemas import CardIn, CharIn
    from tests.test_ktru_reference import DESK_KTRU, _deps

    card = CardIn(
        name="Стол письменный", category_code="01.16.03.01.01", ktru_code=DESK_KTRU,
        characteristics=[CharIn(name="Вид материала столешницы", value="МДФ"),
                         CharIn(name="Вид материала каркаса", value="Дерево", origin="ktru")],
        photo_id=1,  # без фото было бы второе предупреждение (п. 7)
    )  # fmt: skip
    result = check(card, _deps())
    assert result.verdict == "ready"
    ktru_warn = next(i for i in result.issues if i.severity == "warn")
    got = triage(card, [], result)
    assert (got.clean, got.reasons) == (False, [ktru_warn.message])

    others = [ktru_warn.model_copy(update={"field": "name", "rule": "r", "message": f"w{n}"})
              for n in range(3)]  # fmt: skip
    crowded = result.model_copy(update={"issues": [*others, ktru_warn]})
    assert triage(card, [], crowded).reasons[0] == ktru_warn.message


def test_resubmitted_after_return_needs_the_moderator_even_without_module_issues() -> None:
    # модуль не видит, за что вернул человек («фото не того товара»), поэтому
    # исправленная заявка — к модератору, а не в один клик.
    client = _client()
    card = _with_photo(client, CARD)
    sid = client.post("/api/demo/submissions", json={"card": card}).json()["id"]
    note = {"decision": "return", "note": "Фото не того товара, замените фото"}
    assert client.post(f"/api/demo/submissions/{sid}/decision", json=note).status_code == 200
    assert client.put(f"/api/demo/submissions/{sid}", json={"card": card}).status_code == 200
    queued = next(s for s in client.get("/api/demo/submissions").json() if s["id"] == sid)
    assert queued["triage"]["clean"] is False
    assert queued["triage"]["reasons"] == [
        "исправлена после возврата: «Фото не того товара, замените фото»"
    ]
    assert client.post("/api/demo/submissions/accept-clean").json() == {"accepted": []}


def test_queue_checks_only_pending_submissions_and_once_per_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # очередь звала /check на каждую заявку при каждом чтении (~19 мс на заявку).
    import app.api.demo as demo

    calls: list[str] = []
    real = demo.check

    def counting(card, deps):  # type: ignore[no-untyped-def]
        calls.append(card.name)
        return real(card, deps)

    monkeypatch.setattr(demo, "check", counting)
    client = _client()
    card = _with_photo(client, CARD)
    pending = client.post("/api/demo/submissions", json={"card": card}).json()["id"]
    decided = client.post("/api/demo/submissions", json={"card": WARNED}).json()["id"]
    client.post(f"/api/demo/submissions/{decided}/decision", json={"decision": "accept"})
    calls.clear()
    first = {s["id"]: s["triage"] for s in client.get("/api/demo/submissions").json()}
    second = {s["id"]: s["triage"] for s in client.get("/api/demo/submissions").json()}
    assert first == second and first[decided] is None and first[pending]["clean"] is True
    assert calls == [CARD["name"]]  # только ждущая решения и только один раз


def test_submission_keeps_category_and_ktru_codes() -> None:
    client = _client()
    card = {**CARD, "category_code": COLOR, "ktru_code": COLOR}
    r = client.post("/api/demo/submissions", json={"card": card})
    assert r.status_code == 201
    stored = client.get(f"/api/demo/submissions/{r.json()['id']}").json()["card"]
    assert (stored["category_code"], stored["ktru_code"]) == (COLOR, COLOR)
    # Старая заявка без category_code читается: код категории берётся из ktru_code.
    old = client.post("/api/demo/submissions", json={"card": CARD}).json()["id"]
    assert client.get(f"/api/demo/submissions/{old}").json()["card"]["category_code"] is None


def test_submission_remembers_its_batch_row() -> None:
    """«Мои заявки»: заявка из строки пакета помнит пакет и строку, обычная — нет."""
    client = _client()
    single = client.post("/api/demo/submissions", json={"card": CARD}).json()
    from_batch = client.post(
        "/api/demo/submissions",
        json={"card": CARD, "batch": {"job_id": "abc123", "row": 7, "filename": "товары.xlsx"}},
    ).json()
    assert single["batch"] is None
    assert from_batch["batch"] == {"job_id": "abc123", "row": 7, "filename": "товары.xlsx"}
    listed = {s["id"]: s["batch"] for s in client.get("/api/demo/submissions").json()}
    assert listed[from_batch["id"]]["row"] == 7 and listed[single["id"]] is None
    # Исправление заявки связь с пакетом не теряет.
    sid = from_batch["id"]
    back = {"decision": "return", "note": "фото"}
    client.post(f"/api/demo/submissions/{sid}/decision", json=back)
    fixed = client.put(f"/api/demo/submissions/{sid}", json={"card": CARD}).json()
    assert fixed["batch"]["job_id"] == "abc123"
