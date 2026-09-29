"""scripts/fetch-kernel-source.sh proves a source before writing a tree, or refuses.

Every case runs the script against a throwaway signing key, a fake kernel tarball and a
local git repository with signed tags, so nothing leaves the machine and no real key is
involved. The same checks guard the real kernel.org sources in versions.json.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "fetch-kernel-source.sh"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import versions_query as vq  # noqa: E402

TOOLS = ("gpg", "gpgv", "git", "xz", "curl", "sha256sum", "tar")
pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in TOOLS),
    reason=f"needs {', '.join(TOOLS)}",
)

STABLE_MAKEFILE = "VERSION = 1\nPATCHLEVEL = 2\nSUBLEVEL = 3\nEXTRAVERSION =\nNAME = Test\n"
RC_MAKEFILE = "VERSION = 1\nPATCHLEVEL = 3\nSUBLEVEL = 0\nEXTRAVERSION = -rc1\nNAME = Test\n"


def _run(cmd, env=None, cwd=None, stdin=None):
    return subprocess.run(
        cmd, env=env, cwd=cwd, input=stdin, capture_output=True, check=True
    ).stdout


class Signer:
    """A throwaway GnuPG home with a trusted and a stranger signing key."""

    def __init__(self, home: Path):
        self.home = home
        home.mkdir(mode=0o700)
        self.env = {**os.environ, "GNUPGHOME": str(home)}
        self.trusted = self._generate("Trusted Signer <trusted@example.invalid>")
        self.stranger = self._generate("Stranger <stranger@example.invalid>")

    def _generate(self, uid: str) -> str:
        _run(
            [
                "gpg",
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-gen-key",
                uid,
                "ed25519",
                "sign",
                "never",
            ],
            env=self.env,
        )
        listing = _run(["gpg", "--batch", "--with-colons", "--list-keys", uid], env=self.env)
        fprs = [
            line.split(":")[9] for line in listing.decode().splitlines() if line.startswith("fpr:")
        ]
        return fprs[0]

    def export(self, fpr: str, keys: Path) -> None:
        keys.mkdir(exist_ok=True)
        armored = _run(["gpg", "--batch", "--armor", "--export", fpr], env=self.env)
        (keys / f"{fpr}.asc").write_bytes(armored)

    def sign_tar(self, fpr: str, tar_xz: Path, signature: Path) -> None:
        tar = _run(["xz", "-cd", str(tar_xz)])
        _run(
            [
                "gpg",
                "--batch",
                "--yes",
                "--local-user",
                fpr,
                "--armor",
                "--detach-sign",
                "--output",
                str(signature),
                "-",
            ],
            env=self.env,
            stdin=tar,
        )


def _tarball(root: Path, name: str, makefile: str, payload: str) -> Path:
    tree = root / name
    tree.mkdir(parents=True)
    (tree / "Makefile").write_text(makefile, encoding="utf-8")
    (tree / "README").write_text(payload, encoding="utf-8")
    archive = root / f"{name}.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        tar.add(tree, arcname=name)
    return archive


def _git(repo: Path, *args: str, env: dict) -> str:
    return _run(["git", "-C", str(repo), *args], env=env).decode().strip()


def _repository(root: Path, signer: Signer) -> tuple[Path, str]:
    """A bare repository: v1.3-rc1 signed by the trusted key, v1.3-rc2 by the stranger,
    v1.3-rc3 annotated and unsigned. Returns the repository and the tagged commit."""
    env = {**signer.env, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    work = root / "work"
    work.mkdir(parents=True)
    _run(["git", "init", "-q", str(work)], env=env)
    (work / "Makefile").write_text(RC_MAKEFILE, encoding="utf-8")
    ident = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    _git(work, "add", "Makefile", env=env)
    _git(work, *ident, "commit", "-q", "-m", "Linux 1.3-rc1", env=env)
    commit = _git(work, "rev-parse", "HEAD", env=env)
    for tag, key in (("v1.3-rc1", signer.trusted), ("v1.3-rc2", signer.stranger)):
        _git(work, *ident, "-c", f"user.signingkey={key}", "tag", "-s", tag, "-m", tag, env=env)
    _git(work, *ident, "tag", "-a", "v1.3-rc3", "-m", "unsigned", env=env)
    bare = root / "linux.git"
    _run(["git", "clone", "-q", "--bare", str(work), str(bare)], env=env)
    return bare, commit


@pytest.fixture(scope="module")
def forge(tmp_path_factory):
    """Keys, a signed tarball, a tarball signed by the stranger, and a tagged repository."""
    root = tmp_path_factory.mktemp("forge")
    signer = Signer(root / "gnupg")
    keys = root / "keys"
    signer.export(signer.trusted, keys)
    signer.export(signer.stranger, keys)
    good = _tarball(root / "good", "linux-1.2.3", STABLE_MAKEFILE, "the verified tree\n")
    signer.sign_tar(signer.trusted, good, root / "good" / "linux-1.2.3.tar.sign")
    other = _tarball(root / "other", "linux-1.2.3", STABLE_MAKEFILE, "a different tree\n")
    signer.sign_tar(signer.trusted, other, root / "other" / "linux-1.2.3.tar.sign")
    signer.sign_tar(signer.stranger, good, root / "good" / "stranger.tar.sign")
    repo, commit = _repository(root / "git", signer)
    yield {"root": root, "signer": signer, "keys": keys, "repo": repo, "commit": commit}
    subprocess.run(["gpgconf", "--kill", "gpg-agent"], env=signer.env, check=False)


def _tarball_source(forge, **change) -> dict:
    good = forge["root"] / "good" / "linux-1.2.3.tar.xz"
    source = {
        "kind": "tarball",
        "url": good.as_uri(),
        "signature_url": (good.parent / "linux-1.2.3.tar.sign").as_uri(),
        "sha256": hashlib.sha256(good.read_bytes()).hexdigest(),
        "signers": [forge["signer"].trusted],
    }
    return {**source, **change}


def _tag_source(forge, **change) -> dict:
    source = {
        "kind": "git-tag",
        "repository": forge["repo"].as_uri(),
        "tag": "v1.3-rc1",
        "commit": forge["commit"],
        "signers": [forge["signer"].trusted],
    }
    return {**source, **change}


def _fetch(tmp_path, forge, source, version="1.2.3", stream="stable", keys=None):
    manifest = {"streams": {"stable": {"version": version, "tag": f"v{version}", "source": source}}}
    versions = tmp_path / "versions.json"
    versions.write_text(json.dumps(manifest), encoding="utf-8")
    dest = tmp_path / "dest"
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--stream={stream}",
            f"--dest={dest}",
            f"--work={tmp_path / 'work'}",
            f"--versions={versions}",
            f"--keys={keys or forge['keys']}",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, dest


def _refused(result, dest, reason):
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Refused:" in result.stderr and reason in result.stderr, result.stderr
    assert not dest.exists() or not any(dest.iterdir()), "no tree may be written on refusal"


# --- tarball -----------------------------------------------------------------------------------


def test_a_signed_pinned_tarball_is_extracted(tmp_path, forge):
    result, dest = _fetch(tmp_path, forge, _tarball_source(forge))
    assert result.returncode == 0, result.stderr
    assert (dest / "README").read_text() == "the verified tree\n"
    assert f"good signature by {forge['signer'].trusted}" in result.stdout


def test_a_wrong_sha256_is_refused(tmp_path, forge):
    result, dest = _fetch(tmp_path, forge, _tarball_source(forge, sha256="0" * 64))
    _refused(result, dest, "versions.json pins " + "0" * 64)


def test_a_signature_over_other_content_is_refused(tmp_path, forge):
    other = (forge["root"] / "other" / "linux-1.2.3.tar.sign").as_uri()
    result, dest = _fetch(tmp_path, forge, _tarball_source(forge, signature_url=other))
    _refused(result, dest, "the signature does not verify (BADSIG)")


def test_a_missing_signature_is_refused(tmp_path, forge):
    missing = (forge["root"] / "good" / "absent.tar.sign").as_uri()
    result, dest = _fetch(tmp_path, forge, _tarball_source(forge, signature_url=missing))
    _refused(result, dest, "could not download")


def test_a_signature_by_a_key_the_stream_does_not_list_is_refused(tmp_path, forge):
    stranger = (forge["root"] / "good" / "stranger.tar.sign").as_uri()
    result, dest = _fetch(tmp_path, forge, _tarball_source(forge, signature_url=stranger))
    _refused(result, dest, "the signature does not verify (ERRSIG NO_PUBKEY)")


def test_a_key_file_that_holds_another_key_does_not_make_it_a_signer(tmp_path, forge):
    """The keyring is built from the listed names, and the signature must name a listed key."""
    signer = forge["signer"]
    keys = tmp_path / "keys"
    keys.mkdir()
    stranger_key = (forge["keys"] / f"{signer.stranger}.asc").read_bytes()
    (keys / f"{signer.trusted}.asc").write_bytes(stranger_key)
    stranger = (forge["root"] / "good" / "stranger.tar.sign").as_uri()
    source = _tarball_source(forge, signature_url=stranger)
    result, dest = _fetch(tmp_path, forge, source, keys=keys)
    _refused(result, dest, f"signed by {signer.stranger}, which versions.json does not list")


def test_a_signer_without_a_public_key_is_refused(tmp_path, forge):
    keys = tmp_path / "keys"
    keys.mkdir()
    result, dest = _fetch(tmp_path, forge, _tarball_source(forge), keys=keys)
    _refused(result, dest, "has no public key")


def test_a_tree_that_is_not_the_named_release_is_refused_and_removed(tmp_path, forge):
    result, dest = _fetch(tmp_path, forge, _tarball_source(forge), version="1.2.4")
    _refused(result, dest, "the tree is kernel '1.2.3', versions.json names 1.2.4")


def test_an_unknown_stream_is_refused(tmp_path, forge):
    result, dest = _fetch(tmp_path, forge, _tarball_source(forge), stream="nonexistent")
    _refused(result, dest, "declares no usable source for stream 'nonexistent'")


def test_a_non_empty_destination_is_refused(tmp_path, forge):
    (tmp_path / "dest").mkdir()
    (tmp_path / "dest" / "stale").write_text("x")
    result, _ = _fetch(tmp_path, forge, _tarball_source(forge))
    assert result.returncode == 1 and "is not empty" in result.stderr


# --- git tag -----------------------------------------------------------------------------------


def test_a_signed_tag_at_the_pinned_commit_is_exported(tmp_path, forge):
    result, dest = _fetch(tmp_path, forge, _tag_source(forge), version="1.3-rc1")
    assert result.returncode == 0, result.stderr
    assert (dest / "Makefile").read_text() == RC_MAKEFILE
    assert not (dest / ".git").exists()
    assert "points at the pinned commit" in result.stdout


def test_a_tag_at_another_commit_is_refused(tmp_path, forge):
    source = _tag_source(forge, commit="1" * 40)
    result, dest = _fetch(tmp_path, forge, source, version="1.3-rc1")
    _refused(result, dest, "versions.json pins " + "1" * 40)


def test_a_tag_signed_by_a_key_the_stream_does_not_list_is_refused(tmp_path, forge):
    source = _tag_source(forge, tag="v1.3-rc2")
    result, dest = _fetch(tmp_path, forge, source, version="1.3-rc2")
    _refused(result, dest, "tag v1.3-rc2: the signature does not verify (ERRSIG NO_PUBKEY)")


def test_an_unsigned_tag_is_refused(tmp_path, forge):
    source = _tag_source(forge, tag="v1.3-rc3")
    result, dest = _fetch(tmp_path, forge, source, version="1.3-rc3")
    _refused(result, dest, "tag v1.3-rc3: no good signature")


def test_a_ref_that_names_another_signed_tag_object_is_refused(tmp_path, forge):
    """A signed tag object is proof only for the tag it names, not for a ref pointing at it.

    Here refs/tags/v1.3-rc4 points at the tag object of v1.3-rc1: the signature is good, by a
    listed signer, and the tag points at the pinned commit, so only the name check refuses it.
    """
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    moved = tmp_path / "moved.git"
    _run(["git", "clone", "-q", "--bare", str(forge["repo"]), str(moved)], env=env)
    signed = _git(moved, "rev-parse", "refs/tags/v1.3-rc1", env=env)
    _git(moved, "update-ref", "refs/tags/v1.3-rc4", signed, env=env)
    source = _tag_source(forge, repository=moved.as_uri(), tag="v1.3-rc4")
    result, dest = _fetch(tmp_path, forge, source, version="1.3-rc4")
    _refused(result, dest, "the signed tag object names 'v1.3-rc1', not v1.3-rc4")


# --- versions_query ----------------------------------------------------------------------------


def test_the_committed_streams_and_architectures_query_cleanly():
    versions = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
    for stream in versions["streams"]:
        fields = dict(vq.source_fields(versions, stream))
        assert fields["kind"] in ("tarball", "git-tag")
    for arch in versions["architectures"]:
        assert [key for key, _ in vq.arch_fields(versions, arch)] == [
            "kernel_arch",
            "base_config",
            "cross_compile",
            "debian_arch",
        ]


@pytest.mark.parametrize(
    ("version", "expected"),
    [("7.3-rc5", "7.3.0-rc5"), ("7.2.8", "7.2.8"), ("6.18", "6.18.0"), ("6.18.54", "6.18.54")],
)
def test_kernelversion_is_what_make_prints(version, expected):
    assert vq.kernelversion(version) == expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [("7.3-rc5", "7.3~rc5"), ("7.3-rc12", "7.3~rc12"), ("7.2.8", "7.2.8"), ("6.18", "6.18")],
)
def test_debian_version_orders_a_release_candidate_before_its_release(version, expected):
    assert vq.debian_version(version) == expected
    versions = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
    fields = dict(vq.source_fields(versions, "bleeding"))
    assert fields["debian_version"] == vq.debian_version(fields["version"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("url", "http://cdn.example.invalid/linux-1.2.3.tar.xz"),
        ("url", "https://cdn.example.invalid/linux 1.2.3.tar.xz"),
        ("sha256", "ABC"),
        ("signers", ["abaf11c65a2970b130abe3c479be3e4300411886"]),
        ("signers", []),
        ("kind", "zip"),
    ],
)
def test_a_source_field_outside_its_shape_is_refused(field, value):
    source = {
        "kind": "tarball",
        "url": "https://cdn.example.invalid/linux-1.2.3.tar.xz",
        "signature_url": "https://cdn.example.invalid/linux-1.2.3.tar.sign",
        "sha256": "0" * 64,
        "signers": ["ABAF11C65A2970B130ABE3C479BE3E4300411886"],
        field: value,
    }
    versions = {"streams": {"s": {"version": "1.2.3", "source": source}}}
    with pytest.raises(vq.QueryError):
        vq.source_fields(versions, "s")
