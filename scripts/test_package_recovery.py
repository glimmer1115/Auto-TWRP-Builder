import gzip
import hashlib
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from package_recovery import analyze_ramdisk, compress_cpio, rewrite_cpio
from slim_recovery import find_referenced_fonts, should_remove


def cpio_record(name, mode, content, inode, link_count=1):
    encoded_name = name.encode("utf-8") + b"\0"
    fields = (
        inode,
        mode,
        0,
        0,
        link_count,
        0,
        len(content),
        0,
        0,
        0,
        0,
        len(encoded_name),
        0,
    )
    record = b"070701" + b"".join("{:08x}".format(value).encode("ascii") for value in fields)
    record += encoded_name
    record += b"\0" * (-len(record) % 4)
    record += content
    record += b"\0" * (-len(record) % 4)
    return record


def cpio_archive(entries):
    archive = b"".join(
        cpio_record(name, mode, content, inode, link_count)
        for name, mode, content, inode, link_count in entries
    )
    archive += cpio_record("TRAILER!!!", 0, b"", len(entries) + 1)
    return archive + b"\0" * (-len(archive) % 512)


class RamdiskAnalysisTests(unittest.TestCase):
    def test_inventory_and_recompression_simulations(self):
        large_payload = b"".join(
            hashlib.sha256(index.to_bytes(4, "little")).digest()
            for index in range(1024)
        )
        entries = [
            ("./system", stat.S_IFDIR | 0o755, b"", 1, 2),
            ("./system/bin", stat.S_IFDIR | 0o755, b"", 2, 2),
            ("./system/bin/large", stat.S_IFREG | 0o644, large_payload, 3, 1),
            ("./system/bin/config", stat.S_IFREG | 0o644, b"config", 4, 1),
            ("./ramdisk-files.txt", stat.S_IFREG | 0o644, b"metadata", 5, 1),
        ]
        cpio = cpio_archive(entries)
        ramdisk = gzip.compress(cpio, compresslevel=9, mtime=0)
        packaged_cpio, _ = rewrite_cpio(cpio)
        packaged_size = len(compress_cpio(packaged_cpio))

        report = analyze_ramdisk(ramdisk, packaged_size, packaged_size - 1)

        self.assertEqual(report["packaged_ramdisk_bytes"], packaged_size)
        self.assertEqual(report["headroom_bytes"], -1)
        self.assertEqual(report["excess_bytes"], 1)
        self.assertEqual(report["removed_build_metadata"], ["ramdisk-files.txt"])
        paths = {entry["path"] for entry in report["entries"]}
        self.assertIn("system/bin/large", paths)
        self.assertNotIn("TRAILER!!!", paths)

        directory_sizes = {item["path"]: item["payload_bytes"] for item in report["directories"]}
        self.assertEqual(directory_sizes["system"], len(large_payload) + len(b"config"))
        self.assertEqual(directory_sizes["system/bin"], len(large_payload) + len(b"config"))

        simulation = report["largest_file_single_removals"][0]
        self.assertEqual(simulation["path"], "system/bin/large")
        self.assertGreater(simulation["reduction_bytes"], 0)
        self.assertIn("system/bin/large", report["largest_file_cumulative_removals"][0]["paths"])
        self.assertEqual(cpio, cpio_archive(entries))


class RecoverySlimmingTests(unittest.TestCase):
    def test_font_references_include_attributes_text_and_tail(self):
        xml = (
            b'<theme font="My.Font.ttf">Text.ttf'
            b'<item>Roboto-Regular.ttf</item>Tail.ttf</theme>'
        )
        cpio = cpio_archive([
            ("twres/themes/theme.xml", stat.S_IFREG | 0o644, xml, 1, 1),
        ])

        references = find_referenced_fonts(cpio, {"twres/themes/theme.xml"})

        self.assertEqual(
            references,
            {"my.font.ttf", "text.ttf", "roboto-regular.ttf", "tail.ttf"},
        )

    def test_missing_or_malformed_theme_xml_preserves_font_candidates(self):
        self.assertIsNone(find_referenced_fonts(cpio_archive([]), set()))
        malformed = cpio_archive([
            ("twres/themes/theme.xml", stat.S_IFREG | 0o644, b"<theme>", 1, 1),
        ])
        self.assertIsNone(
            find_referenced_fonts(malformed, {"twres/themes/theme.xml"})
        )

    def test_removal_requires_regular_unreferenced_font_and_listed_icu(self):
        regular = stat.S_IFREG | 0o644
        symlink = stat.S_IFLNK | 0o777

        self.assertTrue(
            should_remove("twres/fonts/Unused.ttf", regular, 1, False, set())
        )
        self.assertFalse(
            should_remove("twres/fonts/Unused.ttf", symlink, 1, False, set())
        )
        self.assertFalse(
            should_remove("twres/fonts/Used.ttf", regular, 1, False, {"used.ttf"})
        )
        self.assertTrue(
            should_remove("system/lib/libicuuc.so", regular, 1, True, set())
        )
        self.assertFalse(
            should_remove("system/lib/libicu-other.so", regular, 1, True, set())
        )
        self.assertFalse(
            should_remove("system/lib/libicuuc.so", symlink, 1, True, set())
        )


if __name__ == "__main__":
    unittest.main()