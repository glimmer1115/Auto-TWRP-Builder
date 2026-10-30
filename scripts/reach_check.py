#!/usr/bin/env python3
"""Android recovery 이미지의 ELF 도달성을 읽기 전용으로 분석합니다."""
import argparse
import os
import re
import stat
import sys
from collections import defaultdict, deque
from pathlib import Path

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))
import package_recovery as pr
import ramdisk_report as rr

CORE_ROOTS = (
    "system/bin/init",
    "system/bin/recovery",
    "system/bin/linker",
    "system/bin/linker64",
    "system/bin/fox",
    "system/bin/toybox",
    "system/bin/sh",
    "sbin/magiskboot",
)
EXEC_PATH_RE = re.compile(r"(?:^|/)(?:s?bin|xbin)/[^/]+$")
EXEC_SCANNERS = ("system/bin/recovery", "system/bin/fox", "system/bin/init")
DEFAULT_TARGETS = (
    "adbd",
    "minadbd",
    "libadbd.so",
    "libadbd_services.so",
    "libadbd_fs.so",
    "libadb_crypto.so",
    "libadbconnection_server.so",
    "libadb_protos.so",
    "libadbd_auth.so",
    "libperfetto_c.so",
    "libncurses.so",
    "libandroidicu.so",
    "libicui18n.so",
    "libicuuc.so",
    "libicudata.so",
)

SO_RE = re.compile(rb"lib[A-Za-z0-9_+.\-]*\.so")
TOKEN_RE = re.compile(rb"[A-Za-z0-9_.+\-]{3,}")
PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.+\-])/?((?:(?:system|vendor|system_ext|product)/)?"
    r"(?:s?bin|xbin)/[\w.+\-]+)"
)


class KoreanHelpFormatter(argparse.HelpFormatter):
    def _format_usage(self, usage, actions, groups, prefix=None):
        return super()._format_usage(
            usage, actions, groups, prefix="사용법: " if prefix is None else prefix
        )


def load(image_path):
    image = image_path.read_bytes()
    parsed = pr.parse_image(image)
    cpio = rr.decompress_ramdisk(parsed["components"]["ramdisk"])
    files = {}
    for raw, norm, _header, _name_bytes, data, mode, _nlink, _size in pr.iter_cpio_entries(cpio):
        if raw == "TRAILER!!!":
            break
        files[norm] = (mode, data)
    return files


def resolve_elf(path, files, elfs):
    visited = set()
    current = path.lstrip("/")
    while current not in visited:
        if current in elfs:
            return current
        visited.add(current)
        entry = files.get(current)
        if entry is None or stat.S_IFMT(entry[0]) != stat.S_IFLNK:
            return None
        current = rr.resolve_link(current, entry[1].decode("utf-8", "replace"))
    return None


def bfs(roots, edges):
    parents = {root: None for root in roots}
    queue = deque(roots)
    while queue:
        current = queue.popleft()
        for target in edges.get(current, ()):
            if target not in parents:
                parents[target] = current
                queue.append(target)
    return parents


def chain(parents, node, limit=8):
    path = []
    while node is not None:
        path.append(node)
        node = parents[node]
    path.reverse()
    result = " -> ".join(path[:limit])
    return result + (" -> ..." if len(path) > limit else "")


