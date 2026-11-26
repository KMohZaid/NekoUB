import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pyrogram.raw import types as raw_types

import main
from plugins import sticker


class StickerTimingTests(unittest.TestCase):
    def test_sticker_logger_exposes_debug_timings_under_info_root_logging(self):
        self.assertEqual(sticker.logger.level, 10)

    def test_timing_helper_emits_debug_duration_and_context(self):
        with (
            patch.object(sticker.time, "perf_counter", return_value=10.25),
            patch.object(sticker.logger, "debug") as debug,
        ):
            sticker._log_timing("pack-cache", 9.0, pack_count=3)

        debug.assert_called_once()
        self.assertIn("%s", debug.call_args.args[0])
        self.assertEqual(debug.call_args.args[1], "pack-cache")
        self.assertEqual(debug.call_args.args[2], 1.25)
        self.assertIn("pack_count=3", debug.call_args.args[3])


class StickerWorkerOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_add_sync_runs_after_acknowledgement_and_optional_send(self):
        events = []
        queue = asyncio.Queue()
        source = SimpleNamespace(
            file_id="source-file", file_unique_id="unique", mime_type="image/webp",
            file_size=10, is_video=False, is_animated=False, emoji=None,
        )
        media = SimpleNamespace(sticker=source, photo=None, animation=None, video=None, document=None, id=1)
        message = SimpleNamespace(id=2, chat=SimpleNamespace(id=3))
        queue.put_nowait((media, None, message, {"short_name": "pack"}, True, True))

        async def add_to_target(*args, **kwargs):
            events.append("add")
            kwargs["response_holder"]["sticker_set"] = SimpleNamespace(
                stickers=[SimpleNamespace(file_id="added-file", file_unique_id="added")]
            )
            return "pack", "added-file", True

        async def feedback(*args, **kwargs):
            events.append("feedback")

        async def send_sticker(*args, **kwargs):
            events.append("show")
            return SimpleNamespace(id=4)

        async def sync_cache(*args, **kwargs):
            events.append("cache")

        async def update_index(*args, **kwargs):
            events.append("index")

        with (
            patch.object(sticker, "_worker_running", False),
            patch.object(sticker, "_get_queue", AsyncMock(return_value=queue)),
            patch.object(sticker, "message_media", return_value=("sticker", source)),
            patch.object(sticker, "add_sticker_to_target", add_to_target),
            patch.object(sticker, "send_result_feedback", feedback),
            patch.object(sticker, "send_sticker_set_debug_json", AsyncMock()),
            patch.object(sticker, "cache_owned_packs", sync_cache),
            patch.object(sticker, "_update_index_after_add", update_index),
            patch.object(sticker.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            client = SimpleNamespace(send_sticker=send_sticker, get_sticker_set=AsyncMock())
            with self.assertRaises(asyncio.CancelledError):
                await sticker._queue_worker(client)

        self.assertEqual(events, ["add", "feedback", "show", "cache", "index"])


class OwnedPackRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_matching_pack_metadata_avoids_per_pack_refetch(self):
        client = SimpleNamespace(invoke=AsyncMock())
        covered = SimpleNamespace(
            set=SimpleNamespace(
                id=1, access_hash=2, title="Pack", short_name="pack", count=2,
                emojis=False, archived=False, creator=True,
            ),
            documents=[], cover=None, covers=[],
        )
        cached = {
            "id": 1, "short_name": "pack", "count": 2, "creator": True,
            "is_archived": False, "is_emoji": False, "type": "normal",
            "limit": 120, "icon": "📄",
        }

        result = await sticker._parse_owned_pack(client, covered, cached_pack=cached)

        self.assertEqual(result["type"], cached["type"])
        self.assertEqual(result["count"], cached["count"])
        client.invoke.assert_not_awaited()

    async def test_duplicate_index_check_awaits_initial_refresh_when_coverage_is_missing(self):
        packs = [{"short_name": "pack", "count": 2}]
        media = SimpleNamespace(mime_type="image/webp", file_size=10, is_video=False)
        refreshed = asyncio.get_running_loop().create_future()
        refreshed.set_result(True)
        with (
            patch.object(sticker, "me_user", SimpleNamespace(id=42)),
            patch.object(sticker, "sticker_index") as index,
            patch.object(sticker, "refresh_sticker_index", new_callable=AsyncMock, return_value=True) as refresh,
        ):
            index.check_packs_ready.side_effect = [(False, ["snapshot"]), (True, [])]
            with patch.object(sticker.main, "_sticker_index_refresh_task", None, create=True):
                result = await sticker._ensure_sticker_index(object(), packs, media)

        self.assertTrue(result)
        refresh.assert_awaited_once()
        self.assertEqual(refresh.await_args.kwargs.get("packs"), packs)

    async def test_parse_owned_pack_skips_get_sticker_set_for_emoji_packs(self):
        client = SimpleNamespace(invoke=AsyncMock())
        covered = SimpleNamespace(
            set=SimpleNamespace(
                id=10, access_hash=20, title="Emoji Pack", short_name="emoji_pack",
                count=200, emojis=True, archived=False, creator=True,
            ),
            documents=[], cover=None, covers=[],
        )

        result = await sticker._parse_owned_pack(client, covered, cached_pack=None)

        self.assertEqual(result["type"], "emoji")
        client.invoke.assert_not_awaited()

    async def test_fetch_indexed_pack_reads_raw_documents_without_sticker_parse(self):
        doc = raw_types.Document(
            id=123456789,
            access_hash=987654321,
            file_reference=b"ref",
            date=0,
            mime_type="image/webp",
            size=4321,
            dc_id=5,
            attributes=[],
        )
        pack_set = raw_types.messages.StickerSet(
            set=raw_types.StickerSet(
                id=1, access_hash=2, title="Pack", short_name="pack", count=1, hash=0
            ),
            packs=[],
            keywords=[],
            documents=[doc],
        )
        pack = {"id": 1, "short_name": "pack", "is_emoji": False, "is_archived": False}
        prior = {
            "123456789": {
                "document_id": "123456789",
                "file_unique_id": "cached-uid",
                "mime_type": "image/webp",
                "size": 4321,
                "is_video": False,
                "fingerprint_kind": "phash64",
                "fingerprint": "0000000000000000",
                "verified": 1,
            }
        }
        client = SimpleNamespace(invoke=AsyncMock(), download_media=AsyncMock())

        with (
            patch.object(sticker.pyrogram_types.Sticker, "_parse", AsyncMock(side_effect=AssertionError("must not call Sticker._parse"))),
            patch.object(sticker.sticker_index, "get_pack_records", return_value=prior),
        ):
            _, records = await sticker._fetch_indexed_pack(client, pack, pack_set)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["document_id"], "123456789")
        self.assertTrue(records[0]["file_unique_id"])
        self.assertTrue(records[0]["verified"])
        client.invoke.assert_not_awaited()
        client.download_media.assert_not_awaited()

    def test_animated_tgs_uses_sha256_fingerprint_without_pil(self):
        payload = b"\x1f\x8b\x08\x00fake-tgs-lottie-gzip-bytes"
        with tempfile.TemporaryDirectory() as folder:
            tgs_path = Path(folder) / "anim.tgs"
            tgs_path.write_bytes(payload)
            doc = SimpleNamespace(
                mime_type="application/x-tgsticker",
                is_video=False,
                is_animated=True,
                attributes=[],
            )
            use_exact = sticker._uses_exact_fingerprint(doc)
            self.assertTrue(use_exact)
            digest = sticker._media_fingerprint(str(tgs_path), use_exact)
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())

    async def test_add_sticker_to_pack_skips_pre_fetch_when_allow_duplicates_true(self):
        client = SimpleNamespace(
            invoke=AsyncMock(),
            add_sticker_to_set=AsyncMock(return_value=SimpleNamespace(stickers=[])),
        )
        source = SimpleNamespace(
            file_id="fid", file_unique_id="uid", mime_type="image/webp",
            is_video=False, is_animated=False, emoji="🌟",
        )

        _, added, previous = await sticker.add_sticker_to_pack(
            client, "pack", source, "🌟", allow_duplicates=True
        )

        self.assertTrue(added)
        self.assertIsNone(previous)
        client.invoke.assert_not_awaited()
        client.add_sticker_to_set.assert_awaited_once()

    async def test_duplicate_with_show_sticker_sends_matching_sticker(self):
        queue = asyncio.Queue()
        source = SimpleNamespace(
            file_id="source-file", file_unique_id="unique-1", mime_type="image/webp",
            file_size=10, is_video=False, is_animated=False, emoji=None,
        )
        media = SimpleNamespace(sticker=source, photo=None, animation=None, video=None, document=None, id=1)
        message = SimpleNamespace(id=2, chat=SimpleNamespace(id=3))
        queue.put_nowait((media, None, message, None, True, False))

        matched_item = SimpleNamespace(
            file_id="matched-file-id",
            file_unique_id="unique-1",
            raw=SimpleNamespace(id=555),
        )
        sticker_set = SimpleNamespace(stickers=[matched_item])
        send_sticker = AsyncMock(return_value=SimpleNamespace(id=99))

        with (
            patch.object(sticker, "_worker_running", False),
            patch.object(sticker, "_get_queue", AsyncMock(return_value=queue)),
            patch.object(sticker, "message_media", return_value=("sticker", source)),
            patch.object(
                sticker, "_duplicate_check_targets",
                AsyncMock(return_value=[{"short_name": "pack", "count": 1, "is_emoji": False}]),
            ),
            patch.object(sticker, "_ensure_sticker_index", AsyncMock(return_value=True)),
            patch.object(
                sticker.sticker_index, "find_match",
                return_value={"short_name": "pack", "document_id": "555", "file_unique_id": "unique-1"},
            ),
            patch.object(sticker, "_send_reaction", AsyncMock(return_value=True)),
            patch.object(sticker, "send_sticker_set_debug_json", AsyncMock()),
            patch.object(sticker.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            client = SimpleNamespace(
                send_sticker=send_sticker,
                get_sticker_set=AsyncMock(return_value=sticker_set),
            )
            with self.assertRaises(asyncio.CancelledError):
                await sticker._queue_worker(client)

        send_sticker.assert_awaited_once_with(3, "matched-file-id", reply_to_message_id=2)

    async def test_targeted_refresh_updates_only_requested_pack_and_reconciles_count(self):
        doc = raw_types.Document(
            id=555001,
            access_hash=111,
            file_reference=b"ref",
            date=0,
            mime_type="image/webp",
            size=100,
            dc_id=5,
            attributes=[],
        )
        live_set = raw_types.messages.StickerSet(
            set=raw_types.StickerSet(
                id=7, access_hash=8, title="Target", short_name="target_pack", count=1, hash=0
            ),
            packs=[],
            keywords=[],
            documents=[doc],
        )
        # Deliberately pass a stale cached count (count=99) while Telegram returns 1 sticker.
        pack = {"id": 7, "short_name": "target_pack", "count": 99, "is_emoji": False, "is_archived": False}
        client = SimpleNamespace(invoke=AsyncMock(return_value=live_set))

        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "index.sqlite3"
            with (
                patch.object(sticker, "me_user", SimpleNamespace(id=42)),
                patch.object(sticker.sticker_index, "INDEX_PATH", db_path),
                patch.object(sticker, "fetch_all_owned_packs", AsyncMock(side_effect=AssertionError("must not fetch full inventory"))),
                patch.object(sticker, "_fingerprint_document", AsyncMock(return_value=12345)),
            ):
                # Patch helper defaults to use temp db_path
                orig_get = sticker.sticker_index.get_pack_records
                orig_needing = sticker.sticker_index.packs_needing_refresh
                orig_upsert = sticker.sticker_index.upsert_packs_snapshot
                orig_ready = sticker.sticker_index.check_packs_ready
                with (
                    patch.object(sticker.sticker_index, "get_pack_records", lambda s: orig_get(s, path=db_path)),
                    patch.object(sticker.sticker_index, "packs_needing_refresh", lambda *a, **kw: orig_needing(*a, path=db_path, **kw)),
                    patch.object(sticker.sticker_index, "upsert_packs_snapshot", lambda *a, **kw: orig_upsert(*a, path=db_path, **kw)),
                    patch.object(sticker.sticker_index, "check_packs_ready", lambda *a, **kw: orig_ready(*a, path=db_path, **kw)),
                ):
                    ok = await sticker._ensure_sticker_index(client, [pack], SimpleNamespace())

        self.assertTrue(ok)
        self.assertEqual(pack["count"], 1)
        client.invoke.assert_awaited_once()

    def test_client_kwargs_disable_unneeded_topic_and_story_fetches(self):
        kwargs = main._build_client_kwargs("parse_mode_sentinel")
        self.assertFalse(kwargs["fetch_topics"])
        self.assertFalse(kwargs["fetch_stories"])

    async def test_post_add_cache_update_is_incremental_with_ten_thousand_packs(self):
        ten_thousand = [
            {
                "id": i,
                "access_hash": i * 10,
                "title": f"Pack {i}",
                "short_name": f"pack_{i}",
                "count": 50,
                "is_emoji": i % 2 == 0,
                "is_archived": False,
                "creator": True,
                "type": "emoji" if i % 2 == 0 else "normal",
                "limit": 200 if i % 2 == 0 else 120,
                "icon": "🪎" if i % 2 == 0 else "📄",
            }
            for i in range(10_000)
        ]
        updated_raw_set = raw_types.messages.StickerSet(
            set=raw_types.StickerSet(
                id=42, access_hash=420, title="Pack 42", short_name="pack_42",
                count=51, hash=0, creator=True,
            ),
            packs=[],
            keywords=[],
            documents=[
                raw_types.Document(
                    id=999, access_hash=1, file_reference=b"r", date=0,
                    mime_type="image/webp", size=10, dc_id=5, attributes=[],
                )
            ],
        )
        client = SimpleNamespace(invoke=AsyncMock(side_effect=AssertionError("must not call GetMyStickers")))
        with tempfile.TemporaryDirectory() as folder:
            cache_file = Path(folder) / ".sticker_cache.json"
            with (
                patch.object(sticker, "CACHE_PATH", cache_file),
                patch.object(sticker, "me_user", SimpleNamespace(id=42, first_name="Test", username="tester")),
                patch.object(sticker, "fetch_all_owned_packs", AsyncMock(side_effect=AssertionError("must not refetch all packs"))),
            ):
                sticker.save_cache({
                    "user_id": 42,
                    "owned_packs": ten_thousand,
                    "owned_packs_updated_at": 1.0,
                })
                packs = await sticker.cache_owned_packs(
                    client, updated_pack="pack_42", sticker_set=updated_raw_set
                )

        self.assertEqual(len(packs), 10_000)
        updated = next(p for p in packs if p["short_name"] == "pack_42")
        self.assertEqual(updated["count"], 51)

    async def test_duplicate_check_targets_scales_to_ten_thousand_packs_fast(self):
        import time as _time

        user = SimpleNamespace(id=42, first_name="Test", username="tester")
        _, auto_v1 = sticker.build_default_pack_names(user, 1)
        _, auto_v2 = sticker.build_default_pack_names(user, 2)
        ten_thousand = [
            {"id": i, "short_name": f"other_pack_{i}", "count": 20, "is_emoji": False, "is_archived": False}
            for i in range(10_000)
        ]
        ten_thousand[100]["short_name"] = auto_v1
        ten_thousand[5000]["short_name"] = auto_v2

        with (
            patch.object(sticker, "me_user", user),
            patch.object(sticker, "cached_owned_packs", AsyncMock(return_value=ten_thousand)),
        ):
            started = _time.perf_counter()
            targets = await sticker._duplicate_check_targets(object(), None)
            elapsed = _time.perf_counter() - started

        self.assertEqual({p["short_name"] for p in targets}, {auto_v1, auto_v2})
        self.assertLess(elapsed, 0.2)

    async def test_resolve_themed_target_uses_cached_packs_without_fetching_all(self):
        user = SimpleNamespace(id=42, first_name="Test", username="tester")
        title, short_name = sticker.build_themed_pack_names(user, "cats", 1, "normal")
        cached = [
            {
                "id": 7, "short_name": short_name, "title": title, "count": 5,
                "is_emoji": False, "is_archived": False, "creator": True,
                "type": "normal", "limit": 120, "icon": "📄",
            }
        ]
        with (
            patch.object(sticker, "me_user", user),
            patch.object(sticker, "cached_owned_packs", AsyncMock(return_value=cached)),
            patch.object(sticker, "fetch_all_owned_packs", AsyncMock(side_effect=AssertionError("must not fetch all packs"))),
        ):
            result = await sticker.resolve_themed_target(object(), "cats", "normal")

        self.assertFalse(result["create"])
        self.assertEqual(result["short_name"], short_name)

    def test_separable_dct_phash_matches_reference_2d_dct(self):
        import math
        from PIL import Image

        with tempfile.TemporaryDirectory() as folder:
            img_path = Path(folder) / "sample.png"
            img = Image.new("RGB", (64, 64))
            for y in range(64):
                for x in range(64):
                    img.putpixel((x, y), ((x * 7 + y * 3) % 256, (x * 5) % 256, (y * 11) % 256))
            img.save(img_path)

            # Reference formula
            with Image.open(img_path) as opened:
                gray = opened.convert("L").resize((32, 32))
                pixels = list(
                    gray.get_flattened_data()
                    if hasattr(gray, "get_flattened_data")
                    else gray.getdata()
                )
            ref_coeffs = [
                sum(
                    pixels[y * 32 + x]
                    * math.cos((2 * x + 1) * u * math.pi / 64)
                    * math.cos((2 * y + 1) * v * math.pi / 64)
                    for y in range(32)
                    for x in range(32)
                )
                for v in range(8)
                for u in range(8)
            ]
            ref_median = sorted(ref_coeffs[1:])[len(ref_coeffs[1:]) // 2]
            expected = sum(1 << i for i, val in enumerate(ref_coeffs) if val > ref_median)

            self.assertTrue(hasattr(sticker, "_DCT_COS"))
            actual = sticker._media_fingerprint(str(img_path), False)
            self.assertEqual(actual, expected)

    async def test_scheduler_refresh_targets_only_active_and_indexed_packs(self):
        user = SimpleNamespace(id=42, first_name="Test", username="tester")
        _, auto_v1 = sticker.build_default_pack_names(user, 1)
        ten_thousand = [
            {"id": i, "short_name": f"other_{i}", "count": 50, "is_emoji": False, "is_archived": False, "creator": True}
            for i in range(1000)
        ]
        ten_thousand[0]["short_name"] = auto_v1
        ten_thousand[5]["short_name"] = "previously_used_pack"

        with (
            patch.object(sticker, "me_user", user),
            patch.object(sticker, "cached_owned_packs", AsyncMock(return_value=ten_thousand)),
            patch.object(sticker.sticker_index, "get_indexed_pack_short_names", return_value={"previously_used_pack"}),
            patch.object(sticker, "refresh_sticker_index", AsyncMock(return_value=True)) as refresh,
        ):
            await sticker._refresh_active_sticker_index(object())

        refresh.assert_awaited_once()
        refreshed_packs = refresh.await_args.kwargs.get("packs")
        self.assertEqual(
            {p["short_name"] for p in refreshed_packs},
            {auto_v1, "previously_used_pack"},
        )


if __name__ == "__main__":
    unittest.main()
