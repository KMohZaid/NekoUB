import tempfile
import unittest
from pathlib import Path

from plugins import sticker_index


class StickerIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "index.sqlite3"
        self.pack = {
            "id": 123,
            "short_name": "owner_pack_vol1",
            "is_emoji": False,
            "is_archived": False,
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def record(self, document_id="1", fingerprint="0000000000000000", **kwargs):
        return {
            "document_id": document_id,
            "file_unique_id": kwargs.get("file_unique_id", "unique-1"),
            "mime_type": kwargs.get("mime_type", "image/webp"),
            "size": kwargs.get("size", 100),
            "is_video": kwargs.get("is_video", False),
            "fingerprint_kind": kwargs.get("fingerprint_kind", "phash64"),
            "fingerprint": fingerprint,
            "verified": kwargs.get("verified", True),
        }

    def test_snapshot_is_user_scoped_and_match_is_pack_scoped(self):
        sticker_index.replace_snapshot(
            [(self.pack, [self.record()])], user_id=42, path=self.path
        )
        self.assertTrue(sticker_index.snapshot_is_usable(42, path=self.path))
        self.assertFalse(sticker_index.snapshot_is_usable(43, path=self.path))
        self.assertTrue(sticker_index.packs_indexed({"owner_pack_vol1"}, path=self.path))
        self.assertFalse(sticker_index.packs_indexed({"missing_pack"}, path=self.path))
        self.assertIsNotNone(
            sticker_index.find_match(
                "unique-1", None, False, "image/webp", 100,
                {"owner_pack_vol1"}, path=self.path,
            )
        )
        self.assertIsNone(
            sticker_index.find_match(
                "unique-1", None, False, "image/webp", 100,
                {"other_pack"}, path=self.path,
            )
        )

    def test_perceptual_hash_threshold_is_verified(self):
        sticker_index.replace_snapshot(
            [(self.pack, [self.record()])], user_id=42, path=self.path
        )
        within_threshold = (1 << 4) - 1
        outside_threshold = (1 << 5) - 1
        self.assertIsNotNone(
            sticker_index.find_match(
                None, within_threshold, False, "image/webp", 100,
                {"owner_pack_vol1"}, path=self.path,
            )
        )
        self.assertIsNone(
            sticker_index.find_match(
                None, outside_threshold, False, "image/webp", 100,
                {"owner_pack_vol1"}, path=self.path,
            )
        )

    def test_video_uses_exact_sha256_and_metadata(self):
        video = self.record(
            fingerprint="abc123", is_video=True, fingerprint_kind="sha256",
            mime_type="video/webm",
        )
        sticker_index.replace_snapshot(
            [(self.pack, [video])], user_id=42, path=self.path
        )
        self.assertIsNotNone(
            sticker_index.find_match(
                None, "abc123", True, "video/webm", 100,
                {"owner_pack_vol1"}, path=self.path,
            )
        )
        self.assertIsNone(
            sticker_index.find_match(
                None, "abc124", True, "video/webm", 100,
                {"owner_pack_vol1"}, path=self.path,
            )
        )

    def test_exact_match_requires_compatible_media_metadata(self):
        sticker_index.replace_snapshot(
            [(self.pack, [self.record()])], user_id=42, path=self.path
        )
        self.assertIsNone(
            sticker_index.find_match(
                "unique-1", None, False, "image/png", 100,
                {"owner_pack_vol1"}, path=self.path,
            )
        )
        self.assertIsNone(
            sticker_index.find_match(
                "unique-1", None, False, "image/webp", 101,
                {"owner_pack_vol1"}, path=self.path,
            )
        )

    def test_pack_match_counts_detects_stale_inventory(self):
        sticker_index.replace_snapshot(
            [(self.pack, [self.record()])], user_id=42, path=self.path
        )
        self.assertTrue(
            sticker_index.packs_match_counts(
                {"owner_pack_vol1": 1}, path=self.path
            )
        )
        self.assertFalse(
            sticker_index.packs_match_counts(
                {"owner_pack_vol1": 2}, path=self.path
            )
        )
        self.assertFalse(
            sticker_index.packs_match_counts(
                {"missing_pack": 0}, path=self.path
            )
        )

    def test_empty_snapshot_pack_is_indexed(self):
        empty_pack = {**self.pack, "id": 456, "short_name": "empty_pack"}
        sticker_index.replace_snapshot(
            [(self.pack, [self.record()]), (empty_pack, [])],
            user_id=42,
            path=self.path,
        )
        self.assertTrue(sticker_index.packs_indexed({"empty_pack"}, path=self.path))
        self.assertTrue(sticker_index.packs_usable({"empty_pack"}, path=self.path))

    def test_incomplete_stickers_are_detected(self):
        record = self.record(verified=False, fingerprint=None, fingerprint_kind=None)
        sticker_index.replace_snapshot(
            [(self.pack, [record])], user_id=42, path=self.path
        )
        self.assertTrue(sticker_index.has_unverified({"owner_pack_vol1"}, "image/webp", 100, False, path=self.path))

    def test_pack_upsert_replaces_entries_without_deleting_pack(self):
        sticker_index.replace_snapshot(
            [(self.pack, [self.record()])], user_id=42, path=self.path
        )
        new_record = self.record(document_id="2", file_unique_id="unique-2")
        sticker_index.upsert_pack_records(
            self.pack, [self.record(), new_record], path=self.path
        )
        self.assertEqual(
            set(sticker_index.get_pack_records("owner_pack_vol1", path=self.path)),
            {"1", "2"},
        )
        self.assertTrue(sticker_index.packs_usable({"owner_pack_vol1"}, path=self.path))

    def test_animated_tgs_uses_exact_sha256_with_is_video_false(self):
        tgs = self.record(
            document_id="9",
            file_unique_id="unique-tgs",
            mime_type="application/x-tgsticker",
            size=4096,
            is_video=False,
            fingerprint_kind="sha256",
            fingerprint="deadbeef" * 8,
            verified=True,
        )
        sticker_index.replace_snapshot(
            [(self.pack, [tgs])], user_id=42, path=self.path
        )
        self.assertIsNotNone(
            sticker_index.find_match(
                None, "deadbeef" * 8, False, "application/x-tgsticker", 4096,
                {"owner_pack_vol1"}, path=self.path,
            )
        )
        self.assertIsNone(
            sticker_index.find_match(
                None, "00000000" * 8, False, "application/x-tgsticker", 4096,
                {"owner_pack_vol1"}, path=self.path,
            )
        )

    def test_upsert_packs_snapshot_updates_target_without_wiping_other_packs(self):
        other_pack = {**self.pack, "id": 999, "short_name": "other_pack"}
        sticker_index.replace_snapshot(
            [(other_pack, [self.record(document_id="10", file_unique_id="u10")])],
            user_id=42,
            path=self.path,
        )
        sticker_index.upsert_packs_snapshot(
            [(self.pack, [self.record(document_id="1", file_unique_id="u1")])],
            user_id=42,
            path=self.path,
        )
        self.assertTrue(sticker_index.packs_indexed({"owner_pack_vol1", "other_pack"}, path=self.path))
        self.assertTrue(sticker_index.snapshot_is_usable(42, max_age=None, path=self.path))

        # Switching to a different user clears the previous user's packs.
        sticker_index.upsert_packs_snapshot(
            [(self.pack, [self.record(document_id="2", file_unique_id="u2")])],
            user_id=99,
            path=self.path,
        )
        self.assertTrue(sticker_index.packs_indexed({"owner_pack_vol1"}, path=self.path))
        self.assertFalse(sticker_index.packs_indexed({"other_pack"}, path=self.path))
        self.assertTrue(sticker_index.snapshot_is_usable(99, max_age=None, path=self.path))

    def test_packs_needing_refresh_identifies_only_stale_or_mismatched_packs(self):
        other_pack = {**self.pack, "id": 999, "short_name": "other_pack"}
        unverified_pack = {**self.pack, "id": 777, "short_name": "unverified_pack"}
        sticker_index.replace_snapshot(
            [
                (self.pack, [self.record(document_id="1")]),
                (other_pack, [self.record(document_id="2")]),
                (unverified_pack, [self.record(document_id="3", verified=False, fingerprint=None, fingerprint_kind=None)]),
            ],
            user_id=42,
            path=self.path,
        )
        needing = sticker_index.packs_needing_refresh(
            {
                "owner_pack_vol1": 1,
                "other_pack": 2,
                "unverified_pack": 1,
                "missing_pack": 5,
            },
            user_id=42,
            max_age=3600,
            require_verified=True,
            path=self.path,
        )
        self.assertEqual(needing, {"other_pack", "unverified_pack", "missing_pack"})

    def test_large_pack_set_over_sqlite_param_limit_works_and_reads_fast(self):
        # Exceed SQLite's classic 999 host-parameter limit to prove chunked queries work.
        total_packs = 1200
        batch = [
            (
                {"id": i, "short_name": f"pack_{i}", "is_emoji": False, "is_archived": False},
                [self.record(document_id=str(i), file_unique_id=f"u-{i}")],
            )
            for i in range(total_packs)
        ]
        sticker_index.replace_snapshot(batch, user_id=42, path=self.path)
        all_names = {f"pack_{i}" for i in range(total_packs)}
        all_counts = {f"pack_{i}": 1 for i in range(total_packs)}

        self.assertTrue(sticker_index.packs_indexed(all_names, path=self.path))
        self.assertTrue(sticker_index.packs_match_counts(all_counts, path=self.path))
        self.assertTrue(sticker_index.packs_usable(all_names, path=self.path))
        self.assertEqual(
            sticker_index.packs_needing_refresh(all_counts, user_id=42, path=self.path),
            set(),
        )
        ready, failed = sticker_index.check_packs_ready(
            {"pack_0": 1, "pack_1199": 1}, user_id=42, path=self.path
        )
        self.assertTrue(ready)
        self.assertEqual(failed, [])
        match = sticker_index.find_match(
            "u-1199", None, False, "image/webp", 100, all_names, path=self.path
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["short_name"], "pack_1199")

    def test_readonly_checks_do_not_write_to_database_file(self):
        sticker_index.replace_snapshot(
            [(self.pack, [self.record()])], user_id=42, path=self.path
        )
        # Warm initialization, then verify repeated read checks do not issue writes.
        ready, failed = sticker_index.check_packs_ready(
            {"owner_pack_vol1": 1}, user_id=42, path=self.path
        )
        self.assertTrue(ready)
        self.assertEqual(failed, [])
        before_data_version = sticker_index._connect(self.path, read_only=True).execute(
            "PRAGMA data_version"
        ).fetchone()[0]
        for _ in range(5):
            self.assertTrue(
                sticker_index.check_packs_ready(
                    {"owner_pack_vol1": 1}, user_id=42, path=self.path
                )[0]
            )
            self.assertIsNotNone(
                sticker_index.find_match(
                    "unique-1", None, False, "image/webp", 100,
                    {"owner_pack_vol1"}, path=self.path,
                )
            )
        after_data_version = sticker_index._connect(self.path, read_only=True).execute(
            "PRAGMA data_version"
        ).fetchone()[0]
        self.assertEqual(before_data_version, after_data_version)


if __name__ == "__main__":
    unittest.main()
