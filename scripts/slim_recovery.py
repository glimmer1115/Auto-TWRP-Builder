#!/usr/bin/env python3
"""
slim_recovery.py (V21) — recovery.img 램디스크 물리 슬리밍

V20 대비 변경점
 1. [버그] ICU/libadbd_services 가 REMOVE_PATHS 에 들어 있어 NEEDED 검사 결과와 무관하게
    무조건 삭제되던 문제 수정 → 남은 ELF 가 참조하지 않을 때만 삭제(고정점 반복).
 2. [안전] 삭제로 인해 심볼릭 링크가 끊기면 보고하고, sh/getprop 같은 핵심 링크면 중단.
 3. [안전] 하드링크 삭제 시 데이터 보유 엔트리를 지워 형제 엔트리가 빈 파일이 되는 경우 건너뜀.
 4. [수정] 폰트 참조 스캔을 twres/**/*.xml 전체로 확대(경로 접두사 불일치 시 폰트가 영원히
    안 지워지던 무동작 위험 제거) + 스캔/삭제 결과를 로그에 출력.
 5. [개선] readelf 의존 제거(순수 파이썬 ELF 파서), 입력 램디스크 gzip/xz/lzma/raw 자동 판별,
    카테고리별 절감량 + --limit 대비 여유 출력.
 - 출력 이미지의 SHA-1 은 갱신하지 않음 (패키저 입력 전용)
"""
import argparse
import importlib.util
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict, namedtuple
from pathlib import Path

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))
import ramdisk_report as rr  # noqa: E402  (elf_needed, decompress_ramdisk, resolve_link)

_pr_path = _here / "package_recovery.py"
_spec = importlib.util.spec_from_file_location("package_recovery", _pr_path)
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)

# 실행 파일/스크립트: 무조건 제거 대상 (하드링크 안전 규칙은 적용)
REMOVE_FILES = {
    "system/bin/update_engine_sideload",
    "system/bin/adbd",
    "system/bin/bash",
    "system/bin/nano",
    "system/bin/toolbox",
    "sbin/bash",
}
REMOVE_PREFIXES = ("system/etc/terminfo/", "system/etc/nano/")

# 라이브러리: 남아 있는 어떤 ELF 도 NEEDED 로 참조하지 않을 때만 제거
OPTIONAL_LIB_NAMES = ("libadbd_services.so", "libicui18n.so", "libicuuc.so", "libicudata.so")
OPTIONAL_LIBS = {d + n for d in ("system/lib/", "system/lib64/") for n in OPTIONAL_LIB_NAMES}

KEEP_FONTS = {"roboto-regular.ttf", "roboto-medium.ttf"}

STRIP_TARGETS = {
    "system/bin/recovery", "system/bin/init", "system/bin/linker",
    "sbin/magiskboot",
}

# 삭제로 이 이름의 심볼릭 링크가 끊기면 부팅/스크립트가 깨질 가능성이 커서 중단
CRITICAL_LINK_NAMES = {
    "sh", "ash", "getprop", "setprop", "start", "stop", "init", "linker", "linker64",
    "recovery", "toybox", "busybox", "ls", "cat", "mount", "umount", "sleep", "grep",
    "sed", "awk", "cp", "mv", "rm", "mkdir", "ln", "chmod", "chown", "cut", "tr", "dd",
    "tar", "gzip", "find", "xargs", "ps", "kill", "sync",
}

E = namedtuple("E", "raw norm header name_bytes data mode nlink size key")


def wrap(t):
    raw, norm, header, name_bytes, data, mode, nlink, size = t
    key = (bytes(header[6:14]), bytes(header[62:70]), bytes(header[70:78]))  # ino, devmajor, devminor
    return E(raw, norm, header, name_bytes, data, mode, nlink, size, key)


def is_reg(e):
    return stat.S_IFMT(e.mode) == stat.S_IFREG


def is_twres_xml(path):
    return path.startswith("twres/") and path.endswith(".xml")


# ----------------------------------------------------------------------------- strip
def find_safe_strip_tool():
    for tool in ("llvm-strip", "arm-linux-gnueabi-strip"):
        if shutil.which(tool):
            return tool
    return None


def strip_elf_data(data, strip_tool):
    if data[:4] != b"\x7fELF" or not strip_tool:
        return data
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        res = subprocess.run([strip_tool, "--strip-unneeded", tmp_path], capture_output=True)
        if res.returncode == 0:
            with open(tmp_path, "rb") as f:
                new_data = f.read()
            if rr.elf_needed(new_data) is not None:
                return new_data
            print("WARNING: strip 후 ELF 구조 손상됨. 원본 유지.")
        else:
            print(f"WARNING: strip 실패 (code {res.returncode}). 원본 유지.")
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: strip 예외: {exc}. 원본 유지.")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return data


