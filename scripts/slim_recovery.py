#!/usr/bin/env python3
"""Physically slim a recovery image before package_recovery.py repackages it.

This script preserves the input image and writes an intermediate image whose
boot-header SHA-1 is intentionally stale. Only pass that output to
package_recovery.py; never flash it directly.
"""
import argparse
import gzip
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
from pathlib import Path


_here = Path(__file__).resolve().parent
_pr_path = _here / "package_recovery.py"
_spec = importlib.util.spec_from_file_location("package_recovery", _pr_path)
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)

REMOVE_PATHS = {
    "system/bin/update_engine_sideload",
    "system/bin/adbd",
    "system/lib/libadbd_services.so",
    "system/bin/bash",
    "system/bin/nano",
    "system/bin/toolbox",
    "sbin/bash",
}
REMOVE_PREFIXES = ("system/etc/terminfo/", "system/etc/nano/")
KEEP_FONTS = {"roboto-regular.ttf", "roboto-medium.ttf"}
ICU_LIBS = {
    "system/lib/libicuuc.so",
    "system/lib/libicui18n.so",
    "system/lib/libicudata.so",
}
STRIP_TARGETS = {
    "system/bin/recovery",
    "system/bin/init",
    "system/bin/linker",
    "sbin/magiskboot",
}


def is_theme_xml(path):
    return path.endswith(".xml") and (
        path.startswith("twres/themes/") or path.startswith("twres/resources/")
    )


def should_remove(path, mode, link_count, remove_icu, referenced_fonts):
    if path == "TRAILER!!!" or link_count > 1:
        return False

    file_type = stat.S_IFMT(mode)
    if path in REMOVE_PATHS or any(path.startswith(prefix) for prefix in REMOVE_PREFIXES):
        return file_type == stat.S_IFREG

    if remove_icu and path in ICU_LIBS:
        return file_type == stat.S_IFREG

    if path.startswith("twres/fonts/"):
        if file_type != stat.S_IFREG or not path.endswith(".ttf"):
            return False
        basename = path.rsplit("/", 1)[-1].lower()
        if referenced_fonts is None or basename in referenced_fonts:
            return False
        return basename not in KEEP_FONTS

    return False


def is_elf(data):
    return data[:4] == b"\x7fELF"


def run_tool(command):
    if not shutil.which(command[0]):
        return None
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        return result.stdout if result.returncode == 0 else None
    except Exception:
        return None


