#!/usr/bin/env python3
"""Read-only diagnostic for an SM-T583 Android recovery image."""

import argparse
import gzip
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


_here = Path(__file__).resolve().parent
_pr_path = _here / "package_recovery.py"
if not _pr_path.is_file():
    sys.exit(f"package_recovery.py must sit next to this script: {_pr_path}")
_spec = importlib.util.spec_from_file_location("package_recovery", _pr_path)
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)

DEFAULT_LIMIT = 14680064

CONSERVATIVE_PATHS = {
    "system/bin/update_engine_sideload",
    "system/bin/bash",
    "system/bin/nano",
    "system/bin/toolbox",
    "sbin/bash",
}
CONSERVATIVE_PREFIXES = ("system/etc/terminfo/", "system/etc/nano/")
AGGRESSIVE_EXTRA_PATHS = {
    "system/bin/adbd",
    "system/lib/libadbd_services.so",
}
AGGRESSIVE_EXTRA_PREFIXES = ()

ELF_TARGETS = {
    "system/bin/recovery",
    "system/bin/init",
    "system/bin/linker",
    "system/bin/adbd",
    "system/bin/toolbox",
    "system/bin/update_engine_sideload",
    "system/lib/libadbd_services.so",
}
TEXT_EXT = (".rc", ".sh", ".xml", ".cfg", ".txt", ".prop")
FONT_PATTERN = re.compile(r"([A-Za-z0-9_-]+\.ttf)", re.IGNORECASE)


def path_matches(path, exact, prefixes):
    if path in exact:
        return True
    for prefix in prefixes:
        if not prefix.endswith("/"):
            raise ValueError(f"prefix must end with '/': {prefix!r}")
        if path.startswith(prefix):
            return True
    return False


