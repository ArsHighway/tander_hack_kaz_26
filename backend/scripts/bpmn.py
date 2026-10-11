"""BPMN 2.0 процесса «Создание СТЕ с модулем автозаполнения» и его SVG.

    uv run python -m scripts.bpmn   # пишет docs/bpmn/module.bpmn, module.svg и frontend/public/bpmn

Геометрия (DI) считается здесь же, поэтому .bpmn открывается в bpmn.io и Camunda Modeler как есть,
а SVG рисуется из тех же координат без браузера и без интернета. Схему не правят руками: правят
этот файл, тест tests/test_bpmn.py проверяет ссылки потоков, фигуры и дорожки.
"""

import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import escape
from itertools import pairwise
from pathlib import Path

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)

LANES = [
    ("lane_supplier", "Поставщик (портал)"),
    ("lane_module", "Модуль автозаполнения"),
    ("lane_moderator", "Модератор (портал)"),
]
LANE_H = 190
LABEL_W = 40
COL_W = 170
X0 = LABEL_W + 30
SIZES = {"start": (36, 36), "end": (36, 36), "task": (140, 80), "gateway": (50, 50)}
INK, LINE, MUTED, MARK, RAISED = "#17191c", "#dcdfe3", "#5b616a", "#ffe98f", "#f5f6f7"


@dataclass(frozen=True)
class Node:
    id: str
    kind: str  # start | end | task | gateway
    name: str
    lane: int
    col: int


NODES = [
    Node("start", "start", "Нужна новая СТЕ", 0, 0),
    Node("source", "gateway", "Один товар или файл?", 0, 1),
    Node("enter", "task", "Ввести наименование и описание", 0, 2),
    Node("batch", "task", "Разобрать файл: строки порциями через очередь воркерам", 1, 1),
    Node(
        "autofill",
        "task",
        "Найти категорию и КТРУ, заполнить из текста и СТЕ портала (без модели — правилами)",
        1,
        2,
    ),
    Node("unique", "gateway", "Категория и КТРУ однозначны?", 1, 3),
    Node("answer", "task", "Ответить на вопрос или дополнить описание", 0, 3),
    Node("review", "task", "Принять или поправить значения и подсказки", 0, 4),
    Node("check", "task", "Проверить карточку: модерация, дубли, числа, опечатки", 1, 4),
    Node("blocking", "gateway", "Есть блокирующие замечания?", 1, 5),
    Node("submit", "task", "Отправить на модерацию", 0, 6),
    Node("moderate", "task", "Проверить заявку с замечаниями модуля", 2, 6),
    Node("decision", "gateway", "Принять заявку?", 2, 7),
    Node("publish", "task", "Опубликовать СТЕ в каталоге", 2, 8),
    Node("end", "end", "СТЕ в каталоге", 2, 9),
]
# Маршруты потоков заданы руками: встречные потоки — в разных коридорах, без пересечений с фигурами.
# Вид маршрута: h — вправо в своей дорожке; v — вертикально (dx — сдвиг от центра); lv — влево и
# вниз (out — отступ); rv — вправо,
# потом вертикально к цели; corridor — через горизонтальный коридор на высоте y.
FLOWS: list[tuple[str, str, str, str, dict[str, float]]] = [
    ("start", "source", "", "h", {}),
    ("source", "enter", "один товар", "h", {}),
    ("source", "batch", "файл с товарами", "v", {}),
    ("batch", "autofill", "", "h", {}),
    ("enter", "autofill", "POST /autofill", "v", {}),
    ("autofill", "unique", "", "h", {}),
    ("unique", "answer", "нет", "v", {"dx": -14}),
    ("answer", "unique", "POST /answer", "v", {"dx": 14}),
    ("unique", "review", "да", "corridor", {"y": LANE_H - 30, "out": 60, "dx": -30}),
    ("review", "check", "POST /check", "v", {}),
    ("check", "blocking", "", "h", {}),
    ("blocking", "review", "да", "corridor", {"y": LANE_H + 22, "dx": 40, "up": 1}),
    ("blocking", "submit", "нет", "rv", {"dx": -30}),
    ("submit", "moderate", "", "v", {"dx": 30}),
    ("moderate", "decision", "", "h", {}),
    ("decision", "publish", "да", "h", {}),
    ("publish", "end", "", "h", {}),
    ("decision", "review", "нет: вернуть с замечаниями", "corridor", {"y": 22, "dx": 0, "up": 1}),
]
# Условия исходящих потоков шлюзов (поток без условия — путь по умолчанию, `default` шлюза).
CONDITIONS = {
    ("source", "batch"): "${input == 'file'}",
    ("unique", "answer"): "${ktru.final_code == null}",
    ("blocking", "review"): "${verdict == 'needs_fix'}",
    ("decision", "review"): "${decision == 'returned'}",
}
DEFAULTS = {
    "source": ("source", "enter"),
    "unique": ("unique", "review"),
    "blocking": ("blocking", "submit"),
    "decision": ("decision", "publish"),
}
LABEL_ABOVE = {"source"}
XSI = "http://www.w3.org/2001/XMLSchema-instance"
ET.register_namespace("xsi", XSI)
TAG = {"start": "startEvent", "end": "endEvent", "task": "task", "gateway": "exclusiveGateway"}
MODULE_LANE = 1


