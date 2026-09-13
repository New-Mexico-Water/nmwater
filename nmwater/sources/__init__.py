"""Auto-import every source module so @register populates the registry."""

from __future__ import annotations

import importlib
import logging
import pkgutil

from .base import SOURCES, Context, FetchSummary, Source, SourceUnavailable, register  # noqa: F401

log = logging.getLogger("nmwater.sources")


def load_all() -> dict[str, type[Source]]:
    for m in pkgutil.iter_modules(__path__):
        if m.name.startswith("_") or m.name == "base":
            continue
        try:
            importlib.import_module(f"{__name__}.{m.name}")
        except Exception as e:
            log.warning("could not import source module %s: %s", m.name, e)
    return SOURCES
