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
                               [--fetch-timeout s]

Check runs are read with ``gh api`` (GH_TOKEN in the environment). A check name can have several
runs on one commit (the metadata gate runs once per pull request edit); the newest run, the
highest id, stands for the name, as in branch protection. The gate fails when a check
run ended in anything else, when no check run besides its own exists, and when the others
have not completed before the timeout.

The timeout is a wall-clock deadline, not a number of polls: the time each ``gh api`` call takes
counts against it, and each call is killed after ``--fetch-timeout`` seconds. A call that fails
or hangs is retried on the next poll; when the deadline passes, the gate fails and names the
check runs still pending, or the last error when no poll succeeded.

A check run counts as completed when its status says so, or when it carries a conclusion and a
completion time: the forge has left a finished run at ``in_progress`` before, and the gate then
waited out its whole timeout on it. A job that ``needs`` another one registers its check run only
when it is queued, so the gate passes only after two consecutive polls agree that nothing is
pending and see the same check names.
Exit status: 0 when every check run passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


class FetchError(RuntimeError):
    """``gh api`` failed or did not answer in time."""


def fetch(repo: str, ref: str, timeout: float) -> list[dict]:
    """Every check run on the ref, all pages, as {id, name, status, conclusion}."""
    try:
        out = _gh(repo, ref, timeout)
    except subprocess.TimeoutExpired:
        raise FetchError(f"gh api did not answer within {timeout:g}s") from None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()[-1:] or [""]
        raise FetchError(f"gh api failed (exit {exc.returncode}): {detail[0]}") from None
    return latest([json.loads(line) for line in out.splitlines() if line.strip()])


def _gh(repo: str, ref: str, timeout: float) -> str:
    return subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/commits/{ref}/check-runs?per_page=100",
            "--jq",
            ".check_runs[] | {id, name, status, conclusion, completed_at}",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    ).stdout


def latest(runs: list[dict]) -> list[dict]:
    """The newest run of each check name; an earlier run of the same name is superseded."""
    newest: dict[str, dict] = {}
    for run in runs:
        if run["name"] not in newest or run["id"] > newest[run["name"]]["id"]:
            newest[run["name"]] = run
    return list(newest.values())


def finished(run: dict) -> bool:
    """Completed by status, or by a conclusion and completion time under a stale status."""
    if run["status"] == "completed":
        return True
    return run.get("conclusion") is not None and bool(run.get("completed_at"))


def pending(runs: list[dict], self_name: str) -> list[str]:
    return sorted(r["name"] for r in runs if r["name"] != self_name and not finished(r))


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


def wait(args: argparse.Namespace) -> tuple[list[dict] | None, str]:
    """Poll until two consecutive polls see the same check names and none pending.

    (runs, "") when they settle; (None, reason) when the deadline passes first.
    """
    deadline = time.monotonic() + args.timeout
    # A bound on the loop as well as the clock: a poll takes at least one second.
    polls = max(2, args.timeout // max(1, args.interval) + 1)
    settled: set[str] | None = None
    reason = "no poll completed"
    for _ in range(polls):
        remaining = deadline - time.monotonic()
        try:
            runs = fetch(args.repo, args.ref, max(1.0, min(args.fetch_timeout, remaining)))
        except FetchError as exc:
            reason = str(exc)
            print(f"poll failed: {reason}", flush=True)
            settled = None
            if not _sleep_until(deadline, args.interval):
                break
            continue
        waiting = pending(runs, args.self_name)
        names = {r["name"] for r in runs}
        if not waiting and names == settled:
            return runs, ""
        if waiting:
            print(f"waiting on {len(waiting)}: {', '.join(waiting)}", flush=True)
        else:
            print(f"nothing pending among {len(names)} check runs; polling once more", flush=True)
        settled = None if waiting else names
        reason = f"still pending: {', '.join(waiting)}" if waiting else "check names still changing"
        if not _sleep_until(deadline, args.interval):
            break
    return None, reason


def _sleep_until(deadline: float, interval: int) -> bool:
    """Sleep one interval, or less when the deadline comes first; False once it has passed."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(interval, remaining))
    return time.monotonic() < deadline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--self", dest="self_name", required=True)
    parser.add_argument("--allow-skip", action="append", default=[])
    parser.add_argument("--interval", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=1500)
    parser.add_argument("--fetch-timeout", type=int, default=60)
    args = parser.parse_args(argv)

    runs, reason = wait(args)
    if runs is None:
        print(f"::error::deadline of {args.timeout}s passed; {reason}", file=sys.stderr)
        return 1
    for run in sorted(runs, key=lambda r: r["name"]):
        print(f"{run['name']}: {run['conclusion']}")
    problems = evaluate(runs, args.self_name, set(args.allow_skip))
    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
