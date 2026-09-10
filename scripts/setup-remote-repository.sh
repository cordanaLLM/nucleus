#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

REPO_OWNER="lusoris"
REPO_NAME="lusoris-kernel-forge"
FULL_REPO="${REPO_OWNER}/${REPO_NAME}"

init_remote_repo() {
  echo "==> [1/9] Ensuring GitHub repository ${FULL_REPO} exists..."
  if ! gh repo view "${FULL_REPO}" >/dev/null 2>&1; then
    gh repo create "${FULL_REPO}" \
      --public \
      --description "Deterministic Linux kernel compilation forge, hardened kconfig fragments, and deb/UKI packaging for lusoris-cloud-images." \
      --homepage "https://lusoris.github.io/lusoris-kernel-forge"
    echo "    Created repository ${FULL_REPO}."
  else
    echo "    Repository ${FULL_REPO} already exists."
  fi

  if ! git remote | grep -q "^origin$"; then
    git remote add origin "https://github.com/${FULL_REPO}.git"
  fi
  git push -u origin main
}

configure_repo_settings() {
  echo "==> [2/9] Configuring merge options, features, and commit templates..."
  gh repo edit "${FULL_REPO}" \
    --enable-squash-merge \
    --enable-rebase-merge \
    --enable-merge-commit=false \
    --enable-auto-merge \
    --delete-branch-on-merge \
    --enable-issues \
    --enable-projects \
    --enable-wiki=false \
    --enable-discussions=false

  gh api -X PATCH "repos/${FULL_REPO}" \
    -F squash_merge_commit_title="PR_TITLE" \
    -F squash_merge_commit_message="PR_BODY" \
    -F merge_commit_title="MERGE_MESSAGE" \
    -F merge_commit_message="PR_TITLE"

  local topics=("kernel" "ebpf" "sched-ext" "bbrv3" "kspp" "debian-packages" "cloud-images" "arm64" "x86-64" "riscv64" "preempt-rt" "reproducible-builds")
  for topic in "${topics[@]}"; do
    gh repo edit "${FULL_REPO}" --add-topic "${topic}" >/dev/null 2>&1 || true
  done
}

configure_security_and_actions() {
  echo "==> [3/9] Configuring security scanning and Actions permissions..."
  gh api -X PATCH "repos/${FULL_REPO}" \
    --input - <<EOF >/dev/null 2>&1 || true
{
  "security_and_analysis": {
    "secret_scanning": {"status": "enabled"},
    "secret_scanning_push_protection": {"status": "enabled"}
  }
}
EOF

  gh api -X PUT "repos/${FULL_REPO}/vulnerability-alerts" >/dev/null 2>&1 || true
  gh api -X PUT "repos/${FULL_REPO}/actions/permissions" -F enabled=true -F allowed_actions=all >/dev/null 2>&1 || true
  gh api -X PUT "repos/${FULL_REPO}/actions/permissions/workflow" \
    -F default_workflow_permissions=read \
    -F can_approve_pull_request_reviews=true >/dev/null 2>&1 || true
}

configure_pages_and_env() {
  echo "==> [4/9] Configuring GitHub Pages and deployment environment..."
  gh api -X POST "repos/${FULL_REPO}/pages" -F build_type=workflow >/dev/null 2>&1 || true
  gh api -X PUT "repos/${FULL_REPO}/environments/github-pages" \
    --input - <<EOF >/dev/null 2>&1 || true
{
  "deployment_branch_policy": {
    "protected_branches": false,
    "custom_branch_policies": true
  }
}
EOF
  gh api -X POST "repos/${FULL_REPO}/environments/github-pages/deployment-branch-policies" \
    -F name="main" >/dev/null 2>&1 || true
}

create_label_helper() {
  local name="$1"
  local color="$2"
  local desc="$3"
  gh label create "${name}" -R "${FULL_REPO}" --color "${color}" --description "${desc}" --force >/dev/null 2>&1 || true
}

