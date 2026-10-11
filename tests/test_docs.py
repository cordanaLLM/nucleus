"""Test suite for documentation portal integrity and mkdocs navigation."""

import fnmatch
from pathlib import Path
import re
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


def test_install_page_matches_release_workflow():
    """Ensure docs/install.md and README.md match release workflow signing and publishing contracts."""
    install_file = REPO_ROOT / "docs" / "install.md"
    assert install_file.exists(), "docs/install.md must exist"
    install_content = install_file.read_text(encoding="utf-8")

    readme_file = REPO_ROOT / "README.md"
    assert readme_file.exists(), "README.md must exist"
    readme_content = readme_file.read_text(encoding="utf-8")

    workflow_file = REPO_ROOT / ".github" / "workflows" / "publish-release.yml"
    assert workflow_file.exists(), ".github/workflows/publish-release.yml must exist"
    with open(workflow_file, "r", encoding="utf-8") as f:
        workflow = yaml.load(f, Loader=MkDocsYamlLoader)

    # (a) Verify certificate identity workflow path
    signer_identity_raw = None
    for step in workflow.get("jobs", {}).get("publish", {}).get("steps", []):
        env = step.get("env", {})
        if "SIGNER_IDENTITY" in env:
            signer_identity_raw = env["SIGNER_IDENTITY"]
            break
    assert signer_identity_raw is not None, "SIGNER_IDENTITY missing in publish-release.yml"

    m = re.search(r"https://github\.com/\$\{\{\s*github\.repository\s*\}\}/([^@]+)@", signer_identity_raw)
    assert m is not None, f"Could not parse workflow path from SIGNER_IDENTITY: {signer_identity_raw}"
    expected_workflow_relpath = m.group(1)
    expected_workflow_file = REPO_ROOT / expected_workflow_relpath
    assert expected_workflow_file.exists(), f"Workflow file {expected_workflow_relpath} does not exist"

    for doc_name, doc_text in [("docs/install.md", install_content), ("README.md", readme_content)]:
        id_match = re.search(
            r"--certificate-identity\s+[\"']https://github\.com/[^/]+/[^/]+/([^@\"'\s]+)@refs/tags/<release-tag>[\"']",
            doc_text,
        )
        assert id_match is not None, f"Certificate identity pattern missing in {doc_name}"
        doc_workflow_path = id_match.group(1)
        assert doc_workflow_path == expected_workflow_relpath, (
            f"Certificate identity in {doc_name} names '{doc_workflow_path}', expected '{expected_workflow_relpath}'"
        )
        assert (REPO_ROOT / doc_workflow_path).exists(), (
            f"Referenced workflow file {doc_workflow_path} in {doc_name} does not exist"
        )

    # (b) Verify OIDC issuer string exactly
    expected_issuer = "https://token.actions.githubusercontent.com"
    for doc_name, doc_text in [("docs/install.md", install_content), ("README.md", readme_content)]:
        issuer_match = re.search(r"--certificate-oidc-issuer\s+([^\s\\]+)", doc_text)
        assert issuer_match is not None, f"--certificate-oidc-issuer missing in {doc_name}"
        assert issuer_match.group(1) == expected_issuer, (
            f"OIDC issuer in {doc_name} is '{issuer_match.group(1)}', expected '{expected_issuer}'"
        )

    # (c) Every asset pattern the table names matches one 'files:' glob of 'Publish to GitHub Release'
    publish_step = None
    for step in workflow.get("jobs", {}).get("publish", {}).get("steps", []):
        if step.get("name") == "Publish to GitHub Release":
            publish_step = step
            break
    assert publish_step is not None, "Publish to GitHub Release step missing in publish-release.yml"
    files_entry = publish_step.get("with", {}).get("files", "")
    globs = [line.strip().removeprefix("output/") for line in files_entry.strip().splitlines() if line.strip()]

    table_assets = re.findall(r"^\|\s*`([^`]+)`\s*\|", install_content, re.MULTILINE)
    assert len(table_assets) == 10, f"Expected 10 table assets in docs/install.md, found {len(table_assets)}: {table_assets}"
    for asset in table_assets:
        matched = any(fnmatch.fnmatch(asset, g) for g in globs)
        assert matched, (
            f"Asset pattern '{asset}' in docs/install.md does not match any 'files:' glob in publish-release.yml: {globs}"
        )

    # (d) docs/install.md and README.md carry no kernel version literal inside the install section
    version_regex = re.compile(r"\d+\.\d+\.\d+|-rc\d+")
    m_install = version_regex.search(install_content)
    assert m_install is None, f"Kernel version literal '{m_install.group(0) if m_install else ''}' found in docs/install.md"

    readme_install_match = re.search(r"(###?\s+Install a release.*?)(?=\n###?\s+|\Z)", readme_content, re.DOTALL)
    assert readme_install_match is not None, "Install section missing in README.md"
    readme_install_section = readme_install_match.group(1)
    m_readme = version_regex.search(readme_install_section)
    assert m_readme is None, f"Kernel version literal '{m_readme.group(0) if m_readme else ''}' found in README.md install section"

