#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Sign every kernel module in an image package, after the build, with one recorded key.

The build compiles the modules unsigned (``# CONFIG_MODULE_SIG_ALL is not set`` and
``CONFIG_MODULE_SIG_KEY=""`` in kconfig/security-hardened.config) and builds the key's
certificate into the kernel through ``CONFIG_SYSTEM_TRUSTED_KEYS``. Without a key of its own the
build writes the same bytes twice, so its packages can be compared (issue #77); this step then
signs them. It is the method of the kernel's Documentation/kbuild/reproducible-builds.rst,
section "Module signing".

The step refuses, and leaves the package as it was, unless all of these hold:

- the certificate in ``--key`` is the one the kernel was built to trust: its DER bytes appear
  in ``--vmlinux``;
- the package carries at least one module, every module is an uncompressed ``.ko``, and none
  is signed yet (a signed module means the build signed with a key of its own);
- ``sign-file`` signs every module, and ``openssl cms -verify`` accepts every signature against
  the certificate.

It then writes the signed modules' MD5 sums into ``DEBIAN/md5sums``, rebuilds the package with
``dpkg-deb --root-owner-group`` and the compressor of the package's data member, keeps the
unsigned package in ``--unsigned-dir`` and writes a JSON record of the step to ``--record``.
``SOURCE_DATE_EPOCH`` must be set, as in the build: dpkg-deb clamps the times of the files
signing touched to it, so the same key over the same package writes the same bytes (an RSA
signature without signed attributes is deterministic).

Exit status 0 when every module is signed, 1 when the step is refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

MARKER = b"~Module signature appended~\n"
# struct module_signature (include/linux/module_signature.h): algo, hash, id_type, signer_len,
# key_id_len, __pad[3], sig_len (big endian); id_type 2 is PKEY_ID_PKCS7.
SIGNATURE_INFO_BYTES = 12
PKEY_ID_PKCS7 = 2
COMPRESSED_SUFFIXES = (".ko.gz", ".ko.xz", ".ko.zst")
# The data member's suffix and the dpkg-deb -Z compressor that writes it.
COMPRESSORS = {
    "data.tar.zst": "zstd",
    "data.tar.xz": "xz",
    "data.tar.gz": "gzip",
    "data.tar": "none",
}
AR_MAGIC = b"!<arch>\n"
AR_HEADER_BYTES = 60
MAX_AR_MEMBERS = 16


class SigningError(RuntimeError):
    """The package cannot be signed as asked."""


def run(argv: Sequence[str], what: str) -> bytes:
    result = subprocess.run(list(argv), capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip().splitlines()[-1:] or [""]
        raise SigningError(f"{what} failed (exit {result.returncode}): {detail[0]}")
    return result.stdout


def split_signature(data: bytes) -> tuple[bytes, bytes] | None:
    """The module's content and its PKCS#7 signature, or None when no valid one is appended."""
    if not data.endswith(MARKER):
        return None
    body = data[: -len(MARKER)]
    if len(body) < SIGNATURE_INFO_BYTES:
        return None
    info = body[-SIGNATURE_INFO_BYTES:]
    sig_len = int.from_bytes(info[8:12], "big")
    if info[2] != PKEY_ID_PKCS7 or sig_len == 0 or sig_len > len(body) - SIGNATURE_INFO_BYTES:
        return None
    end = len(body) - SIGNATURE_INFO_BYTES
    return body[: end - sig_len], body[end - sig_len : end]


def data_compressor(deb: Path) -> str:
    """The -Z compressor of a package's data member, read from the ar archive's headers."""
    with deb.open("rb") as handle:
        if handle.read(len(AR_MAGIC)) != AR_MAGIC:
            raise SigningError(f"{deb.name} is not an ar archive")
        for _ in range(MAX_AR_MEMBERS):
            header = handle.read(AR_HEADER_BYTES)
            if len(header) < AR_HEADER_BYTES:
                break
            name = header[:16].decode("ascii", "replace").strip().rstrip("/")
            if name in COMPRESSORS:
                return COMPRESSORS[name]
            size = int(header[48:58].decode("ascii").strip() or 0)
            handle.seek(size + size % 2, os.SEEK_CUR)
    raise SigningError(f"{deb.name} carries no data member dpkg-deb can rebuild")


def certificate_der(key: Path, out: Path) -> bytes:
    """Write the key file's certificate to ``out`` as PEM and return its DER bytes."""
    run(["openssl", "x509", "-in", str(key), "-out", str(out)], "openssl x509 (PEM)")
    return run(["openssl", "x509", "-in", str(key), "-outform", "DER"], "openssl x509 (DER)")


def check_trusted(der: bytes, vmlinux: Path) -> None:
    with vmlinux.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as m:
        if m.find(der) < 0:
            raise SigningError(f"{vmlinux} does not carry the certificate of the signing key")


def find_modules(root: Path) -> list[Path]:
    compressed = sorted(p for p in root.rglob("*") if p.name.endswith(COMPRESSED_SUFFIXES))
    if compressed:
        raise SigningError(f"compressed modules cannot be signed here: {compressed[0].name}")
    modules = sorted(p for p in root.rglob("*.ko") if p.is_file() and "DEBIAN" not in p.parts)
    if not modules:
        raise SigningError("the package carries no kernel module")
    for module in modules:
        if module.read_bytes().endswith(MARKER):
            raise SigningError(
                f"{module.relative_to(root)} is already signed; the build must not sign "
                "(CONFIG_MODULE_SIG_ALL)"
            )
    return modules


def verify(module: Path, cert: Path, scratch: Path) -> None:
    parts = split_signature(module.read_bytes())
    if parts is None:
        raise SigningError(f"{module.name}: sign-file appended no PKCS#7 signature")
    content, signature = (scratch / "content", scratch / "signature")
    content.write_bytes(parts[0])
    signature.write_bytes(parts[1])
    run(
        ["openssl", "cms", "-verify", "-binary", "-inform", "DER", "-in", str(signature),
         "-content", str(content), "-certfile", str(cert), "-nointern", "-noverify",
         "-out", os.devnull],
        f"openssl cms -verify of {module.name}",
    )  # fmt: skip


def rewrite_md5sums(root: Path, modules: Sequence[Path]) -> None:
    md5sums = root / "DEBIAN" / "md5sums"
    # dpkg's md5sums format; integrity against corruption, not a security digest.
    sums = {
        m.relative_to(root).as_posix(): hashlib.md5(
            m.read_bytes(), usedforsecurity=False
        ).hexdigest()
        for m in modules
    }
    lines = []
    for line in md5sums.read_text(encoding="utf-8").splitlines():
        _, sep, path = line.partition("  ")
        lines.append(f"{sums.pop(path)}  {path}" if sep and path in sums else line)
    if sums:
        raise SigningError(f"DEBIAN/md5sums lists no {next(iter(sums))}")
    md5sums.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sign_package(args: argparse.Namespace, scratch: Path) -> dict[str, object]:
    cert = scratch / "certificate.pem"
    der = certificate_der(args.key, cert)
    check_trusted(der, args.vmlinux)
    root = scratch / "root"
    run(["dpkg-deb", "-R", str(args.deb), str(root)], f"dpkg-deb -R {args.deb.name}")
    modules = find_modules(root)
    for module in modules:
        run(
            [str(args.sign_file), args.hash, str(args.key), str(cert), str(module)],
            f"sign-file {module.name}",
        )
        verify(module, cert, scratch)
    rewrite_md5sums(root, modules)
    signed = scratch / args.deb.name
    run(
        ["dpkg-deb", f"-Z{data_compressor(args.deb)}", "--root-owner-group", "--build",
         str(root), str(signed)],
        "dpkg-deb --build",
    )  # fmt: skip
    return {
        "certificate_sha256": hashlib.sha256(der).hexdigest(),
        "hash": args.hash,
        "modules": len(modules),
        "unsigned_package_sha256": hashlib.sha256(args.deb.read_bytes()).hexdigest(),
        "signed_package_sha256": hashlib.sha256(signed.read_bytes()).hexdigest(),
        "_signed": signed,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--deb", type=Path, required=True, help="the linux-image package")
    parser.add_argument("--key", type=Path, required=True, help="PEM: private key and certificate")
    parser.add_argument(
        "--sign-file", type=Path, required=True, help="the build's scripts/sign-file"
    )
    parser.add_argument("--hash", required=True, help="CONFIG_MODULE_SIG_HASH of the build")
    parser.add_argument("--vmlinux", type=Path, required=True, help="the build's vmlinux")
    parser.add_argument(
        "--unsigned-dir", type=Path, required=True, help="keeps the unsigned package"
    )
    parser.add_argument("--record", type=Path, required=True, help="JSON record of this step")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.hash.isalnum():
            raise SigningError(f"--hash must be a hash name such as sha512, not {args.hash!r}")
        if not os.environ.get("SOURCE_DATE_EPOCH", "").isdigit():
            raise SigningError("SOURCE_DATE_EPOCH must be set to the build's epoch")
        for tool in ("openssl", "dpkg-deb"):
            if shutil.which(tool) is None:
                raise SigningError(f"{tool} is required and is not installed")
        with tempfile.TemporaryDirectory(prefix="sign-modules-") as tmp:
            record = sign_package(args, Path(tmp))
            args.unsigned_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.deb, args.unsigned_dir / args.deb.name)
            shutil.move(str(record.pop("_signed")), str(args.deb))
    except (SigningError, OSError) as exc:
        print(f"sign_kernel_modules: refused: {exc}", file=sys.stderr)
        return 1
    args.record.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"==> Signed {record['modules']} modules in {args.deb.name} ({args.hash})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