def run_tool(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None


def tool_error(label, result):
    if result is None:
        return f"{label}: executable not found"
    detail = result.stderr.strip().splitlines()
    suffix = f": {detail[0][:240]}" if detail else ""
    return f"{label}: exit {result.returncode}{suffix}"


def elf_report(name, blob, temp_dir):
    target = Path(temp_dir) / name.replace("/", "_")
    target.write_bytes(blob)
    report = {"file": None, "needed": None, "sections": None, "errors": []}

    if not blob.startswith(b"\x7fELF"):
        report["errors"].append("ELF magic missing")
        return report

    file_tool = shutil.which("file")
    if file_tool:
        result = run_tool([file_tool, str(target)])
        if result is not None and result.returncode == 0:
            report["file"] = result.stdout.strip()
        else:
            report["errors"].append(tool_error("file", result))
    else:
        report["errors"].append("file: executable not found")

    readelf = shutil.which("readelf")
    if not readelf:
        report["errors"].append("readelf: executable not found")
        return report

    result = run_tool([readelf, "-dW", str(target)])
    if result is not None and result.returncode == 0:
        needed = []
        for line in result.stdout.splitlines():
            if "NEEDED" in line:
                match = re.search(r"\[(.*?)\]", line)
                if match:
                    needed.append(match.group(1))
        report["needed"] = needed
    else:
        report["errors"].append(tool_error("readelf -dW", result))

    result = run_tool([readelf, "-SW", str(target)])
    if result is not None and result.returncode == 0:
        report["sections"] = [
            line.strip()
            for line in result.stdout.splitlines()
            if ".debug_" in line or ".symtab" in line or ".strtab" in line
        ]
    else:
        report["errors"].append(tool_error("readelf -SW", result))
    return report


def font_references(extracted_xml):
    references = set()
    fallback_paths = []
    for path, blob in extracted_xml.items():
        text = blob.decode("utf-8", "ignore")
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            fallback_paths.append(path)
            references.update(match.group(1).lower() for match in FONT_PATTERN.finditer(text))
            continue

        for element in root.iter():
            values = list(element.attrib.values())
            if element.text:
                values.append(element.text)
            if element.tail:
                values.append(element.tail)
            for value in values:
                references.update(
                    match.group(1).lower()
                    for match in FONT_PATTERN.finditer(value)
                )
    return references, fallback_paths


def main():
    parser = argparse.ArgumentParser(description="Inspect a recovery image without modifying it")
    parser.add_argument("image", type=Path)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()

    image_data = args.image.read_bytes()
    parsed = pr.parse_image(image_data)
    kernel_size, _, ramdisk_size, _, second_size, _, _, page_size, dt_size = parsed["fields"]

    print("=== Boot image header (v0 layout) ===")
    print(f"file_size: {len(image_data)} | kernel_size: {kernel_size} | ramdisk_size: {ramdisk_size}")
    print(f"second_size: {second_size} | page_size: {page_size} | dt_size (offset 40): {dt_size}")
    print(f"limit hint: {args.limit} | headroom: {args.limit - ramdisk_size}")

    ramdisk = parsed["components"]["ramdisk"]
    if ramdisk.startswith(b"\x1f\x8b"):
        cpio = gzip.decompress(ramdisk)
    elif ramdisk[:6] in (b"070701", b"070702"):
        cpio = ramdisk
    else:
        sys.exit(f"unsupported ramdisk compression magic: {ramdisk[:4].hex()}")

    inventory = []
    rewritten, removed_metadata = pr.rewrite_cpio(cpio, inventory=inventory)
    inventory_by_path = {entry["path"]: entry for entry in inventory}
    inventory_paths = set(inventory_by_path)

    text_paths = {
        entry["path"]
        for entry in inventory
        if entry["type"] == "file" and entry["path"].lower().endswith(TEXT_EXT)
    }
    xml_paths = {
        path for path in text_paths
        if path.startswith("twres/") and path.lower().endswith(".xml")
    }
    font_paths = {
        path for path in inventory_paths
        if path.startswith("twres/fonts/") and path.lower().endswith(".ttf")
    }
    wanted = set(ELF_TARGETS) | text_paths | xml_paths
    extracted = {}
    for _, normalized_name, _, _, entry_data, _, _, _ in pr.iter_cpio_entries(cpio):
        if normalized_name in wanted:
            extracted.setdefault(normalized_name, entry_data)

    baseline_gzip = pr.compress_cpio(rewritten)
    if len(baseline_gzip) != ramdisk_size:
        print(
            f"WARN: packager baseline {len(baseline_gzip)} != "
            f"header ramdisk_size {ramdisk_size}; compare cautiously."
        )

    print("\n=== CPIO inventory ===")
    print(f"entries: {len(inventory)} | cpio_bytes: {len(cpio)} | baseline_gzip: {len(baseline_gzip)}")
    print(f"removed build metadata: {removed_metadata}")

    present_fonts = {Path(path).name.lower() for path in font_paths}
    xml_data = {path: extracted[path] for path in xml_paths if path in extracted}
    referenced_fonts, fallback_paths = font_references(xml_data)
    print("\n=== Fonts (unused entries are candidates only) ===")
    print(f"present: {sorted(present_fonts)}")
    print(f"referenced: {sorted(referenced_fonts)}")
    print(f"unused candidates: {sorted(present_fonts - referenced_fonts)}")
    if fallback_paths:
        print(f"regex fallback used for malformed XML: {sorted(fallback_paths)}")
    print("NOTE: dynamic references may be missed; unused does not prove runtime-unused.")

    print("\n=== ELF diagnostics (direct NEEDED only; not a safety proof) ===")
    with tempfile.TemporaryDirectory(prefix="diag_elf_") as temp_dir:
        for target in sorted(ELF_TARGETS):
            blob = extracted.get(target)
            if blob is None:
                print(f"\n--- {target}: MISSING in CPIO ---")
                continue

            report = elf_report(target, blob, temp_dir)
            print(f"\n--- {target} ({len(blob)} bytes) ---")
            if report["file"]:
                print(f"  file: {report['file']}")
            if report["needed"] is not None:
                matches = {}
                for library in report["needed"]:
                    matches[library] = [
                        {"path": path, "type": inventory_by_path[path]["type"]}
                        for path in sorted(inventory_paths)
                        if path.startswith("system/lib/")
                        and Path(path).name == library
                    ]
                print(f"  NEEDED: {report['needed']}")
                print(f"  system/lib basename matches (not resolved): {matches}")
            if report["sections"] is not None:
                print(f"  debug/symbol sections: {report['sections'] or '(none listed)'}")
            if report["errors"]:
                print(f"  ERRORS: {report['errors']}")

            basename = Path(target).name
            mentions = [
                path for path in sorted(text_paths)
                if path in extracted
                and basename in extracted[path].decode("utf-8", "ignore")
            ]
            if mentions:
                print(f"  text mentions (heuristic): {mentions[:5]}" + ("..." if len(mentions) > 5 else ""))

    print("\n=== Removal size simulations (exact packager rewrite+compress) ===")
    print("NOTE: size-only; no files are removed and runtime safety is not checked.")

    def scenario(name, exact, prefixes):
        excluded = set()
        skipped_links = 0
        for entry in inventory:
            if entry["type"] != "file" or not path_matches(entry["path"], exact, prefixes):
                continue
            if entry["link_count"] == 1:
                excluded.add(entry["path"])
            else:
                skipped_links += 1

        rewritten_scenario, removed = pr.rewrite_cpio(cpio, excluded_names=excluded)
        size = len(pr.compress_cpio(rewritten_scenario))
        removed_candidates = sorted(set(removed) & excluded)
        print(f"\n[{name}]")
        print(f"  candidate regular files: {len(excluded)}")
        print(f"  skipped multi-link entries: {skipped_links}")
        print(f"  candidate paths removed: {len(removed_candidates)}")
        print(f"  gzip result: {size} bytes | headroom: {args.limit - size}")
        return size

    conservative_size = scenario(
        "CONSERVATIVE_SIZE_ONLY", CONSERVATIVE_PATHS, CONSERVATIVE_PREFIXES
    )
    aggressive_size = scenario(
        "AGGRESSIVE_SIZE_ONLY",
        CONSERVATIVE_PATHS | AGGRESSIVE_EXTRA_PATHS,
        CONSERVATIVE_PREFIXES + AGGRESSIVE_EXTRA_PREFIXES,
    )
    print("\n=== Summary (informational only) ===")
    print(f"current packager baseline: {len(baseline_gzip)}")
    print(f"conservative candidate size: {conservative_size}")
    print(f"aggressive candidate size: {aggressive_size}")
    print(f"limit hint: {args.limit}")
    print("PASS/FAIL belongs to the workflow validation step, not this diagnostic.")


if __name__ == "__main__":
    main()