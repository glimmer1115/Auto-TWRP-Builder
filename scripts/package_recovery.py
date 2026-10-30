import argparse
import gzip
import hashlib
import json
import stat
import struct
import sys
from collections import defaultdict
from pathlib import PurePosixPath
from pathlib import Path

PARTITION_SIZE = 39845888
TRAILER = b"SEANDROIDENFORCE"
BUILD_METADATA = {"ramdisk-files.txt", "ramdisk-files.sha256sum"}
CPIO_BLOCK_SIZE = 512
RAMDISK_LIMIT = 39845888


def parse_image(data):
    if len(data) < 44 or data[:8] != b"ANDROID!":
        raise ValueError("input is not an Android boot image")
    fields = struct.unpack("<9I", data[8:44])
    kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, second_addr, tags_addr, page_size, dt_size = fields
    if page_size < 512 or page_size & (page_size - 1):
        raise ValueError("invalid Android boot image page size")

    def align(size):
        return (size + page_size - 1) // page_size * page_size

    kernel_start = page_size
    ramdisk_start = kernel_start + align(kernel_size)
    second_start = ramdisk_start + align(ramdisk_size)
    dt_start = second_start + align(second_size)
    components = {
        "kernel": data[kernel_start:kernel_start + kernel_size],
        "ramdisk": data[ramdisk_start:ramdisk_start + ramdisk_size],
        "second": data[second_start:second_start + second_size],
        "dt": data[dt_start:dt_start + dt_size],
    }
    expected = (kernel_size, ramdisk_size, second_size, dt_size)
    actual = tuple(len(components[name]) for name in ("kernel", "ramdisk", "second", "dt"))
    if actual != expected:
        raise ValueError("Android boot image component is truncated")
    return {
        "header": data[:page_size],
        "fields": fields,
        "components": components,
        "component_end": dt_start + dt_size,
    }


def iter_cpio_entries(data):
    offset, size = 0, len(data)
    while offset + 110 <= size:
        header = data[offset:offset + 110]
        if header[:6] not in (b"070701", b"070702"):
            raise ValueError("ramdisk is not a newc CPIO archive")
        try:
            file_size = int(header[54:62], 16)
            mode = int(header[14:22], 16)
            link_count = int(header[38:46], 16)
            name_size = int(header[94:102], 16)
        except ValueError as exc:
            raise ValueError("invalid CPIO numeric field") from exc
        name_start = offset + 110
        name_end = name_start + name_size
        if name_size < 1 or name_end > size or data[name_end - 1:name_end] != b"\0":
            raise ValueError("invalid CPIO filename")
        name_bytes = data[name_start:name_end]
        raw_name = name_bytes[:-1].decode("utf-8", "replace")
        normalized_name = raw_name[2:] if raw_name.startswith("./") else raw_name
        data_start = (name_end + 3) & ~3
        data_end = data_start + file_size
        if data_end > size:
            raise ValueError("truncated CPIO entry: " + raw_name)

        entry_data = data[data_start:data_end]
        if raw_name == "TRAILER!!!":
            if any(data[data_end:]):
                raise ValueError("non-zero data follows the CPIO trailer")
            yield (
                raw_name, normalized_name, header, name_bytes, entry_data,
                mode, link_count, file_size,
            )
            return

        yield (
            raw_name, normalized_name, header, name_bytes, entry_data,
            mode, link_count, file_size,
        )
        offset = (data_end + 3) & ~3
    raise ValueError("CPIO trailer is missing")


def rewrite_cpio(data, excluded_names=None, inventory=None):
    output = bytearray()
    removed = []
    excluded_names = set(excluded_names or ())
    trailer_found = False
    for (
        raw_name, normalized_name, header, name_bytes, entry_data,
        mode, link_count, file_size,
    ) in iter_cpio_entries(data):
        if inventory is not None and raw_name != "TRAILER!!!":
            file_type = stat.S_IFMT(mode)
            type_names = {
                stat.S_IFDIR: "directory",
                stat.S_IFREG: "file",
                stat.S_IFLNK: "symlink",
                stat.S_IFCHR: "character-device",
                stat.S_IFBLK: "block-device",
                stat.S_IFIFO: "fifo",
                stat.S_IFSOCK: "socket",
            }
            inventory.append({
                "path": normalized_name,
                "type": type_names.get(file_type, "unknown"),
                "mode": format(mode, "06o"),
                "link_count": link_count,
                "size_bytes": file_size,
            })

        if raw_name == "TRAILER!!!":
            output.extend(header)
            output.extend(name_bytes)
            output.extend(b"\0" * (-len(output) % 4))
            output.extend(entry_data)
            output.extend(b"\0" * (-len(output) % 4))
            trailer_found = True
            break

        if normalized_name in BUILD_METADATA or normalized_name in excluded_names:
            removed.append(normalized_name)
        else:
            output.extend(header)
            output.extend(name_bytes)
            output.extend(b"\0" * (-len(output) % 4))
            output.extend(entry_data)
            output.extend(b"\0" * (-len(output) % 4))

    if not trailer_found:
        raise ValueError("CPIO trailer is missing")
    output.extend(b"\0" * (-len(output) % CPIO_BLOCK_SIZE))
    return bytes(output), removed


