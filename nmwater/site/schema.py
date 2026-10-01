"""Validate a site data bundle against docs/site-data/v1 and check what a schema cannot say."""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "docs" / "site-data" / "v1"
RIVER_FILES = {"summary.json": "summary", "flow.json": "flow", "normal.json": "normal", "drying.json": "drying",
               "quality.json": "quality", "watershed.json": "watershed", "notes.json": "notes"}


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text())


def _validate(data, name: str, where: str) -> list[str]:
    from jsonschema import Draft202012Validator

    v = Draft202012Validator(load_schema(name))
    return [f"{where}: {'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message[:200]}" for e in sorted(v.iter_errors(data), key=str)[:8]]


def river_consistency(d: Path, summary: dict, files: dict[str, dict]) -> list[str]:
    """Things a schema cannot say: parallel arrays, referenced files, cited sources."""
    out: list[str] = []
    w = d.name
    f = files.get("flow")
    if f:
        n = len(f["weeks"])
        out += [f"{w}/flow.json: series {s['name']!r} has {len(s['v'])} values and {len(s['n'])} counts for {n} weeks"
                for s in f["series"] if len(s["v"]) != n or len(s["n"]) != n]
    nm = files.get("normal")
    if nm:
        n = len(nm["weeks"])
        out += [f"{w}/normal.json: segment {s['name']!r} arrays do not match {n} weeks" for s in nm["segments"]
                if len(s["cls"]) != n or len(s["pct"]) != n]
        out += [f"{w}/normal.json: gauge {g['name']!r} arrays do not match {n} weeks" for g in nm["gauges"]
                if any(len(g[k]) != n for k in ("mean_cfs", "p10", "p25", "p50", "p75", "p90", "cls"))]
    dr = files.get("drying")
    if dr:
        n = len(dr["years"])
        out += [f"{w}/drying.json: segment {s['name']!r} arrays do not match {n} years" for s in dr["segments"]
                if len(s["dry_days"]) != n or len(s["days_with_data"]) != n]
    ws = files.get("watershed")
    if ws:
        for s in ws["segments"]:
            p, dd, t, sn, dg = s["precipitation"], s["daily_precipitation"], s["temperature"], s["snow"], s["drought"]
            for label, a, b in (("precipitation", p["months"], [p["total_in"], p["normal_in"], p["days_counted"], p["complete"]]),
                                ("daily_precipitation", dd["dates"], [dd["inches"]]), ("temperature", t["months"], [t["anomaly_c"]]),
                                ("drought", dg["dates"], [dg["dsci"]])):
                if any(len(x) != len(a) for x in b):
                    out.append(f"{w}/watershed.json: {s['name']!r} {label} arrays differ in length")
            if any(len(x["inches"]) != len(sn["weeks_of_water_year"]) for x in sn["winters"]) or len(sn["median_2004_2025_in"]) != len(sn["weeks_of_water_year"]):
                out.append(f"{w}/watershed.json: {s['name']!r} snow arrays differ in length")
    # files named in the summary exist, tabs have their files, cited sources are defined
    for key, name in summary["files"].items():
        for fn in (name if isinstance(name, list) else [name]):
            if not (d / fn).exists():
                out.append(f"{w}: summary.files.{key} names {fn}, which is missing")
    needs = {"flow": "flow", "normal": "normal", "drying": "drying", "quality": "quality", "watershed": "watershed", "notes": "notes"}
    out += [f"{w}: tab {t!r} has no file" for t in summary["tabs"] if t in needs and needs[t] not in summary["files"]]
    ids = {s["id"] for s in summary["sources"]}
    cited = [n for sec in ("culture", "habitat", "users") for x in summary[sec] for n in x["sources"]]
    cited += [n for x in summary["description"]["background"] for n in x["sources"]]
    cited += [n for x in summary["acequias"]["items"] for n in x["sources"]]
    cited += [n for x in summary["reservoirs"] if x["note"] for n in x["note"]["sources"]]
    out += [f"{w}: statement cites source {n}, which is not in the source list" for n in sorted(set(cited) - ids)]
    if len(ids) != len(summary["sources"]) or ids != set(range(1, len(ids) + 1)):
        out.append(f"{w}: source ids are not 1..n")
    svg = (d / summary["map"]["file"]).read_text() if (d / summary["map"]["file"]).exists() else ""
    if "<svg" not in svg or 'data-river="' not in svg:
        out.append(f"{w}: map.svg is not the zoomable map")
    if [s["name"] for s in summary["segments"]] != [k["name"] for k in summary["map"]["key"]]:
        out.append(f"{w}: the map key and the segments differ")
    return out


def validate_bundle(root: Path) -> list[str]:
    """Problems found in the bundle at root (an empty list means it conforms)."""
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return ["jsonschema is not installed (uv sync), so the bundle was not validated"]
    problems: list[str] = []
    man_path = root / "manifest.json"
    if not man_path.exists():
        return [f"{man_path} is missing"]
    man = json.loads(man_path.read_text())
    problems += _validate(man, "manifest", "manifest.json")
    for e in man.get("rivers", []):
        d = root / e["path"]
        if not d.is_dir():
            problems.append(f"manifest lists {e['slug']}, but {d} is missing")
            continue
        files = {}
        before = len(problems)
        for fn, name in RIVER_FILES.items():
            p = d / fn
            if not p.exists():
                continue
            data = json.loads(p.read_text())
            files[name] = data
            problems += _validate(data, name, f"{e['slug']}/{fn}")
        if "summary" not in files:
            problems.append(f"{e['slug']}: summary.json is missing")
            continue
        if len(problems) > before:                           # the cross-file checks assume the files match their schemas
            continue
        try:
            problems += river_consistency(d, files["summary"], files)
        except (KeyError, TypeError, IndexError) as err:
            problems.append(f"{e['slug']}: consistency checks could not run ({type(err).__name__}: {err})")
    return problems


if __name__ == "__main__":
    import sys

    found = validate_bundle(Path(sys.argv[1] if len(sys.argv) > 1 else "dist/site-data"))
    for line in found:
        print(line)
    print(f"{len(found)} problem(s)" if found else "bundle matches the schema")
    sys.exit(1 if found else 0)
