"""The reservoir part of the site bundle: normals, measures, the registry, and the validation rules."""

import json

import numpy as np
import pandas as pd
import pytest

from nmwater.derived import reservoirs as rv
from nmwater.site import reservoirs as sr
from nmwater.site import schema as sch

pytest.importorskip("jsonschema")


# ---------------------------------------------------------------------------- the registry
def test_registry_slugs_are_unique_and_every_reservoir_has_rivers():
    reg = rv.load_registry()
    slugs = [v["slug"] for v in reg.values()]
    assert len(slugs) == len(set(slugs)) == len(reg) == 24
    assert all(sr.slugify(v["name"]) == v["slug"] for v in reg.values())
    assert all(isinstance(v.get("river_slugs"), list) and v["river_slugs"] for v in reg.values())
    assert {v["role"] for v in reg.values()} <= {"storage", "flood_control", "dry_flood", "diversion", "municipal"}


def test_the_six_owner_reported_reservoirs_show_acre_feet_only():
    reg = rv.load_registry()
    over = sorted(k for k, v in reg.items() if v.get("capacity_basis_override"))
    assert over == ["bluewater", "costilla", "maloya", "mcclure", "nichols", "ute"]
    assert all(sr.measure_of(reg[k]["role"], "owner_reported", True) == "storage_only" for k in over)


def test_measure_follows_the_role_and_how_well_capacity_is_known():
    assert sr.measure_of("storage", "current_table", False) == "percent_full"
    assert sr.measure_of("diversion", "high", False) == "percent_full"
    assert sr.measure_of("flood_control", "published_pool", False) == "conservation_pool"
    assert sr.measure_of("dry_flood", "current_table_unverified", False) == "flood_pool_use"
    assert sr.measure_of("storage", None, False) == "storage_only"
    assert sr.measure_of("storage", "owner_reported", False) == "storage_only"


# ---------------------------------------------------------------------------- normal for the date
def history(years, conf="high", base=lambda y, d: 50.0 + (y % 7) * 5 + 10 * np.sin(d / 58)):
    rows = []
    for y in years:
        for d in pd.date_range(f"{y}-01-01", f"{y}-12-31"):
            rows.append({"date": d, "confidence": conf if not callable(conf) else conf(y), "pct": base(y, d.dayofyear)})
    return pd.DataFrame(rows)


def test_normal_is_percentiles_of_trusted_years_by_day_of_year():
    st = history(range(1991, 2021))
    n = sr.normal_for_date(st)
    assert n is not None and len(n["years"]) == 30
    assert all(n[f"p{a}"][100] <= n[f"p{b}"][100] for a, b in ((10, 25), (25, 50), (50, 75), (75, 90)))
    assert n["p50"][100] == pytest.approx(np.median([50.0 + (y % 7) * 5 + 10 * np.sin(101 / 58) for y in range(1991, 2021)]), abs=3)
    assert n["p10"][365] > 0                                                           # Dec 31 (day 366 of the leap calendar) is covered


def test_too_few_trusted_years_means_no_normal_and_untrusted_years_are_left_out():
    assert sr.normal_for_date(history(range(2000, 2012))) is None                      # 12 years < MIN_NORMAL_YEARS
    mixed = history(range(1991, 2021), conf=lambda y: "high" if y % 2 else "low")      # half the years are 'low' confidence
    n = sr.normal_for_date(mixed)
    assert n is not None and len(n["years"]) == 15 and all(y % 2 for y in n["years"])
    assert sr.normal_for_date(history(range(1991, 2021), conf="before_elevation_record")) is None
    outside = history(range(1950, 1990))                                               # trusted but outside 1991-2020
    assert sr.normal_for_date(outside) is None


def test_feb_29_has_its_own_day_and_march_1_is_the_same_day_every_year():
    d = pd.Series(pd.to_datetime(["2023-03-01", "2024-03-01", "2024-02-29", "2024-12-31", "2023-12-31"]))
    assert list(sr.doy366(d)) == [61, 61, 60, 366, 366]


def test_owner_names_are_tidied():
    assert sr.tidy_owner("BUREAU OF RECLAMATION") == "Bureau of Reclamation"
    assert sr.tidy_owner("USACE ALBUQUERQUE DISTRICT") == "USACE Albuquerque District"
    assert sr.tidy_owner(None) is None and sr.tidy_owner(float("nan")) is None


# ---------------------------------------------------------------------------- the bundle section
from tests.test_site_bundle import edit, make_bundle                      # noqa: E402


