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


def _extract_nav_paths(nav_item):
    paths = []
    if isinstance(nav_item, str):
        paths.append(nav_item)
    elif isinstance(nav_item, dict):
        for val in nav_item.values():
            paths.extend(_extract_nav_paths(val))
    elif isinstance(nav_item, list):
        for item in nav_item:
            paths.extend(_extract_nav_paths(item))
    return paths


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
    assert len(adrs) >= 6, "ADR 0001 to 0006 must exist"
    numbers = [adr.name[:4] for adr in adrs]
    assert len(numbers) == len(set(numbers)), "ADR numbers must be unique"
    for adr in adrs:
        content = adr.read_text(encoding="utf-8")
        for section in ("## Status", "## Context", "## Decision", "## Consequences"):
            assert section in content, f"{adr.name} lacks {section}"
        assert f"({adr.name})" in index, f"{adr.name} is missing from docs/adr/README.md"
        assert f"adr/{adr.name}" in nav_paths, f"{adr.name} is missing from mkdocs.yml nav"
