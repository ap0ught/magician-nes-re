# GitHub Actions ROM build and releases

Workflow: [`.github/workflows/release.yml`](../.github/workflows/release.yml).

## Prerequisites

The project needs the author's locally owned **Magician (USA) (Beta 1) (1990-03-02).nes** to run `make rom`. It is **not in Git** and must not be committed. Install a trusted, dedicated **self-hosted Linux GitHub Actions runner** with labels `self-hosted`, `linux`, and `magician-rom-builder`. Install `python3` and `make`. The reference ROM must be accessible at the path printed by `python3 tools/cartref.py` (currently resolved via `asm/patches.py:CART_DIR`). The runner must be trusted and restricted to this repository; workflows execute local code and can read files on the runner.

Before enabling public releases, verify you have the rights to distribute the resulting game ROM, including the incorporated proprietary game assets. A workflow artifact is not the same as a public GitHub Release asset.

## Behavior

- Manually start **Build and release Magician NES** from Actions with `release_tag` empty to run tests, build and validate the ROM, then upload a workflow artifact (14-day retention). No GitHub Release is created.
- To publish a release, create/push an existing `vX.Y.Z` tag or manually dispatch the workflow with the name of an **existing** release tag. The workflow builds on the self-hosted runner, then publishes `magician-rebuilt.nes` to the tagged GitHub Release using `gh`.
- GitHub release assets are downloadable publicly when the repository/release is public. Do not trigger a tagged release unless redistribution is authorized.
- The workflow does not commit the `.nes` file to Git. `*.nes` remains ignored.
- The manual build on a feature branch will only become selectable in the Actions UI after the workflow is merged into the repository's default branch. A tag pointing to a commit containing the workflow can also trigger it.

## Verification

The workflow validates iNES signature, exact 262,160-byte size, and Beta 1 header fields; it prints SHA-256. Full assembly correctness remains the responsibility of `make rom`, `make check-py`, and the project's emulator tests. The workflow has **not been executed on a runner yet**.

## Retail IPS included in releases

The workflow also decodes the committed text asset `ips/retail-modules.ips.b64` into `retail-modules.ips`, validates its SHA-256 and the 23 IPS records / 662 payload bytes, and uploads it as a separate workflow artifact and release asset. The asset is the user's supplied 785-byte retail patch, not a rebuilt Beta 1 patch. The encoded patch SHA-256 is `11f0b4e51e41086a4e8f1c3cd261df9b8da831f8f24ba3369abf338abb9911a5`.

**Compatibility:** This IPS applies to the unmodified **retail** `Magician (USA).nes` with full-file SHA-1 `7b0da149c3892b9aec3e97400b79e1089cb8cc47`; the expected patched ROM SHA-1 is `ef8bb81bd6da2af0ee4b82671a636ba3a506e8eb`. It does **not** apply to the Beta 1 `magician-rebuilt.nes` produced by the current HUD branch. The IPS represents seven retail-addressed modules; it does not hook Inspect and is not a complete new HUD. See the supplied `21-retail-ips-howto.md` for the distinct `baseline/release-ips` branch procedure. That branch was not available in the linked GitHub repository at integration time, so the workflow packages the exact supplied patch rather than claiming to regenerate it from that branch.

**Release outputs:** `magician-rebuilt.nes` (Beta 1 based) and `retail-modules.ips` (retail-only patch) are separate assets. Do not apply the IPS to the rebuilt ROM. The GitHub workflow was not executed during this change; a configured self-hosted runner is still required for `make rom`.
