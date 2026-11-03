"""Review of the automatic `colocated` site links: which pairs the evidence settles, and how.

The proximity step of `links.build_links` pairs sites of the same kind from different sources within 250 m. Most pairs are one gauge published twice, or a gauge and the water-quality
point at its bridge, but some are neighbours. A pair is promoted to `catalog/sites_manual.csv` only when something other than distance settles it; the rest stay `colocated`
candidates. The rules, strictest first:

  same_sensor (series)   both sites hold the same measured variable (stage, reservoir elevation or storage) over at least MIN_DAYS shared days and the daily values agree:
                         median difference within MEASURED_TOL of the larger value and correlation of at least MIN_CORR. The two sites are one instrument republished.
                         Discharge is held to the looser DISCHARGE_TOL, because two agencies apply their own rating to the same stage.
  same_sensor (id)       two OSE datasets that list the same station: the same station number at the same coordinates (`ose_arcgis:rtm:N` and `ose_meas:N`).
  same_location_different_sensor
                         the names agree on the place (NAME_MATCH of the place words, no above/below conflict) and the sites are within NEAR_M, but the series do not agree or
                         cannot be compared: a sampling point at a gauge, or two feeds of one place. It claims the place is the same, not the instrument.
  (no row)               anything else: a neighbour, a computed series, or too little evidence. The pair stays a candidate.

Wells have their own rule (`decide_well`): the same depth within WELL_DEPTH_TOL_M at NEAR_WELL_M or closer is one well measured by two programmes, and a station number
shared with a USGS well (WQP ids such as TX001-341525102435405) names the same well; nested piezometers at different depths are different wells and stay candidates.

Pure functions here; `scripts/review_site_links.py` reads the catalog, applies them and writes the rows.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

MIN_DAYS = 60            # shared days of data before a series comparison is trusted
MEASURED_TOL = 0.005     # stage, elevation, storage: median relative difference
DISCHARGE_TOL = 0.03     # discharge: two agencies' ratings differ a little
MIN_CORR = 0.99
NAME_MATCH = 0.66        # share of the place words two names have in common
NEAR_M = 100.0
GAUGE_NEAR_M = 25.0      # two gauge feeds with no series evidence must be this close
MEASURED = ("stage", "reservoir_elevation", "reservoir_storage")
SAMPLING_SOURCES = {"wqp", "nmed_st2"}     # sampling programmes, not gauges

_ABBR = {"nr": "near", "bl": "below", "blw": "below", "abv": "above", "ab": "above", "r": "river", "riv": "river", "cr": "creek", "crk": "creek", "ck": "creek",
         "res": "reservoir", "rsvr": "reservoir", "ds": "downstream", "us": "upstream", "fldwy": "floodway", "cn": "canal", "mt": "mount", "mtn": "mountain",
         "spgs": "springs", "spg": "springs", "cty": "city", "ft": "fort", "st": "saint", "rd": "road", "br": "bridge", "bdg": "bridge", "hwy": "highway"}
# words that say what kind of thing it is or where it sits relative to a place, not which place
_GENERIC = set("near at below above downstream upstream of the in on to and from nm co tx az ut river creek rio reservoir lake dam canal ditch diversion gage gauge station "
               "usgs wqp flow nmed site sampling stream channel".split())
_DERIVED = {"plus", "minus", "pumpage", "computed", "adjusted", "combined", "total", "net"}      # a computed series, not the gauge itself
_DOWN, _UP = {"below", "downstream"}, {"above", "upstream"}


def words(name: str) -> list[str]:
    s = re.sub(r"[^a-z0-9 ]", " ", str(name).lower().replace("@", " at "))
    return [_ABBR.get(w, w) for w in s.split()]


def place_words(name: str) -> set[str]:
    return {w for w in words(name) if w not in _GENERIC and not w.isdigit() and len(w) > 1}


def name_match(a: str, b: str) -> float:
    """Share of the place words the two names have in common (0 when either has none, when one says above and the other below the same place, or when only one is a computed series such as flow plus pumpage)."""
    wa, wb = set(words(a)), set(words(b))
    if (wa & _DOWN and wb & _UP) or (wa & _UP and wb & _DOWN):
        return 0.0
    if bool(wa & _DERIVED) != bool(wb & _DERIVED):
        return 0.0
    pa, pb = place_words(a), place_words(b)
    if not pa or not pb:
        return 0.0
    return len(pa & pb) / len(pa | pb)


def same_name(a: str, b: str) -> bool:
    """True when one name's place words are all in the other's ("Larga" in "Acequia Larga") or the two run together to the same word ("Left Hand", "Lefthand")."""
    pa, pb = place_words(a), place_words(b)
    if not pa or not pb:
        return False
    return pa <= pb or pb <= pa or "".join(sorted(pa)) == "".join(sorted(pb)) or "".join(words(a)) == "".join(words(b))


@dataclass
class Agreement:
    variable: str
    days: int
    median_rel: float       # median of |x - y| / max(|x|, |y|) over the shared days
    corr: float


def agrees(ag: Agreement) -> bool:
    if ag.days < MIN_DAYS or math.isnan(ag.median_rel) or math.isnan(ag.corr):
        return False
    tol = MEASURED_TOL if ag.variable in MEASURED else DISCHARGE_TOL if ag.variable == "discharge" else None
    return tol is not None and ag.median_rel <= tol and ag.corr >= MIN_CORR


def _is_gauge(source: str) -> bool:
    return source not in SAMPLING_SOURCES


def decide(a: dict, b: dict, metres: float, agreements: list[Agreement]) -> tuple[str, str] | None:
    """(link_type, evidence) for a pair, or None when the evidence does not settle it.
    `a` and `b`: {"site_uid", "name", "source"}. `agreements`: one per variable both sites hold."""
    if a["source"] == b["source"]:
        return None
    ok = [g for g in agreements if agrees(g)]
    if ok:
        g = max(ok, key=lambda x: x.days)
        # a pair that agrees on stage but not on discharge is one instrument with two ratings; say so
        diff = [x for x in agreements if x.variable == "discharge" and not agrees(x)]
        extra = f"; their discharge differs ({diff[0].median_rel:.0%} median) because each applies its own rating" if diff else ""
        return ("same_sensor", f"Same {g.variable.replace('_', ' ')} record: {g.days:,} shared days, median difference {g.median_rel:.2%}, correlation {g.corr:.4f}{extra}. "
                               f"Either series stands for both; do not add them")
    ids = {x["source"]: x["site_uid"].split(":", 1)[1] for x in (a, b)}
    # the same station number is not enough: two datasets can reuse a number for different meters, so the names must also be the same meter's name
    if set(ids) == {"ose_arcgis", "ose_meas"} and ids["ose_arcgis"] == f"rtm:{ids['ose_meas']}" and metres <= 10 and same_name(a["name"], b["name"]):
        return ("same_sensor", f"Station number {ids['ose_meas']} in both OSE datasets at the same coordinates; ose_arcgis lists the station, ose_meas holds the readings")
    m = name_match(a["name"], b["name"])
    both_gauges = _is_gauge(a["source"]) and _is_gauge(b["source"])
    if m >= NAME_MATCH and (metres <= NEAR_M if not both_gauges else metres <= GAUGE_NEAR_M):
        what = "a gauge and a sampling point" if not both_gauges else "two feeds"
        compared = "series do not agree" if any(not agrees(g) and g.days >= MIN_DAYS for g in agreements) else "no series in common to compare"
        return ("same_location_different_sensor", f"Names agree on the place ({m:.0%} of place words), {metres:.0f} m apart: {what} at one place; {compared}")
    return None


WELL_DEPTH_TOL_M = 0.3   # one foot
NEAR_WELL_M = 10.0


def decide_well(a: dict, b: dict, metres: float, depth_a: float | None, depth_b: float | None) -> tuple[str, str] | None:
    """(link_type, evidence) for two wells from different sources, or None. A well is the same well when the depths agree and the sites are close, or when one carries the
    other's USGS station number in its id and the depths do not disagree. It is `same_location_different_sensor` (the same well, two programmes), never `same_sensor`: no
    water-level series were compared."""
    if a["source"] == b["source"]:
        return None
    known = depth_a is not None and depth_b is not None and not (math.isnan(depth_a) or math.isnan(depth_b))
    gap = abs(depth_a - depth_b) if known else None
    if known and gap > WELL_DEPTH_TOL_M:
        return None
    for usgs, other in ((a, b), (b, a)):
        n = usgs["site_uid"].split(":", 1)[1]
        if usgs["source"] == "usgs" and n.isdigit() and len(n) >= 8 and other["site_uid"].split(":", 1)[1].endswith("-" + n) and metres <= 25:
            d = f"depth {depth_a:.1f} m in both" if known else "depth not recorded in one"
            return ("same_location_different_sensor", f"The id of {other['site_uid']} carries USGS station number {n}: the same well, {metres:.0f} m apart, {d}")
    if known and metres <= NEAR_WELL_M:
        return ("same_location_different_sensor", f"Same well in two programmes: depth {depth_a:.1f} m and {depth_b:.1f} m (within {WELL_DEPTH_TOL_M} m), {metres:.1f} m apart")
    return None
