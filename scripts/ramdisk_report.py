#!/usr/bin/env python3
"""
ramdisk_report.py — Android boot/recovery 이미지(헤더 v0, Samsung 포함)의 램디스크 분석 도구.

- 어디서 용량이 나가는지: 디렉터리별 / 파일별 (비압축 + 개별 압축 추정)
- 같은 cpio 를 gzip / bzip2 / lzma / xz 로 압축했을 때 전체 크기 비교
- 끊어진 심볼릭 링크 목록
- slim_recovery.py 가 import 해서 쓰는 헬퍼(ELF NEEDED 파서, cpio 파서, 코덱 판별) 포함

사용법:
  python3 ramdisk_report.py recovery.img --limit 14680064 --top 40 --json report.json
"""
from __future__ import annotations

import argparse
import bz2
import gzip
import json
import lzma
import stat
import struct
import sys
import time
import zlib
from collections import defaultdict, namedtuple
from pathlib import Path

MIB = 1024 * 1024

Entry = namedtuple("Entry", "path mode nlink size ino data")

# 램디스크가 아니라 런타임에 마운트되는 경로 (심볼릭 링크 대상이 여기면 '외부'로 취급)
EXTERNAL_TOPS = {
    "proc", "sys", "dev", "data", "cache", "sdcard", "storage", "mnt", "tmp",
    "vendor", "odm", "system_ext", "product", "apex", "config", "metadata",
    "firmware", "persist", "efs", "oem", "acct", "debug_ramdisk",
    "second_stage_resources", "linkerconfig", "system_root",
}


# --------------------------------------------------------------------------- boot image
def parse_boot_image(data: bytes) -> dict:
    if data[:8] != b"ANDROID!":
        raise SystemExit("Android boot 이미지가 아닙니다 (매직 불일치).")
    (kernel_size, _ka, ramdisk_size, _ra, second_size, _sa, _tags,
     page_size, dt_size) = struct.unpack_from("<9I", data, 8)
    if page_size < 512 or page_size & (page_size - 1):
        raise SystemExit(f"page_size 가 올바르지 않습니다: {page_size}")

    def align(n: int) -> int:
        return (n + page_size - 1) // page_size * page_size

    off = page_size
    kernel = data[off:off + kernel_size]
    off += align(kernel_size)
    ramdisk = data[off:off + ramdisk_size]
    return {
        "kernel_size": kernel_size,
        "ramdisk_size": ramdisk_size,
        "second_size": second_size,
        "dt_size": dt_size,
        "page_size": page_size,
        "kernel": kernel,
        "ramdisk": ramdisk,
    }


