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


def precip_problems(root: Path, man: dict) -> list[str]:
    """The precipitation section: files against their schemas, parallel arrays, the map and the rivers it links to."""
    block = man.get("precipitation")
    if not block:
        return []
    base = root / block["path"]
    out: list[str] = []
    idx_path = base / block["files"]["index"]
    if not idx_path.exists():
        return [f"precipitation: {idx_path} is missing"]
    idx = json.loads(idx_path.read_text())
    out += _validate(idx, "precip_index", "precipitation/index.json")
    if out:
        return out
    svg = (base / block["files"]["map"]).read_text() if (base / block["files"]["map"]).exists() else ""
    rivers = {e["slug"] for e in man.get("rivers", [])}
    listed = {w["huc8"] for w in idx["watersheds"]}
    if len(listed) != len(idx["watersheds"]) or len(listed) != block["watersheds"]:
        out.append("precipitation: the manifest count and the index list differ")
    for w in idx["watersheds"]:
        h = w["huc8"]
        if w["nm_fraction"] > 0 and f'data-huc8="{h}"' not in svg:           # a watershed wholly outside the state has a page but is not on the map
            out.append(f"precipitation: watershed {h} is not in map.svg")
        out += [f"precipitation/{h}: river {r['slug']} is not in the bundle" for r in w["rivers"] if r["slug"] not in rivers]
        f = base / h / "precip.json"
        if not f.exists():
            out.append(f"precipitation: {f} is missing")
            continue
        d = json.loads(f.read_text())
        bad = _validate(d, "precip_watershed", f"precipitation/{h}/precip.json")
        out += bad
        if bad:
            continue
        n = len(d["months"])
        if any(len(d[k]) != n for k in ("total_in", "normal_in", "days_counted", "complete")):
            out.append(f"precipitation/{h}: monthly arrays differ in length")
        if len(d["daily"]["dates"]) != len(d["daily"]["inches"]):
            out.append(f"precipitation/{h}: daily dates and inches differ in length")
        if d["stats"] != w["stats"]:
            out.append(f"precipitation/{h}: stats differ between index.json and precip.json")
    for h in sorted({p.name for p in base.iterdir() if p.is_dir()} - listed):
        out.append(f"precipitation: directory {h} is not listed in the index")
    return out


def files_problems(root: Path) -> list[str]:
    """files.json (optional): every listed file exists with the recorded size and SHA-256, and nothing in the bundle is unlisted."""
    f = root / "files.json"
    if not f.exists():
        return []
    import hashlib

    idx = json.loads(f.read_text())
    out = _validate(idx, "files", "files.json")
    if out:
        return out
    listed = {e["path"] for e in idx["files"]}
    for e in idx["files"]:
        p = root / e["path"]
        if not p.is_file():
            out.append(f"files.json lists {e['path']}, which is missing")
        elif p.stat().st_size != e["bytes"] or hashlib.sha256(p.read_bytes()).hexdigest() != e["sha256"]:
            out.append(f"files.json: {e['path']} does not match its recorded size or hash")
    out += [f"{p.relative_to(root).as_posix()} is in the bundle but not in files.json" for p in sorted(root.rglob("*"))
            if p.is_file() and p.name != "files.json" and p.relative_to(root).as_posix() not in listed][:10]
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
    problems += precip_problems(root, man)
    problems += files_problems(root)
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
