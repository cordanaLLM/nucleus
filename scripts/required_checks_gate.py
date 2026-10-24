#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Evaluate every other check run on a commit, in one-shot status mode or polling wait mode.

The aggregator used to accept ``skipped`` for every check, so a required job that was skipped
by a wrong condition counted as passed. Here a check run passes only with conclusion
``success``. A check named with ``--allow-skip`` may also be ``skipped``: that is for jobs a
change detector in the same run may exclude, and a separate required check (the Security
Scan Gate for Semgrep and Trivy) proves the skip was the detector's decision.

Usage: required_checks_gate.py --repo <owner/name> --ref <sha> --self <check name>
                               [--allow-skip <check name>]... [--interval s] [--timeout s]
                               [--fetch-timeout s] [--once] [--bound s]
                               [--exclude-workflow <path>]... [--ignore-check <name>]...
                               [--now <iso8601>] [--post-status] [--target-url <url>]
                               [--only-if-pending]

With ``--once``, the script inspects the commit's workflow runs and check runs without waiting
on a runner, decides ``(state, description)`` in ``{"pending", "success", "failure"}``, and with
``--post-status`` posts a commit status under ``--self``. It exits 0 whenever the status was
posted (or decided without ``--post-status``), and exits 1 only when fetching or posting failed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime

STATUS_DESC_LIMIT = 140


class FetchError(RuntimeError):
    """``gh api`` failed or did not answer in time."""


def _truncate(text: str) -> str:
    return text[:STATUS_DESC_LIMIT]


