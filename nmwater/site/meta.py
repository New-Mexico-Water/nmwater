"""Site name, licence block and the short text describing a river (page meta description)."""

from __future__ import annotations
import html


E = html.escape


DEFAULT_SITE = {"name": "New Mexico Water", "base_url": None, "locale": "en_US", "twitter": None, "license": None,
                "attribution": "New Mexico Water", "attribution_url": None}


def license_block(site: dict) -> dict | None:
    """The licence our contribution is published under, for the data bundle (None when none is configured)."""
    if not site.get("license"):
        return None
    return {"spdx": "CC-BY-SA-4.0" if "by-sa/4.0" in site["license"] else None, "url": site["license"],
            "attribution": site.get("attribution") or site["name"], "attribution_url": site.get("attribution_url"),
            "scope": "Our reports, charts, maps, text and derived data files. Upstream data keep their own terms; each page lists its sources.",
            "not_covered": ["The logo and icon", "Upstream source data"]}


def site_config(cfg: dict) -> dict:
    s = {**DEFAULT_SITE, **(cfg.get("site") or {})}
    if s["base_url"] and not s["base_url"].endswith("/"):
        s["base_url"] += "/"
    return s


def trim(text: str, n: int = 158) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def status_line(rows: list[dict]) -> str:
    """Last week's rating in words: one segment's class, or how many segments were below normal."""
    rated = [x for x in rows if x["cls"]]
    if not rated:
        return ""
    if len(rows) == 1:
        return f"Last week's flow: {rated[0]['cls']}."
    below = sum("below" in x["cls"] for x in rated)
    return (f"Last week {below} of {len(rated)} segments were below normal." if below
            else f"Last week none of its {len(rated)} rated segments was below normal.")


def description(river: str, rows: list[dict], tabs: list[str], as_of) -> str:
    what = ["weekly streamflow", "drying"]
    if "quality" in tabs:
        what.append("water temperature")
    if "watershed" in tabs:
        what.append("rainfall, snowpack and drought")
    status = status_line(rows)
    when = f"Data through {as_of:%b} {as_of.day}, {as_of.year}."
    for n in range(len(what), 0, -1):                  # drop topics before dropping the status
        topics = what[0] if n == 1 else ", ".join(what[:n - 1]) + " and " + what[n - 1]
        text = f"The {river}, New Mexico: {topics} by watershed segment. {status} {when}".replace("  ", " ")
        if len(text) <= 160:
            return text
    return trim(text)
