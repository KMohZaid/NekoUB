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


if __name__ == "__main__":
    unittest.main()