def compress_cpio(cpio):
    buf = gzip.compress(cpio, 9, mtime=0)
    return buf


def compress_cpio_best(cpio, iterations=15):
    base = compress_cpio(cpio)
    try:
        from zopfli.gzip import compress as zcompress
    except ImportError:
        print("WARNING: zopfli 미설치 → zlib 결과 사용")
        return base

    z = bytearray(zcompress(cpio, numiterations=iterations))
    z[8], z[9] = 0, 3  # OS 타입 강제 지정 (Unix)
    if gzip.decompress(bytes(z)) != cpio:
        print("WARNING: zopfli 검증 실패 → zlib 결과 사용")
        return base
    print(f"zopfli 압축 결과: {len(base):,} → {len(z):,} bytes")
    return bytes(z) if len(z) < len(base) else base


def compress_ramdisk(blob):
    if blob.startswith(b"\x1f\x8b"):
        cpio = gzip.decompress(blob)
    else:
        cpio = blob
    rewritten, removed = rewrite_cpio(cpio)
    # [수정] zopfli 압축 적용
    compressed = compress_cpio_best(rewritten)
    return compressed, len(rewritten), removed


def analyze_ramdisk(blob, packaged_size, ramdisk_limit=RAMDISK_LIMIT, candidate_count=12):
    cpio = gzip.decompress(blob) if blob.startswith(b"\x1f\x8b") else blob
    entries = []
    rewritten, removed = rewrite_cpio(cpio, inventory=entries)
    
    baseline_size = packaged_size

    directories = defaultdict(lambda: {"entry_count": 0, "payload_bytes": 0})
    for entry in entries:
        path = PurePosixPath(entry["path"])
        if entry["type"] == "directory":
            directories[entry["path"]]
        parent = path.parent
        while parent.as_posix() != ".":
            directory = directories[parent.as_posix()]
            directory["entry_count"] += 1
            directory["payload_bytes"] += entry["size_bytes"]
            parent = parent.parent

    candidates = []
    seen_paths = set()
    for entry in sorted(
        entries,
        key=lambda item: (-item["size_bytes"], item["path"]),
    ):
        if (
            entry["type"] == "file"
            and entry["link_count"] == 1
            and entry["size_bytes"] > 0
            and entry["path"] not in BUILD_METADATA
            and entry["path"] not in seen_paths
        ):
            candidates.append(entry)
            seen_paths.add(entry["path"])
        if len(candidates) >= candidate_count:
            break

    single_removals = []
    for entry in candidates:
        simulated, _ = rewrite_cpio(cpio, excluded_names={entry["path"]})
        simulated_size = len(compress_cpio_best(simulated))
        single_removals.append({
            "path": entry["path"],
            "input_payload_bytes": entry["size_bytes"],
            "ramdisk_size_bytes": simulated_size,
            "reduction_bytes": baseline_size - simulated_size,
        })

    cumulative_removals = []
    cumulative_counts = [count for count in (3, 6, 9, 12) if count <= len(candidates)]
    if candidates and len(candidates) not in cumulative_counts:
        cumulative_counts.append(len(candidates))
    for count in cumulative_counts:
        excluded = {entry["path"] for entry in candidates[:count]}
        simulated, _ = rewrite_cpio(cpio, excluded_names=excluded)
        simulated_size = len(compress_cpio_best(simulated))
        cumulative_removals.append({
            "largest_files_removed": count,
            "paths": [entry["path"] for entry in candidates[:count]],
            "ramdisk_size_bytes": simulated_size,
            "reduction_bytes": baseline_size - simulated_size,
            "headroom_bytes": ramdisk_limit - simulated_size,
        })

    directory_report = [
        {"path": path, **sizes}
        for path, sizes in sorted(directories.items())
    ]
    
    # [수정] 리포트의 기준 크기를 실제 패키징된 크기(packaged_size)로 맞춤
    return {
        "format_version": 1,
        "compression": "gzip level 9 + zopfli",
        "source_ramdisk_bytes": len(blob),
        "source_cpio_bytes": len(cpio),
        "packaged_cpio_bytes": len(rewritten),
        "packaged_ramdisk_bytes": packaged_size, 
        "ramdisk_limit_bytes": ramdisk_limit,
        "headroom_bytes": ramdisk_limit - packaged_size,
        "excess_bytes": max(0, packaged_size - ramdisk_limit),
        "removed_build_metadata": removed,
        "entries": entries,
        "directories": directory_report,
        "simulation_note": (
            "Hypothetical size-only simulations; no files were removed. "
            "They do not establish runtime dependency safety."
        ),
        "largest_file_single_removals": single_removals,
        "largest_file_cumulative_removals": cumulative_removals,
    }


