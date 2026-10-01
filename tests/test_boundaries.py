"""The data pipeline does not present: data modules know nothing about the bundle or the website."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "nmwater"


def imports(path: Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.ImportFrom):
            base = "." * n.level + (n.module or "")
            out.add(base)
            out.update(f"{base}.{a.name}" for a in n.names)
        elif isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
    return out


def test_the_legacy_html_reports_are_gone():
    assert not (ROOT / "reports").exists()
    for p in ROOT.rglob("*.py"):
        assert not any("reports" in i.split(".") and "nmwater" in i or i.startswith("..reports") or i.startswith(".reports") for i in imports(p)), p


def test_derived_data_modules_do_not_import_the_exporter():
    for p in (ROOT / "derived").glob("*.py"):
        bad = [i for i in imports(p) if i.startswith("..site") or i.startswith("nmwater.site")]
        assert not bad, f"{p.name} imports the bundle exporter: {bad}"


def test_the_exporter_and_derived_data_write_no_html():
    for p in [*(ROOT / "derived").glob("*.py"), *(ROOT / "site").glob("*.py")]:
        text = p.read_text()
        assert "<!doctype html" not in text.lower() and "<html" not in text.lower(), f"{p} writes an HTML page"
