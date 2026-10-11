"""Test suite for documentation portal integrity and mkdocs navigation."""

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


PATH_MENTION_PATTERN = re.compile(
    r'(?<![A-Za-z0-9_/.-])(?:\./)?((?:scripts|kconfig|patches|tests|docker)/[^\s`"\'\)\]>]+|\.github/workflows/[^\s`"\'\)\]>]+\.ya?ml)'
)

ALLOW_LIST_ABSENT_PATHS = [
    ("docs/principles.md", "kconfig/base.config", "document explicitly states there is no kconfig/base.config"),
    ("docs/principles.md", "scripts/kconfig/merge_config.sh", "upstream kernel source tree script invoked by merge-config.sh"),
    ("docs/streams.md", "scripts/kconfig/merge_config.sh", "upstream kernel source tree script invoked by merge-config.sh"),
    ("docs/packaging.md", "scripts/package/mkdebian", "upstream kernel source tree packaging script"),
    ("docs/packaging.md", "scripts/package/builddeb", "upstream kernel source tree packaging script"),
    ("docs/patches.md", "scripts/checkpatch.pl", "upstream kernel source tree coding style checker"),
    ("docs/hardware/amd-rocm.md", "kconfig/drivers", "document explicitly states no kconfig/drivers directory exists"),
    ("docs/hardware/nvidia-blackwell.md", "kconfig/drivers", "document explicitly states no kconfig/drivers directory exists"),
]


def test_documented_paths_exist():
    """Ensure documented filesystem and workflow paths in non-ADR docs exist on disk."""
    allow_set = {(doc, path) for doc, path, _ in ALLOW_LIST_ABSENT_PATHS}
    docs_files = [REPO_ROOT / "README.md", REPO_ROOT / "CONTRIBUTING.md"] + sorted(
        [p for p in (REPO_ROOT / "docs").rglob("*.md") if not p.is_relative_to(REPO_ROOT / "docs" / "adr")]
    )

    missing = []
    used = set()
    for doc in docs_files:
        rel_doc = str(doc.relative_to(REPO_ROOT))
        for line_no, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
            for match in PATH_MENTION_PATTERN.finditer(line):
                candidate = match.group(1).rstrip(".,;:)\"\'`>?]")
                candidate = re.sub(r":\d+(?::\d+)?$", "", candidate)
                if "::" in candidate:
                    candidate = candidate.split("::")[0]
                if "#" in candidate:
                    candidate = candidate.split("#")[0]
                candidate = candidate.rstrip("/")
                if any(char in candidate for char in ("<", "*", "{", "}", ">")):
                    continue
                if (rel_doc, candidate) in allow_set:
                    used.add((rel_doc, candidate))
                    continue
                if not (REPO_ROOT / candidate).exists():
                    missing.append(f"{rel_doc}:{line_no} -> {candidate}")

    assert not missing, "Documented paths do not exist:\n" + "\n".join(missing)
    stale = sorted(allow_set - used)
    assert not stale, f"Allow-list entries that no document needs any more: {stale}"


STREAM_VERSION_PATTERN = re.compile(r"\d+\.\d+(\.\d+|-rc\d+|-rt)")


def test_issue_templates_carry_no_stream_version():
    """Ensure issue templates name streams without carrying stale kernel version numbers."""
    templates = sorted((REPO_ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"))
    assert templates, "Issue templates must exist"

    failures = []
    for tmpl in templates:
        data = yaml.safe_load(tmpl.read_text(encoding="utf-8"))
        for field in data.get("body", []):
            attributes = field.get("attributes", {})
            if STREAM_VERSION_PATTERN.search(str(attributes.get("label", ""))):
                failures.append(f"{tmpl.name}: label '{attributes['label']}' carries version literal")
            for option in attributes.get("options", []):
                label = option if isinstance(option, str) else option.get("label", "")
                if STREAM_VERSION_PATTERN.search(label):
                    failures.append(f"{tmpl.name}: option '{label}' carries version literal")

    assert not failures, "Issue template options carry version literals:\n" + "\n".join(failures)


def test_security_policy_paths_exist():
    """Ensure all workflow and script paths referenced in security documentation exist."""
    security_files = [REPO_ROOT / "SECURITY.md", REPO_ROOT / "docs" / "community" / "security.md"]
    workflow_re = re.compile(r"\.github/workflows/[a-zA-Z0-9_.-]+\.ya?ml")
    script_re = re.compile(r"scripts/[a-zA-Z0-9_.-]+")

    for sec_file in security_files:
        assert sec_file.exists(), f"{sec_file} must exist"
        content = sec_file.read_text(encoding="utf-8")

        for wf_match in workflow_re.findall(content):
            target = REPO_ROOT / wf_match
            assert target.exists(), f"Workflow referenced in {sec_file.name} does not exist: {wf_match}"

        for script_match in script_re.findall(content):
            clean_script = script_match.rstrip(".,;:)\"'")
            target = REPO_ROOT / clean_script
            assert target.exists(), f"Script referenced in {sec_file.name} does not exist: {clean_script}"


def test_security_md_mirrored_in_community():
    """Ensure docs/community/security.md hand-mirrors SECURITY.md except for versions.json link form."""
    root_security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    comm_security = (REPO_ROOT / "docs" / "community" / "security.md").read_text(encoding="utf-8")

    norm_root = root_security.replace(
        "[`versions.json`](versions.json)",
        "[`versions.json`](https://github.com/cordanaLLM/nucleus/blob/main/versions.json)",
    )
    assert norm_root == comm_security, (
        "docs/community/security.md must mirror SECURITY.md (differing only in versions.json link form)"
    )
