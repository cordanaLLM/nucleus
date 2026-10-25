"""The module signing step signs every module of an image package, or refuses and changes nothing.

``sign-file`` is replaced here by a shim that signs the way the kernel's does (CMS, binary, no
signed attributes, no certificates, the PKCS#7 appended with struct module_signature and the
marker); ``openssl`` and ``dpkg-deb`` are real, and the tests that need them are skipped where
either is not installed (the build containers and the Ubuntu CI runners have both).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sign_kernel_modules as sign  # noqa: E402

KR = "7.2.10-lusoris1-mainstream"
MODULES = (f"lib/modules/{KR}/kernel/fs/a.ko", f"lib/modules/{KR}/kernel/net/b.ko")
EPOCH = "1760000000"

needs_tools = pytest.mark.skipif(
    shutil.which("dpkg-deb") is None or shutil.which("openssl") is None,
    reason="dpkg-deb or openssl not installed",
)

SIGN_FILE = """#!/usr/bin/env python3
import subprocess, sys
hash_, key, cert, module = sys.argv[1:5]
signature = subprocess.run(
    ["openssl", "cms", "-sign", "-binary", "-noattr", "-nocerts", "-nosmimecap",
     "-outform", "DER", "-md", hash_, "-signer", cert, "-inkey", key, "-in", module],
    capture_output=True, check=True,
).stdout
GARBLE
info = bytes([0, 0, 2, 0, 0, 0, 0, 0]) + len(signature).to_bytes(4, "big")
with open(module, "ab") as handle:
    handle.write(signature + info + b"~Module signature appended~\\n")
