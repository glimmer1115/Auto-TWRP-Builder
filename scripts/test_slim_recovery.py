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
            "libadbd.so",
            "libadbd_fs.so",
            "libadb_crypto.so",
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

    def test_reachable_or_soft_only_adb_daemons_are_not_forced_removed(self):
        for inode, path in enumerate(
            ("system/bin/adbd", "system/bin/minadbd"), start=1
        ):
            daemon = make_entry(
                path, stat.S_IFREG | 0o755, b"\x7fELF" + path.encode(), inode
            )

            with patch.object(slim_recovery.rr, "elf_needed", return_value=[]):
                removed = slim_recovery.plan_removals([daemon], set())

            self.assertNotIn(daemon.norm, removed)

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

    def test_optional_library_referenced_only_by_removed_nano_is_removed(self):
        nano = make_entry(
            "system/bin/nano", stat.S_IFREG | 0o755, b"\x7fELFnano", 1
        )
        library = make_entry(
            "system/lib/libncurses.so", stat.S_IFREG | 0o644, b"\x7fELFncurses", 2
        )

        def needed(data):
            return ["libncurses.so"] if data == nano.data else []

        with patch.object(slim_recovery.rr, "elf_needed", side_effect=needed):
            removed = slim_recovery.plan_removals([nano, library], set())

        self.assertEqual(removed.get(nano.norm), "explicit")
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

    def test_orphan_adb_library_chain_is_removed_but_live_perfetto_is_kept(self):
        recovery = make_entry(
            "system/bin/recovery", stat.S_IFREG | 0o755, b"\x7fELFrecovery", 1
        )
        servicemanager = make_entry(
            "system/bin/servicemanager",
            stat.S_IFREG | 0o755,
            b"\x7fELFservicemanager",
            2,
        )
        adbd = make_entry(
            "system/lib/libadbd.so", stat.S_IFREG | 0o644, b"\x7fELFlibadbd", 3
        )
        adb_crypto = make_entry(
            "system/lib/libadb_crypto.so",
            stat.S_IFREG | 0o644,
            b"\x7fELFlibadb_crypto",
            4,
        )
        adb_libraries = [
            make_entry(
                f"system/lib/{name}",
                stat.S_IFREG | 0o644,
                b"\x7fELF" + name.encode("ascii"),
                inode,
            )
            for inode, name in enumerate(
                (
                    "libadbconnection_server.so",
                    "libadbd_auth.so",
                    "libadbd_fs.so",
                    "libadb_protos.so",
                ),
                start=5,
            )
        ]
        perfetto = make_entry(
            "system/lib/libperfetto_c.so",
            stat.S_IFREG | 0o644,
            b"\x7fELFlibperfetto_c",
            9,
        )
        entries = [recovery, servicemanager, adbd, adb_crypto, *adb_libraries, perfetto]

        def needed(data):
            if data == adbd.data:
                return ["libadbconnection_server.so", "libadbd_auth.so", "libadbd_fs.so"]
            if data == adb_crypto.data:
                return ["libadb_protos.so"]
            if data == servicemanager.data:
                return ["libperfetto_c.so"]
            return []

        with patch.object(slim_recovery.rr, "elf_needed", side_effect=needed):
            removed = slim_recovery.plan_removals(entries, set())

        self.assertIn("system/lib/libadbd.so", slim_recovery.OPTIONAL_LIBS)
        self.assertIn("system/lib/libadb_crypto.so", slim_recovery.OPTIONAL_LIBS)
        for entry in (adbd, adb_crypto, *adb_libraries):
            self.assertEqual(removed.get(entry.norm), "unused-lib")
        self.assertNotIn(perfetto.norm, removed)

    def test_reachable_adb_library_is_kept_through_dependency_chain(self):
        recovery = make_entry(
            "system/bin/recovery", stat.S_IFREG | 0o755, b"\x7fELFrecovery", 1
        )
        platform_lib = make_entry(
            "system/lib/libplatform.so",
            stat.S_IFREG | 0o644,
            b"\x7fELFplatform",
            2,
        )
        adb_library = make_entry(
            "system/lib/libadbconnection_server.so",
            stat.S_IFREG | 0o644,
            b"\x7fELFadbconnection",
            3,
        )

        def needed(data):
            if data == recovery.data:
                return ["libplatform.so"]
            if data == platform_lib.data:
                return ["libadbconnection_server.so"]
            return []

        with patch.object(slim_recovery.rr, "elf_needed", side_effect=needed):
            removed = slim_recovery.plan_removals(
                [recovery, platform_lib, adb_library], set()
            )

        self.assertNotIn(adb_library.norm, removed)

    def test_non_adb_optional_library_keeps_existing_direct_reference_policy(self):
        root = make_entry(
            "system/bin/recovery", stat.S_IFREG | 0o755, b"\x7fELFrecovery", 1
        )
        orphan = make_entry(
            "system/lib/liborphan.so", stat.S_IFREG | 0o644, b"\x7fELForphan", 2
        )
        library = make_entry(
            "system/lib/libicuuc.so", stat.S_IFREG | 0o644, b"\x7fELFicuuc", 3
        )

        def needed(data):
            if data == orphan.data:
                return ["libicuuc.so"]
            return []

        with patch.object(slim_recovery.rr, "elf_needed", side_effect=needed):
            removed = slim_recovery.plan_removals([root, orphan, library], set())

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