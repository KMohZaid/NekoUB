import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from plugins import sticker


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
            index.snapshot_is_usable.side_effect = [False, True]
            index.packs_indexed.return_value = True
            index.packs_match_counts.return_value = True
            index.packs_usable.return_value = True
            with patch.object(sticker.main, "_sticker_index_refresh_task", None, create=True):
                result = await sticker._ensure_sticker_index(object(), packs, media)

        self.assertTrue(result)
        refresh.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
