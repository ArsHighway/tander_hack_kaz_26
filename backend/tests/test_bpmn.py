import xml.etree.ElementTree as ET
from itertools import pairwise

from scripts.bpmn import NS, build_bpmn, build_svg

B = f"{{{NS['bpmn']}}}"
DI = f"{{{NS['bpmndi']}}}"
DC = f"{{{NS['dc']}}}"


def _tree() -> ET.Element:
    return ET.fromstring(build_bpmn())


def test_xml_parses_and_has_three_lanes() -> None:
    lanes = _tree().findall(f".//{B}lane")
    assert [lane.get("name") for lane in lanes] == [
        "Поставщик (портал)",
        "Модуль автозаполнения",
        "Модератор (портал)",
    ]


def test_every_flow_points_to_existing_nodes() -> None:
    root = _tree()
    ids = {el.get("id") for el in root.iter() if el.get("id")}
    flows = root.findall(f".//{B}sequenceFlow")
    assert flows
    for f in flows:
        assert f.get("sourceRef") in ids and f.get("targetRef") in ids


def test_every_node_and_flow_has_diagram_shape() -> None:
    root = _tree()
    process = root.find(f"{B}process")
    assert process is not None
    nodes = [
        el.get("id") for el in process if el.tag != f"{B}sequenceFlow" and el.tag != f"{B}laneSet"
    ]
    shapes = {s.get("bpmnElement") for s in root.iter(f"{DI}BPMNShape")}
    edges = {e.get("bpmnElement") for e in root.iter(f"{DI}BPMNEdge")}
    assert set(nodes) <= shapes
    assert {f.get("id") for f in process.findall(f"{B}sequenceFlow")} <= edges


def test_lanes_do_not_overlap() -> None:
    root = _tree()
    lane_ids = {lane.get("id") for lane in root.iter(f"{B}lane")}
    bands = []
    for shape in root.iter(f"{DI}BPMNShape"):
        if shape.get("bpmnElement") in lane_ids:
            b = shape.find(f"{DC}Bounds")
            assert b is not None
            bands.append((float(b.get("y", 0)), float(b.get("y", 0)) + float(b.get("height", 0))))
    bands.sort()
    assert all(a[1] <= b[0] for a, b in pairwise(bands))


def test_every_node_sits_in_its_lane() -> None:
    root = _tree()
    bounds = {s.get("bpmnElement"): s.find(f"{DC}Bounds") for s in root.iter(f"{DI}BPMNShape")}
    for lane in root.iter(f"{B}lane"):
        lb = bounds[lane.get("id")]
        assert lb is not None
        top, bottom = float(lb.get("y", 0)), float(lb.get("y", 0)) + float(lb.get("height", 0))
        for ref in lane.iter(f"{B}flowNodeRef"):
            nb = bounds[ref.text]
            assert nb is not None
            assert (
                top <= float(nb.get("y", 0))
                and float(nb.get("y", 0)) + float(nb.get("height", 0)) <= bottom
            )


def test_svg_is_valid_and_mentions_every_lane() -> None:
    svg = build_svg()
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    for name in ("Поставщик (портал)", "Модуль автозаполнения", "Модератор (портал)"):
        assert name in svg


def test_task_types_and_batch_path() -> None:
    # задачи без типа, нет пакетной загрузки через очередь и шага «дополнить данные»
    root = ET.fromstring(build_bpmn())
    process = root.find(f"{B}process")
    assert process is not None
    assert process.find(f"{B}task") is None  # у каждой задачи тип: пользовательская или сервисная
    users = {t.get("name") for t in process.findall(f"{B}userTask")}
    services = {t.get("name") for t in process.findall(f"{B}serviceTask")}
    assert any("файл" in (n or "").lower() for n in services)  # пакет порциями через очередь
    assert any("дополнить" in (n or "").lower() for n in users)  # не определить — шаги поставщику


def test_gateways_have_default_and_conditions_and_start_does_not_branch() -> None:
    # у старта было два безусловных выхода, у шлюзов — ни условий, ни default.
    process = _tree().find(f"{B}process")
    assert process is not None
    flows = process.findall(f"{B}sequenceFlow")
    out: dict[str, list[ET.Element]] = {}
    for f in flows:
        out.setdefault(f.get("sourceRef", ""), []).append(f)
    assert len(out["start"]) == 1
    for gateway in process.findall(f"{B}exclusiveGateway"):
        branches = out[gateway.get("id", "")]
        assert len(branches) >= 2
        default = gateway.get("default")
        assert default in {f.get("id") for f in branches}
        for f in branches:
            if f.get("id") != default:
                assert f.find(f"{B}conditionExpression") is not None