# --------------------------------------------------------------------------- codecs
def detect_codec(blob: bytes) -> str:
    if blob[:2] == b"\x1f\x8b":
        return "gzip"
    if blob[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if blob[:3] == b"BZh":
        return "bzip2"
    if blob[:3] == b"\x5d\x00\x00":
        return "lzma"
    if blob[:4] == b"\x02\x21\x4c\x18":
        return "lz4-legacy"
    if blob[:6] in (b"070701", b"070702"):
        return "raw"
    return "unknown"


def decompress_ramdisk(blob: bytes) -> bytes:
    codec = detect_codec(blob)
    if codec == "gzip":
        return gzip.decompress(blob)
    if codec in ("xz", "lzma"):
        d = lzma.LZMADecompressor()
        out = d.decompress(blob)
        if not d.eof:
            raise SystemExit("lzma/xz 스트림이 끝나지 않았습니다 (손상?).")
        return out
    if codec == "bzip2":
        d = bz2.BZ2Decompressor()
        out = d.decompress(blob)
        if not d.eof:
            raise SystemExit("bzip2 스트림이 끝나지 않았습니다 (손상?).")
        return out
    if codec == "raw":
        return blob
    raise SystemExit(f"지원하지 않는 램디스크 형식: {codec}")


# --------------------------------------------------------------------------- cpio (newc)
def parse_cpio(buf: bytes) -> list[Entry]:
    entries: list[Entry] = []
    pos = 0
    while pos + 110 <= len(buf):
        hdr = buf[pos:pos + 110]
        if hdr[:6] not in (b"070701", b"070702"):
            raise SystemExit(f"cpio 매직 오류 (offset {pos}): {hdr[:6]!r}")
        f = [int(hdr[6 + 8 * i:14 + 8 * i], 16) for i in range(13)]
        ino, mode, _uid, _gid, nlink, _mtime, filesize = f[:7]
        namesize = f[11]
        name_off = pos + 110
        name = buf[name_off:name_off + namesize - 1].decode("utf-8", "replace")
        data_off = (name_off + namesize + 3) & ~3
        data = buf[data_off:data_off + filesize]
        pos = (data_off + filesize + 3) & ~3
        if name == "TRAILER!!!":
            break
        if name.startswith("./"):
            name = name[2:]
        if name == ".":
            name = ""
        entries.append(Entry(name, mode, nlink, filesize, ino, data))
    return entries


# --------------------------------------------------------------------------- ELF
def elf_needed(data: bytes):
    """DT_NEEDED 목록 반환. ELF 가 아니면 None, 파싱 실패도 None, 정적 ELF 는 []."""
    if data[:4] != b"\x7fELF" or len(data) < 64:
        return None
    ei_class, ei_data = data[4], data[5]
    if ei_data != 1:  # little endian 만 지원 (ARM/ARM64/x86 모두 해당)
        return None
    try:
        if ei_class == 1:
            (phoff,) = struct.unpack_from("<I", data, 0x1C)
            phentsize, phnum = struct.unpack_from("<HH", data, 0x2A)
            ph_fmt, dyn_fmt, word = "<IIIIIIII", "<iI", 4
        elif ei_class == 2:
            (phoff,) = struct.unpack_from("<Q", data, 0x20)
            phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
            ph_fmt, dyn_fmt, word = "<IIQQQQQQ", "<qQ", 8
        else:
            return None

        loads, dynamic = [], None
        for i in range(phnum):
            base = phoff + i * phentsize
            ph = struct.unpack_from(ph_fmt, data, base)
            if word == 4:
                p_type, p_offset, p_vaddr, _pa, p_filesz = ph[0], ph[1], ph[2], ph[3], ph[4]
            else:
                p_type, _fl, p_offset, p_vaddr, _pa, p_filesz = ph[:6]
            if p_type == 1:      # PT_LOAD
                loads.append((p_vaddr, p_offset, p_filesz))
            elif p_type == 2:    # PT_DYNAMIC
                dynamic = (p_offset, p_filesz)
        if dynamic is None:
            return []            # 정적 링크 실행 파일
        dyn_off, dyn_size = dynamic
        esz = struct.calcsize(dyn_fmt)
        needed_offs, strtab_va = [], None
        for off in range(dyn_off, dyn_off + dyn_size, esz):
            tag, val = struct.unpack_from(dyn_fmt, data, off)
            if tag == 0:
                break
            if tag == 1:
                needed_offs.append(val)
            elif tag == 5:
                strtab_va = val
        if not needed_offs:
            return []
        if strtab_va is None:
            return None
        str_off = None
        for va, off, fsz in loads:
            if va <= strtab_va < va + fsz:
                str_off = off + (strtab_va - va)
                break
        if str_off is None:
            return None
        names = []
        for rel in needed_offs:
            start = str_off + rel
            end = data.index(b"\0", start)
            names.append(data[start:end].decode("utf-8", "replace"))
        return names
    except (struct.error, ValueError, IndexError):
        return None


# --------------------------------------------------------------------------- symlinks
def resolve_link(link_path: str, target: str) -> str:
    """심볼릭 링크 대상을 램디스크 루트 기준 정규화 경로로 변환."""
    base = [] if target.startswith("/") else link_path.split("/")[:-1]
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if base:
                base.pop()
            continue
        base.append(part)
    return "/".join(base)


def find_dangling(entries: list[Entry]) -> list[tuple[str, str]]:
    paths = {e.path for e in entries}
    out = []
    for e in entries:
        if stat.S_IFMT(e.mode) != stat.S_IFLNK:
            continue
        target = e.data.decode("utf-8", "replace")
        dest = resolve_link(e.path, target)
        top = dest.split("/")[0] if dest else ""
        if top in EXTERNAL_TOPS and dest not in paths:
            continue
        if dest and dest not in paths:
            out.append((e.path, target))
    return out


# --------------------------------------------------------------------------- report
def codec_trials(cpio: bytes) -> dict:
    ext = 9 | lzma.PRESET_EXTREME
    trials = {}

    def run(name, fn, cfg):
        t0 = time.time()
        size = len(fn())
        trials[name] = {"bytes": size, "kernel_cfg": cfg, "sec": round(time.time() - t0, 1)}

    run("gzip -9", lambda: gzip.compress(cpio, 9, mtime=0), "CONFIG_RD_GZIP")
    run("bzip2 -9", lambda: bz2.compress(cpio, 9), "CONFIG_RD_BZIP2")
    run("lzma (alone)", lambda: lzma.compress(
        cpio, format=lzma.FORMAT_ALONE,
        filters=[{"id": lzma.FILTER_LZMA1, "preset": ext, "dict_size": 1 << 23}]), "CONFIG_RD_LZMA")
    run("xz (crc32)", lambda: lzma.compress(
        cpio, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32,
        filters=[{"id": lzma.FILTER_LZMA2, "preset": ext, "dict_size": 1 << 23}]), "CONFIG_RD_XZ")
    return trials


def type_name(mode: int) -> str:
    return {stat.S_IFREG: "file", stat.S_IFDIR: "dir", stat.S_IFLNK: "link"}.get(
        stat.S_IFMT(mode), "other")


def group_key(path: str) -> str:
    parts = path.split("/")
    if len(parts) == 1:
        return "(root)"
    return "/".join(parts[:2]) if len(parts) > 2 else parts[0] + "/"


def build_report(img_path: Path, limit: int | None, top: int, do_codecs: bool) -> dict:
    data = img_path.read_bytes()
    boot = parse_boot_image(data)
    codec = detect_codec(boot["ramdisk"])
    cpio = decompress_ramdisk(boot["ramdisk"])
    entries = parse_cpio(cpio)

    files = [e for e in entries if stat.S_IFMT(e.mode) == stat.S_IFREG and e.size]
    est = {e.path: len(zlib.compress(e.data, 6)) for e in files}

    groups = defaultdict(lambda: [0, 0, 0])
    for e in files:
        g = groups[group_key(e.path)]
        g[0] += 1
        g[1] += e.size
        g[2] += est[e.path]

    rep = {
        "image": str(img_path),
        "kernel_size": boot["kernel_size"],
        "ramdisk_size": boot["ramdisk_size"],
        "ramdisk_codec": codec,
        "cpio_size": len(cpio),
        "limit": limit,
        "headroom": (limit - boot["ramdisk_size"]) if limit else None,
        "counts": {t: sum(1 for e in entries if type_name(e.mode) == t)
                   for t in ("file", "dir", "link", "other")},
        "hardlinked_entries": sum(1 for e in entries if e.nlink > 1),
        "groups": sorted(
            ({"dir": k, "files": v[0], "raw": v[1], "est_gz": v[2]} for k, v in groups.items()),
            key=lambda x: -x["est_gz"]),
        "top_files": [
            {"path": e.path, "raw": e.size, "est_gz": est[e.path]}
            for e in sorted(files, key=lambda x: -est[x.path])[:top]],
        "dangling_links": find_dangling(entries),
        "codecs": codec_trials(cpio) if do_codecs else None,
    }
    return rep


def print_report(rep: dict) -> None:
    def mib(n):
        return f"{n / MIB:7.2f} MiB"

    print("=== 이미지 ===")
    print(f"파일          : {rep['image']}")
    print(f"커널          : {rep['kernel_size']:>10,} B ({mib(rep['kernel_size']).strip()})")
    print(f"램디스크(압축) : {rep['ramdisk_size']:>10,} B ({mib(rep['ramdisk_size']).strip()})  [{rep['ramdisk_codec']}]")
    print(f"cpio(비압축)  : {rep['cpio_size']:>10,} B ({mib(rep['cpio_size']).strip()})")
    if rep["limit"]:
        h = rep["headroom"]
        tag = "여유" if h >= 0 else "초과"
        print(f"한도          : {rep['limit']:>10,} B → {tag} {abs(h):,} B")
    c = rep["counts"]
    print(f"엔트리        : 파일 {c['file']}, 디렉터리 {c['dir']}, 링크 {c['link']}, 기타 {c['other']}"
          f" / 하드링크 {rep['hardlinked_entries']}")

    print("\n=== 디렉터리별 (개별 압축 추정 기준, 상위 15) ===")
    print(f"{'dir':32} {'files':>5} {'raw':>12} {'est.gz':>12}")
    for g in rep["groups"][:15]:
        print(f"{g['dir']:32} {g['files']:>5} {g['raw']:>12,} {g['est_gz']:>12,}")

    print(f"\n=== 큰 파일 TOP {len(rep['top_files'])} (개별 압축 추정 기준) ===")
    print(f"{'raw':>12} {'est.gz':>12}  path")
    for f in rep["top_files"]:
        print(f"{f['raw']:>12,} {f['est_gz']:>12,}  {f['path']}")
    print("※ est.gz 는 파일을 하나씩 zlib 으로 압축한 값이라 실제 기여도와 다소 다릅니다.")

    if rep["codecs"]:
        print("\n=== 같은 cpio 를 다른 코덱으로 압축했을 때 (램디스크 전체) ===")
        for name, v in rep["codecs"].items():
            line = f"{name:14} {v['bytes']:>11,} B  ({v['bytes'] / MIB:6.2f} MiB)  필요 커널옵션 {v['kernel_cfg']}"
            if rep["limit"]:
                diff = rep["limit"] - v["bytes"]
                line += f"  → {'통과 여유' if diff >= 0 else '초과'} {abs(diff):,} B"
            print(line)

    if rep["dangling_links"]:
        print("\n=== 끊어진 심볼릭 링크 (램디스크 내부 기준) ===")
        for p, t in rep["dangling_links"][:40]:
            print(f"  {p} -> {t}")
        if len(rep["dangling_links"]) > 40:
            print(f"  ... 외 {len(rep['dangling_links']) - 40}개")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path)
    ap.add_argument("--limit", type=int, default=None, help="램디스크(압축) 한도 bytes")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--no-codecs", action="store_true", help="코덱 비교 생략 (빠름)")
    args = ap.parse_args()

    rep = build_report(args.image, args.limit, args.top, not args.no_codecs)
    print_report(rep)
    if args.json:
        args.json.write_text(json.dumps(rep, indent=2, ensure_ascii=False))
        print(f"\nJSON 저장: {args.json}")


if __name__ == "__main__":
    sys.exit(main())
