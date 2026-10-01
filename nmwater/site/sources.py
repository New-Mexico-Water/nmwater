"""One numbered source list per river page, shared by every researched statement on it."""

from __future__ import annotations


class Sources:
    """Numbers sources 1, 2, 3 ... in order of first use, one number per URL, across research files."""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self._by_url: dict[str, int] = {}

    def ids(self, refs, table: dict) -> list[int]:
        """Numbers for refs (ids into `table`, a research file's `sources` mapping); unknown ids are skipped."""
        out: list[int] = []
        for i in refs or []:
            s = table.get(i) or table.get(str(i)) or (table.get(int(i)) if str(i).isdigit() else None)
            if not s or not s.get("url"):
                continue
            n = self._by_url.get(s["url"])
            if n is None:
                n = self._by_url[s["url"]] = len(self.items) + 1
                self.items.append({"id": n, "title": s.get("title") or s["url"], "publisher": s.get("publisher") or "",
                                   "url": s["url"], "accessed": str(s.get("accessed") or "")})
            if n not in out:
                out.append(n)
        return out

    def statements(self, items, table: dict) -> list[dict]:
        """[{text, sources: [n]}] from a research section."""
        return [{"text": " ".join(str(x["text"]).split()), "sources": self.ids(x.get("sources"), table)} for x in items or []]
