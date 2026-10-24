# Repository Governance

> How [praetor](https://github.com/cordanaLLM/praetor) governs this repository: the policy
> `.standards.yaml` declares, the pinned catalog it resolves against, the review mode, the
> branch ruleset, and how to move the praetor pin.

---

## 1. Start here

praetor is the fleet's governance tool. Its command-line interface, `praetorctl`, reads
`.standards.yaml`, resolves an effective policy from a catalog of archetypes vendored under
`.config/archetypes/`, and renders the files that carry that policy into the repository.

Three commands cover everyday work. Each needs a `praetorctl` built from the pinned praetor
commit (section 5).

| Command | Run it when | What it does |
| :--- | :--- | :--- |
| `make context` | you changed `AGENTS.md` | recompiles `CLAUDE.md`, `.codex/rules.md`, `.cursor/rules/hiss-invariants.mdc`, `.gemini/GEMINI.md`, `.github/copilot-instructions.md` and `.windsurfrules`, then verifies them |
| `make ruleset` | you added, removed or renamed a job in a workflow that runs on pull requests, or changed the review policy | re-renders `.github/rulesets/main.json` locally; it never contacts GitHub |
| `make governance-check PRAETOR_SRC=<praetor checkout at the pin>` | before you push a governance change | runs the checks the `Praetor Governance` CI job runs |

Commit what `make context` and `make ruleset` write together with the change that caused it.
`make test` fails when `.github/rulesets/main.json` requires a check that no pull request
reports, or omits one that every pull request reports
(`tests/test_workflows.py::test_ruleset_requires_exactly_the_checks_every_pull_request_reports`).
That test compares check names only. The `Praetor Governance` CI job also fails when the file
differs from the policy `.standards.yaml` declares, for example after an edited approval count
or a removed review-mode override (section 5).

## 2. Profile and facets

`.standards.yaml` selects the `os-image` profile and four facets: `security:high`,
`api:public-contract`, `docs:seo-portal` and `agent:sandboxed`. The four facets are praetor's
default facet set, which the repository received when it was adopted.

`praetorctl plan` prints the effective policy. In this revision it resolves to:

| Control | Value |
| :--- | :--- |
| Maximum cyclomatic complexity | 10 |
| Maximum function length | 60 lines |
| Linear history | required |
| Signed commits | required |
| Approving reviewers | 0 (review mode `single_maintainer`; configured minimum 2) |
| Dismiss stale reviews | yes |
| SLSA provenance level | 3 |
| Cosign attestation, SBOM | required |

`.standards.lock` pins the SHA-256 digest of each of the five catalog files and an aggregate
digest over them. `praetorctl plan` fails when the vendored files no longer match the lock, and
`praetorctl plan --catalog-root <praetor checkout>` fails when the lock does not match the
catalog at that praetor commit. Neither fails when `.standards.lock` is missing: `plan` then
resolves built-in defaults, which are weaker than this policy (no signed commits, SLSA 1, no
Cosign), and reports the missing file as drift without failing. `praetorctl sync
--catalog-root <praetor checkout>` fails on a missing lock, so CI runs it as well (section 5);
together the three hold the vendored catalog to the pin.

## 3. Review mode

`.standards.yaml` declares `overrides.branch_protection.review_mode: single_maintainer`. The
repository has one maintainer and no review bot, so a required approving review would block
every change that maintainer authors. The owner approved this relaxation on 2026-09-28
(issue #26). The rendered ruleset then requires no approving review and no code-owner
review; linear history, signed commits, dismissal of stale reviews, resolved review threads
and the required checks stay enforced.

The catalog disagrees on the reviewer count: `os-image.yaml` declares 1 approving reviewer,
`facets/security-high.yaml` and `facets/api-public.yaml` declare 2. praetor resolves the
disagreement to the stricter value, 2, and `praetorctl plan` reports it as
`Configured Reviewer Minimum: 2`. The review mode then sets the enforced count to 0. Remove
the override when a second maintainer or a review bot exists; independent review with two
approvals and code-owner review then applies again.

## 4. Branch ruleset

### What is committed, and what is live

`.github/rulesets/main.json` is the repository ruleset `praetor-main-protection`, rendered by
`make ruleset` from `.standards.yaml` and the workflows. It targets `main` and every `lts-*`
branch. **Committing the file does not apply it.** It was applied on 2026-09-29 as ruleset
`24167009` (#26), and the classic branch protection was removed the same day, so the ruleset
alone protects `main`. After a change to the file, apply it again (section 4) and read it back
with `gh api repos/cordanaLLM/nucleus/rules/branches/main`.

### Required checks

praetor requires the check of every job that reports on every pull request: the workflow
triggers on `pull_request` without a `paths` filter, and the job has no `if:` condition or one
that holds on every run, such as `always()`. A job its condition skips is reported as
successful, so a lane that can be skipped is never required. In this revision the ruleset
requires eleven checks:

| Check | Workflow | Reported on |
| :--- | :--- | :--- |
| `Praetor Governance` | `ci.yml` | every pull request to `main` |
| `Lint & Pytest Quality Gates` | `ci.yml` | every pull request to `main` |
| `UKI Real ukify Build` | `ci.yml` | every pull request to `main` |
| `Release UKI Dry Run` | `ci.yml` | every pull request to `main` |
| `CodeQL (python)`, `CodeQL (actions)` | `codeql.yml` | every pull request to `main` |
| `REUSE lint` | `licence.yml` | every pull request to `main` |
| `Validate PR Milestone & Metadata` | `pr-project-gate.yml` | every pull request (`opened`, `reopened`, `edited`, `labeled`, `unlabeled`, `synchronize`, `milestoned`), and every push to `main`, where it checks the merged pull request whose merge commit the push is and fails when none is |
| `required-checks` | `required-aggregator.yml` | commit status posted on every push to `main`, `workflow_run` completion, and 10-minute sweep |
| `Detect Security Scan Paths`, `Gitleaks History Scan` | `security-scans.yml` | every pull request |
| `Security Scan Gate` | `security-scans.yml` | every pull request; fails unless every scan job succeeded or the change detector excluded it |

`Semgrep SAST` and `Trivy Filesystem CVE Scan` are skipped on pull requests that touch no
code path, so they are not required on their own. `Security Scan Gate` needs both. It passes a
skipped Semgrep or Trivy only on a pull request on which `Detect Security Scan Paths`
succeeded and found no code path; on a push, on a schedule, or when the detector failed, a
skipped scan fails the gate. `Detect Security Scan Paths` and `Gitleaks History Scan` run on
every event and must succeed.

`required-checks` is a commit status posted by the `evaluate` job (`Evaluate required checks`,
`timeout-minutes: 5`) of `required-aggregator.yml` via `scripts/required_checks_gate.py --once
--post-status`. Job discovery cannot derive a commit status, so `.standards.yaml` declares it under
`overrides.branch_protection.status_contexts` (praetor's declared status contexts, added by
cordanaLLM/praetor#1510), and `make ruleset` writes it into `.github/rulesets/main.json`. The
ruleset requires the context `required-checks` with no integration pin, so a commit status with
that context satisfies it. The
workflow used to poll on an Ubuntu runner for up to 330 minutes (`--interval 20 --timeout 19800`),
which held runners of the organisation's single free-plan hosted runner pool while builds queued
(measured 2026-10-22 at 03:56Z: five `required-aggregator` jobs had been polling in progress since 00:56Z while 32
kernel build jobs sat queued). GitHub's `check_suite` and `check_run` events do not trigger for
suites created by GitHub Actions, so `required-aggregator.yml` instead triggers on `push` to `main`, `workflow_run`
(`types: [completed]`) for every other workflow in `.github/workflows` except reusable-only
`release-build.yml`, a 10-minute `schedule` sweep (`*/10 * * * *`) for the age bound and any
non-Actions check, and `workflow_dispatch`. It has no `pull_request` trigger: a `pull_request`
run executes the pull request's own copy of the gate with `statuses: write`, so a branch could
post its own `success`, and the job would become a check that job discovery derives and the
ruleset must require. A `workflow_run` run always executes the default branch's copy. On each trigger the job runs once, posts the
`required-checks` commit status (`pending`, `success`, or `failure` with a description truncated
to at most 140 characters), and exits 0 whenever the status was posted so a failing pull request
never paints the default branch's rollup red through `workflow_run`. If fetching or posting fails,
the script attempts to post `pending` (`evaluation failed: ...`) and exits 1. On the scheduled
sweep, `--only-if-pending` skips any commit whose newest `required-checks` status is already
`success` or `failure`.

In `--once` mode, `decide()` inspects both workflow runs and check runs for the commit SHA.
Workflow runs of `required-aggregator.yml` are excluded (`--exclude-workflow`), and for each
workflow path the highest run ID stands for it. While any workflow run is not `completed`, the
decision is `pending` unless its `created_at` age exceeds `--bound 19800`, in which case it is
`failure` (`deadline of 19800s passed; still running: ...`). Once all workflow runs have
completed, `decide()` evaluates the check runs, ignoring its own check-run names (`--ignore-check
"Evaluate required checks"` and legacy `--ignore-check required-checks`). Any remaining pending
check run (such as a non-Actions status check) yields `pending` for the scheduled sweep to
re-evaluate. Otherwise every check run must have concluded `success`. The newest run of a check
name stands for it, since `Validate PR Milestone & Metadata` runs once per pull request edit.
A skipped check passes only when its name is listed with `--allow-skip`. The list holds
`Semgrep SAST` and `Trivy Filesystem CVE Scan`, whose skips the Security Scan Gate judges, and
`verify-tool-release`, which `release-please.yml` runs only on the push to `main` that creates a
release, or on a dispatch that names a tag. Every other push to `main` skips it, and main commit
3392531 went red on that skip. `tests/test_gates_refuse_skipped.py` fails when the list differs
from the detector-gated jobs in `security-scans.yml` plus the release-gated jobs in
`release-please.yml`, and when a listed check that failed or was cancelled passes. The gate also
fails when it finds no other check run. `tests/test_required_checks_once.py` tests `decide()`
across positive, negative, pending, boundary (`bound` vs `bound + 1` s), never-finishing build,
excluded workflow, ignored check-name, and non-Actions pending cases, plus end-to-end
`--once --post-status` invocations against a stubbed `gh`.

`scripts/required_checks_gate.py` also retains its standalone polling wait mode for CLI callers,
where two forge behaviours are handled and tested in `tests/test_gates_refuse_skipped.py`. A job
that `needs` another one registers its check run only when queued, so wait mode passes only after
two consecutive polls see the same check names with none pending. And the forge has left a
finished check run at `in_progress` (the Gitleaks run on `e685ec1`, 2026-10-11); `finished()`
counts a run with both a conclusion and a `completed_at` timestamp as completed in both modes.

`verify-requirements.yml` runs on pull requests only when they touch the paths its trigger
lists, so none of its jobs is a required check on its own: `Verify Downstream Kernel
Requirements`, the `Resolve KConfig (<stream>)` legs and `Verify Requirements Against the
Resolved KConfig` report on the pull requests they concern, and there `required-checks`
evaluates them and requires their success like any other check run. `build-matrix.yml` works the
same way: a pull request that touches a file the build runs or reads (the paths its
`pull_request` trigger lists: the build and module scripts, `kconfig/`, `keys/`, `versions.json`,
`versions.schema.json`, the builder image and the workflow itself) compiles, gates and boots all
twelve legs, then builds the OpenZFS and NVIDIA open kernel modules against the x86_64 headers
packages produced in that same run, and `required-checks` evaluates them. The organisation's
hosted runners are one pool for every repository, so the matrix runs at most six legs at once
(`max-parallel`), and at most half of them, which `tests/test_workflows.py` checks; on 2026-10-17
two runs of all twelve legs left other repositories' jobs queued. While kernel execution takes 54
to 3002 seconds per leg, queue delay under shared-runner saturation dominates the total elapsed
time. Across 10 measured runs in `tests/data/required-checks-spans.json`, pull request build-matrix
spans reached 13397 s (run 39626121442). The `--bound` of 19800 s (330 minutes, giving ~1.48x
headroom over the worst span) covers both waves, while the `evaluate` job's `timeout-minutes: 5`
never holds a runner while waiting. `test_required_checks_deadline_covers_every_measured_build_span`,
`test_evaluate_job_never_holds_a_runner_while_waiting`, and
`test_required_aggregator_workflow_run_lists_every_other_workflow` in `tests/test_workflows.py`
assert that `--bound` covers every measured run span with at least 25% margin, the evaluator
job timeout stays at most 10 minutes, and `workflow_run.workflows` lists every other workflow.
One build runs per branch: a pull request run and a dispatch on its branch share
a concurrency group, so a second run waits for the first. A new push to the pull request cancels
the build still running for its branch, a dispatched one included; a scheduled run, or a dispatch
on a branch without a pull request, is never cancelled. `tests/test_workflows.py` fails when the
trigger, its paths, the cap, the group or the bound change.

A required check that is never reported leaves a pull request waiting forever. Three cases
produce that:

- A pull request into an `lts-*` branch: `ci.yml` and `codeql.yml` run only for pull requests
  into `main`. No `lts-*` branch exists yet; extend both triggers before one does.
- A head commit whose message carries a GitHub skip marker (for example the bracketed
  `skip ci`): GitHub then starts no workflow. On a pull request branch that blocks the merge;
  on `main` it lands a commit with no verification, because the squash message is the pull
  request body. `pr-project-gate.yml` therefore refuses a title or body that contains a
  marker (#39).
- A workflow change that renames a required job: `make test` fails until `make ruleset` has
  re-rendered the file.

### Hosted runner share

The cordanaLLM organisation has 20 hosted runner job slots, shared by every repository in it. On
2026-10-22 at 10:02Z nucleus held 18 of them: 11 kernel legs from four `build-matrix` runs and 7
required-checks pollers. `strategy.max-parallel` caps one run only, so four pull requests at once
filled the organisation and the other repositories waited.

Every job that compiles a kernel or a module (`build-matrix`, `nvidia-open-module`,
`openzfs-module`, `package-lifecycle`, `verify-requirements`, `release-build`) takes one of six
repository-wide slots, `nucleus-kernel-slot-0` to `nucleus-kernel-slot-5`, picked from its
`strategy.job-index`. A job-level concurrency group runs one job at a time, so at most six kernel
jobs of nucleus run at once across all workflows and runs; the others wait in their slot's queue in
order (`queue: max`, `cancel-in-progress: false`). The scanners in `security-scans` (Semgrep,
Trivy, Gitleaks) queue the same way, one group per scanner. A new push to a pull request still
cancels the run of its old head (the workflow-level `cancel-in-progress` on `pull_request`).
`tests/test_workflows.py` refuses a kernel job outside the pool.

The cost is wall time: with several kernel pull requests open, a run can wait in the queue long
enough to reach the required-checks deadline of 330 minutes, which then fails on time, not on a
check. actionlint 1.7.12 does not know the `queue` key yet, so `.github/actionlint.yaml` ignores
that one message in exactly the workflows that use it.

### Applying the ruleset

The ruleset is applied after the pull request that commits `.github/rulesets/main.json` has
merged, from an up-to-date checkout of `main` whose `origin` is `github.com/cordanaLLM/nucleus`:

```bash
gh api --method PUT repos/cordanaLLM/nucleus/rulesets/24167009 --input .github/rulesets/main.json
```

Then read the live ruleset back and check that every field the file sets has the same value
there. GitHub adds defaults the file does not set, such as `allowed_merge_methods`, so a plain
`diff` of the two documents always differs; the check below compares only what the file sets,
prints `true` and exits 0 on a match, and prints `false` and exits 1 on any difference:

```bash
gh api repos/cordanaLLM/nucleus/rulesets/24167009 | jq -e --slurpfile want .github/rulesets/main.json '
  def sub($a; $b):
    if ($a|type) == "object" then ($b|type) == "object" and all($a|keys[]; . as $k | ($b|has($k)) and sub($a[$k]; $b[$k]))
    elif ($a|type) == "array" then ($b|type) == "array" and ($a|length) == ($b|length) and all(range($a|length); . as $i | sub($a[$i]; $b[$i]))
    else $a == $b end;
  sub($want[0]; .)'
```

Labels and repository metadata are not part of this step.

The ruleset is the only protection of `main`: the classic branch protection, which required
only `required-checks`, was removed on 2026-09-29. Under the ruleset:

- Every commit of a pull request must carry a verified signature, not only the merge result.
- Review threads must be resolved before a merge.
- The repository admin role (`RepositoryRole` 5) may bypass the ruleset, and only through a pull
  request (`bypass_mode: pull_request`); nobody pushes to `main` outside a pull request.
  The bypass was applied to ruleset 24167009 on 2026-10-15 with the agent kit (#129).

The signature rule has one trap. The ruleset also requires an up-to-date branch, and GitHub's
**Update branch** button in rebase mode (`gh pr update-branch --rebase`) rewrites the pull
request's commits without a signature, so the pull request is then blocked. That happened to the
release pull request of 0.2.0. Bring a branch up to date locally instead, with signing enabled:

```sh
git fetch origin main <branch>
git switch --detach origin/<branch>
git rebase origin/main          # commit.gpgsign=true signs every rewritten commit
git push --force-with-lease=<branch>:$(git rev-parse origin/<branch>) origin HEAD:<branch>
```

A release pull request that is only behind can also wait: release-please rebuilds it on the next
push to `main`. To re-sign a single rewritten release commit, `git commit --amend --no-edit -S`
keeps `github-actions[bot]` as its author.

A draft pull request runs no gate. Every job a pull request starts begins with the step
**Stop on a draft pull request**, which fails on a draft, so each required check reports failure
until the pull request is marked ready for review. The `ready_for_review` event then starts the
gates (praetor rule HISS-18, `docs/guides/workflow-triggers.md` in praetor).
`test_every_pull_request_job_stops_on_a_draft` in `tests/test_workflows.py` refuses a workflow
without that step or without the event. Four workflows are declared as HISS-18 exceptions in
`.standards.yaml`: `security-scans.yml` and `verify-requirements.yml` (expiring on 2027-01-13),
and `build-matrix.yml` and `package-lifecycle.yml` (expiring on
2027-01-15). Their jobs that need another job are skipped on a draft, and the audit reports them
although nothing can merge through them. The last two build a matrix that a plan job computes
(`build-matrix.yml` runs `Build OpenZFS Module (<stream>)` and `Build NVIDIA Open Module (<stream>)`
against the same-SHA x86_64 headers artifacts, while `openzfs-module.yml` and `nvidia-open-module.yml`
run after a successful `publish-release.yml` run, on `schedule` and on `workflow_dispatch` against
published releases),
and no job of theirs is a required check; their exceptions say so, and
`tests/test_hiss18_exceptions.py` refuses that claim for a workflow with a job the ruleset in
`.github/rulesets/main.json` requires. All four close with cordanaLLM/praetor#1368.

## 5. The praetor pin

`PRAETOR_REF` in `.github/workflows/ci.yml` is the one place the pin lives: a full commit
SHA of `cordanaLLM/praetor`. The git hooks (`.config/lefthook/engine.sh`) read the same
`PRAETOR_REF` line, so a hook judges with the pinned engine, not the praetorctl on `PATH`.
`versions.json` pins kernel streams, not tools. The `Praetor Governance` job checks praetor
out at that commit, builds `praetorctl` from it and runs:

1. `praetorctl plan --catalog-root praetor-src`: `.standards.lock` matches the catalog of the
   pinned praetor.
2. `praetorctl sync --catalog-root praetor-src`: `.standards.lock` exists, and
   `.github/rulesets/main.json` matches the policy `.standards.yaml` declares (approval count,
   review mode, signature, history and check rules). This step runs locally, without
   `--remote`, and writes only `.config/labels.yaml` into the CI workspace. A missing lock, a
   removed review-mode override, a changed review mode or an edited approval count fails it.
3. `praetorctl plan`: the vendored catalog matches `.standards.lock`, and the effective policy
   resolves from it alone.
4. `praetorctl compile-context --verify`: the six compiled agent context files match
   `AGENTS.md`, and `AGENTS.md` and every `.agents/skills/*/SKILL.md` pass praetor's caveman
   lint. The human-authored part of `AGENTS.md` sits between `<!-- caveman:off -->` and
   `<!-- caveman:on -->`, the exemption praetor documents for text that must stay in full
   sentences; the Text Register block after it is rendered from `.standards.yaml`.

The lock's `pinned_version` and the `version` of each entry, `v0.0.0+catalog.3536de06c71a`,
name the catalog content, not a praetor release: `praetorctl adopt` takes the first twelve hex
digits of a digest over every archetype and facet under praetor's `.config/archetypes/`, so the
version moves when that catalog changes and stays put when only praetor's code does. No check
reads it; the digests hold the vendored catalog to the lock. The version does not name the
pin either; `PRAETOR_REF` does.

### Building praetorctl at the pin

The CI job builds with these flags; build the same way locally:

```bash
git clone https://github.com/cordanaLLM/praetor.git praetor-src
git -C praetor-src checkout <PRAETOR_REF>
cd praetor-src
GOTOOLCHAIN=local CGO_ENABLED=0 GOFLAGS=-mod=readonly go mod download
GOTOOLCHAIN=local CGO_ENABLED=0 GOFLAGS=-mod=readonly go mod verify
GOTOOLCHAIN=local CGO_ENABLED=0 GOFLAGS=-mod=readonly \
  go build -trimpath -buildvcs=false -o <bin dir>/praetorctl ./cmd/standardsctl
```

Pass it to the Makefile targets as `PRAETORCTL=<bin dir>/praetorctl`.

### Moving the pin

1. Choose the new praetor commit and build `praetorctl` from it as above.
2. Compare the five vendored files with praetor's copies at that commit:
   `.config/archetypes/os-image.yaml` and `.config/archetypes/facets/{security-high,api-public,docs-seoportal,agent-sandboxed}.yaml`.
3. Regenerate `.standards.lock` in a scratch clone of this repository, never in your working
   checkout:
   `praetorctl adopt --force --profile os-image --lock-source-root <praetor checkout>`.
   Copy back the five files if any differs, and `.standards.lock` if it differs from the
   committed one; the lock can change while the five files do not, because its version
   covers every archetype and facet in praetor's catalog. Copy back nothing else; adoption
   rewrites many other files, and none of them belongs to a re-pin. praetor has no lock-only
   command yet.
4. Set `PRAETOR_REF` in `.github/workflows/ci.yml` to the full SHA.
5. Run `make governance-check PRAETOR_SRC=<praetor checkout>`, `make context`,
   `make ruleset`, `make lint` and `make test`, and commit every file they changed.
   `governance-check` refuses a praetor checkout whose `HEAD` is not `PRAETOR_REF`; it
   cannot tell which commit your `praetorctl` was built from, so build it at the pin.

A pin whose catalog and lock did not move needs step 4 and step 5 only.

## 6. What praetor does not enforce here yet

This repository declares its policy and verifies the pin; it is not fully adopted.
`praetorctl audit` passes the lockfile and HISS stages and then stops at the first gate this
repository does not satisfy, the managed README governance block. The gates after it (the
documentation gate, agent-harness and Paperclip checks, agent-definition layout and git
hooks) belong to a full adoption and are not run in CI. `praetorctl flavor audit .` finds no
matching flavor for a kernel forge, and praetor's HISS scanners read Python but no shell.

`.github/milestones.json` and `.github/epics.json` mirror the forge: the milestones and the
issues labelled `epic`, as the API named in each file's `source` returned them at
`mirrored_at`. An epic row keeps two fields the forge does not hold, its `id` (the `EPIC-NN`
form `pr-project-gate.yml` accepts as a reference) and its `cadence`. Nothing compares the
files with the forge afterwards; `tests/test_governance.py` checks their fields, the two
forge states, and that every epic names a listed milestone.

The pinned `praetorctl` expands a literal matrix axis in a required job, and parses a matrix
written as a single expression (`matrix: ${{ fromJSON(...) }}`) in a job that is not
required. praetor commits before `9a00443c086a` refused that form with a parse error, even in
a workflow that is never a required check, and the error failed `make ruleset` and the
`praetorctl sync` step of the governance job. The `resolve` job of `verify-requirements.yml`
takes the plan as its matrix, `matrix: ${{ fromJSON(needs.verify.outputs.plan) }}`, and each
leg reports as `Resolve KConfig (<stream>)`. `codeql.yml` keeps its languages as `include`
legs, because `tests/test_workflows.py` expands required check names from `include` legs
only.
