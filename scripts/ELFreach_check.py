#!/usr/bin/env python3
"""ELF DT_NEEDED 하드 도달성만 검사하는 단계별 진입점."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reach_check


if __name__ == "__main__":
    raise SystemExit(reach_check.main(["--phase", "elf", *sys.argv[1:]]))