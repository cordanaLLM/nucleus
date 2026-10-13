#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Wait for every other check run on a commit, then require each one to have succeeded.

The aggregator used to accept ``skipped`` for every check, so a required job that was skipped
by a wrong condition counted as passed. Here a check run passes only with conclusion
``success``. A check named with ``--allow-skip`` may also be ``skipped``: that is for jobs a
change detector in the same run may exclude, and a separate required check (the Security
Scan Gate for Semgrep and Trivy) proves the skip was the detector's decision.

Usage: required_checks_gate.py --repo <owner/name> --ref <sha> --self <check name>
                               [--allow-skip <check name>]... [--interval s] [--timeout s]

Check runs are read with ``gh api`` (GH_TOKEN in the environment). A check name can have several
runs on one commit (the metadata gate runs once per pull request edit); the newest run, the
highest id, stands for the name, as in branch protection. The gate fails when a check
run ended in anything else, when no check run besides its own exists, and when the others
have not completed before the timeout.
Exit status: 0 when every check run passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def fetch(repo: str, ref: str) -> list[dict]:
    """Every check run on the ref, all pages, as {id, name, status, conclusion}."""
    out = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/commits/{ref}/check-runs?per_page=100",
            "--jq",
            ".check_runs[] | {id, name, status, conclusion}",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return latest([json.loads(line) for line in out.splitlines() if line.strip()])


def latest(runs: list[dict]) -> list[dict]:
    """The newest run of each check name; an earlier run of the same name is superseded."""
    newest: dict[str, dict] = {}
    for run in runs:
        if run["name"] not in newest or run["id"] > newest[run["name"]]["id"]:
            newest[run["name"]] = run
    return list(newest.values())


def pending(runs: list[dict], self_name: str) -> list[str]:
    return sorted(r["name"] for r in runs if r["name"] != self_name and r["status"] != "completed")


def evaluate(runs: list[dict], self_name: str, allow_skip: set[str]) -> list[str]:
    """One line per check run that did not pass; empty when all passed."""
    problems = []
    others = [r for r in runs if r["name"] != self_name]
    if not others:
        return [f"no check run besides {self_name!r}; nothing was verified"]
    for run in others:
        conclusion = run["conclusion"]
        if conclusion == "success" or (conclusion == "skipped" and run["name"] in allow_skip):
            continue
        problems.append(f"{run['name']}: {conclusion}")
    return problems


def wait(args: argparse.Namespace) -> list[dict] | None:
    """Poll until no other check run is pending; None when the timeout passes first."""
    polls = max(1, args.timeout // max(1, args.interval) + 1)
    for attempt in range(polls):
        runs = fetch(args.repo, args.ref)
        waiting = pending(runs, args.self_name)
        if not waiting:
            return runs
        print(f"waiting on {len(waiting)}: {', '.join(waiting)}", flush=True)
        if attempt + 1 < polls:
            time.sleep(args.interval)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--self", dest="self_name", required=True)
    parser.add_argument("--allow-skip", action="append", default=[])
    parser.add_argument("--interval", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=1500)
    args = parser.parse_args(argv)

    runs = wait(args)
    if runs is None:
        print(f"::error::check runs still pending after {args.timeout}s", file=sys.stderr)
        return 1
    for run in sorted(runs, key=lambda r: r["name"]):
        print(f"{run['name']}: {run['conclusion']}")
    problems = evaluate(runs, args.self_name, set(args.allow_skip))
    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
