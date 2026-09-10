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
"""Automated verification suite for Antigravity agent fleet and skills.

Asserts agent definitions, hooks configuration, privacy guardrails, and
progressive disclosure standards for lusoris-kernel-forge.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / ".agents" / "agents"
HOOKS_JSON = REPO_ROOT / ".agents" / "hooks.json"
GUARD_PRIVACY_SCRIPT = REPO_ROOT / ".agents" / "hooks-scripts" / "guard_privacy.py"
MCP_CONFIG_JSON = REPO_ROOT / ".agents" / "mcp_config.json"

EXPECTED_AGENTS = {
    "lusoris-kernel-architect",
    "lusoris-security-compliance",
    "lusoris-qa-gatekeeper",
    "lusoris-deep-researcher",
    "lusoris-docs-architect",
    "lusoris-downstream-sync",
}


class TestAgentsFleetIntegrity:
    """Validates declarative configuration for Antigravity Managed Agents."""

    def test_expected_agents_present(self) -> None:
        assert AGENTS_DIR.is_dir(), f"Missing {AGENTS_DIR}"
        discovered = set()
        for d in AGENTS_DIR.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                agent_json = d / "agent.json"
                agents_md = d / "AGENTS.md"
                assert agent_json.is_file(), f"Missing agent.json in {d}"
                assert agents_md.is_file(), f"Missing AGENTS.md in {d}"

                with open(agent_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                discovered.add(data.get("id"))

        assert discovered == EXPECTED_AGENTS, (
            f"Expected agents {EXPECTED_AGENTS}, found {discovered}"
        )

    def test_agent_json_schemas(self) -> None:
        for d in AGENTS_DIR.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                with open(d / "agent.json", "r", encoding="utf-8") as f:
                    data = json.load(f)

                assert "id" in data
                assert "base_agent" in data
                assert "description" in data
                assert "agent_config" in data
                assert "tools" in data
                assert isinstance(data["tools"], list)
                assert len(data["tools"]) > 0

    def test_hooks_configuration_validity(self) -> None:
        assert HOOKS_JSON.is_file(), f"Missing {HOOKS_JSON}"
        with open(HOOKS_JSON, "r", encoding="utf-8") as f:
            hooks_data = json.load(f)

        assert "events" in hooks_data
        for event in hooks_data["events"]:
            assert "event" in event
            assert "tools" in event
            assert "hooks" in event
            for hook in event["hooks"]:
                assert hook.get("type") in ("command", "http")

    def test_mcp_config_validity(self) -> None:
        assert MCP_CONFIG_JSON.is_file(), f"Missing {MCP_CONFIG_JSON}"
        with open(MCP_CONFIG_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "mcpServers" in data
        assert "hindsight" in data["mcpServers"]
        assert "cauda-kb" in data["mcpServers"]

    def test_guard_privacy_hook_behavior(self) -> None:
        assert GUARD_PRIVACY_SCRIPT.is_file()

        # Clean payload should exit 0
        clean_input = json.dumps({"arguments": {"content": "target: 192.0.2.1\nname: test"}})
        res_clean = subprocess.run(
            [sys.executable, str(GUARD_PRIVACY_SCRIPT)],
            input=clean_input,
            capture_output=True,
            text=True,
        )
        assert res_clean.returncode == 0

        # Prohibited IP payload should exit 1 (dynamically constructed to avoid static check match)
        prohibited_ip = f"{192}.{168}.{1}.{1}"
        leak_input = json.dumps({"arguments": {"content": f"gateway: {prohibited_ip}"}})
        res_leak = subprocess.run(
            [sys.executable, str(GUARD_PRIVACY_SCRIPT)],
            input=leak_input,
            capture_output=True,
            text=True,
        )
        assert res_leak.returncode == 1
        assert "SECURITY VIOLATION" in res_leak.stderr

        # Workstation path payload should exit 1
        prohibited_path = f"/{'home'}/developer/"
        res_path = subprocess.run(
            [sys.executable, str(GUARD_PRIVACY_SCRIPT)],
            input=json.dumps({"arguments": {"content": prohibited_path}}),
            capture_output=True,
            text=True,
        )
        assert res_path.returncode == 1
        assert "SECURITY VIOLATION" in res_path.stderr

    def test_skills_progressive_disclosure_structure(self) -> None:
        """Verify .agents/skills/ adhere to 3-layer progressive disclosure standard."""
        skills_dir = REPO_ROOT / ".agents" / "skills"
        assert skills_dir.is_dir(), f"Missing {skills_dir}"

        discovered_skills = [
            d for d in skills_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
        ]
        assert len(discovered_skills) >= 6, (
            f"Expected at least 6 skills, found {len(discovered_skills)}"
        )

        for skill_dir in discovered_skills:
            skill_md = skill_dir / "SKILL.md"
            assert skill_md.is_file(), f"Missing SKILL.md in {skill_dir.name}"

            content = skill_md.read_text(encoding="utf-8")
            assert content.startswith("---"), (
                f"SKILL.md in {skill_dir.name} missing YAML frontmatter opening"
            )
            parts = content.split("---", 2)
            assert len(parts) >= 3, f"SKILL.md in {skill_dir.name} malformed frontmatter"

            frontmatter = parts[1]
            assert "name:" in frontmatter, f"SKILL.md in {skill_dir.name} missing 'name'"
            assert "description:" in frontmatter, (
                f"SKILL.md in {skill_dir.name} missing 'description'"
            )

            # If references are defined, assert each referenced file exists
            if "references:" in frontmatter:
                for line in frontmatter.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("- ") and stripped.endswith(".md"):
                        ref_rel = stripped[2:].strip().strip("'\"")
                        ref_file = skill_dir / ref_rel
                        assert ref_file.is_file(), (
                            f"Referenced file {ref_rel} not found in {skill_dir}"
                        )
