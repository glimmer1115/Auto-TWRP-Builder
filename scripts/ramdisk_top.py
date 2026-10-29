#!/usr/bin/env python3
"""ramdisk_top.py — 램디스크를 '압축 후 기여도' 기준으로 분석한다.
  
  python3 ramdisk_top.py --image recovery-slim.img [--top 40]

출력
  1) 압축 후 크기 상위 파일   (* = 압축률 90% 이상 → 지우면 거의 1:1로 줄어듦)
  2) 디렉터리(2단계)별 합계
  3) 내용이 완전히 같은 중복 파일 (절감 가능량 순)
"""
import argparse
import gzip
import hashlib
import importlib.util
import stat
import zlib
from collections import defaultdict
from pathlib import Path

here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("package_recovery", here / "package_recovery.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()

    parsed = pr.parse_image(args.image.read_bytes())
    blob = parsed["components"]["ramdisk"]
    cpio = gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else blob
    print(f"램디스크(압축) {len(blob):,} B / cpio {len(cpio):,} B")

    rows = []
    by_hash = defaultdict(list)
    by_dir = defaultdict(lambda: [0, 0, 0])
    for raw, norm, _hdr, _nb, data, mode, nlink, size in pr.iter_cpio_entries(cpio):
        if raw == "TRAILER!!!" or stat.S_IFMT(mode) != stat.S_IFREG or size == 0:
            continue
        comp = len(zlib.compress(data, 6))
        rows.append((comp, size, nlink, norm))
        by_hash[hashlib.sha1(data).hexdigest()].append((norm, size, comp))
        parts = norm.split("/")
        key = "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) > 1 else "(root)")
        d = by_dir[key]
        d[0] += 1
        d[1] += size
        d[2] += comp

    rows.sort(reverse=True)
    print(f"\n[1] 압축 후 상위 {args.top}개 파일 (파일 단위 zlib 추정치, 총 {sum(r[0] for r in rows):,} B)")
    print(f"{'comp':>11} {'raw':>11} {'ratio':>6} nl  path")
    for comp, size, nlink, norm in rows[:args.top]:
        ratio = comp / size
        mark = "*" if ratio >= 0.9 else " "
        print(f"{comp:>11,} {size:>11,} {ratio:>5.0%}{mark} {nlink:<3} {norm}")

    print("\n[2] 디렉터리별 합계 (압축 후 기준 상위 20)")
    print(f"{'comp':>11} {'raw':>11} {'files':>5}  dir")
    for key, (n, raw_b, comp_b) in sorted(by_dir.items(), key=lambda kv: -kv[1][2])[:20]:
        print(f"{comp_b:>11,} {raw_b:>11,} {n:>5}  {key}")

    dups = []
    for items in by_hash.values():
        if len(items) > 1:
            saving = items[0][2] * (len(items) - 1)
            dups.append((saving, items))
    dups.sort(key=lambda t: -t[0])
    print(f"\n[3] 중복 파일 (총 절감 가능 약 {sum(s for s, _ in dups):,} B)")
    for saving, items in dups[:15]:
        print(f"  -{saving:,} B  ({len(items)}개, 각 {items[0][1]:,} B)")
        for norm, _s, _c in items:
            print(f"      {norm}")


if __name__ == "__main__":
    main()
