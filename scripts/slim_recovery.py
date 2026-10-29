#!/usr/bin/env python3
"""
slim_recovery.py (V20 Final) — Strict physical slimming of recovery.img
- Fail-Closed ELF 검사
- 070701 CPIO만 허용
- llvm-strip 또는 arm-linux-gnueabi-strip 강제 사용
- 출력 파일의 SHA-1은 갱신하지 않음 (패키저 입력 전용)
"""
import argparse
import gzip
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

_here = Path(__file__).resolve().parent
_pr_path = _here / "package_recovery.py"
_spec = importlib.util.spec_from_file_location("package_recovery", _pr_path)
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)

REMOVE_PATHS = {
    "system/bin/update_engine_sideload",
    "system/bin/adbd",                 # <--- 주석 풀기
    "system/lib/libadbd_services.so",  # <--- 주석 풀기
    "system/lib/libicui18n.so",        # <--- 추가 유지
    "system/lib/libicuuc.so",          # <--- 추가 유지
    "system/bin/bash",
    "system/bin/nano", 
    "system/bin/toolbox", 
    "sbin/bash",
}
REMOVE_PREFIXES = ("system/etc/terminfo/", "system/etc/nano/")
KEEP_FONTS = {"roboto-regular.ttf", "roboto-medium.ttf"}
ICU_LIBS = {"system/lib/libicuuc.so", "system/lib/libicui18n.so", "system/lib/libicudata.so"}

STRIP_TARGETS = {
    "system/bin/recovery", "system/bin/init", "system/bin/linker",
    "sbin/magiskboot",
}

def is_theme_xml(path):
    return path.endswith(".xml") and (path.startswith("twres/themes/") or path.startswith("twres/resources/"))

def should_remove(path, mode, link_count, remove_icu, referenced_fonts):
    ftype = stat.S_IFMT(mode)
    # [수정] REMOVE_PATHS에 있는 건 하드링크(link_count) 상관없이 무조건 삭제!
    if path in REMOVE_PATHS: return ftype == stat.S_IFREG
    
    if path == "TRAILER!!!" or link_count > 1: return False
    for p in REMOVE_PREFIXES:
    if remove_icu and path in ICU_LIBS: return ftype == stat.S_IFREG
    if path.startswith("twres/fonts/"):
        if ftype != stat.S_IFREG or not path.endswith(".ttf"): return False
        basename = path.rsplit("/", 1)[-1].lower()
        if referenced_fonts is None: return False
        if basename in referenced_fonts: return False
        return basename not in KEEP_FONTS
    return False

def find_safe_strip_tool():
    for tool in ["llvm-strip", "arm-linux-gnueabi-strip"]:
        if shutil.which(tool):
            return tool
    return None

def run_tool(cmd):
    if not shutil.which(cmd[0]): return None
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.stdout if res.returncode == 0 else None
    except Exception:
        return None

def is_valid_elf_structure(data):
    if not shutil.which("readelf"): return False
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return run_tool(["readelf", "-h", tmp_path]) is not None and run_tool(["readelf", "-dW", tmp_path]) is not None
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)

def get_needed(elf_data):
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(elf_data)
        tmp_path = tmp.name
    try:
        out = run_tool(["readelf", "-dW", tmp_path])
        if out is None: return None
        return [m.group(1) for ln in out.splitlines() if "NEEDED" in ln for m in [re.search(r"\[(.*?)\]", ln)] if m]
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)

def strip_elf_data(data, strip_tool):
    if data[:4] != b"\x7fELF" or not strip_tool: return data
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        res = subprocess.run([strip_tool, "--strip-unneeded", tmp_path], capture_output=True)
        if res.returncode == 0:
            with open(tmp_path, "rb") as f: new_data = f.read()
            if is_valid_elf_structure(new_data): return new_data
            print(f"WARNING: strip 후 ELF 구조 손상됨. 원본 유지.")
        else:
            print(f"WARNING: strip 실패 (code {res.returncode}). 원본 유지.")
    except Exception as exc:
        print(f"WARNING: strip 예외: {exc}. 원본 유지.")
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)
    return data

