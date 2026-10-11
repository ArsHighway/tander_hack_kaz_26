"""Audit/filter the existing JSON against the organizer XML and the KTRU schema.

Does not start services and never replaces the input. Writes a separate cleaned file.
Only non-template positions are retained. Distinct official codes and optional characteristics
are not duplicates.
"""

import argparse
import collections
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.catalog import Catalog  # noqa: E402
from app.catalog.hack_scope import CATEGORIES, category_of, is_service  # noqa: E402
from app.ingest.units import normalize_unit  # noqa: E402


def local(tag):
    return tag.rsplit("}", 1)[-1]


def child(el, name):
    return next((c for c in el if local(c.tag) == name), None) if el is not None else None


def text(el, name):
    c = child(el, name)
    return " ".join((c.text or "").split()) if c is not None else ""


def stamp(value):
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=3)))
        if parsed.tzinfo is None
        else parsed
    )


def rank(record):
    zero = dt.datetime.min.replace(tzinfo=dt.UTC)
    return (
        int(record["version"] or 0),
        stamp(record["updateDate"]) or zero,
        stamp(record["publishDate"]) or zero,
        record["actual_file"],
    )


def record_from(el, source, actual_file):
    data = child(el, "data")
    keys = (
        "code",
        "name",
        "version",
        "versionCode",
        "status",
        "actual",
        "inclusionDate",
        "publishDate",
        "updateDate",
        "applicationDateStart",
        "applicationDateEnd",
        "isTemplate",
        "noNewFeatures",
    )
    record = {k: text(data, k) for k in keys}
    record["okpd2"] = text(child(data, "OKPD2"), "code")
    record["cancelDate"] = text(child(data, "cancelInfo"), "cancelDate")
    record["parent"] = text(child(data, "parentPositionInfo"), "code")
    record["source_file"] = source
    record["actual_file"] = actual_file
    chars = child(data, "characteristics")
    record["characteristics"] = (
        [
            {
                k: text(c, k)
                for k in ("code", "name", "actual", "type", "kind", "isRequired", "choiceType")
            }
            for c in chars
            if local(c.tag) == "characteristic"
        ]
        if chars is not None
        else []
    )
    return record


def iter_xml(el_stream, source, actual_file, wanted):
    stack = []
    for event, el in ET.iterparse(el_stream, events=("start", "end")):
        if event == "start":
            stack.append(el)
            continue
        if local(el.tag) == "position":
            code = text(child(el, "data"), "code")
            if code in wanted:
                yield record_from(el, source, actual_file)
            if len(stack) > 1:
                stack[-2].remove(el)
            el.clear()
        stack.pop()


def scan_xml(folder, wanted):
    latest = {}
    conflicts = []
    files = []
    seen = collections.Counter()
    status_counts = collections.Counter()
    # A loose XML may be an extracted copy of a zip member. Read both, count identical copies.
    inputs = sorted(p for p in folder.rglob("*") if p.suffix.lower() in (".xml", ".zip"))
    for index, path in enumerate(inputs, 1):
        if path.suffix.lower() == ".zip":
            zf = zipfile.ZipFile(path)
            names = sorted(n for n in zf.namelist() if n.lower().endswith(".xml"))
            streams = [(n, zf.open(n)) for n in names]
        else:
            zf = None
            streams = [(path.name, path.open("rb"))]
        try:
            for name, stream in streams:
                source = str(path) + (":" + name if zf else "")
                actual_file = "_actual_" in name and "not-actual" not in name
                files.append({"source": source, "actual_file": actual_file})
                with stream:
                    for record in iter_xml(stream, source, actual_file, wanted):
                        code = record["code"]
                        seen[code] += 1
                        status_counts[record["status"]] += 1
                        old = latest.get(code)
                        if old is None or rank(record) > rank(old):
                            latest[code] = record
                        elif rank(record) == rank(old):
                            a = {k: v for k, v in record.items() if k != "source_file"}
                            b = {k: v for k, v in old.items() if k != "source_file"}
                            if a != b:
                                conflicts.append(
                                    {"code": code, "files": [old["source_file"], source]}
                                )
        finally:
            if zf:
                zf.close()
        if index % 25 == 0 or index == len(inputs):
            print(f"XML scan: {index}/{len(inputs)}, matched codes {len(latest)}", flush=True)
    if conflicts:
        raise ValueError(
            f"Conflicting XML records with the same version/timestamps: {conflicts[:5]}"
        )
    return latest, {
        "xml_files": files,
        "matched_records": sum(seen.values()),
        "repeated_source_records": sum(n - 1 for n in seen.values()),
        "source_status_counts": dict(status_counts),
    }


