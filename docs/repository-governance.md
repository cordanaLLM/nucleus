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
| `CodeQL (python)`, `CodeQL (actions)` | `codeql.yml` | every pull request to `main` |
| `REUSE lint` | `licence.yml` | every pull request to `main` |
| `Validate PR Milestone & Metadata` | `pr-project-gate.yml` | every pull request (`opened`, `reopened`, `edited`, `labeled`, `unlabeled`, `synchronize`, `milestoned`) |
| `required-checks` | `required-aggregator.yml` | every pull request |
| `Detect Security Scan Paths`, `Gitleaks History Scan` | `security-scans.yml` | every pull request |
| `Security Scan Gate` | `security-scans.yml` | every pull request; fails unless every scan job succeeded or the change detector excluded it |

`Semgrep SAST` and `Trivy Filesystem CVE Scan` are skipped on pull requests that touch no
code path, so they are not required on their own. `Security Scan Gate` needs both. It passes a
skipped Semgrep or Trivy only on a pull request on which `Detect Security Scan Paths`
succeeded and found no code path; on a push, on a schedule, or when the detector failed, a
skipped scan fails the gate. `Detect Security Scan Paths` and `Gitleaks History Scan` run on
every event and must succeed.

`required-checks` runs `scripts/required_checks_gate.py`. It waits until every other check run
on the head commit has completed, then requires each to have concluded `success`. The newest
run of a check name stands for it, since `Validate PR Milestone & Metadata` runs once per pull
request edit. A skipped check passes only when its name is listed with `--allow-skip`. The
list holds just `Semgrep SAST` and `Trivy Filesystem CVE Scan`, whose skips the Security Scan
Gate judges, and `tests/test_gates_refuse_skipped.py` fails when that list differs from the
detector-gated jobs in `security-scans.yml`. The gate also fails when it finds no other check
run, or when the others are still running after 100 minutes. Before this, the aggregator
accepted `skipped` from any check.

Two forge behaviours shape the wait. A job that `needs` another one registers its check run
only when it is queued, so `Security Scan Gate` and `Verify Requirements Against the Resolved
KConfig` appear minutes after the rest; the gate passes only after two consecutive polls see the
same check names with none pending. And the forge has left a finished check run at
`in_progress` (the Gitleaks run on `e685ec1`, 2026-10-11, held the aggregator for its whole
timeout); a run that carries a conclusion and a completion time counts as completed whatever
its status says, and its conclusion is judged like any other. Both are tested in
`tests/test_gates_refuse_skipped.py` against a stubbed `gh`.

`verify-requirements.yml` runs on pull requests only when they touch the paths its trigger
lists, so none of its jobs is a required check on its own: `Verify Downstream Kernel
Requirements`, the `Resolve KConfig (<stream>)` legs and `Verify Requirements Against the
Resolved KConfig` report on the pull requests they concern, and there `required-checks` waits
for them and requires their success like any other check run. `build-matrix.yml` works the
same way: a pull request that touches a file the build runs or reads (the paths its
`pull_request` trigger lists: the build scripts, `kconfig/`, `keys/`, `versions.json`, the
builder image and the workflow itself) compiles, gates and boots all twelve legs, and
`required-checks` waits for them. That is why the gate waits 100 minutes: the arm64 leg may run
for 90, and the longest measured leg took 40 (run 36507112520). A new push to the pull request
cancels the build still running for the previous one; a scheduled or dispatched run is never
cancelled. `tests/test_workflows.py` fails when the trigger, its paths or the wait change.

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
without that step or without the event. Two workflows are declared as HISS-18 exceptions in
`.standards.yaml`: `security-scans.yml` and `verify-requirements.yml`. Their jobs that need
another job are skipped on a draft, and the audit reports them although nothing can merge
through them. The exceptions expire on 2027-01-13 and close with cordanaLLM/praetor#1368.

## 5. The praetor pin

`PRAETOR_COMMIT` in `.github/workflows/ci.yml` is the one place the pin lives: a full commit
SHA of `cordanaLLM/praetor`. `versions.json` pins kernel streams, not tools. The
`Praetor Governance` job checks praetor out at that commit, builds `praetorctl` from it and
runs:

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
pin either; `PRAETOR_COMMIT` does.

### Building praetorctl at the pin

The CI job builds with these flags; build the same way locally:

```bash
git clone https://github.com/cordanaLLM/praetor.git praetor-src
git -C praetor-src checkout <PRAETOR_COMMIT>
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
4. Set `PRAETOR_COMMIT` in `.github/workflows/ci.yml` to the full SHA.
5. Run `make governance-check PRAETOR_SRC=<praetor checkout>`, `make context`,
   `make ruleset`, `make lint` and `make test`, and commit every file they changed.
   `governance-check` refuses a praetor checkout whose `HEAD` is not `PRAETOR_COMMIT`; it
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
