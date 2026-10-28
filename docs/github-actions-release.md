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
