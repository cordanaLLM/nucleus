# Curated Patch Queue & Upstreaming Policy

> Rules governing the inclusion, maintenance, and upstreaming of custom Linux kernel patches in `cordanaLLM/nucleus`.

---

## 1. Patch Inclusion Criteria

Every patch introduced into `cordanaLLM/nucleus` must meet all of the following conditions:

1. **Targeted Justification**: Must address a verified performance, hardware enablement, or security need not yet merged in the upstream release branch.
2. **Upstream Path**: Must either be queued in an upstream maintainer tree (e.g. `tip.git`, `net-next.git`, `drm-next.git`) or explicitly tracked against an upstream LKML discussion.
3. **Clean Application**: Must apply cleanly with `patch -p1` with zero rejects (`*.rej`).
4. **Isolated Scope**: Patches should touch the minimal set of files required and adhere to Linux kernel coding style (`scripts/checkpatch.pl`).

---

## 2. Directory Layout

```text
patches/
├── common/             # Patches applied to every stream
├── <series>/           # A series pinned under "patches" in versions.json (patches/bbr3/)
└── <stream>/           # Patches scoped to one stream
```

`patches/common/` applies to every stream, `patches/<series>/` to the streams its entry in `versions.json` names, and `patches/<stream>/` only to the stream of that name, which must be declared in `versions.json`. A stream's patches are common, then each series naming it in name order, then its own, and they apply as one series: each patch on top of the ones before it, with `patch -p1 -F0`, so a patch that needs fuzz is refused like one that does not apply.

`scripts/check_patches_apply.py` refuses a directory that is none of these, a file that is not a `.patch`, a series whose files are not its pinned commits in order (file N must start with `From`, a space and `commits[N]`), and a patch that does not apply. Without `--apply` it changes nothing: it copies the files the patches name to a scratch directory and applies the series there. With `--apply` it dry-runs each patch before applying it, so a refusal leaves no half-applied patch, pins the files the series changed to the mtime of the tree's `Makefile` (the release time `SOURCE_DATE_EPOCH` is read from), and writes `.nucleus-patches.json` with each patch and its SHA-256; a tree that already holds that file is refused. A refusal after the first patch leaves the earlier patches applied and the message names them; fetch a fresh tree.

- `scripts/build_kernel.sh` applies the patches right after it fetched and verified the source, before it resolves the configuration (`apply_patches`, and the `patches:` line of `--dry-run`).
- The `resolve` job of `.github/workflows/verify-requirements.yml` applies them before `Resolve the Configuration`, because the resolved `.config` needs the Kconfig symbols a series adds. Without them `scripts/kconfig_survival.py` refuses `CONFIG_TCP_CONG_BBR3=m` as `resolved unrecorded`.
- On the `lts` leg, step `Refuse a Patch of Another Base` binds every series to `lts` in a copy of `versions.json` and requires the check to fail with `does not apply to`, because 6.18 is not a base of `bbr3`. That step is the planted defect: if a series ever applies to `lts`, the step fails.

To apply the patches by hand before `make resolve-config SOURCE_TREE=<dir>`:

```bash
python3 scripts/check_patches_apply.py --stream=<stream> --source-tree=<dir> --apply
```

Changes to Linux kernel files carry the kernel's licence, `GPL-2.0-only`, through the `patches/**` annotation in `REUSE.toml`; `patches/bbr3/**` has its own annotation (below).

### The `bbr3` series

`patches/bbr3/` is BBRv3 from Google's BBR repository, <https://github.com/google/bbr>, branch `bbr-v3-2026-09-16-01`. On 2026-10-17 that was the newest BBRv3 branch: its newest tag, `bbrv3-2025-03-18`, is the commit its `v3` branch still points at, so the dated branch is eighteen months newer than any tag. A branch moves, so `versions.json` pins the series by commit:

| Key | Value |
| :--- | :--- |
| `commit` | `674859761ded9f32690c7bdaf22ef585531452ed`, the branch head that was taken |
| `base` | `c1c3d01e3a9038d3e8f497e773e1f7b5d6b8212a`, the `net-next` parent of the first patch (Linux 7.1-rc5) |
| `commits` | the 13 kernel commits of the series, in order; the branch's later commits change only its test scripts, `iproute2` and the README, and are not carried |
| `streams` | `bleeding` (7.3-rc7), `mainstream` and `realtime` (7.2.10) |

Twelve patches extend TCP rate sampling and the congestion-control hooks (for example `skb_marked_lost()` and loss counts repaired by TLP). The thirteenth adds `net/ipv4/tcp_bbr3.c` as its own module, `tcp_bbr3` with algorithm name `bbr3`, and the option `CONFIG_TCP_CONG_BBR3`. BBRv1 (`tcp_bbr`) is left as it is and stays the default (`CONFIG_DEFAULT_BBR=y`). The streams that carry the series build BBRv3 as a module (`CONFIG_TCP_CONG_BBR3=m` in `kconfig/streams/<stream>.config`), and the `bbr3` claim in `kconfig/required-config.json` refuses a resolved configuration where it is off. A node selects it with `sysctl net.ipv4.tcp_congestion_control=bbr3`.

The series applies to 7.2.10 and 7.3-rc7 without fuzz, and `net/ipv4/` compiles there without warnings. It does not apply to `lts` 6.18.56: patch `0002` refuses both hunks in `net/ipv4/tcp_input.c`. `lts` carries no BBRv3.

Criterion 2 of section 1 holds only in part: its authors maintain the series out of tree at google/bbr, and it is not queued in a maintainer tree. Issue #192 asked for it.

`net/ipv4/tcp_bbr3.c` is licensed `GPL-2.0 OR BSD-3-Clause` by its authors, and the other changes are under the kernel's `GPL-2.0-only`. `REUSE.toml` states `GPL-2.0-only AND (GPL-2.0-only OR BSD-3-Clause)` for `patches/bbr3/**`, with Google LLC and the Linux kernel authors as copyright holders, and `LICENSES/` carries both texts.

To move the series to a newer branch:

1. Find the newest branch with `gh api repos/google/bbr/branches --paginate --jq '.[].name'`, and its commit list with `gh api repos/google/bbr/compare/<base>...<branch> --jq '.commits[] | [.sha, .commit.message] | @tsv'`.
2. Export each kernel commit unchanged: `gh api -H "Accept: application/vnd.github.patch" repos/google/bbr/commits/<sha> > patches/bbr3/NNNN-<slug>.patch`.
3. Update `ref`, `commit`, `base` and `commits` in `versions.json`.
4. Run `scripts/check_patches_apply.py` against each stream's verified tree, then `make lint` and `make test`, then the build matrix (`gh workflow run build-matrix.yml -R cordanaLLM/nucleus --ref <branch> -f dry_run=false`).

---

## 3. Retiring Patches

When an upstream release incorporates a previously backported patch:

1. Remove the corresponding patch file from `patches/common/` or `patches/<stream>/`, or a whole series with its entry under `patches` in `versions.json`.
2. Document the retirement in the PR commit message.
3. Re-verify the build pipeline with `scripts/build_kernel.sh`.