def script_token_references(elfs, texts, exes):
    references = set()
    for scanner in EXEC_SCANNERS:
        if scanner not in elfs:
            continue
        for token in set(TOKEN_RE.findall(elfs[scanner])):
            name = token.decode("ascii", "ignore")
            for target in exes.get(name, ()):
                references.add(("ELF-TOKEN", scanner, name, target))

    for source, data in texts.items():
        for token in set(TOKEN_RE.findall(data)):
            name = token.decode("ascii", "ignore")
            for target in exes.get(name, ()):
                references.add(("SCRIPT-TOKEN", source, name, target))
    return references


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=KoreanHelpFormatter, add_help=False
    )
    parser._positionals.title = "위치 인수"
    parser._optionals.title = "옵션"
    parser.add_argument("-h", "--help", action="help", help="도움말 표시")
    parser.add_argument("--image", required=True, type=Path, metavar="이미지", help="분석할 recovery 이미지")
    parser.add_argument(
        "--phase",
        choices=("combined", "elf", "script-token"),
        default="combined",
        help="검증 단계 (기본값: combined)",
    )
    parser.add_argument(
        "libs", nargs="*", metavar="라이브러리", help="출력할 ELF 파일 기본 이름"
    )
    args = parser.parse_args(argv)

    try:
        files = load(args.image)
    except (OSError, ValueError, KeyError) as exc:
        print(f"이미지 분석 실패: {exc}", file=sys.stderr)
        return 2

    elfs = {
        path: data
        for path, (mode, data) in files.items()
        if stat.S_IFMT(mode) == stat.S_IFREG and data[:4] == b"\x7fELF"
    }
    by_name = defaultdict(list)
    for path in elfs:
        by_name[os.path.basename(path)].append(path)

    needed = {}
    if args.phase != "script-token":
        unparsed = []
        for path, data in elfs.items():
            dependencies = rr.elf_needed(data)
            if dependencies is None:
                unparsed.append(path)
            else:
                needed[path] = dependencies
        if unparsed:
            print(
                f"분석 중단: ELF {len(unparsed)}개를 해석하지 못했습니다 "
                f"(예: {unparsed[0]}). ORPHAN 판정을 출력하지 않습니다.",
                file=sys.stderr,
            )
            return 2

    exes = defaultdict(set)
    for path in elfs:
        if EXEC_PATH_RE.search(path):
            exes[os.path.basename(path)].add(path)
    for path, (mode, data) in files.items():
        if stat.S_IFMT(mode) != stat.S_IFLNK or not EXEC_PATH_RE.search(path):
            continue
        target = resolve_elf(path, files, elfs)
        if target:
            exes[os.path.basename(path)].add(target)

    roots = set()
    root_sources = {}

    def add_root(path, source):
        target = resolve_elf(path, files, elfs)
        if target:
            roots.add(target)
            root_sources.setdefault(target, source)

    for root in CORE_ROOTS:
        add_root(root, "core")

    texts = {
        path: data
        for path, (mode, data) in files.items()
        if stat.S_IFMT(mode) == stat.S_IFREG
        and (path.endswith((".rc", ".sh")) or path.startswith("FFiles/"))
    }
    path_references = set()
    for path, data in texts.items():
        text = data.decode("utf-8", "ignore")
        for match in PATH_RE.finditer(text):
            add_root(match.group(1), f"{path}에서 참조")
            for candidate in (match.group(1), match.group(1).replace("system/", "", 1)):
                target = resolve_elf(candidate, files, elfs)
                if target:
                    path_references.add((path, match.group(1), target))

    if args.phase == "script-token":
        print("분석 단계: script/token 참조만")
        for source, reference, target in sorted(path_references):
            print(f"PATH-REFERENCE: {source}: {reference} => {target}")
        references = script_token_references(elfs, texts, exes)
        print(f"토큰 참조 {len(references)}개")
        for kind, source, name, target in sorted(references):
            print(f"{kind}: {source}: {name} => {target}")
        return 0

    print(f"ELF {len(elfs)}개, 루트 {len(roots)}개")
    for root in sorted(roots):
        print(f"  루트: {root} ({root_sources[root]})")

    hard = {
        path: {
            target
            for name in needed[path]
            for target in by_name.get(name, ())
        }
        for path in elfs
    }
    hard_parents = bfs(roots, hard)
    if args.phase == "elf":
        print("분석 단계: ELF DT_NEEDED 하드 간선만")
        for name in args.libs or DEFAULT_TARGETS:
            paths = by_name.get(name, ())
            if not paths:
                print(f"== {name}: 램디스크에 없음")
                continue
            for path in paths:
                refs = sorted(source for source in elfs if path in hard[source])
                rendered_refs = ", ".join(
                    f"{source}[{'R' if source in hard_parents else '-'}]"
                    for source in refs
                ) or "없음"
                print(f"== {path}")
                print(f"   직접 NEEDED 참조: {rendered_refs}")
                if path in hard_parents:
                    print("   판정: HARD-REACHABLE")
                else:
                    print("   판정: NO-HARD-PATH (소프트 참조는 이 단계에서 검사하지 않음)")
                print()
        return 0

    soft = defaultdict(set)
    for path, data in elfs.items():
        for match in SO_RE.findall(data):
            name = match.decode("ascii", "ignore")
            soft[path].update(target for target in by_name.get(name, ()) if target != path)
        soft[path].difference_update(hard[path])

    for kind, source, _name, target in script_token_references(elfs, texts, exes):
        if kind == "ELF-TOKEN":
            soft[source].add(target)
        else:
            soft["__scripts__"].add(target)

    all_edges = {path: hard[path] | soft.get(path, set()) for path in elfs}
    soft_roots = roots | soft["__scripts__"]
    all_parents = bfs(soft_roots, all_edges)

    def mark(path):
        return "R" if path in hard_parents else ("s" if path in all_parents else "-")

    print("\n표기: R=하드 도달, s=소프트(dlopen/exec) 도달, -=도달 불가\n")
    for name in args.libs or DEFAULT_TARGETS:
        paths = by_name.get(name, ())
        if not paths:
            print(f"== {name}: 램디스크에 없음")
            continue
        for path in paths:
            refs = sorted(source for source in elfs if name in needed[source])
            soft_refs = sorted(source for source in elfs if path in soft.get(source, ()))
            print(f"== {path}")
            print(
                "   직접 참조(NEEDED): "
                + (", ".join(f"{source}[{mark(source)}]" for source in refs) or "없음")
            )
            if soft_refs:
                print(
                    "   문자열 참조(dlopen/exec 후보): "
                    + ", ".join(f"{source}[{mark(source)}]" for source in soft_refs)
                )
            if path in hard_parents:
                print(f"   판정: REACHABLE-HARD → 삭제 금지\n   경로: {chain(hard_parents, path)}")
            elif path in all_parents:
                print(f"   판정: SOFT-ONLY → 런타임 확인 필요\n   경로: {chain(all_parents, path)}")
            else:
                print("   판정: ORPHAN → 삭제 후보 (런타임 확인 권장)")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())