def exclusion(record, as_of):
    # Select newest version BEFORE testing status: an older ACTIVE version cannot resurrect it.
    if record["status"] != "ACTIVE":
        return "status_" + (record["status"] or "missing")
    if not record["actual_file"]:
        return "historical_xml_only"
    if record["actual"] in ("false", "0"):
        return "position_not_actual"
    if record["actual"] not in ("", "true", "1"):
        raise ValueError(f"Unknown boolean actual: {record['code']}")
    if record["cancelDate"] and stamp(record["cancelDate"]) <= as_of:
        return "cancelled"
    if record["applicationDateEnd"] and stamp(record["applicationDateEnd"]) < as_of:
        return "application_ended"
    if record["isTemplate"] in ("true", "1"):
        return "template"
    if record["isTemplate"] not in ("false", "0"):
        raise ValueError(f"Cannot confirm non-template position: {record['code']}")
    if not category_of(record["okpd2"]):
        return "outside_scope_in_latest_xml"
    if is_service(record["name"]):
        return "service_in_latest_xml"
    return None


def verify_values(output, metadata):
    """Independently compare retained values/units/types to the newest XML records."""
    wanted = {p["code"]: p for p in output["positions"]}
    sources = collections.defaultdict(set)
    for code in wanted:
        sources[metadata[code]["source_file"]].add(code)
    checked = set()
    checks = 0
    signs = {"greaterOrEqual": ">=", "greater": ">", "lessOrEqual": "<=", "less": "<"}
    math_signs = {"≥": ">=", "≤": "<=", ">": ">", "<": "<"}
    bound = re.compile(r"([≥≤<>])\s*(-?\d+(?:[.,]\d+)?)")
    for source, codes in sources.items():
        path, sep, member = source.partition(".zip:")
        if sep:
            zf = zipfile.ZipFile(path + ".zip")
            stream = zf.open(member)
        else:
            zf = None
            stream = pathlib.Path(source).open("rb")  # noqa: SIM115
        try:
            with stream:
                stack = []
                for event, el in ET.iterparse(stream, events=("start", "end")):
                    if event == "start":
                        stack.append(el)
                        continue
                    if local(el.tag) == "position":
                        data = child(el, "data")
                        code = text(data, "code")
                        if code in codes:
                            raw = record_from(el, source, metadata[code]["actual_file"])
                            if rank(raw) == rank(metadata[code]):
                                json_chars = {c["name"]: c for c in wanted[code]["characteristics"]}
                                xml_chars = child(data, "characteristics")
                                for c in xml_chars if xml_chars is not None else []:
                                    if text(c, "actual") in ("false", "0"):
                                        continue
                                    name = text(c, "name")
                                    jc = json_chars[name]
                                    values = []
                                    units = collections.Counter()
                                    container = child(c, "values")
                                    for v in container if container is not None else []:
                                        found = []
                                        quality = text(v, "qualityDescription")
                                        if quality:
                                            matches = bound.findall(quality)
                                            residue = (
                                                bound.sub("", quality).replace("и", "").strip()
                                            )
                                            found.append(
                                                " ... ".join(math_signs[s] + n for s, n in matches)
                                                if matches and not residue
                                                else quality
                                            )
                                        ranges = child(v, "rangeSet")
                                        for vr in ranges if ranges is not None else []:
                                            bounds = [
                                                signs[text(vr, b + "MathNotation")] + text(vr, b)
                                                for b in ("min", "max")
                                                if text(vr, b)
                                                and text(vr, b + "MathNotation") in signs
                                            ]
                                            if bounds:
                                                found.append(" ... ".join(bounds))
                                        exact = child(v, "valueSet")
                                        found += (
                                            [
                                                (n.text or "").strip()
                                                for n in exact
                                                if (n.text or "").strip()
                                            ]
                                            if exact is not None
                                            else []
                                        )
                                        values.extend(found)
                                        unit = text(child(v, "OKEI"), "name")
                                        if found and unit:
                                            units[unit] += 1
                                    expected = set(values)
                                    if set(jc.get("values", [])) != expected:
                                        raise ValueError(
                                            f"Stale/different JSON values: {code}/{name}"
                                        )
                                    unit = units.most_common(1)[0][0] if units else None
                                    normalized = normalize_unit(unit) if unit else None
                                    if unit and unit.casefold() in (
                                        "градус",
                                        "градусов",
                                        "градусы",
                                        "град",
                                        "град.",
                                        "° градус",
                                        "°",
                                    ):
                                        normalized = (
                                            "°C"
                                            if re.search(
                                                "температур|нагрев|охлажд|мороз|хранени|плавлен|кипен|заморо|термо|цельс",
                                                name.casefold(),
                                            )
                                            else "°"
                                        )
                                    if unit and not normalized:
                                        normalized = unit
                                    if normalized != jc.get("unit"):
                                        raise ValueError(
                                            f"Stale/different JSON unit: {code}/{name}: {normalized!r} != {jc.get('unit')!r}"  # noqa: E501
                                        )
                                    kind = (
                                        "quantitative"
                                        if text(c, "type") == "2" or unit
                                        else "qualitative"
                                    )
                                    if jc["kind"] != kind:
                                        raise ValueError(
                                            f"Stale JSON characteristic type: {code}/{name}"
                                        )
                                    checks += 1
                                checked.add(code)
                        if len(stack) > 1:
                            stack[-2].remove(el)
                        el.clear()
                    stack.pop()
        finally:
            if zf:
                zf.close()
    if checked != set(wanted):
        raise ValueError(f"XML value verification incomplete: {set(wanted) - checked}")
    return {
        "positions_checked": len(checked),
        "characteristics_checked": checks,
        "checks": "values, ranges, units, types against latest source XML; names/required/actual also checked",  # noqa: E501
    }