# ----------------------------------------------------------------------------- fonts
def find_referenced_fonts(ents):
    """twres/**/*.xml 이 참조하는 .ttf 이름 집합. 하나라도 파싱 실패하면 None(=폰트 삭제 안 함)."""
    xmls = [e for e in ents if is_reg(e) and is_twres_xml(e.norm)]
    if not xmls:
        print("폰트 스캔: twres/ 아래 XML 이 없습니다 → 폰트는 삭제하지 않습니다.")
        return None
    refs = set()
    for e in xmls:
        try:
            root = ET.fromstring(e.data.decode("utf-8", "ignore"))
        except ET.ParseError:
            print(f"WARNING: XML 파싱 실패({e.norm}) → 폰트는 삭제하지 않습니다.")
            return None
        for elem in root.iter():
            vals = list(elem.attrib.values()) + [elem.text or "", elem.tail or ""]
            for v in vals:
                for m in re.finditer(r"([A-Za-z0-9_\-\.]+\.ttf)", v, re.IGNORECASE):
                    refs.add(m.group(1).lower())
    print(f"폰트 스캔: XML {len(xmls)}개, 참조된 폰트 {len(refs)}종")
    return refs


# ----------------------------------------------------------------------------- planning
def hardlink_safe_to_drop(e, groups, removed):
    """newc 하드링크는 데이터가 한 엔트리에만 있을 수 있다. 그 엔트리를 지우면 형제가 빈 파일이 된다."""
    if e.nlink <= 1 or e.size == 0:
        return True
    sibs = [s for s in groups.get(e.key, []) if s is not e and s.norm not in removed]
    return not any(s.size == 0 for s in sibs)


def policy_reason(e, referenced_fonts):
    if not is_reg(e):
        return None
    if e.norm in REMOVE_FILES:
        return "explicit"
    if e.nlink > 1:
        return None  # 접두사/폰트 규칙은 하드링크 보호 (V20 동작 유지)
    if any(e.norm.startswith(p) for p in REMOVE_PREFIXES):
        return "prefix"
    if e.norm.startswith("twres/fonts/") and e.norm.endswith(".ttf"):
        if referenced_fonts is None:
            return None
        base = e.norm.rsplit("/", 1)[-1].lower()
        if base in referenced_fonts or base in KEEP_FONTS:
            return None
        return "font"
    return None


def plan_removals(ents, referenced_fonts):
    removed = {}
    groups = defaultdict(list)
    for e in ents:
        if e.nlink > 1:
            groups[e.key].append(e)

    # 1단계: 정책 기반
    for e in ents:
        if e.raw == "TRAILER!!!":
            continue
        reason = policy_reason(e, referenced_fonts)
        if not reason:
            continue
        if not hardlink_safe_to_drop(e, groups, removed):
            print(f"WARNING: {e.norm} 은(는) 하드링크 데이터 보유 엔트리라 삭제를 건너뜁니다.")
            continue
        removed[e.norm] = reason

    # 2단계: 선택적 라이브러리 — 남은 ELF 의 NEEDED 가 참조하지 않을 때만 (고정점 반복)
    needs = {}
    for e in ents:
        if is_reg(e) and e.data[:4] == b"\x7fELF":
            needs[e.norm] = rr.elf_needed(e.data)
    print(f"ELF 의존성 검사: {len(needs)}개 분석")
    bad = [p for p, v in needs.items() if v is None]
    cands = [e for e in ents if e.norm in OPTIONAL_LIBS and is_reg(e)]
    kept_refs = {}
    if bad:
        print(f"WARNING: ELF 파싱 실패 {len(bad)}개(예: {bad[0]}) → 선택적 라이브러리는 모두 유지합니다.")
    else:
        changed = True
        while changed:
            changed = False
            live = defaultdict(list)
            for p, lst in needs.items():
                if p in removed:
                    continue
                for n in lst:
                    live[n].append(p)
            for e in cands:
                if e.norm in removed:
                    continue
                name = os.path.basename(e.norm)
                if name in live:
                    kept_refs[e.norm] = live[name]
                    continue
                if not hardlink_safe_to_drop(e, groups, removed):
                    continue
                removed[e.norm] = "unused-lib"
                kept_refs.pop(e.norm, None)
                changed = True
    for e in cands:
        if e.norm not in removed:
            refs = kept_refs.get(e.norm, [])
            why = f"참조: {', '.join(refs[:3])}{' ...' if len(refs) > 3 else ''}" if refs else "검사 불가"
            print(f"  유지: {e.norm} ({why})")
    return removed


