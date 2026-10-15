# Upstream signing keys

The public keys `scripts/fetch-kernel-source.sh` and `scripts/check-openzfs-module.sh` verify
kernel and module sources with. Each file is the armored, minimal export of one key, named by its
primary-key fingerprint, and a stream or module source accepts only the keys its `versions.json`
lists in `signers`. The policy is
[ADR-0008](../docs/adr/0008-signed-kernel-sources-and-resolved-configuration.md).

| File | Key | Signs | Used by |
| :--- | :--- | :--- | :--- |
| `ABAF11C65A2970B130ABE3C479BE3E4300411886.asc` | Linus Torvalds `<torvalds@kernel.org>`, RSA 2048 | mainline tags and `x.y` release tarballs | `bleeding` (`git verify-tag`) |
| `647F28654894E3BD457199BE38DBBDC86092693E.asc` | Greg Kroah-Hartman `<gregkh@kernel.org>`, RSA 4096 | stable and longterm release tarballs (`.tar.sign`) | `mainstream`, `lts`, `realtime` (`gpgv`) |
| `B8868C80BA62A1FFFAF5FDA9632D3A06589DA6B1.asc` | Kernel.org checksum autosigner `<autosigner@kernel.org>`, RSA 4096 | `sha256sums.asc` in each release directory | no stream; checks the `sha256` of a tarball when a stream is bumped |
| `4F3BA9AB6D1F8D683DC2DFB56AD860EED4598027.asc` | Tony Hutter `<hutter2@llnl.gov>`, RSA 4096 | OpenZFS release tarballs (`zfs-*.tar.gz.asc`) | `modules.openzfs` (`gpgv`) |

## Provenance

### kernel.org developer keys

<https://www.kernel.org/signature.html> names the developer keys and says to fetch them from
the kernel.org Web Key Directory rather than a keyserver. The three files were fetched on
2026-09-29 with:

```bash
export GNUPGHOME="$(mktemp -d)"
gpg --locate-keys torvalds@kernel.org gregkh@kernel.org autosigner@kernel.org
gpg --fingerprint torvalds@kernel.org gregkh@kernel.org autosigner@kernel.org
```

The fingerprints printed matched the Torvalds and Kroah-Hartman fingerprints on
signature.html. The autosigner fingerprint is not on that page; it is the key the page's
section "Kernel.org checksum autosigner and sha256sums.asc" describes, and the one that signs
`sha256sums.asc` (checked with `gpgv` against `v7.x/sha256sums.asc` and `v6.x/sha256sums.asc`).
The autosigner signature is a mirror check that signature.html says does not replace a
developer signature, which is why no stream lists it as a signer.

### OpenZFS release key

The OpenZFS release key (`4F3BA9AB6D1F8D683DC2DFB56AD860EED4598027.asc`, Tony Hutter) was
fetched 2026-10-13 from keyserver.ubuntu.com, fingerprint cross-checked against Debian
`zfs-linux` 2.4.4-1 `debian/upstream/signing-key.asc`, and `gpgv` verified
`zfs-2.3.9.tar.gz.asc` and, on 2026-10-14, `zfs-2.4.4.tar.gz.asc`.

## Refreshing a key

A key changes when its owner adds a subkey or a signature, or extends an expiry. Fetch it again
through WKD in a fresh `GNUPGHOME`, check that the primary fingerprint is unchanged, and
export it over the old file:

```bash
gpg --locate-keys gregkh@kernel.org
gpg --armor --export-options export-minimal \
  --export 647F28654894E3BD457199BE38DBBDC86092693E > keys/647F28654894E3BD457199BE38DBBDC86092693E.asc
```

A new signer is a new file named by its fingerprint, and the fingerprint is added to the
`signers` of the streams it signs for. `tests/test_manifest.py` checks that every listed signer
has a file here.

## gpgv reads a binary keyring

`gpgv` does not read an armored key file: given one as `--keyring` it reports
`invalid packet (ctb=2d)` and treats the signature as made by an unknown key. The fetch script
therefore dearmors the keys of the stream's signers into a binary keyring first:

```bash
gpg --dearmor --output - < keys/647F28654894E3BD457199BE38DBBDC86092693E.asc > signers.gpg
xz -cd linux-7.2.10.tar.xz | gpgv --keyring ./signers.gpg linux-7.2.10.tar.sign -
```

`gpgv` also reads a `--keyring` name without a slash from its home directory, so the path must
contain one.
