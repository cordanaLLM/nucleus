"""Test suite for documentation portal integrity and mkdocs navigation."""

from pathlib import Path
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


class MkDocsYamlLoader(yaml.SafeLoader):
    pass


MkDocsYamlLoader.add_multi_constructor("!python/name:", lambda loader, suffix, node: None)
MkDocsYamlLoader.add_multi_constructor("tag:yaml.org,2002:python/name:", lambda loader, suffix, node: None)


def _load_mkdocs_config():
    mkdocs_file = REPO_ROOT / "mkdocs.yml"
    with open(mkdocs_file, "r", encoding="utf-8") as f:
        return yaml.load(f, Loader=MkDocsYamlLoader)


def test_mkdocs_config_exists():
    """Ensure mkdocs.yml exists and has material theme."""
    mkdocs_file = REPO_ROOT / "mkdocs.yml"
    assert mkdocs_file.exists(), "mkdocs.yml must exist"
    config = _load_mkdocs_config()
    assert config.get("theme", {}).get("name") == "material"
    assert config.get("site_name") == "nucleus"


# Upper bound on the entries one mkdocs nav tree may hold; the walk below is iterative
# and bounded (HISS-01, HISS-02), and fails instead of truncating when a tree exceeds it.
MAX_NAV_ENTRIES = 4096


def _extract_nav_paths(nav_item):
    """Return every page path in a mkdocs nav tree, in document order."""
    paths = []
    pending = [nav_item]
    for _ in range(MAX_NAV_ENTRIES):
        if not pending:
            return paths
        item = pending.pop()
        if isinstance(item, str):
            paths.append(item)
        elif isinstance(item, dict):
            pending.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item))
    assert not pending, f"mkdocs nav holds more than {MAX_NAV_ENTRIES} entries"
    return paths


def test_extract_nav_paths_keeps_document_order():
    """The iterative nav walk returns the pages of nested sections in document order."""
    nav = [
        "index.md",
        {"Section": ["a.md", {"Sub": ["b.md", "c.md"]}, "d.md"]},
        {"Other": "e.md"},
    ]
    assert _extract_nav_paths(nav) == ["index.md", "a.md", "b.md", "c.md", "d.md", "e.md"]


def test_mkdocs_nav_files_exist():
    """Ensure all markdown files declared in mkdocs.yml navigation exist on disk."""
    config = _load_mkdocs_config()

    nav = config.get("nav", [])
    nav_paths = _extract_nav_paths(nav)
    assert len(nav_paths) >= 15, f"Expected at least 15 documentation pages, found {len(nav_paths)}"

    docs_dir = REPO_ROOT / "docs"
    for rel_path in nav_paths:
        target_file = docs_dir / rel_path
        assert target_file.exists(), f"Document referenced in nav does not exist: {rel_path}"
        assert target_file.stat().st_size > 0, f"Document is empty: {rel_path}"


def test_adr_files_complete():
    """Ensure every ADR carries the proper sections and is indexed and in the navigation."""
    adr_dir = REPO_ROOT / "docs" / "adr"
    index = (adr_dir / "README.md").read_text(encoding="utf-8")
    nav_paths = _extract_nav_paths(_load_mkdocs_config().get("nav", []))
    adrs = sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md"))
    assert len(adrs) >= 7, "ADR 0001 to 0007 must exist"
    numbers = [adr.name[:4] for adr in adrs]
    assert len(numbers) == len(set(numbers)), "ADR numbers must be unique"
    for adr in adrs:
        content = adr.read_text(encoding="utf-8")
        for section in ("## Status", "## Context", "## Decision", "## Consequences"):
            assert section in content, f"{adr.name} lacks {section}"
        assert f"({adr.name})" in index, f"{adr.name} is missing from docs/adr/README.md"
        assert f"adr/{adr.name}" in nav_paths, f"{adr.name} is missing from mkdocs.yml nav"
