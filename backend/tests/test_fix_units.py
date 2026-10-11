import json
from pathlib import Path

from app.tools.fix_units import fix_catalog


def test_old_catalog_gets_angles_and_semicolon_units_fixed(tmp_path: Path) -> None:
    # Угол с °C, «Мегаватт; тысяча киловатт», «° градус»
    def spec(name: str, unit: str) -> dict:
        return {"name": name, "kind": "quantitative", "unit": unit}

    chars = [
        spec("Угол наклона", "°C"),
        spec("Температура хранения", "°C"),
        spec("Мощность турбины", "Мегаватт; тысяча киловатт"),
        spec("Угол открывания", "° градус"),
        spec("Длина", "мм"),
    ]
    path = tmp_path / "catalog.json"
    position = {"code": "P", "name": "x", "okpd2": "", "source": "t", "characteristics": chars}
    path.write_text(json.dumps({"positions": [position]}, ensure_ascii=False), encoding="utf-8")
    changes = fix_catalog(path)
    units = [
        c["unit"]
        for c in json.loads(path.read_text(encoding="utf-8"))["positions"][0]["characteristics"]
    ]
    assert units == ["°", "°C", "МВт", "°", "мм"]
    assert sum(changes.values()) == 3