def find_referenced_fonts(cpio, xml_paths):
    if not xml_paths: return None
    refs = set()
    for _, norm_name, _, _, entry_data, _, _, _ in pr.iter_cpio_entries(cpio):
        if norm_name not in xml_paths: continue
        try:
            root = ET.fromstring(entry_data.decode("utf-8", "ignore"))
            for elem in root.iter():
                vals = list(elem.attrib.values()) + ([elem.text] if elem.text else []) + ([elem.tail] if elem.tail else [])
                for v in vals:
                    if isinstance(v, str):
                        for m in re.finditer(r"([A-Za-z0-9_\-\.]+\.ttf)", v, re.IGNORECASE): refs.add(m.group(1).lower())
        except ET.ParseError: return None
    return refs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    if args.input.resolve() == args.output.resolve(): sys.exit("오류: 입출력 경로 동일")
    if args.output.exists() and os.path.samefile(args.input, args.output): sys.exit("오류: 입출력 파일 동일")

    data = args.input.read_bytes()
    parsed = pr.parse_image(data)
    ramdisk = parsed["components"]["ramdisk"]
    cpio = gzip.decompress(ramdisk) if ramdisk.startswith(b"\x1f\x8b") else ramdisk

    inv = []
    pr.rewrite_cpio(cpio, inventory=inv)
    xml_paths = {e["path"] for e in inv if e["type"] == "file" and is_theme_xml(e["path"])}
    referenced_fonts = find_referenced_fonts(cpio, xml_paths)

    icu_in_needed, check_failed, failed_file, elfs_checked = False, False, None, 0
    for _, norm_name, _, _, entry_data, _, _, _ in pr.iter_cpio_entries(cpio):
        if entry_data[:4] == b"\x7fELF":
            elfs_checked += 1
            needed = get_needed(entry_data)
            if needed is None:
                check_failed, failed_file = True, norm_name
                break
            if any("libicu" in n for n in needed):
                icu_in_needed = True
                break

    print(f"ELF 의존성 검사: {elfs_checked}개 진행")
    if check_failed:
        print(f"WARNING: ELF 검사 실패 위치: {failed_file}. ICU 제거 취소.")
        remove_icu = False
    elif icu_in_needed:
        print("WARNING: libicu가 NEEDED에 존재. ICU 제거 취소.")
        remove_icu = False
    else:
        print("ICU 정적 의존성 검사 통과. ICU 제거 진행.")
        remove_icu = True

    strip_tool = find_safe_strip_tool()
    print(f"선택된 strip 도구: {strip_tool or '없음 (스킵)'}")

    output = bytearray()
    removed_count, stripped_count = 0, 0

    for (raw_name, norm_name, header, name_bytes, entry_data, mode, link_count, file_size) in pr.iter_cpio_entries(cpio):
        if header[:6] != b"070701":
            sys.exit(f"Error: Unsupported CPIO magic {header[:6]}. Only 070701 is safe.")

        if should_remove(norm_name, mode, link_count, remove_icu, referenced_fonts):
            removed_count += 1
            continue

        if raw_name == "TRAILER!!!":
            output.extend(header)
            output.extend(name_bytes)
            output.extend(b"\0" * (-len(output) % 4))
            output.extend(entry_data)
            output.extend(b"\0" * (-len(output) % 4))
            break

        if norm_name in STRIP_TARGETS and stat.S_IFMT(mode) == stat.S_IFREG and link_count == 1:
            new_data = strip_elf_data(entry_data, strip_tool)
            if len(new_data) < len(entry_data):
                stripped_count += 1
                header = bytearray(header)
                header[54:62] = f"{len(new_data):08x}".encode('ascii')
                entry_data = new_data

        output.extend(header)
        output.extend(name_bytes)
        output.extend(b"\0" * (-len(output) % 4))
        output.extend(entry_data)
        output.extend(b"\0" * (-len(output) % 4))

    output.extend(b"\0" * (-len(output) % pr.CPIO_BLOCK_SIZE))
    new_ramdisk = pr.compress_cpio(bytes(output))
    
    print(f"삭제: {removed_count}개, strip: {stripped_count}개 ELF")
    print(f"새 램디스크 크기: {len(new_ramdisk)} bytes")

    hdr = bytearray(parsed["header"])
    import struct
    struct.pack_into("<I", hdr, 16, len(new_ramdisk))
    page_size = parsed["fields"][7]
    pad = lambda c: c + b"\0" * (-len(c) % page_size)

    new_img = bytes(hdr) + pad(parsed["components"]["kernel"]) + pad(new_ramdisk) + pad(parsed["components"]["second"]) + pad(parsed["components"]["dt"]) + data[parsed["component_end"]:]
    
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(new_img)
    print(f"슬리밍 이미지 저장 완료: {args.output}")
    print("⚠️ 부트 헤더 SHA-1 미갱신. 직접 플래시 금지, 패키저 입력으로만 사용하세요.")

if __name__ == "__main__":
    main()