def _tag(n: Node) -> str:
    """Задача модуля — сервисная, задача поставщика или модератора — пользовательская."""
    if n.kind != "task":
        return TAG[n.kind]
    return "serviceTask" if n.lane == MODULE_LANE else "userTask"


def _q(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


def _bounds(n: Node) -> tuple[float, float, float, float]:
    w, h = SIZES[n.kind]
    cx = X0 + n.col * COL_W + 70
    cy = n.lane * LANE_H + LANE_H / 2
    return cx - w / 2, cy - h / 2, w, h


def _center(n: Node) -> tuple[float, float]:
    x, y, w, h = _bounds(n)
    return x + w / 2, y + h / 2


def _edge_y(n: Node, toward_y: float) -> float:
    """Верх или низ фигуры — со стороны, откуда приходит поток."""
    _, y, _, h = _bounds(n)
    return y if toward_y < y else y + h


def _waypoints(a: Node, b: Node, kind: str, p: dict[str, float]) -> list[tuple[float, float]]:
    (ax, ay), (bx, by) = _center(a), _center(b)
    aw, _ = SIZES[a.kind]
    bw, _ = SIZES[b.kind]
    dx = p.get("dx", 0)
    if kind == "h":
        return [(ax + aw / 2, ay), (bx - bw / 2, by)]
    if kind == "v":
        return [(ax + dx, _edge_y(a, by)), (bx + dx, _edge_y(b, ay))]
    if kind == "lv":  # влево из фигуры, затем вниз к цели: не через подпись события под ней
        tx = ax - aw / 2 - p.get("out", 30)
        return [(ax - aw / 2, ay), (tx, ay), (tx, _edge_y(b, ay))]
    if kind == "rv":
        tx = bx + dx
        return [(ax + aw / 2, ay), (tx, ay), (tx, _edge_y(b, ay))]
    # corridor: выход из фигуры, коридор на высоте y, вход в цель со стороны коридора
    y, tx = p["y"], bx + dx
    if p.get("up"):
        start = (ax, _edge_y(a, y))
        return [start, (ax, y), (tx, y), (tx, _edge_y(b, y))]
    out = ax + aw / 2 + (p.get("out", 30) - aw / 2)
    return [(ax + aw / 2, ay), (out, ay), (out, y), (tx, y), (tx, _edge_y(b, y))]


def _width() -> float:
    return X0 + (max(n.col for n in NODES) + 1) * COL_W + 20


def build_bpmn() -> str:
    defs = ET.Element(
        _q("bpmn", "definitions"), {"id": "defs", "targetNamespace": "https://tenderhack.ru/kazan"}
    )
    collab = ET.SubElement(defs, _q("bpmn", "collaboration"), {"id": "collab"})
    ET.SubElement(
        collab,
        _q("bpmn", "participant"),
        {"id": "pool", "name": "Создание СТЕ на Портале поставщиков", "processRef": "process"},
    )
    proc = ET.SubElement(defs, _q("bpmn", "process"), {"id": "process", "isExecutable": "false"})
    lane_set = ET.SubElement(proc, _q("bpmn", "laneSet"), {"id": "lanes"})
    for i, (lid, lname) in enumerate(LANES):
        lane = ET.SubElement(lane_set, _q("bpmn", "lane"), {"id": lid, "name": lname})
        for n in NODES:
            if n.lane == i:
                ET.SubElement(lane, _q("bpmn", "flowNodeRef")).text = n.id
    flow_id = {(a, b): f"flow{i}" for i, (a, b, *_) in enumerate(FLOWS)}
    for n in NODES:
        attrs = {"id": n.id, "name": n.name}
        if n.id in DEFAULTS:
            attrs["default"] = flow_id[DEFAULTS[n.id]]
        ET.SubElement(proc, _q("bpmn", _tag(n)), attrs)
    for i, (a, b, label, _, _) in enumerate(FLOWS):
        attrs = {"id": f"flow{i}", "sourceRef": a, "targetRef": b}
        if label:
            attrs["name"] = label
        flow = ET.SubElement(proc, _q("bpmn", "sequenceFlow"), attrs)
        if (a, b) in CONDITIONS:
            formal = {f"{{{XSI}}}type": "bpmn:tFormalExpression"}
            cond = ET.SubElement(flow, _q("bpmn", "conditionExpression"), formal)
            cond.text = CONDITIONS[(a, b)]

    diagram = ET.SubElement(defs, _q("bpmndi", "BPMNDiagram"), {"id": "diagram"})
    plane = ET.SubElement(
        diagram, _q("bpmndi", "BPMNPlane"), {"id": "plane", "bpmnElement": "collab"}
    )

    def shape(element: str, box: tuple[float, float, float, float], **extra: str) -> None:
        attrs = {"id": f"{element}_di", "bpmnElement": element, **extra}
        s = ET.SubElement(plane, _q("bpmndi", "BPMNShape"), attrs)
        x, y, w, h = box
        dims = {"x": f"{x:g}", "y": f"{y:g}", "width": f"{w:g}", "height": f"{h:g}"}
        ET.SubElement(s, _q("dc", "Bounds"), dims)

    width = _width()
    shape("pool", (0, 0, width, LANE_H * len(LANES)), isHorizontal="true")
    for i, (lid, _) in enumerate(LANES):
        shape(lid, (LABEL_W, i * LANE_H, width - LABEL_W, LANE_H), isHorizontal="true")
    by_id = {n.id: n for n in NODES}
    for n in NODES:
        extra = {"isMarkerVisible": "true"} if n.kind == "gateway" else {}
        shape(n.id, _bounds(n), **extra)
    for i, (a, b, _, kind, params) in enumerate(FLOWS):
        e = ET.SubElement(
            plane, _q("bpmndi", "BPMNEdge"), {"id": f"flow{i}_di", "bpmnElement": f"flow{i}"}
        )
        for x, y in _waypoints(by_id[a], by_id[b], kind, params):
            ET.SubElement(e, _q("di", "waypoint"), {"x": f"{x:g}", "y": f"{y:g}"})
    ET.indent(defs)
    return ET.tostring(defs, encoding="unicode", xml_declaration=True) + "\n"


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return [*lines, line] if line else lines


def _text(x: float, y: float, text: str, **attrs: str) -> str:
    extra = "".join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return f'<text x="{x:g}" y="{y:g}"{extra}>{escape(text)}</text>'


def _svg_lanes(width: float) -> list[str]:
    out = []
    for i, (_, name) in enumerate(LANES):
        y = i * LANE_H
        fill = RAISED if i == 1 else "#ffffff"
        out.append(
            f'<rect x="{LABEL_W}" y="{y}" width="{width - LABEL_W:g}" height="{LANE_H}" '
            f'fill="{fill}" stroke="{LINE}"/>'
        )
        out.append(
            f'<rect x="0" y="{y}" width="{LABEL_W}" height="{LANE_H}" '
            f'fill="#ffffff" stroke="{LINE}"/>'
        )
        cx, cy = LABEL_W / 2, y + LANE_H / 2
        out.append(
            _text(cx, cy, name, transform=f"rotate(-90 {cx:g} {cy:g})", text_anchor="middle",
                  dominant_baseline="middle", fill=INK, font_weight="600")
        )  # fmt: skip
    return out


def _svg_flows() -> list[str]:
    by_id = {n.id: n for n in NODES}
    out = []
    for a, b, label, kind, params in FLOWS:
        pts = _waypoints(by_id[a], by_id[b], kind, params)
        d = " ".join(f"{'M' if i == 0 else 'L'}{x:g} {y:g}" for i, (x, y) in enumerate(pts))
        out.append(
            f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="1.2" '
            'marker-end="url(#arrow)"/>'
        )
        if label:
            # Подпись — у самого длинного отрезка: справа от вертикального, над горизонтальным.
            (x1, y1), (x2, y2) = max(
                pairwise(pts), key=lambda s: abs(s[1][0] - s[0][0]) + abs(s[1][1] - s[0][1])
            )
            if x1 == x2:
                lx, ly, anchor = x1 + 6, (y1 + y2) / 2 + 4, "start"
            else:
                lx, ly, anchor = (x1 + x2) / 2, y1 - 6, "middle"
            out.append(_text(lx, ly, label, fill=MUTED, font_size="11", text_anchor=anchor))
    return out


def _svg_nodes() -> list[str]:
    out = []
    for n in NODES:
        x, y, w, h = _bounds(n)
        cx, cy = x + w / 2, y + h / 2
        if n.kind in ("start", "end"):
            sw = 1.5 if n.kind == "start" else 3.5
            out.append(
                f'<circle cx="{cx:g}" cy="{cy:g}" r="{w / 2:g}" fill="#ffffff" stroke="{INK}" '
                f'stroke-width="{sw}"/>'
            )
            out.append(_text(cx, y + h + 16, n.name, text_anchor="middle", fill=INK))
        elif n.kind == "gateway":
            pts = f"{cx:g},{y:g} {x + w:g},{cy:g} {cx:g},{y + h:g} {x:g},{cy:g}"
            out.append(f'<polygon points="{pts}" fill="{MARK}" stroke="{INK}" stroke-width="1.2"/>')
            out.append(_text(cx, cy + 5, "×", text_anchor="middle", fill=INK, font_size="16"))
            # Подпись под шлюзом: над ним идут встречные потоки вопроса. У шлюза, из которого
            # поток уходит вниз, подпись над ним.
            parts = _wrap(n.name, 22)
            above = n.id in LABEL_ABOVE
            for k, part in enumerate(parts):
                ty = y - 10 - 14 * (len(parts) - 1 - k) if above else y + h + 16 + 14 * k
                out.append(_text(cx, ty, part, text_anchor="middle", fill=INK))
        else:
            out.append(
                f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="8" fill="#ffffff" '
                f'stroke="{INK}" stroke-width="1.2"/>'
            )
            parts = _wrap(n.name, 19)
            top = cy - (len(parts) - 1) * 7
            for k, part in enumerate(parts):
                out.append(_text(cx, top + 14 * k + 4, part, text_anchor="middle", fill=INK))
    return out


def build_svg() -> str:
    """Тот же процесс картинкой: дорожки, задачи, шлюзы, события, потоки с подписями."""
    width, height = _width(), LANE_H * len(LANES)
    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:g} {height:g}" '
        f'width="{width:g}" height="{height:g}" font-family="Onest, system-ui, sans-serif" '
        f'font-size="12">',
        "<title>BPMN: создание СТЕ с модулем автозаполнения</title>",
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{INK}"/>'
        "</marker></defs>",
        f'<rect width="{width:g}" height="{height:g}" fill="#ffffff"/>',
    ]
    body = _svg_lanes(width) + _svg_flows() + _svg_nodes()
    return "\n".join([*head, *body, "</svg>"]) + "\n"


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    targets = {
        root / "docs" / "bpmn" / "module.bpmn": build_bpmn(),
        root / "docs" / "bpmn" / "module.svg": build_svg(),
        root / "frontend" / "public" / "bpmn" / "module.svg": build_svg(),
    }
    for path, text in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"записано: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
