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
catalog at that praetor commit; together they hold the vendored catalog to the pin.

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
branch. **Committing the file does not apply it.** Until it is applied, `main` is protected by
the classic branch protection alone: the single required check `required-checks`, strict
up-to-date branches, linear history, no approving review and no signature requirement.

### Required checks

praetor requires the check of every job that reports on every pull request: the workflow
triggers on `pull_request` without a `paths` filter, and the job has no `if:` condition or one
that holds on every run, such as `always()`. A job its condition skips is reported as
successful, so a lane that can be skipped is never required. In this revision the ruleset
requires nine checks:

| Check | Workflow | Reported on |
| :--- | :--- | :--- |
| `Praetor Governance` | `ci.yml` | every pull request to `main` |
| `Lint & Pytest Quality Gates` | `ci.yml` | every pull request to `main` |
| `CodeQL (python)`, `CodeQL (actions)` | `codeql.yml` | every pull request to `main` |
| `Validate PR Milestone & Metadata` | `pr-project-gate.yml` | every pull request (opened, edited, labelled, synchronized, milestoned) |
| `required-checks` | `required-aggregator.yml` | every pull request |
| `Detect Security Scan Paths`, `Gitleaks History Scan` | `security-scans.yml` | every pull request |
| `Security Scan Gate` | `security-scans.yml` | every pull request; fails when any scan job failed or was cancelled |

`Semgrep SAST` and `Trivy Filesystem CVE Scan` are skipped on pull requests that touch no
code path, so they are not required on their own; `Security Scan Gate` needs both and fails
when either failed or was cancelled.

A required check that is never reported leaves a pull request waiting forever. Three cases
produce that:

- A pull request into an `lts-*` branch: `ci.yml` and `codeql.yml` run only for pull requests
  into `main`. No `lts-*` branch exists yet; extend both triggers before one does.
- A head commit whose message carries `[skip ci]`: GitHub then starts no workflow.
- A workflow change that renames a required job: `make test` fails until `make ruleset` has
  re-rendered the file.

### Applying the ruleset

The ruleset is applied after the pull request that commits it has merged, from a checkout of
`main` whose `origin` is `github.com/cordanaLLM/nucleus`:

```bash
GITHUB_TOKEN=<token with administration rights> praetorctl sync --remote
gh api repos/cordanaLLM/nucleus/rulesets
```

`praetorctl sync --remote` creates or updates the ruleset through the rulesets API and reads it
back. It also reconciles labels and repository metadata. `.config/labels.yaml` does not exist
in this repository, so the sync writes praetor's default taxonomy of 14 labels there and
creates the missing ones on GitHub; it deletes no label. The description, homepage and
topics are left alone, because `.standards.yaml` leaves them empty.

GitHub enforces a ruleset and the classic branch protection of the same branch together; where
both define a rule, the most restrictive one applies. After the ruleset is applied:

- Every commit of a pull request must carry a verified signature, not only the merge result.
- Review threads must be resolved before a merge.
- No role bypasses the ruleset; the ruleset declares no bypass actors.

## 5. The praetor pin

`PRAETOR_COMMIT` in `.github/workflows/ci.yml` is the one place the pin lives: a full commit
SHA of `cordanaLLM/praetor`. `versions.json` pins kernel streams, not tools. The
`Praetor Governance` job checks praetor out at that commit, builds `praetorctl` from it and
runs:

1. `praetorctl plan --catalog-root praetor-src`: `.standards.lock` matches the catalog of the
   pinned praetor.
2. `praetorctl plan`: the vendored catalog matches `.standards.lock`, and the effective policy
   resolves from it alone.
3. `praetorctl compile-context --verify`: the six compiled agent context files match
   `AGENTS.md`, and `AGENTS.md` and every `.agents/skills/*/SKILL.md` pass praetor's caveman
   lint. The human-authored part of `AGENTS.md` sits between `<!-- caveman:off -->` and
   `<!-- caveman:on -->`, the exemption praetor documents for text that must stay in full
   sentences; the Text Register block after it is rendered from `.standards.yaml`.

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
3. If any differs, copy the five files from praetor and regenerate `.standards.lock` in a
   scratch clone of this repository, never in your working checkout:
   `praetorctl adopt --force --profile os-image --lock-source-root <praetor checkout>`.
   Copy back only `.standards.lock`; adoption rewrites many other files, and none of them
   belongs to a re-pin. praetor has no lock-only command yet.
4. Set `PRAETOR_COMMIT` in `.github/workflows/ci.yml` to the full SHA.
5. Run `make governance-check PRAETOR_SRC=<praetor checkout>`, `make context`,
   `make ruleset`, `make lint` and `make test`, and commit every file they changed.

A pin whose catalog and lock did not move needs step 4 and step 5 only.

## 6. What praetor does not enforce here yet

This repository declares its policy and verifies the pin; it is not fully adopted.
`praetorctl audit` passes the lockfile and HISS stages and then stops at the first gate this
repository does not satisfy, the managed README governance block. The gates after it (the
documentation gate, agent-harness and Paperclip checks, agent-definition layout and git
hooks) belong to a full adoption and are not run in CI. `praetorctl flavor audit .` finds no
matching flavor for a kernel forge, and praetor's HISS scanners read Python but no shell.
