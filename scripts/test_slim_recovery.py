import stat
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import slim_recovery


def make_entry(path, mode, data, inode):
    name_bytes = path.encode("utf-8") + b"\0"
    fields = (inode, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name_bytes), 0)
    header = b"070701" + b"".join(f"{value:08x}".encode("ascii") for value in fields)
    return slim_recovery.wrap((path, path, header, name_bytes, data, mode, 1, len(data)))


class RecoverySlimmingTests(unittest.TestCase):
    def test_malformed_theme_xml_fallback_preserves_literal_font_references(self):
        entry = make_entry(
            "twres/themes/theme.xml",
            stat.S_IFREG | 0o644,
            b'<theme font="Roboto-Bold.ttf"><broken',
            1,
        )

        self.assertEqual(
            slim_recovery.find_referenced_fonts([entry]), {"roboto-bold.ttf"}
        )

    def test_malformed_theme_xml_without_references_keeps_font_candidates(self):
        entry = make_entry(
            "twres/themes/theme.xml", stat.S_IFREG | 0o644, b"<theme>", 1
        )

        self.assertIsNone(slim_recovery.find_referenced_fonts([entry]))

    def test_adb_and_perfetto_libraries_are_optional_not_forced_removals(self):
        library_names = {
            "libadbconnection_server.so",
            "libadb_protos.so",
            "libadbd_auth.so",
            "libperfetto_c.so",
        }

        for name in library_names:
            for directory in ("system/lib/", "system/lib64/"):
                path = directory + name
                self.assertNotIn(path, slim_recovery.REMOVE_FILES)
                self.assertIn(path, slim_recovery.OPTIONAL_LIBS)

    def test_unreferenced_optional_library_is_removed(self):
        consumer = make_entry(
            "system/bin/consumer", stat.S_IFREG | 0o755, b"\x7fELFconsumer", 1
        )
        library = make_entry(
            "system/lib/libperfetto_c.so", stat.S_IFREG | 0o644, b"\x7fELFlibrary", 2
        )

        with patch.object(slim_recovery.rr, "elf_needed", return_value=[]):
            removed = slim_recovery.plan_removals([consumer, library], set())

        self.assertEqual(removed.get(library.norm), "unused-lib")

    def test_referenced_optional_library_is_kept(self):
        consumer = make_entry(
            "system/bin/consumer", stat.S_IFREG | 0o755, b"\x7fELFconsumer", 1
        )
        library = make_entry(
            "system/lib/libperfetto_c.so", stat.S_IFREG | 0o644, b"\x7fELFlibrary", 2
        )

        def needed(data):
            return ["libperfetto_c.so"] if data.endswith(b"consumer") else []

        with patch.object(slim_recovery.rr, "elf_needed", side_effect=needed):
            removed = slim_recovery.plan_removals([consumer, library], set())

        self.assertNotIn(library.norm, removed)

    def test_optional_libraries_are_kept_when_elf_analysis_is_incomplete(self):
        malformed_elf = make_entry(
            "system/bin/consumer", stat.S_IFREG | 0o755, b"\x7fELFbroken", 1
        )
        library = make_entry(
            "system/lib/libperfetto_c.so", stat.S_IFREG | 0o644, b"\x7fELFlibrary", 2
        )

        with patch.object(slim_recovery.rr, "elf_needed", return_value=None):
            removed = slim_recovery.plan_removals([malformed_elf, library], set())

        self.assertNotIn(library.norm, removed)


if __name__ == "__main__":
    unittest.main()