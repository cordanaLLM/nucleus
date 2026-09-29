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
"""The verifier's command line, run the way a downstream contract gate runs it.

cordanaLLM/Aegis-OS pins this repository and runs scripts/verify_kernel_requirement.py with
``python3 -I`` (isolated: no site-packages, no script directory on sys.path), then asserts on
the --report-json keys. These tests hold that interface: the invocation, the exit codes and
the report shape for an accepted document, a correlated refusal and an empty feature list.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "kernel-requirement" / "aegis-os.json"
TOP_KEYS = {"schema", "nucleus_revision", "evidence_level", "policy", "verdict", "documents"}


def _run(tmp_path: Path, document: dict) -> tuple[int, dict]:
    doc_path = tmp_path / "requirement.json"
    doc_path.write_text(json.dumps(document), encoding="utf-8")
    report_path = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "scripts/verify_kernel_requirement.py",
            f"--requirement=aegis-os={doc_path}",
            f"--report-json={report_path}",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert report_path.exists(), completed.stderr
    return completed.returncode, json.loads(report_path.read_text(encoding="utf-8"))


def _document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_an_accepted_document_exits_zero_in_isolated_mode(tmp_path):
    code, report = _run(tmp_path, _document())
    assert code == 0
    assert TOP_KEYS <= set(report)
    assert report["schema"] == "nucleus.kernel-requirement-report.v1"
    assert report["verdict"] == "PASS"
    document = report["documents"][0]
    assert document["status"] == "PASS"
    assert document["correlation_id"] == _document()["correlation-id"]


def test_an_unsatisfiable_feature_is_refused_with_its_correlation_id(tmp_path):
    document = _document()
    document["features"].append(
        {
            "symbol": "CONFIG_NUCLEUS_DOES_NOT_SET_THIS",
            "state": "built-in",
            "probe": "kernel-config",
            "required-by": "REQ-TEST-01",
        }
    )
    code, report = _run(tmp_path, document)
    assert code == 1
    assert report["verdict"] == "FAIL"
    refused = report["documents"][0]
    assert refused["status"] == "FAIL"
    assert refused["correlation_id"] == document["correlation-id"]
    reason = next(r for r in refused["reasons"] if "CONFIG_NUCLEUS_DOES_NOT_SET_THIS" in r)
    assert reason.startswith(document["correlation-id"] + ": ")
    assert "(required-by REQ-TEST-01)" in reason


def test_an_empty_feature_list_is_rejected_not_passed(tmp_path):
    document = _document()
    document["features"] = []
    code, report = _run(tmp_path, document)
    assert code == 1
    assert report["verdict"] == "FAIL"
    rejected = report["documents"][0]
    assert rejected["status"] == "REJECTED"
    assert rejected["rejection"]["kind"] == "NoFeatures"
    assert rejected["rejection"]["message"]


def test_an_isolated_run_leaves_no_bytecode_in_the_checkout(tmp_path):
    """A pinned consumer checkout stays clean: the sibling import writes no __pycache__."""
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    for name in ("verify_kernel_requirement.py", "versions_query.py"):
        shutil.copy2(REPO_ROOT / "scripts" / name, checkout / "scripts" / name)
    shutil.copytree(REPO_ROOT / "kconfig", checkout / "kconfig")
    shutil.copy2(REPO_ROOT / "versions.json", checkout / "versions.json")
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "scripts/verify_kernel_requirement.py",
            f"--requirement=aegis-os={FIXTURE}",
        ],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not list(checkout.rglob("__pycache__")), "the run wrote bytecode into the checkout"
