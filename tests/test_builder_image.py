"""docker/Dockerfile.builder is the CI build environment, not a second toolchain definition.

It used to start from ubuntu:24.04 and install its own package list (clang, dracut, diffoscope,
no dwarves), so a local build used a toolchain no workflow ran. It now starts from the
ubuntu:26.04 digest the workflows pin and installs scripts/install-build-toolchain.sh, and
build-matrix.yml builds it and checks the toolchain inside it.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.builder"
BUILD_MATRIX = REPO_ROOT / ".github" / "workflows" / "build-matrix.yml"
INSTALLER = "scripts/install-build-toolchain.sh"


def _instructions(text: str) -> list[tuple[str, str]]:
    """(keyword, arguments) per instruction, continuation lines joined, comments dropped."""
    joined = re.sub(r"\\\n", " ", text)
    found = []
    for line in joined.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            keyword, _, rest = stripped.partition(" ")
            found.append((keyword.upper(), rest.strip()))
    return found


def builder_problems(text: str, image: str) -> list[str]:
    """One line per way the Dockerfile departs from the pinned image and the shared installer."""
    instructions = _instructions(text)
    problems = []
    bases = [rest for keyword, rest in instructions if keyword == "FROM"]
    if bases != [image]:
        problems.append(f"FROM {bases} is not the pinned {image}")
    runs = " ".join(rest for keyword, rest in instructions if keyword == "RUN")
    if INSTALLER.rsplit("/", 1)[1] not in runs:
        problems.append(f"no RUN calls {INSTALLER}")
    if re.search(r"\bapt(-get)?\s+install\b", runs):
        problems.append("a RUN installs packages beside the shared installer")
    copies = [rest for keyword, rest in instructions if keyword in ("COPY", "ADD")]
    if not any(rest.split()[0] == INSTALLER for rest in copies):
        problems.append(f"{INSTALLER} is not copied into the image")
    return problems


def _pinned_image() -> str:
    parsed = yaml.safe_load(BUILD_MATRIX.read_text(encoding="utf-8"))
    return parsed["jobs"]["build"]["container"]["image"]


def test_the_builder_uses_the_pinned_image_and_the_shared_installer():
    image = _pinned_image()
    assert re.fullmatch(r"ubuntu:26\.04@sha256:[0-9a-f]{64}", image), image
    assert builder_problems(DOCKERFILE.read_text(encoding="utf-8"), image) == []


GOOD = """FROM ubuntu:26.04@sha256:{d}
COPY scripts/install-build-toolchain.sh /usr/local/sbin/install-build-toolchain.sh
RUN /usr/local/sbin/install-build-toolchain.sh --with-qemu \\
    && rm -rf /var/lib/apt/lists/*
"""
IMAGE = "ubuntu:26.04@sha256:" + "a" * 64


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        pytest.param(GOOD.replace("{d}", "b" * 64), "is not the pinned", id="other-digest"),
        pytest.param(GOOD.replace("@sha256:{d}", ""), "is not the pinned", id="tag-only"),
        pytest.param(
            GOOD.replace("{d}", "a" * 64) + "FROM ubuntu:24.04\n",
            "is not the pinned",
            id="second-stage",
        ),
        pytest.param(
            GOOD.replace("{d}", "a" * 64) + "RUN apt-get update && apt-get install -y clang\n",
            "beside the shared installer",
            id="extra-packages",
        ),
        pytest.param(
            GOOD.replace("{d}", "a" * 64).replace(
                "    && rm", "    && apt install -y \\\n    dracut && rm"
            ),
            "beside the shared installer",
            id="extra-packages-on-a-continuation-line",
        ),
        pytest.param(
            f"FROM {IMAGE}\nRUN apt-get update && apt-get install -y build-essential\n",
            "no RUN calls",
            id="own-package-list",
        ),
        pytest.param(
            GOOD.replace("{d}", "a" * 64).replace("COPY", "# COPY"),
            "is not copied",
            id="installer-not-copied",
        ),
    ],
)
def test_a_departing_dockerfile_is_refused(text, problem):
    problems = builder_problems(text, IMAGE)
    assert any(problem in line for line in problems), problems


def test_the_good_example_passes():
    assert builder_problems(GOOD.replace("{d}", "a" * 64), IMAGE) == []


def test_build_matrix_builds_the_image_and_checks_its_toolchain():
    parsed = yaml.safe_load(BUILD_MATRIX.read_text(encoding="utf-8"))
    job = parsed["jobs"]["builder-image"]
    assert "if" not in job, "the builder image is checked on every build-matrix run"
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "docker build" in runs and "-f docker/Dockerfile.builder" in runs
    installed = re.search(r"readonly PACKAGES=\((.*?)\)", (REPO_ROOT / INSTALLER).read_text(), re.S)
    assert installed and "dwarves" in installed.group(1)
    for tool in (
        "aarch64-linux-gnu-gcc",
        "riscv64-linux-gnu-gcc",
        "pahole",
        "gnuinstall",
        "qemu-system-x86_64",
    ):
        assert tool in runs, f"the job does not check {tool}"
    assert "scripts/build_kernel.sh --stream=mainstream --arch=x86_64 --dry-run" in runs