def package(stock_data, built_data):
    stock = parse_image(stock_data)
    built = parse_image(built_data)
    stock_fields = stock["fields"]
    stock_parts = stock["components"]
    built_parts = built["components"]
    kernel = stock_parts["kernel"]
    dt = stock_parts["dt"]
    if not kernel or kernel[56:60] != b"ARM\x64":
        raise ValueError("stock recovery does not contain an Exynos ARM64 Image kernel")
    if not dt:
        raise ValueError("stock recovery image has no separate DT component")
    stock_suffix = stock_data[stock["component_end"]:]
    if not stock_suffix.startswith(TRAILER):
        raise ValueError("stock recovery image is missing the SEANDROIDENFORCE trailer")
    stock_footer = stock_suffix[len(TRAILER):]
    if stock_footer and not stock_footer.startswith(b"SignerVer02"):
        raise ValueError("stock recovery image has an unknown footer after SEANDROIDENFORCE")

    ramdisk, cpio_size, removed = compress_ramdisk(built_parts["ramdisk"])
    header = bytearray(stock["header"])
    kernel_addr, ramdisk_addr, second_addr, tags_addr, page_size = (
        stock_fields[1], stock_fields[3], stock_fields[5], stock_fields[6], stock_fields[7]
    )
    struct.pack_into("<9I", header, 8,
                     len(kernel), kernel_addr, len(ramdisk), ramdisk_addr,
                     0, second_addr, tags_addr, page_size, len(dt))

    digest = hashlib.sha1()
    for component in (kernel, ramdisk, b"", dt):
        digest.update(component)
        digest.update(struct.pack("<I", len(component)))
    header[576:596] = digest.digest()
    header[596:608] = b"\0" * 12

    pad = lambda component: component + b"\0" * (-len(component) % page_size)
    image = bytes(header) + pad(kernel) + pad(ramdisk) + pad(b"") + pad(dt) + TRAILER
    if len(image) > PARTITION_SIZE:
        raise ValueError("packaged image exceeds recovery partition: {} > {}".format(len(image), PARTITION_SIZE))

    parsed = parse_image(image)
    if parsed["components"]["kernel"] != kernel or parsed["components"]["ramdisk"] != ramdisk:
        raise ValueError("packaged image component verification failed")
    if parsed["components"]["dt"] != dt or parsed["fields"][8] != len(dt):
        raise ValueError("packaged stock DT verification failed")
    if parsed["header"][576:596] != digest.digest():
        raise ValueError("Android boot header ID verification failed")
    if image[parsed["component_end"]:] != TRAILER:
        raise ValueError("packaged SEANDROIDENFORCE trailer verification failed")
    if not gzip.decompress(parsed["components"]["ramdisk"]).startswith((b"070701", b"070702")):
        raise ValueError("packaged ramdisk is not a CPIO archive")
    return image, len(ramdisk), cpio_size, removed


def main():
    parser = argparse.ArgumentParser(description="Package an SM-T583 recovery with its stock kernel and DT")
    parser.add_argument("--stock", required=True, type=Path)
    parser.add_argument("--built", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--analysis-report", type=Path)
    parser.add_argument("--ramdisk-limit", type=int, default=RAMDISK_LIMIT)
    args = parser.parse_args()

    stock_data = args.stock.read_bytes()
    stock_image = parse_image(stock_data)
    stock_footer_size = max(0, len(stock_data) - stock_image["component_end"] - len(TRAILER))
    
    built_data = args.built.read_bytes()
    image, ramdisk_size, cpio_size, removed = package(stock_data, built_data)
    
    # [수정] slim_recovery.py에서 잘못 복사된 NameError 유발 코드 삭제

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image)
    
    if args.analysis_report:
        built_ramdisk = parse_image(built_data)["components"]["ramdisk"]
        report = analyze_ramdisk(built_ramdisk, ramdisk_size, args.ramdisk_limit)
        args.analysis_report.parent.mkdir(parents=True, exist_ok=True)
        args.analysis_report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print("Ramdisk analysis report:", args.analysis_report)
    print("Final image bytes:", len(image))
    print("Stock SignerVer02 footer excluded bytes:", stock_footer_size)
    print("Ramdisk compressed bytes:", ramdisk_size)
    print("Ramdisk CPIO bytes:", cpio_size)
    print("Stock DT bytes:", struct.unpack_from("<I", image, 40)[0])
    print("Removed non-runtime build metadata:", ", ".join(removed) if removed else "none")


if __name__ == "__main__":
    main()