""".config/lint-exceptions.yaml: every ruff exclusion, adoption decline and codespell skip is a
dated exception.

An exception names one file and one rule. It expires on its category's interval and then fails
this suite, so it is a debt with a due date and never grows into a tier of unlinted files. In the
NOTICE_DAYS before its expiry an entry raises an ExpiryNotice warning, which pytest lists in the
warnings summary of every run, so the fix starts before the gate turns red.
"""

import configparser
import datetime
import tomllib
import warnings
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
EXCEPTIONS = REPO_ROOT / ".config" / "lint-exceptions.yaml"
PYPROJECT = REPO_ROOT / "pyproject.toml"
STANDARDS = REPO_ROOT / ".standards.yaml"
CODESPELLRC = REPO_ROOT / ".codespellrc"

CATEGORY_DAYS = {"B": 3, "C": 10, "D": 120}
FIELDS = {"path", "tool", "rule", "category", "entered", "expires", "reason"}
TOOLS = {"ruff-lint", "ruff-format", "adoption-decline", "codespell"}
# licence.yml already runs the REUSE lint job the ruleset requires (comment in .standards.yaml).
PERMANENT_DECLINES = {"reuse-gate"}
NOTICE_DAYS = 3


class ExpiryNotice(UserWarning):
    """An exception that still holds but expires within NOTICE_DAYS."""


def load_entries(path=EXCEPTIONS):
    return yaml.safe_load(path.read_text(encoding="utf-8"))["exceptions"]


def entry_problems(entry, today):
    """Return what is wrong with one entry on the given day; an empty list means it holds."""
    if set(entry) != FIELDS:
        return [f"fields {sorted(entry)} are not {sorted(FIELDS)}"]
    problems = []
    if entry["tool"] not in TOOLS:
        problems.append(f"unknown tool {entry['tool']!r}")
    if entry["category"] not in CATEGORY_DAYS:
        return [*problems, f"unknown category {entry['category']!r}"]
    entered = datetime.date.fromisoformat(entry["entered"])
    expires = datetime.date.fromisoformat(entry["expires"])
    if expires != entered + datetime.timedelta(days=CATEGORY_DAYS[entry["category"]]):
        problems.append(f"expiry {expires} does not match category {entry['category']}")
    if expires < today:
        problems.append(f"expired on {expires}")
    if not entry["reason"].strip():
        problems.append("empty reason")
    return problems


def expiring_soon(entries, today, notice_days=NOTICE_DAYS):
    """Return 'path rule expires on <date>' for each entry that holds today but not for long."""
    soon = []
    for entry in entries:
        expires = datetime.date.fromisoformat(entry["expires"])
        if today <= expires <= today + datetime.timedelta(days=notice_days):
            soon.append(f"{entry['path']} {entry['rule']} expires on {expires}")
    return soon


def warn_expiring(entries, today):
    for notice in expiring_soon(entries, today):
        warnings.warn(ExpiryNotice(notice), stacklevel=2)


def pairs(entries, tool):
    return {(entry["path"], entry["rule"]) for entry in entries if entry["tool"] == tool}


def test_every_entry_holds_today():
    today = datetime.date.today()
    problems = {
        f"{entry.get('path')} {entry.get('rule')}": entry_problems(entry, today)
        for entry in load_entries()
    }
    assert not {name: found for name, found in problems.items() if found}


def test_an_entry_near_its_expiry_raises_a_notice():
    warn_expiring(load_entries(), datetime.date.today())


def test_a_planted_entry_expiring_tomorrow_raises_a_notice():
    today = datetime.date(2026, 11, 5)
    entry = {"path": "pyproject.toml", "rule": "C420", "expires": "2026-11-06"}
    with pytest.warns(ExpiryNotice, match="pyproject.toml C420 expires on 2026-11-06"):
        warn_expiring([entry], today)


@pytest.mark.parametrize(
    ("expires", "noticed"),
    [
        ("2026-11-04", False),
        ("2026-11-05", True),
        ("2026-11-08", True),
        ("2026-11-09", False),
    ],
)
def test_the_notice_window_is_today_through_three_days_ahead(expires, noticed):
    entry = {"path": "pyproject.toml", "rule": "C420", "expires": expires}
    assert bool(expiring_soon([entry], datetime.date(2026, 11, 5))) is noticed


def test_every_entry_names_an_existing_file():
    missing = [
        entry["path"] for entry in load_entries() if not (REPO_ROOT / entry["path"]).is_file()
    ]
    assert not missing


def test_ruff_lint_ignores_are_exactly_the_declared_entries():
    lint = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["ruff"]["lint"]
    declared = {
        (path, rule) for path, rules in lint.get("per-file-ignores", {}).items() for rule in rules
    }
    assert declared == pairs(load_entries(), "ruff-lint")


def test_ruff_format_excludes_are_exactly_the_declared_entries():
    ruff = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["ruff"]
    declared = {(path, "format") for path in ruff.get("format", {}).get("exclude", [])}
    assert declared == pairs(load_entries(), "ruff-format")
    assert "extend-exclude" not in ruff


def codespell_skips(path=CODESPELLRC):
    config = configparser.ConfigParser()
    config.read_string(path.read_text(encoding="utf-8"))
    return {
        (item.strip(), "skip") for item in config["codespell"]["skip"].split(",") if item.strip()
    }


def test_codespell_skips_are_exactly_the_declared_entries():
    assert codespell_skips() == pairs(load_entries(), "codespell")


def test_an_undeclared_codespell_skip_is_refused(tmp_path):
    planted = tmp_path / ".codespellrc"
    text = CODESPELLRC.read_text(encoding="utf-8")
    planted.write_text(text.replace("skip = ", "skip = *.js,", 1), encoding="utf-8")
    assert codespell_skips(planted) != pairs(load_entries(), "codespell")


def test_adoption_declines_are_permanent_or_declared():
    manifest = yaml.safe_load(STANDARDS.read_text(encoding="utf-8"))
    declines = set(manifest.get("adoption", {}).get("decline", []))
    declared = {rule for _, rule in pairs(load_entries(), "adoption-decline")}
    assert declines == PERMANENT_DECLINES | declared


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"expires": "2026-10-13", "entered": "2026-10-03"}, "expired on 2026-10-13"),
        ({"expires": "2026-10-25"}, "does not match category C"),
        ({"category": "Z"}, "unknown category"),
        ({"tool": "pylint"}, "unknown tool"),
        ({"reason": " "}, "empty reason"),
    ],
)
def test_a_planted_bad_entry_is_refused(change, problem):
    entry = {
        "path": "pyproject.toml",
        "tool": "ruff-lint",
        "rule": "C420",
        "category": "C",
        "entered": "2026-10-14",
        "expires": "2026-10-24",
        "reason": "planted",
    }
    assert not entry_problems(entry, datetime.date(2026, 10, 14))
    found = entry_problems({**entry, **change}, datetime.date(2026, 10, 14))
    assert any(problem in item for item in found)


def test_an_entry_holds_on_its_expiry_day_and_fails_the_day_after():
    entry = load_entries()[0]
    expires = datetime.date.fromisoformat(entry["expires"])
    assert not entry_problems(entry, expires)
    assert entry_problems(entry, expires + datetime.timedelta(days=1)) == [f"expired on {expires}"]