def _parse_iso(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _gh_api(api_args: list[str], timeout: float) -> str:
    try:
        return subprocess.run(
            ["gh", "api", *api_args],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        ).stdout
    except subprocess.TimeoutExpired:
        raise FetchError(f"gh api did not answer within {timeout:g}s") from None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()[-1:] or [""]
        raise FetchError(f"gh api failed (exit {exc.returncode}): {detail[0]}") from None


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


def fetch_workflow_runs(repo: str, ref: str, timeout: float) -> list[dict]:
    """Every workflow run on the ref, all pages."""
    out = _gh_api(
        [
            "--paginate",
            f"repos/{repo}/actions/runs?head_sha={ref}&per_page=100",
            "--jq",
            ".workflow_runs[] | {id, name, path, status, conclusion, created_at, run_attempt}",
        ],
        timeout,
    )
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def latest(runs: list[dict]) -> list[dict]:
    """The newest run of each check name; an earlier run of the same name is superseded."""
    newest: dict[str, dict] = {}
    for run in runs:
        if run["name"] not in newest or run["id"] > newest[run["name"]]["id"]:
            newest[run["name"]] = run
    return list(newest.values())


def latest_workflows(runs: list[dict], excluded: set[str]) -> list[dict]:
    """The newest workflow run for each workflow path, dropping excluded paths."""
    newest: dict[str, dict] = {}
    for run in runs:
        path = run.get("path") or run.get("name", "")
        if path in excluded or run.get("name") in excluded:
            continue
        if path not in newest or run["id"] > newest[path]["id"]:
            newest[path] = run
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


def decide(
    check_runs: list[dict],
    workflow_runs: list[dict],
    self_name: str,
    allow_skip: set[str],
    excluded: set[str],
    bound: int,
    now: str | datetime,
    ignored: set[str] | frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """Pure one-shot decision returning (state, description) for commit status posting."""
    now_dt = _parse_iso(now)
    wf_latest = latest_workflows(workflow_runs, set(excluded))
    running = [r for r in wf_latest if r.get("status") != "completed"]
    if running:
        names = sorted(r["name"] for r in running)
        expired = any(
            (now_dt - _parse_iso(r["created_at"])).total_seconds() > bound for r in running
        )
        if expired:
            return "failure", _truncate(
                f"deadline of {bound}s passed; still running: {', '.join(names)}"
            )
        return "pending", _truncate(f"waiting on {len(names)}: {', '.join(names)}")
    ignore = {self_name} | set(ignored)
    checks = [r for r in latest(check_runs) if r["name"] not in ignore]
    waiting = pending(checks, self_name)
    if waiting:
        return "pending", _truncate(f"waiting on {len(waiting)}: {', '.join(waiting)}")
    problems = evaluate(checks, self_name, set(allow_skip))
    if problems:
        extra = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
        return "failure", _truncate(f"{problems[0]}{extra}")
    return "success", _truncate(f"{len(checks)} check runs passed")


def status_is_settled(repo: str, ref: str, context: str, timeout: float) -> bool:
    """Whether the newest commit status on ref for context is already success or failure."""
    out = _gh_api(
        [
            "--paginate",
            f"repos/{repo}/commits/{ref}/statuses?per_page=100",
            "--jq",
            ".[] | {id, context, state}",
        ],
        timeout,
    )
    entries = [json.loads(line) for line in out.splitlines() if line.strip()]
    matching = [s for s in entries if s.get("context") == context]
    if not matching:
        return False
    newest = max(matching, key=lambda s: s.get("id", 0))
    return newest.get("state") in {"success", "failure"}


def post_status(
    repo: str,
    ref: str,
    state: str,
    context: str,
    description: str,
    target_url: str,
    timeout: float,
) -> None:
    """Post a commit status on ref for context, truncating description to 140 chars."""
    api_args = [
        "-X",
        "POST",
        f"repos/{repo}/statuses/{ref}",
        "-f",
        f"state={state}",
        "-f",
        f"context={context}",
        "-f",
        f"description={_truncate(description)}",
    ]
    if target_url:
        api_args.extend(["-f", f"target_url={target_url}"])
    _gh_api(api_args, timeout)


def run_once(args: argparse.Namespace) -> int:
    """Evaluate required checks once and optionally post the commit status."""
    try:
        if args.only_if_pending and status_is_settled(
            args.repo, args.ref, args.self_name, args.fetch_timeout
        ):
            print(f"{args.ref}: {args.self_name} already settled; skipping")
            return 0
        wf_runs = fetch_workflow_runs(args.repo, args.ref, args.fetch_timeout)
        check_runs = fetch(args.repo, args.ref, args.fetch_timeout)
        now_dt = _parse_iso(args.now) if args.now else datetime.now(UTC)
        state, description = decide(
            check_runs,
            wf_runs,
            args.self_name,
            set(args.allow_skip),
            set(args.exclude_workflows),
            args.bound,
            now_dt,
            set(args.ignore_checks),
        )
    except (FetchError, ValueError, KeyError) as exc:
        description = _truncate(f"evaluation failed: {exc}")
        print(f"::error::{description}", file=sys.stderr)
        if args.post_status:
            try:
                post_status(
                    args.repo,
                    args.ref,
                    "pending",
                    args.self_name,
                    description,
                    args.target_url,
                    args.fetch_timeout,
                )
            except FetchError as post_exc:
                print(f"::error::post status failed: {post_exc}", file=sys.stderr)
        return 1
    print(f"{state}: {description}")
    if args.post_status:
        try:
            post_status(
                args.repo,
                args.ref,
                state,
                args.self_name,
                description,
                args.target_url,
                args.fetch_timeout,
            )
        except FetchError as exc:
            print(f"::error::post status failed: {exc}", file=sys.stderr)
            return 1
    return 0


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--self", dest="self_name", required=True)
    parser.add_argument("--allow-skip", action="append", default=[])
    parser.add_argument("--interval", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=1500)
    parser.add_argument("--fetch-timeout", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--bound", type=int, default=19800)
    parser.add_argument("--exclude-workflow", dest="exclude_workflows", action="append", default=[])
    parser.add_argument("--ignore-check", dest="ignore_checks", action="append", default=[])
    parser.add_argument("--now", default=None)
    parser.add_argument("--post-status", action="store_true")
    parser.add_argument("--target-url", default="")
    parser.add_argument("--only-if-pending", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.once:
        return run_once(args)
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