def clean(payload, metadata, as_of):
    reasons = collections.Counter()
    removed = []
    kept = []
    char_removed = []
    char_updates = []
    codes = set()
    for original in payload["positions"]:
        p = json.loads(json.dumps(original))
        code = p["code"]
        if not category_of(p["okpd2"]):
            reason = "outside_scope"
        elif is_service(p["name"]):
            reason = "service"
        elif code not in metadata:
            reason = "missing_in_source_xml"
        else:
            reason = exclusion(metadata[code], as_of)
        if reason:
            reasons[reason] += 1
            removed.append({"code": code, "name": p["name"], "okpd2": p["okpd2"], "reason": reason})
            continue
        if code in codes:
            if next(k for k in kept if k["code"] == code) != p:
                raise ValueError(f"Conflicting JSON duplicates: {code}")
            reasons["duplicate_code"] += 1
            removed.append({"code": code, "reason": "duplicate_code"})
            continue
        r = metadata[code]
        if (p["name"], p["okpd2"]) != (r["name"], r["okpd2"]):
            raise ValueError(f"JSON no longer matches latest XML: {code}; rebuild it from XML")
        active_chars = {}
        for c in r["characteristics"]:
            if c["actual"] in ("false", "0"):
                continue
            if c["actual"] not in ("", "true", "1"):
                raise ValueError(f"Unknown characteristic actual: {code}/{c['name']}")
            if c["name"] in active_chars:
                raise ValueError(f"Ambiguous XML characteristic: {code}/{c['name']}")
            active_chars[c["name"]] = c
        chars = []
        char_seen = set()
        for c in p["characteristics"]:
            c["name"] = " ".join(c["name"].split())
            if c["name"] not in active_chars:
                char_removed.append(
                    {"code": code, "name": c["name"], "reason": "not_in_active_xml_characteristics"}
                )
                continue
            required = active_chars[c["name"]]["isRequired"] in ("true", "1")
            if bool(c.get("required", False)) != required:
                char_updates.append({"code": code, "name": c["name"], "required": required})
                if required:
                    c["required"] = True
                else:
                    c.pop("required", None)
            c["values"] = list(dict.fromkeys(c.get("values", [])))
            signature = json.dumps(c, ensure_ascii=False, sort_keys=True)
            if signature in char_seen:
                char_removed.append(
                    {"code": code, "name": c["name"], "reason": "duplicate_characteristic"}
                )
                continue
            char_seen.add(signature)
            chars.append(c)
        if {c["name"] for c in chars} != set(active_chars):
            raise ValueError(f"JSON lacks active XML characteristics: {code}; rebuild it from XML")
        p["characteristics"] = chars
        kept.append(p)
        codes.add(code)
    output = {**payload, "positions": kept}
    from app.catalog.model import Position

    Catalog([Position.model_validate(p) for p in kept], version="cleanup")
    if not kept or set(category_of(p["okpd2"]) for p in kept) != set(CATEGORIES):
        raise ValueError("Empty/missing category after filtering")
    return output, {
        "removal_counts": dict(reasons),
        "removed_positions": removed,
        "removed_characteristics": char_removed,
        "required_updates": char_updates,
        "kept_by_category": dict(collections.Counter(category_of(p["okpd2"]) for p in kept)),
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xml", type=pathlib.Path, required=True)
    ap.add_argument("--catalog", type=pathlib.Path, default=ROOT / "data/catalog/ktru_xml.json")
    ap.add_argument("--as-of", default="2026-10-11T00:00:00+03:00")
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()
    as_of = stamp(args.as_of)
    raw = args.catalog.read_bytes()
    payload = json.loads(raw)
    wanted = {
        p["code"]
        for p in payload["positions"]
        if category_of(p["okpd2"]) and not is_service(p["name"])
    }
    outdir = args.catalog.parent / "maintenance"
    outdir.mkdir(exist_ok=True)
    cache = outdir / "xml-metadata.json"
    # Cache is only reused after explicit validation of the complete source file inventory.
    inventory = [
        {"path": str(p), "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
        for p in sorted(args.xml.rglob("*"))
        if p.suffix.lower() in (".xml", ".zip")
    ]
    cached = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else None
    if cached and cached["inventory"] == inventory and cached["wanted"] == sorted(wanted):
        metadata, scan = cached["metadata"], cached["scan"]
    else:
        metadata, scan = scan_xml(args.xml, wanted)
        cache.write_text(
            json.dumps(
                {
                    "inventory": inventory,
                    "wanted": sorted(wanted),
                    "metadata": metadata,
                    "scan": scan,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    output, report = clean(payload, metadata, as_of)
    report["xml_value_validation"] = verify_values(output, metadata)
    report.update(
        {
            "as_of": args.as_of,
            "input_positions": len(payload["positions"]),
            "output_positions": len(output["positions"]),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "source_xml_snapshot": "2026-10-03/2026-10-05 organizer export; no live EIS query",
            "document": "Описание структуры xml с КТРУ.docx; nsiKTRUNew/data and characteristic tables",  # noqa: E501
            "scope_prefixes": CATEGORIES,
            "xml_scan": scan,
            "future_mandatory_start_kept": [
                c
                for c in (p["code"] for p in output["positions"])
                if metadata[c]["applicationDateStart"]
                and stamp(metadata[c]["applicationDateStart"]) > as_of
            ],
            "templates_kept": sum(
                metadata[p["code"]]["isTemplate"] in ("true", "1") for p in output["positions"]
            ),
            "policy": [
                "Newest version first, then status/actual/cancellation/end date",
                "Missing position actual allowed by FTP schema",
                "Future applicationDateStart is mandatory-use start, not an expiry",
                "Distinct official codes are preserved; equal names are not duplicates",
                "User scope: only isTemplate=false positions; all templates excluded",
                "noNewFeatures is not an invalidity flag",
                "Optional characteristics remain; actual=false characteristics excluded",
            ],
        }
    )
    candidate = args.out or args.catalog.with_name(args.catalog.stem + ".cleaned.json")
    if candidate.resolve() == args.catalog.resolve():
        raise ValueError("Output must be a separate file; original cannot be replaced")
    candidate.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Catalog.load([candidate])
    report["output_sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if args.catalog.read_bytes() != raw:
        raise ValueError("Original catalog changed during audit")
    report["applied"] = False
    report["original_unchanged"] = True
    report["output_file"] = str(candidate.resolve())
    (outdir / "cleanup-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "input_positions",
                    "output_positions",
                    "removal_counts",
                    "kept_by_category",
                    "templates_kept",
                    "applied",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
