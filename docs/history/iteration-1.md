# OrangeFox SM-T583 - Iteration 1 Handoff

Date: 2026-10-26
Workspace: `C:\Auto-TWRP-Builder-New`
Branch: `orangefox`

## Goal

Prepare a separate OrangeFox Android 16 build workflow for Samsung SM-T583 (`gtaxladwifikx`, Exynos 7870), while preserving the existing TWRP workflow and its local work.

## What changed

- Added `.github/workflows/OrangeFox Build_SM-T583.yml` in commit `070d68ae5cf63e8c16053dffc119a08aec0f0fe9`.
- Pushed that commit to `origin/orangefox`; local and remote HEAD matched after push.
- Follow-up commit `c3cd7748391a73a1cbc17bf0b37bb0182175a222` changed the workflow to consume the prior TWRP Actions artifact instead of requiring a URL. It is also pushed to `origin/orangefox`.
- Added the workflow file alone to default branch `master` in commit `49a276d` so GitHub would allow `workflow_dispatch`; the existing TWRP workflow and unrelated master content were not included.
- Run `40306963340` failed at `Initialize OrangeFox manifest`: GitLab required authentication for `sm-t583/Manifest`.
- Added non-interactive `GIT_ASKPASS` support using the repository secret `GITLAB_MANIFEST_TOKEN`; it is not embedded in a URL or printed. The fix was pushed to `orangefox` as `2ba18da` and mirrored to `master` as `aa42622`.
- Current remote heads: `orangefox` = `2ba18da4124b7383eee37bc0a7f2515ba73967fe`; `master` = `aa4262285271b37afda062396acf0f9fe71067eb`.
- GitHub now lists `OrangeFox Recovery Build (SM-T583)` as workflow ID `396912684`.
- User owns workflow dispatch. Do not trigger another run; the user must first configure the GitHub Actions secret with a GitLab token that has `read_repository` access.
- The workflow uses the `sm-t583/Manifest` repository at manifest commit `c5bc4610ea5ace4b2d1f74278b99574e93adf6f8` and removes the Xiaomi `mondrian` and `sm84xx-common` device projects from the resolved manifest.
- GitHub Actions dependencies are pinned to commit SHAs. `actions/checkout` has `persist-credentials: false`.
- The workflow downloads artifact `twrp-gtaxladwifikx` from Actions run `40253587740` (artifact ID `12621677678`). GitHub API confirmed it was not expired when checked on 2026-10-26. The downloaded `recovery.img` must match the exact byte size and SHA-256 below before device-tree generation. It rejects other files, including the local stock image.
- The workflow grants only `actions: read` and `contents: read`; `actions/download-artifact` is SHA-pinned. Checkout credentials are not persisted.
- GitHub's default branch is `master`. The workflow file is now present on both `master` and `orangefox` so dispatch can target the latter.
- After adding the workflow to `master`, dispatched run `40306963340` on ref `orangefox`: https://github.com/glimmer1115/Auto-TWRP-Builder/actions/runs/40306963340
- Build target follows the OrangeFox Android 16 instructions: `FOX_BUILD_DEVICE=gtaxladwifikx`, `lunch twrp_gtaxladwifikx-bp2a-eng`, then `mka recoveryimage`.
- Updated shared `overrides/BoardConfig.mk` brightness values from max/default `150/120` to `306/200`, based on the panel facts in the handoff. These values have not yet been validated on-device.

## Recovery image provenance

Do not use the workspace-root `recovery.img` as a TWRP input. It is 23,753,248 bytes and its SHA-256 matches the preserved stock AP recovery exactly:

`2F9D11FE10E12953486290C9A388EBC23A257D71B167930671159387719CB0ED`

The last verified TWRP build artifact is:

- File: `evidence/twrp-40253587740/recovery.img`
- Size: 31,246,352 bytes
- SHA-256: `BC2202CADC9D7FB556E28BADD531B021555C1C1137AE6BC78B04C8BCF0037BC1`
- Build/run: GitHub Actions run `40253587740`; CI build/package succeeded, but device boot was not confirmed.

The OrangeFox workflow no longer needs a manually hosted URL or access to a Windows path. It retrieves the exact TWRP artifact from run `40253587740` and verifies its hash and size. The artifact is retained for 14 days by the TWRP workflow; confirm it is still available before dispatching.

## Validation performed

- VS Code diagnostics for the new workflow: no errors.
- `python scripts/patch_bringup.py --self-test`: passed.
- Compared the pinned TWRP image size/hash with the preserved image; matched.
- Queried the Actions API; artifact `12621677678` (`twrp-gtaxladwifikx`) was present and not expired.
- Initial dispatch attempts failed with HTTP 404 because the workflow was not on default branch `master`; adding it to `master` resolved discovery.
- Run `40306963340` reached the runner and downloaded the TWRP artifact, then failed at manifest initialization because the private GitLab repository required authentication.
- The askpass script passed local POSIX shell syntax and dummy-response checks. The actual private GitLab authentication has not yet been validated because the GitHub secret is not available in this workspace.
- Confirmed the workflow is registered in GitHub and the `master` and `orangefox` copies have matching SHA-256.
- No successful OrangeFox build, device flash, or device test has occurred.
- `git diff --cached --check`: passed before commit.
- Workflow diagnostics and `git diff --check`: passed after the artifact-download update.

## Local work intentionally not committed

The following existing changes/files remain local and must not be swept into another commit without explicit review:

- Modified `.github/workflows/Recovery Build_SW.yml`
- Modified `scripts/patch_bringup.py`
- Modified `docs/history/NOTICE.txt`
- Untracked `.github/agents/`, `.vscode/`, `artifacts/`, `backups/`, `evidence/`, `scripts/__pycache__/`, and `stock_recovery.img`
- All files under `docs/history/` are local-only. Never stage, commit, or push them.

## Next steps

1. Add repository secret `GITLAB_MANIFEST_TOKEN` in GitHub Settings > Secrets and variables > Actions. Use a GitLab token with `read_repository` access; never paste the token into chat or commit it.
2. Manually re-run `OrangeFox Recovery Build (SM-T583)` on ref `orangefox`. The workflow reads the TWRP artifact from run `40253587740` and checks its hash/size.
3. If manifest authentication still fails, inspect the new run logs without exposing the token.
4. Record CI build outcome separately from device boot/ADB/display validation.

## Session Update - 2026-10-27

- Run `40505359865` on `master` failed because the workflow file contained only 28 lines of Telegram notification YAML, not the complete workflow; GitHub reported a YAML sequence error at line 1.
- Restored the complete workflow from `orangefox` to `master`; `actionlint` passed.
- Commit `a2369c4` was pushed to `master`. GitHub Actions API then listed `OrangeFox Recovery Build (SM-T583)` as workflow ID `396912684`.
- The `orangefox` branch already contains the 16 GiB swap/32 GiB output-space configuration and Telegram notifications in commit `f77d25f`.
- No build or workflow dispatch was run by the assistant; the user will run the build.
- This history note is local-only and must not be committed or pushed.