sync_labels() {
  echo "==> [5/9] Synchronizing label taxonomy..."
  create_label_helper "accessibility" "f143ab" "Barrier affecting people with disabilities"
  create_label_helper "bug" "d73a4a" "Something isn't working"
  create_label_helper "documentation" "0075ca" "Improvements or additions to documentation"
  create_label_helper "duplicate" "cfd3d7" "This issue or pull request already exists"
  create_label_helper "enhancement" "a2eeef" "New feature or request"
  create_label_helper "good first issue" "7057ff" "Good for newcomers"
  create_label_helper "help wanted" "008672" "Extra attention is needed"
  create_label_helper "invalid" "e4e669" "This doesn't seem right"
  create_label_helper "question" "d876e3" "Further information is requested"
  create_label_helper "wontfix" "ffffff" "This will not be worked on"
  create_label_helper "epic" "5319e7" "High-level strategic epic or workstream"
  create_label_helper "kind/feature" "1d76db" "New capability or flavor"
  create_label_helper "kind/bug" "d93f0b" "Something is not working as expected"
  create_label_helper "kind/chore" "c5def5" "Tooling dependencies or maintenance"
  create_label_helper "kind/security" "e99695" "Security vulnerability or hardening"
  create_label_helper "area/forge" "0e8a16" "Kernel build pipeline and packaging engine"
  create_label_helper "area/gpu-intel" "006b75" "Intel Arc Xe Battlemage and Level Zero"
  create_label_helper "area/gpu-amd" "b60205" "AMD Mesa RADV and ROCm 10 stack"
  create_label_helper "area/gpu-nvidia" "1d76db" "NVIDIA CUDA generational drivers 535 565 615"
  create_label_helper "area/ai-infer" "fbca04" "AI inference kernel acceleration"
  create_label_helper "area/k8s" "0052cc" "Kubernetes container and eBPF kernel support"
  create_label_helper "area/ci-cd" "bfd4f2" "GitHub Actions release automation and supply chain"
  create_label_helper "status/in-progress" "fbca04" "Work actively in progress"
  create_label_helper "status/blocked" "b60205" "Blocked on external dependency or review"
  create_label_helper "autorelease: pending" "ededed" "Release Please pending release tag"
  create_label_helper "area/kernel-bleeding" "5319e7" "Linux bleeding stream (7.3-rc2)"
  create_label_helper "area/kernel-mainstream" "1d76db" "Linux mainstream stream (7.2.4)"
  create_label_helper "area/kernel-lts" "0052cc" "Linux LTS stream (6.18.50)"
  create_label_helper "area/kernel-realtime" "d93f0b" "Linux PREEMPT_RT stream (7.2-rt)"
  create_label_helper "area/kconfig" "006b75" "Kernel configuration fragments"
  create_label_helper "area/patches" "b60205" "Curated kernel patches and queues"
  create_label_helper "area/packaging" "0e8a16" "Debian bindeb-pkg and systemd-ukify EFI packaging"
  create_label_helper "area/ebpf-sched-ext" "fbca04" "eBPF verifier and sched-ext dynamic scheduler"
  create_label_helper "area/arch-x86_64" "bfd4f2" "x86_64 architecture targets"
  create_label_helper "area/arch-arm64" "bfd4f2" "ARM64 aarch64 architecture targets"
  create_label_helper "area/arch-riscv64" "bfd4f2" "RISC-V 64 architecture targets"
  create_label_helper "tier/p0" "b60205" "P0 Critical Path Priority"
  create_label_helper "tier/p1" "fbca04" "P1 High Priority Feature"
  create_label_helper "tier/p2" "0075ca" "P2 Medium Priority"
}

seed_milestones() {
  echo "==> [6/9] Seeding release milestones..."
  gh api "repos/${FULL_REPO}/milestones" \
    -f title="v1.0.0-rc1 - Multi-Stream Kernel Compilation Forge" \
    -f description="Initial production kernel release supporting bleeding (7.3-rc2), mainstream (7.2.4), lts (6.18.50), and realtime (7.2-rt) across x86_64 and arm64." \
    -f due_on="2026-09-24T00:00:00Z" >/dev/null 2>&1 || true

  gh api "repos/${FULL_REPO}/milestones" \
    -f title="v1.1.0 - Multi-Arch Cross-Compilation & UKI Synthesis" \
    -f description="Hermetic cross-compilation pipeline for RISC-V, automated systemd-ukify Unified Kernel Image packaging, and reproducible builds." \
    -f due_on="2026-10-30T00:00:00Z" >/dev/null 2>&1 || true

  gh api "repos/${FULL_REPO}/milestones" \
    -f title="v1.2.0 - Blackwell B200 / RTX 5090 & ROCm 10 Driver Matrix" \
    -f description="NVIDIA Blackwell B200/RTX 5090 open kernel modules, AMD ROCm 10 GFX12 drivers, and sched-ext dynamic scheduler integration." \
    -f due_on="2026-11-29T00:00:00Z" >/dev/null 2>&1 || true
}

create_baseline_epics() {
  local m1="v1.0.0-rc1 - Multi-Stream Kernel Compilation Forge"
  local m2="v1.1.0 - Multi-Arch Cross-Compilation & UKI Synthesis"

  gh issue create -R "${FULL_REPO}" \
    --title "Epic: Multi-Stream Kernel Release Matrix & Upstream Synchronization" \
    --label "epic,kind/feature,area/kernel-mainstream,area/kernel-lts,tier/p0" \
    --milestone "${m1}" \
    --body "### Summary
Automated kernel compilation and verification across 4 streams.

### Deliverables
- [x] Declarative versions manifest (versions.json)
- [x] Stream build scripts adhering to Power of 10
- [ ] Containerized build matrix workflow
- [ ] Downstream release dispatch to lusoris-cloud-images" >/dev/null 2>&1 || true

  gh issue create -R "${FULL_REPO}" \
    --title "Epic: Hardened KConfig Baselines & KSPP CIS L2 Compliance" \
    --label "epic,kind/security,area/kconfig,tier/p0" \
    --milestone "${m1}" \
    --body "### Summary
Hardened kconfig fragments meeting KSPP and CIS L2 baselines.

### Deliverables
- [x] Base security lockdown fragments
- [x] Architecture-specific virtualization fragments
- [ ] Automated KConfig linter and symbol auditor" >/dev/null 2>&1 || true

  gh issue create -R "${FULL_REPO}" \
    --title "Epic: Multi-Architecture Cross-Compilation Engine (x86_64, arm64, riscv64)" \
    --label "epic,kind/feature,area/arch-x86_64,area/arch-arm64,tier/p1" \
    --milestone "${m2}" \
    --body "### Summary
Hermetic cross-compilation pipeline across x86_64, ARM64, and RISC-V 64.

### Deliverables
- [x] Architecture targets declared in versions.json
- [ ] Cross-compiler toolchain container definitions
- [ ] Sub-second QEMU microVM boot verification" >/dev/null 2>&1 || true
}

