"""
Access to navegador's own documentation, shipped inside the package.

The docs are bundled in the wheel rather than left on the website so that both
answers work offline: a human running ``navegador manual`` on a plane, and — the
reason it matters more — an agent that would otherwise have to grep the
filesystem or fetch a URL to learn how to use the tool it is already holding.

Pages are addressed by slug: the path under ``docs/`` without its extension,
e.g. ``guide/mcp-integration`` or ``getting-started/quickstart``.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DOCS_DIRNAME = "docs"

_HEADING = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


class ManualError(RuntimeError):
    """Raised when documentation is missing or a page cannot be found."""


@dataclass(frozen=True)
class DocPage:
    """One documentation page."""

    slug: str
    title: str
    path: Path

    @property
    def section(self) -> str:
        """Top-level grouping (``guide``, ``api``, …); empty for top-level pages."""
        head, sep, _ = self.slug.partition("/")
        return head if sep else ""

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def to_dict(self) -> dict:
        return {"slug": self.slug, "title": self.title, "section": self.section}


def docs_root() -> Path:
    """
    Directory holding the packaged documentation.

    Raises:
        ManualError: when the package was built without its docs.
    """
    root = Path(__file__).parent / DOCS_DIRNAME
    if not root.is_dir():
        raise ManualError(
            "No documentation is bundled with this navegador installation. "
            "It ships in the wheel; a source checkout keeps it at navegador/docs/. "
            "Online: https://navegador.dev"
        )
    return root


def _title_of(path: Path, slug: str) -> str:
    """First level-1 heading, falling back to a readable form of the slug."""
    try:
        match = _HEADING.search(path.read_text(encoding="utf-8"))
    except OSError:
        match = None
    if match:
        return match.group(1)
    return slug.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()


@lru_cache(maxsize=1)
def list_pages() -> tuple[DocPage, ...]:
    """Every packaged documentation page, ordered by slug."""
    root = docs_root()
    pages = []
    for path in sorted(root.rglob("*.md")):
        slug = path.relative_to(root).with_suffix("").as_posix()
        pages.append(DocPage(slug=slug, title=_title_of(path, slug), path=path))
    return tuple(pages)


def find_page(slug: str) -> DocPage:
    """
    Resolve *slug* to a page.

    Accepts the exact slug, a trailing path fragment (``quickstart`` for
    ``getting-started/quickstart``), or a slug with a ``.md`` suffix. An
    ambiguous fragment is an error naming the candidates rather than a guess.
    """
    wanted = slug.strip().strip("/").removesuffix(".md")
    if not wanted:
        raise ManualError("No page requested.")

    pages = list_pages()
    for page in pages:
        if page.slug == wanted:
            return page

    matches = [p for p in pages if p.slug.endswith(f"/{wanted}")]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(p.slug for p in matches)
        raise ManualError(f"'{slug}' is ambiguous — did you mean one of: {names}?")

    partial = [p for p in pages if wanted in p.slug]
    if len(partial) == 1:
        return partial[0]

    available = ", ".join(p.slug for p in pages)
    raise ManualError(f"No documentation page '{slug}'. Available: {available}")


def search(query: str, limit: int = 20) -> list[dict]:
    """
    Find pages mentioning *query*, with the matching lines as context.

    Case-insensitive substring search over titles and bodies. Title matches rank
    above body matches, and pages with more hits rank above those with fewer.
    """
    needle = query.strip().lower()
    if not needle:
        return []

    results = []
    for page in list_pages():
        body = page.read()
        lines = [
            line.strip() for line in body.splitlines() if needle in line.lower() and line.strip()
        ]
        in_title = needle in page.title.lower()
        if not lines and not in_title:
            continue
        results.append(
            {
                "slug": page.slug,
                "title": page.title,
                "matches": len(lines),
                "context": lines[:3],
                "_rank": (0 if in_title else 1, -len(lines)),
            }
        )

    results.sort(key=lambda r: r["_rank"])
    for r in results:
        del r["_rank"]
    return results[:limit]