def make_reservoirs(root, man):
    base = root / "reservoirs"
    (base / "heron-reservoir").mkdir(parents=True)
    n = 3
    dates = ["2026-09-26", "2026-09-27", "2026-09-28"]
    status = {"latest_date": "2026-09-28", "storage_af": 27000.0, "capacity_af": 400000.0, "percent": 6.9, "percentile": 0.0, "class": "much below normal", "class_floored": False,
              "normal_percent": 59.5, "storage_30_days_ago_af": 30000.0, "percent_30_days_ago": 7.5, "storage_year_ago_af": 150000.0, "percent_year_ago": 37.5,
              "capacity_confidence": "high", "days_in_flood_storage_this_year": None, "above_conservation_pool_now": None, "last_flood_storage_date": None}
    daily = {"dates": dates, "storage_af": [1.0] * n, "capacity_af": [4.0] * n, "percent": [1.0] * n, "confidence": ["high"] * n, "inflow_cfs": [None] * n, "release_cfs": [3.0] * n,
             **{f"normal_p{q}": [10.0 * q / 10] * n for q in (10, 25, 50, 75, 90)}}
    ann = {k: [None, None] for k in ("n_days", "capacity_confidence", "full_pool_af", "peak_af", "mean_af", "low_af", "peak_pct", "mean_pct", "low_pct", "peak_date", "flood_pool_af",
                                    "peak_flood_pct", "days_flood_storage", "inflow_af", "release_af", "peak_inflow_cfs", "peak_release_cfs")}
    ann["years"] = [2025, 2026]
    fill = {"schema_version": 2, "slug": "heron-reservoir", "key": "heron", "name": "Heron Reservoir", "dam": "Heron Dam", "river": "Willow Creek", "basin": "Rio Grande", "role": "storage",
            "measure": "percent_full", "river_slugs": ["rio-x"], "rivers": [{"slug": "rio-x", "name": "Rio X"}], "record": {"first_date": "1974-01-01", "last_date": "2026-09-28", "years": 53},
            "status": status, "daily": daily, "normal": {"basis": "30 years", "years": [1991], "baseline": "1991-2020", "window_days": 3}, "annual": ann,
            "capacity": {"basis": "operator_table", "primary_pool": "full", "pools": [], "eras": [], "override": None, "table": None, "validation": None},
            "provenance": {"series": [], "credits": []}, "guidance": {"summary": "s", "uses": [], "cautions": []}, "dam": {}, "files": {"csv": []}}
    (base / "heron-reservoir" / "fill.json").write_text(json.dumps(fill))
    row = {"slug": "heron-reservoir", "key": "heron", "name": "Heron Reservoir", "dam": "Heron Dam", "basin": "Rio Grande", "role": "storage", "measure": "percent_full",
           "rivers": [{"slug": "rio-x", "name": "Rio X"}], "latest_date": "2026-09-28", "storage_af": 27000.0, "capacity_af": 400000.0, "percent": 6.9, "percentile": 0.0,
           "class": "much below normal", "class_floored": False, "normal_percent": 59.5, "storage_30_days_ago_af": 30000.0, "percent_30_days_ago": 7.5, "capacity_confidence": "high",
           "days_in_flood_storage_this_year": None, "above_conservation_pool_now": None, "has_normal": True, "first_year": 1974, "stale": False}
    index = {"schema_version": 2, "generated": "2026-10-01T00:00:00+00:00", "as_of": "2026-09-28", "baseline": "1991-2020", "min_normal_years": 15, "classes": ["normal"],
             "reservoirs": [row], "files": {"index": "index.json", "csv": []}}
    (base / "index.json").write_text(json.dumps(index))
    man["reservoirs"] = {"path": "reservoirs/", "reservoirs": 1, "as_of": "2026-09-28", "files": index["files"]}
    (root / "manifest.json").write_text(json.dumps(man))
    return base


def test_reservoir_section_validates_and_catches_inconsistencies(tmp_path):
    make_bundle(tmp_path)
    man = json.loads((tmp_path / "manifest.json").read_text())
    base = make_reservoirs(tmp_path, man)
    assert sch.validate_bundle(tmp_path) == []
    f = base / "heron-reservoir"
    edit(f, "fill.json", lambda o: o["daily"].update(release_cfs=[1.0]))
    assert any("daily arrays differ" in p for p in sch.validate_bundle(tmp_path))
    edit(f, "fill.json", lambda o: o["daily"].update(release_cfs=[3.0, 3.0, 3.0]))
    edit(f, "fill.json", lambda o: o["rivers"].append({"slug": "nope", "name": "Nope"}))
    assert any("river nope is not in the bundle" in p for p in sch.validate_bundle(tmp_path))
    edit(f, "fill.json", lambda o: o.update(rivers=[{"slug": "rio-x", "name": "Rio X"}], measure="storage_only"))
    assert any("storage-only" in p for p in sch.validate_bundle(tmp_path))
    edit(f, "fill.json", lambda o: o.update(measure="percent_full", normal=None))
    assert any("rated without a normal" in p for p in sch.validate_bundle(tmp_path))
    edit(f, "fill.json", lambda o: o.update(normal={"basis": "b", "years": [1991], "baseline": "1991-2020", "window_days": 3}))
    edit(f, "fill.json", lambda o: o["status"].update(percent=7.0))
    assert any("disagree on the latest values" in p for p in sch.validate_bundle(tmp_path))
    edit(f, "fill.json", lambda o: o["status"].update(percent=6.9, class_floored=True))
    assert any("class_floored" in p for p in sch.validate_bundle(tmp_path))
