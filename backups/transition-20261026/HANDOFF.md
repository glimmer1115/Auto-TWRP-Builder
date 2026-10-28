# TWRP Transition Handoff

Snapshot date: 2026-10-26

## Current lane

- Keep Android/TWRP branch at `android-8.1`; user confirmed this is the branch used so far.
- Do not spend another build just to increase build count. No build or flash was run for this snapshot.
- If the current line is abandoned, evaluate a GitLab re-port and re-check the full build/package pipeline. Required source details still needed: GitLab URL, branch, manifest, and device-tree source.

## Build and images

- GitHub Actions run: `40253587740`, workflow `Recovery Build (SM-T583 32-bit)`.
- Run commit: `20783d6b1d7bb81bb71b2343f53c640ec382dc22`; CI build/package conclusion was success, not device validation.
- Stock AP `recovery.img`: 23,753,248 bytes; SHA-256 `2F9D11FE10E12953486290C9A388EBC23A257D71B167930671159387719CB0ED`.
- TWRP `recovery.img` from run 40253587740: 31,246,352 bytes; SHA-256 `BC2202CADC9D7FB556E28BADD531B021555C1C1137AE6BC78B04C8BCF0037BC1`.

## Device facts reported by user

- `ro.hardware` and `ro.boot.hardware`: `samsungexynos7870`.
- Framebuffer: virtual size `1200,1920`, `bits_per_pixel=32`, `stride=4800`.
- Backlight path resolves to `/sys/devices/14800000.dsim/backlight/panel`; `max_brightness=306`.
- TWRP ADB connection and the screen result for the exact 31,246,352-byte image are not confirmed.

## Ramdisk evidence

- TWRP CPIO entries `init`, `sbin/recovery`, and `sbin/adbd` are regular files, mode `0750`, owner `root:root`.
- `sbin` symlinks are present; sampled links point to `toybox`.
- This rules out missing execute bits or absent sampled symlinks for those entries, but does not prove successful init/recovery startup.
- Stock AP and TWRP ramdisk extracts are both preserved under `unpacked/`; compare them only when requested.

## Pending configuration

- Local source now sets brightness path `/sys/devices/14800000.dsim/backlight/panel/brightness`, maximum `306`, default `200` in BoardConfig, workflow injection, and validation.
- These edits have not been tested in a new build or on-device.

## References

- `evidence/run-40253587740/debug/` preserves the build log and generated device tree.
- `references/iteration-107-last_recovery.txt` preserves historical framebuffer ioctl output as reference only; it is not a run-to-run comparison.