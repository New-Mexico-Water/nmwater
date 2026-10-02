"""Site name and the licence block published in the bundle."""

from __future__ import annotations

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
