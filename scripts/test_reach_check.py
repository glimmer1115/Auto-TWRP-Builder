import io
import stat
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reach_check


class ReachCheckTests(unittest.TestCase):
    def setUp(self):
        self.files = {
            "init.rc": (
                stat.S_IFREG | 0o644,
                b"service adbd /system/bin/adbd\n",
            ),
            "system/bin/recovery": (
                stat.S_IFREG | 0o755,
                b"\x7fELFrecovery libsoft.so",
            ),
            "system/bin/adbd": (
                stat.S_IFREG | 0o755,
                b"\x7fELFadbd",
            ),
            "system/bin/minadbd": (
                stat.S_IFREG | 0o755,
                b"\x7fELFminadbd",
            ),
            "system/lib/libfoo.so": (
                stat.S_IFREG | 0o644,
                b"\x7fELFlibfoo",
            ),
            "system/lib/libsoft.so": (
                stat.S_IFREG | 0o644,
                b"\x7fELFlibsoft",
            ),
            "system/lib/liborphan.so": (
                stat.S_IFREG | 0o644,
                b"\x7fELFliborphan",
            ),
        }
        self.dependencies = {
            b"\x7fELFrecovery libsoft.so": ["libfoo.so"],
            b"\x7fELFadbd": ["libfoo.so"],
            b"\x7fELFminadbd": ["liborphan.so"],
            b"\x7fELFlibfoo": [],
            b"\x7fELFlibsoft": [],
            b"\x7fELFliborphan": [],
        }

    def run_report(self, parser_result=None):
        output = io.StringIO()
        errors = io.StringIO()
        side_effect = parser_result or self.dependencies.get
        with (
            patch.object(reach_check, "load", return_value=self.files),
            patch.object(reach_check.rr, "elf_needed", side_effect=side_effect),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            result = reach_check.main(
                ["--image", "fixture.img", "libfoo.so", "libsoft.so", "liborphan.so"]
            )
        return result, output.getvalue(), errors.getvalue()

    def test_reports_hard_soft_and_orphan_reachability(self):
        result, output, _errors = self.run_report()

        self.assertEqual(result, 0)
        self.assertIn("루트: system/bin/adbd", output)
        self.assertIn("판정: REACHABLE-HARD", output)
        self.assertIn("판정: SOFT-ONLY", output)
        self.assertIn("판정: ORPHAN", output)
        self.assertIn("system/bin/minadbd[-]", output)

    def test_stops_without_orphan_verdict_when_elf_parse_fails(self):
        result, output, errors = self.run_report(parser_result=lambda _data: None)

        self.assertEqual(result, 2)
        self.assertNotIn("판정: ORPHAN", output)
        self.assertIn("ORPHAN 판정을 출력하지 않습니다", errors)


if __name__ == "__main__":
    unittest.main()