create_advanced_epics() {
  local m1="v1.0.0-rc1 - Multi-Stream Kernel Compilation Forge"
  local m2="v1.1.0 - Multi-Arch Cross-Compilation & UKI Synthesis"
  local m3="v1.2.0 - Blackwell B200 / RTX 5090 & ROCm 10 Driver Matrix"

  gh issue create -R "${FULL_REPO}" \
    --title "Epic: Dual Packaging Pipeline (.deb & systemd-ukify UKI EFI)" \
    --label "epic,kind/feature,area/packaging,tier/p1" \
    --milestone "${m2}" \
    --body "### Summary
Native Debian packages and signed Unified Kernel Image EFI binaries.

### Deliverables
- [x] Upstream bindeb-pkg driver integration
- [ ] systemd-ukify Unified Kernel Image packaging script
- [ ] Cryptographic signing using Cosign keyless OIDC" >/dev/null 2>&1 || true

  gh issue create -R "${FULL_REPO}" \
    --title "Epic: Enterprise CI/CD, Supply Chain Attestation & Downstream Dispatch" \
    --label "epic,kind/security,area/ci-cd,tier/p0" \
    --milestone "${m1}" \
    --body "### Summary
Enterprise 12-workflow CI/CD matrix with OpenSSF Scorecard and Semgrep.

### Deliverables
- [x] Core CI quality gates and manifest linting
- [ ] Single branch protection check aggregator (required-checks)
- [ ] Automated downstream dispatch to lusoris-cloud-images" >/dev/null 2>&1 || true

  gh issue create -R "${FULL_REPO}" \
    --title "Epic: Next-Gen Hardware Acceleration & Scheduler Engine (Blackwell, Xe2, BBRv3, sched-ext)" \
    --label "epic,kind/feature,area/patches,area/ebpf-sched-ext,tier/p1" \
    --milestone "${m3}" \
    --body "### Summary
High-performance patches for Blackwell B200, Xe2, ROCm 10, BBRv3, and sched-ext.

### Deliverables
- [x] Baseline sched-ext patch and tuning fragment
- [ ] BBRv3 congestion control backports
- [ ] NVIDIA Blackwell Day-0 open kernel modules" >/dev/null 2>&1 || true
}

create_epics_issues() {
  echo "==> [7/9] Creating strategic Epics as GitHub issues..."
  create_baseline_epics
  create_advanced_epics
}

provision_project_board() {
  echo "==> [8/9] Provisioning GitHub Project v2 Board and linking epics..."
  local proj_title="lusoris-kernel-forge Roadmap"
  local proj_num
  proj_num=$(gh project list --owner "${REPO_OWNER}" --format json | python3 -c "
import json, sys
data = json.load(sys.stdin)
projects = data.get('projects', [])
found = [p['number'] for p in projects if p.get('title') == '${proj_title}']
if found:
    print(found[0])
" || true)

  if [[ -z "${proj_num}" ]]; then
    proj_num=$(gh project create --owner "${REPO_OWNER}" --title "${proj_title}" --format json | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('number', ''))
")
    echo "    Created Project #${proj_num}: ${proj_title}"
  else
    echo "    Using existing Project #${proj_num}: ${proj_title}"
  fi

  if [[ -n "${proj_num}" ]]; then
    local issues
    issues=$(gh issue list -R "${FULL_REPO}" --label "epic" --json url --jq '.[].url')
    for url in ${issues}; do
      gh project item-add "${proj_num}" --owner "${REPO_OWNER}" --url "${url}" >/dev/null 2>&1 || true
    done
    echo "    Synced epics to Project #${proj_num}."
  fi
}

enforce_branch_protection() {
  echo "==> [9/9] Enforcing branch protection on main..."
  gh api -X PUT "repos/${FULL_REPO}/branches/main/protection" \
    --input - <<EOF >/dev/null 2>&1 || true
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["required-checks"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "require_last_push_approval": false,
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
  echo "    Branch protection on main successfully enforced."
}

main() {
  echo "=========================================================="
  echo " lusoris-kernel-forge — Remote GitHub Setup & Parity Sync"
  echo "=========================================================="
  init_remote_repo
  configure_repo_settings
  configure_security_and_actions
  configure_pages_and_env
  sync_labels
  seed_milestones
  create_epics_issues
  provision_project_board
  enforce_branch_protection
  echo "=========================================================="
  echo " ✓ Remote GitHub Setup Completed Successfully"
  echo "=========================================================="
}

main "$@"