def is_valid_elf(data):
    """Check that readelf can parse the ELF header and dynamic section."""
    with tempfile.NamedTemporaryFile(delete=False) as temporary:
        temporary.write(data)
        temporary_path = temporary.name
    try:
        if run_tool(["readelf", "-h", temporary_path]) is None:
            return False
        return run_tool(["readelf", "-dW", temporary_path]) is not None
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def strip_elf(data):
    """Strip unneeded symbols, retaining the original unless checks pass."""
    with tempfile.NamedTemporaryFile(delete=False) as temporary:
        temporary.write(data)
        temporary_path = temporary.name
    try:
        if not shutil.which("strip"):
            print("WARNING: strip tool unavailable; keeping original ELF.")
            return data
        result = subprocess.run(
            ["strip", "--strip-unneeded", temporary_path], capture_output=True
        )
        if result.returncode != 0:
            print(f"WARNING: strip failed ({result.returncode}); keeping original ELF.")
            return data
        with open(temporary_path, "rb") as stripped_file:
            stripped = stripped_file.read()
        if not is_valid_elf(stripped):
            print("WARNING: stripped ELF structure check failed; keeping original.")
            return data
        return stripped
    except Exception as exc:
        print(f"WARNING: strip exception: {exc}; keeping original ELF.")
        return data
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def get_needed(elf_data):
    """Return DT_NEEDED names, or None when readelf cannot inspect the ELF."""
    with tempfile.NamedTemporaryFile(delete=False) as temporary:
        temporary.write(elf_data)
        temporary_path = temporary.name
    try:
        output = run_tool(["readelf", "-dW", temporary_path])
        if output is None:
            return None
        return [
            match.group(1)
            for line in output.splitlines()
            if "NEEDED" in line
            for match in [re.search(r"\[(.*?)\]", line)]
            if match
        ]
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def find_referenced_fonts(cpio, xml_paths):
    if not xml_paths:
        print("WARNING: no theme XML files found; canceling all font removal.")
        return None

    references = set()
    for _, normalized_name, _, _, entry_data, _, _, _ in pr.iter_cpio_entries(cpio):
        if normalized_name not in xml_paths:
            continue
        text = entry_data.decode("utf-8", "ignore")
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            print(f"WARNING: XML parse failed at {normalized_name}; keeping all fonts.")
            return None
        for element in root.iter():
            values = list(element.attrib.values())
            if element.text:
                values.append(element.text)
            if element.tail:
                values.append(element.tail)
            for value in values:
                if isinstance(value, str):
                    references.update(
                        match.group(1).lower()
                        for match in re.finditer(
                            r"([A-Za-z0-9_.-]+\.ttf)", value, re.IGNORECASE
                        )
                    )
    return references


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.input.resolve() == args.output.resolve():
        sys.exit("ERROR: --input and --output resolve to the same path.")
    if args.output.exists() and os.path.samefile(args.input, args.output):
        sys.exit("ERROR: --output and --input refer to the same file.")

    data = args.input.read_bytes()
    parsed = pr.parse_image(data)
    ramdisk = parsed["components"]["ramdisk"]
    cpio = gzip.decompress(ramdisk) if ramdisk.startswith(b"\x1f\x8b") else ramdisk

    inventory = []
    pr.rewrite_cpio(cpio, inventory=inventory)
    xml_paths = {
        entry["path"]
        for entry in inventory
        if entry["type"] == "file" and is_theme_xml(entry["path"])
    }

    icu_dependency_found = False
    dependency_check_failed = False
    failed_file = None
    elf_count = 0
    for _, normalized_name, _, _, entry_data, _, _, _ in pr.iter_cpio_entries(cpio):
        if not is_elf(entry_data):
            continue
        elf_count += 1
        needed = get_needed(entry_data)
        if needed is None:
            dependency_check_failed = True
            failed_file = normalized_name
            break
        if any("libicu" in library for library in needed):
            icu_dependency_found = True
            break

    referenced_fonts = find_referenced_fonts(cpio, xml_paths)
    if referenced_fonts is not None:
        print(f"Referenced fonts: {sorted(referenced_fonts)}")
    print(f"ELF dependency scan: checked {elf_count} ELFs (may stop early).")

    if dependency_check_failed:
        print(f"WARNING: readelf check failed at {failed_file}; skipping ICU removal.")
        remove_icu = False
    elif icu_dependency_found:
        print("WARNING: libicu is required by DT_NEEDED; skipping ICU removal.")
        remove_icu = False
    else:
        print("ICU static dependency check passed; proceeding with listed ICU removal.")
        remove_icu = True

    output = bytearray()
    removed_count = 0
    stripped_count = 0
    skipped_symlinks = []
    for _, normalized_name, header, name_bytes, entry_data, mode, link_count, _ in pr.iter_cpio_entries(cpio):
        if header[:6] != b"070701":
            sys.exit(f"Unsupported CPIO magic: {header[:6]!r}; only 070701 is accepted.")

        file_type = stat.S_IFMT(mode)
        if file_type == stat.S_IFLNK and (
            normalized_name in REMOVE_PATHS
            or any(normalized_name.startswith(prefix) for prefix in REMOVE_PREFIXES)
        ):
            skipped_symlinks.append(normalized_name)

        if should_remove(
            normalized_name, mode, link_count, remove_icu, referenced_fonts
        ):
            removed_count += 1
            continue

        if (
            normalized_name in STRIP_TARGETS
            and file_type == stat.S_IFREG
            and link_count == 1
            and is_elf(entry_data)
        ):
            stripped = strip_elf(entry_data)
            if len(stripped) < len(entry_data):
                stripped_count += 1
                header = bytearray(header)
                header[54:62] = f"{len(stripped):08x}".encode("ascii")
                entry_data = stripped

        output.extend(header)
        output.extend(name_bytes)
        output.extend(b"\0" * (-len(output) % 4))
        output.extend(entry_data)
        output.extend(b"\0" * (-len(output) % 4))

    output.extend(b"\0" * (-len(output) % pr.CPIO_BLOCK_SIZE))
    new_ramdisk = pr.compress_cpio(bytes(output))
    print(f"Removed: {removed_count} entries; stripped: {stripped_count} ELF files.")
    if skipped_symlinks:
        print(f"Preserved symlinks for review: {skipped_symlinks}")
    print(f"New ramdisk size: {len(new_ramdisk)} bytes")

    header = bytearray(parsed["header"])
    struct.pack_into("<I", header, 16, len(new_ramdisk))
    page_size = parsed["fields"][7]
    pad = lambda component: component + b"\0" * (-len(component) % page_size)
    new_image = (
        bytes(header)
        + pad(parsed["components"]["kernel"])
        + pad(new_ramdisk)
        + pad(parsed["components"]["second"])
        + pad(parsed["components"]["dt"])
        + data[parsed["component_end"]:]
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(new_image)
    print(f"Wrote slimmed image to: {args.output}")
    print("WARNING: header SHA-1 is stale; use only as package_recovery.py --built input.")


if __name__ == "__main__":
    main()