"""


def _key(path: Path) -> Path:
    subprocess.run(
        ["openssl", "req", "-new", "-nodes", "-x509", "-newkey", "rsa:2048", "-days", "1",
         "-subj", "/CN=nucleus test", "-keyout", str(path), "-out", str(path.with_suffix(".crt"))],
        check=True, capture_output=True,
    )  # fmt: skip
    path.write_bytes(path.read_bytes() + path.with_suffix(".crt").read_bytes())
    return path


def _der(key: Path) -> bytes:
    result = subprocess.run(
        ["openssl", "x509", "-in", str(key), "-outform", "DER"], check=True, capture_output=True
    )
    return result.stdout


def _image(out: Path, files: dict[str, bytes]) -> Path:
    root = out / "root"
    (root / "DEBIAN").mkdir(parents=True)
    (root / "DEBIAN" / "control").write_text(
        f"Package: linux-image-{KR}\nVersion: 7.2.10-lusoris1\nArchitecture: amd64\n"
        "Maintainer: test <test@example.com>\nDescription: test package\n",
        encoding="utf-8",
    )
    sums = []
    for member, data in files.items():
        target = root / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        sums.append(f"{hashlib.md5(data, usedforsecurity=False).hexdigest()}  {member}")
    (root / "DEBIAN" / "md5sums").write_text("\n".join(sums) + "\n", encoding="utf-8")
    deb = out / f"linux-image-{KR}_7.2.10-lusoris1_amd64.deb"
    subprocess.run(
        ["dpkg-deb", "-Zxz", "--root-owner-group", "--build", str(root), str(deb)],
        check=True,
        capture_output=True,
    )
    shutil.rmtree(root)
    return deb


class Setup:
    def __init__(self, tmp_path: Path, files: dict[str, bytes] | None = None, garble: str = ""):
        self.tmp = tmp_path
        self.key = _key(tmp_path / "key.pem")
        self.vmlinux = tmp_path / "vmlinux"
        self.vmlinux.write_bytes(b"\0" * 4096 + _der(self.key) + b"\0" * 4096)
        self.sign_file = tmp_path / "sign-file"
        self.sign_file.write_text(SIGN_FILE.replace("GARBLE", garble), encoding="utf-8")
        self.sign_file.chmod(0o755)
        out = tmp_path / "out"
        out.mkdir()
        default = {m: f"\x7fELF module {m}".encode() * 32 for m in MODULES}
        self.deb = _image(out, default if files is None else files)
        self.original = self.deb.read_bytes()

    def run(self, *extra: str, epoch: str | None = EPOCH, deb: Path | None = None) -> int:
        env = {k: v for k, v in os.environ.items() if k != "SOURCE_DATE_EPOCH"}
        if epoch is not None:
            env["SOURCE_DATE_EPOCH"] = epoch
        argv = [
            sys.executable, str(REPO_ROOT / "scripts" / "sign_kernel_modules.py"),
            f"--deb={deb or self.deb}", f"--key={self.key}", f"--sign-file={self.sign_file}",
            "--hash=sha512", f"--vmlinux={self.vmlinux}",
            f"--unsigned-dir={self.tmp / 'unsigned'}", f"--record={self.tmp / 'signing.json'}",
            *extra,
        ]  # fmt: skip
        result = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
        self.stderr = result.stderr
        return result.returncode

    def members(self) -> dict[str, bytes]:
        root = self.tmp / "check"
        shutil.rmtree(root, ignore_errors=True)
        subprocess.run(["dpkg-deb", "-R", str(self.deb), str(root)], check=True)
        return {
            p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
        }


@needs_tools
def test_every_module_is_signed_and_the_step_is_recorded(tmp_path):
    s = Setup(tmp_path)
    assert s.run() == 0, s.stderr
    members = s.members()
    lines = members["DEBIAN/md5sums"].decode().splitlines()
    sums = {path: digest for digest, _, path in (line.partition("  ") for line in lines)}
    for module in MODULES:
        parts = sign.split_signature(members[module])
        assert parts is not None and parts[0] == f"\x7fELF module {module}".encode() * 32
        assert sums[module] == hashlib.md5(members[module], usedforsecurity=False).hexdigest()
    assert (tmp_path / "unsigned" / s.deb.name).read_bytes() == s.original
    record = json.loads((tmp_path / "signing.json").read_text(encoding="utf-8"))
    assert record["modules"] == len(MODULES) and record["hash"] == "sha512"
    assert record["certificate_sha256"] == hashlib.sha256(_der(s.key)).hexdigest()
    assert record["unsigned_package_sha256"] == hashlib.sha256(s.original).hexdigest()
    assert record["signed_package_sha256"] == hashlib.sha256(s.deb.read_bytes()).hexdigest()
    assert sign.data_compressor(s.deb) == "xz"


@needs_tools
def test_the_same_key_over_the_same_package_writes_the_same_bytes(tmp_path):
    s = Setup(tmp_path)
    again = tmp_path / "again" / s.deb.name
    again.parent.mkdir()
    again.write_bytes(s.original)
    assert s.run() == 0, s.stderr
    assert s.run(deb=again) == 0, s.stderr
    assert again.read_bytes() == s.deb.read_bytes()


@needs_tools
@pytest.mark.parametrize(
    ("files", "garble", "finding"),
    [
        (
            {MODULES[0]: b"\x7fELF"},
            "signature = signature[:-1] + bytes([signature[-1] ^ 0xFF])",
            "openssl cms -verify",
        ),
        ({MODULES[0]: b"\x7fELF" + b"~Module signature appended~\n"}, "", "already signed"),
        ({MODULES[0] + ".zst": b"\x28\xb5\x2f\xfd"}, "", "compressed modules"),
        ({"boot/vmlinuz": b"MZ"}, "", "carries no kernel module"),
    ],
)
def test_a_package_that_cannot_be_signed_is_left_as_it_was(tmp_path, files, garble, finding):
    s = Setup(tmp_path, files=files, garble=garble)
    assert s.run() == 1
    assert finding in s.stderr, s.stderr
    assert s.deb.read_bytes() == s.original
    assert not (tmp_path / "signing.json").exists() and not (tmp_path / "unsigned").exists()


@needs_tools
def test_a_key_the_kernel_does_not_trust_is_refused(tmp_path):
    s = Setup(tmp_path)
    s.vmlinux.write_bytes(b"\0" * 8192)
    assert s.run() == 1
    assert "does not carry the certificate" in s.stderr
    assert s.deb.read_bytes() == s.original


@needs_tools
@pytest.mark.parametrize(
    ("extra", "epoch", "finding"),
    [
        (("--hash=sha512;id",), EPOCH, "--hash must be a hash name"),
        ((), None, "SOURCE_DATE_EPOCH must be set"),
    ],
)
def test_a_request_that_cannot_be_reproduced_is_refused(tmp_path, extra, epoch, finding):
    s = Setup(tmp_path)
    assert s.run(*extra, epoch=epoch) == 1
    assert finding in s.stderr
    assert s.deb.read_bytes() == s.original


def test_split_signature_reads_only_a_pkcs7_block_that_fits():
    info = bytes([0, 0, 2, 0, 0, 0, 0, 0]) + (3).to_bytes(4, "big")
    assert sign.split_signature(b"mod" + b"sig" + info + sign.MARKER) == (b"mod", b"sig")
    assert sign.split_signature(b"mod") is None
    assert sign.split_signature(b"sig" + info[:2] + b"\x01" + info[3:] + sign.MARKER) is None
    too_long = info[:8] + (4).to_bytes(4, "big")
    assert sign.split_signature(b"sig" + too_long + sign.MARKER) is None


def test_data_compressor_refuses_a_file_that_is_not_a_package(tmp_path):
    path = tmp_path / "x.deb"
    path.write_bytes(b"not ar")
    with pytest.raises(sign.SigningError, match="not an ar archive"):
        sign.data_compressor(path)