def check_dangling(ents, removed, allow):
    """삭제된 파일을 가리키게 된 심볼릭 링크를 보고. 핵심 링크면 중단(allow 가 아니면)."""
    hits = []
    for e in ents:
        if e.norm in removed or stat.S_IFMT(e.mode) != stat.S_IFLNK:
            continue
        target = e.data.decode("utf-8", "replace")
        if rr.resolve_link(e.norm, target) in removed:
            hits.append((e.norm, target))
    if not hits:
        return
    print(f"WARNING: 삭제로 끊어진 심볼릭 링크 {len(hits)}개:")
    for p, t in hits[:30]:
        print(f"  {p} -> {t}")
    critical = [p for p, _ in hits if os.path.basename(p) in CRITICAL_LINK_NAMES]
    if critical and not allow:
        sys.exit("오류: 핵심 링크가 끊어집니다: " + ", ".join(critical[:10]) +
                 "\n  → 해당 대상 파일을 REMOVE_FILES 에서 빼거나 --allow-dangling 으로 무시하세요.")


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=None, help="램디스크(압축) 한도 bytes — 로그에 여유만 출력")
    ap.add_argument("--allow-dangling", action="store_true", help="핵심 링크가 끊어져도 계속")
    args = ap.parse_args()

    if args.input.resolve() == args.output.resolve():
        sys.exit("오류: 입출력 경로 동일")
    if args.output.exists() and os.path.samefile(args.input, args.output):
        sys.exit("오류: 입출력 파일 동일")

    data = args.input.read_bytes()
    parsed = pr.parse_image(data)
    ramdisk = parsed["components"]["ramdisk"]
    print(f"입력 램디스크 형식: {rr.detect_codec(ramdisk)}, 압축 크기 {len(ramdisk):,} bytes")
    cpio = rr.decompress_ramdisk(ramdisk)

    ents = [wrap(t) for t in pr.iter_cpio_entries(cpio)]
    for e in ents:
        if e.header[:6] != b"070701":
            sys.exit(f"Error: Unsupported CPIO magic {e.header[:6]}. Only 070701 is safe.")

    referenced_fonts = find_referenced_fonts(ents)
    removed = plan_removals(ents, referenced_fonts)
    check_dangling(ents, removed, args.allow_dangling)

    strip_tool = find_safe_strip_tool()
    print(f"선택된 strip 도구: {strip_tool or '없음 (스킵)'}")

    output = bytearray()
    by_reason = defaultdict(lambda: [0, 0])
    stripped_count = stripped_bytes = 0
    raw_before = sum(e.size for e in ents)

    for e in ents:
        if e.norm in removed:
            r = by_reason[removed[e.norm]]
            r[0] += 1
            r[1] += e.size
            continue

        header, entry_data = e.header, e.data
        if e.raw == "TRAILER!!!":
            output.extend(header)
            output.extend(e.name_bytes)
            output.extend(b"\0" * (-len(output) % 4))
            output.extend(entry_data)
            output.extend(b"\0" * (-len(output) % 4))
            break

        if e.norm in STRIP_TARGETS and is_reg(e) and e.nlink == 1:
            new_data = strip_elf_data(entry_data, strip_tool)
            if len(new_data) < len(entry_data):
                stripped_count += 1
                stripped_bytes += len(entry_data) - len(new_data)
                header = bytearray(header)
                header[54:62] = f"{len(new_data):08x}".encode("ascii")
                entry_data = new_data

        output.extend(header)
        output.extend(e.name_bytes)
        output.extend(b"\0" * (-len(output) % 4))
        output.extend(entry_data)
        output.extend(b"\0" * (-len(output) % 4))

    output.extend(b"\0" * (-len(output) % pr.CPIO_BLOCK_SIZE))
    new_ramdisk = pr.compress_cpio(bytes(output))

    print("--- 카테고리별 삭제 (비압축 기준) ---")
    for reason in ("explicit", "prefix", "font", "unused-lib"):
        n, b = by_reason.get(reason, (0, 0))
        print(f"  {reason:11} {n:4}개  {b:>12,} B")
    print(f"strip: {stripped_count}개 ELF, {stripped_bytes:,} B 절감")
    print(f"cpio(비압축): {raw_before:,} → {len(output):,} bytes")
    print(f"램디스크(압축): {len(ramdisk):,} → {len(new_ramdisk):,} bytes")
    if args.limit:
        headroom = args.limit - len(new_ramdisk)
        print(f"한도 {args.limit:,} 대비 {'여유' if headroom >= 0 else '초과'} {abs(headroom):,} bytes")

    hdr = bytearray(parsed["header"])
    struct.pack_into("<I", hdr, 16, len(new_ramdisk))
    page_size = parsed["fields"][7]

    def pad(c):
        return c + b"\0" * (-len(c) % page_size)

    comps = parsed["components"]
    new_img = (bytes(hdr) + pad(comps["kernel"]) + pad(new_ramdisk) + pad(comps["second"])
               + pad(comps["dt"]) + data[parsed["component_end"]:])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(new_img)
    print(f"슬리밍 이미지 저장 완료: {args.output}")
    print("⚠️ 부트 헤더 SHA-1 미갱신. 직접 플래시 금지, 패키저 입력으로만 사용하세요.")


if __name__ == "__main__":
    main()
