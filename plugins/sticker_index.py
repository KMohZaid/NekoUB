"""Persistent fingerprint index for sticker duplicate checks."""

import asyncio
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

INDEX_PATH = Path(__file__).resolve().parent.parent / ".sticker_index.sqlite3"
SCHEMA_VERSION = 1


@contextmanager
def _database(path: Path = INDEX_PATH):
    connection = _connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _connect(path: Path = INDEX_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS packs (
            pack_id TEXT PRIMARY KEY,
            short_name TEXT NOT NULL,
            is_emoji INTEGER NOT NULL,
            is_archived INTEGER NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS packs_short_name ON packs(short_name);
        CREATE TABLE IF NOT EXISTS stickers (
            pack_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            file_unique_id TEXT,
            mime_type TEXT,
            size INTEGER,
            is_video INTEGER NOT NULL,
            fingerprint_kind TEXT,
            fingerprint TEXT,
            verified INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (pack_id, document_id),
            FOREIGN KEY (pack_id) REFERENCES packs(pack_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS stickers_unique_id ON stickers(file_unique_id);
        CREATE INDEX IF NOT EXISTS stickers_media ON stickers(mime_type, size, is_video);
        CREATE INDEX IF NOT EXISTS stickers_fingerprint ON stickers(fingerprint_kind, fingerprint);
        """
    )
    connection.execute("PRAGMA foreign_keys=ON")
    with connection:
        version = connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        if version is not None and int(version["value"]) != SCHEMA_VERSION:
            connection.execute("DELETE FROM stickers")
            connection.execute("DELETE FROM packs")
            connection.execute("DELETE FROM metadata")
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
    return connection


def _as_text(value):
    return str(value) if value is not None else None


def _fingerprint_text(fingerprint):
    if fingerprint is None:
        return None
    return f"{fingerprint:016x}" if isinstance(fingerprint, int) else str(fingerprint)


def _fingerprint_value(kind, value):
    if value is None:
        return None
    if kind == "phash64":
        return int(value, 16)
    return value


def upsert_pack_records(pack: dict, records: list[dict], *, path: Path = INDEX_PATH) -> None:
    pack_id = _as_text(pack.get("id") or pack.get("short_name"))
    if not pack_id:
        raise ValueError("pack needs an id or short_name")
    with _database(path) as connection:
        connection.execute(
            """INSERT INTO packs
               (pack_id,short_name,is_emoji,is_archived,updated_at) VALUES(?,?,?,?,?)
               ON CONFLICT(pack_id) DO UPDATE SET short_name=excluded.short_name,
               is_emoji=excluded.is_emoji,is_archived=excluded.is_archived,
               updated_at=excluded.updated_at""",
            (pack_id, pack["short_name"], int(bool(pack.get("is_emoji"))),
             int(bool(pack.get("is_archived"))), time.time()),
        )
        connection.executemany(
            """INSERT OR REPLACE INTO stickers(pack_id,document_id,file_unique_id,mime_type,
               size,is_video,fingerprint_kind,fingerprint,verified) VALUES(?,?,?,?,?,?,?,?,?)""",
            [
                (pack_id, _as_text(item["document_id"]), item.get("file_unique_id"),
                 item.get("mime_type"), item.get("size"), int(bool(item.get("is_video"))),
                 item.get("fingerprint_kind"), item.get("fingerprint"),
                 int(bool(item.get("verified"))))
                for item in records
            ],
        )


def replace_pack(pack: dict, records: list[dict], *, path: Path = INDEX_PATH) -> None:
    """Atomically replace one pack's metadata and sticker records."""
    pack_id = _as_text(pack.get("id") or pack.get("short_name"))
    if not pack_id:
        raise ValueError("pack needs an id or short_name")
    now = time.time()
    with _database(path) as connection:
        connection.execute(
            """INSERT INTO packs
               (pack_id,short_name,is_emoji,is_archived,updated_at) VALUES(?,?,?,?,?)
               ON CONFLICT(pack_id) DO UPDATE SET short_name=excluded.short_name,
               is_emoji=excluded.is_emoji,is_archived=excluded.is_archived,
               updated_at=excluded.updated_at""",
            (pack_id, pack["short_name"], int(bool(pack.get("is_emoji"))),
             int(bool(pack.get("is_archived"))), now),
        )
        connection.execute("DELETE FROM stickers WHERE pack_id=?", (pack_id,))
        connection.executemany(
            """INSERT INTO stickers(pack_id,document_id,file_unique_id,mime_type,size,
               is_video,fingerprint_kind,fingerprint,verified) VALUES(?,?,?,?,?,?,?,?,?)""",
            [
                (pack_id, _as_text(item["document_id"]), item.get("file_unique_id"),
                 item.get("mime_type"), item.get("size"), int(bool(item.get("is_video"))),
                 item.get("fingerprint_kind"), item.get("fingerprint"),
                 int(bool(item.get("verified"))))
                for item in records
            ],
        )


def replace_snapshot(packs: list[tuple[dict, list[dict]]], user_id: int,
                     *, path: Path = INDEX_PATH) -> None:
    """Publish a complete index snapshot in one SQLite transaction."""
    with _database(path) as connection:
        connection.execute("DELETE FROM stickers")
        connection.execute("DELETE FROM packs")
        for pack, records in packs:
            pack_id = _as_text(pack.get("id") or pack.get("short_name"))
            if not pack_id:
                continue
            connection.execute(
                "INSERT INTO packs VALUES(?,?,?,?,?)",
                (pack_id, pack["short_name"], int(bool(pack.get("is_emoji"))),
                 int(bool(pack.get("is_archived"))), time.time()),
            )
            connection.executemany(
                """INSERT INTO stickers(pack_id,document_id,file_unique_id,mime_type,size,
                   is_video,fingerprint_kind,fingerprint,verified) VALUES(?,?,?,?,?,?,?,?,?)""",
                [
                    (pack_id, _as_text(item["document_id"]), item.get("file_unique_id"),
                     item.get("mime_type"), item.get("size"), int(bool(item.get("is_video"))),
                     item.get("fingerprint_kind"), item.get("fingerprint"),
                     int(bool(item.get("verified"))))
                    for item in records
                ],
            )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('user_id',?)",
            (str(user_id),),
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('refreshed_at',?)",
            (str(time.time()),),
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('complete','1')"
        )


def get_pack_records(short_name: str, *, path: Path = INDEX_PATH) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        with _database(path) as connection:
            rows = connection.execute(
                """SELECT s.* FROM stickers s JOIN packs p USING(pack_id)
                   WHERE p.short_name=?""",
                (short_name,),
            ).fetchall()
        return {row["document_id"]: dict(row) for row in rows}
    except (OSError, sqlite3.Error):
        return {}


def packs_usable(short_names: set[str], *, max_age: float = 7 * 86400,
                 path: Path = INDEX_PATH) -> bool:
    if not short_names or not path.exists():
        return True
    try:
        with _database(path) as connection:
            placeholders = ",".join("?" for _ in short_names)
            rows = connection.execute(
                f"""SELECT short_name,updated_at FROM packs
                    WHERE short_name IN ({placeholders})""",
                tuple(short_names),
            ).fetchall()
        return (
            len({row["short_name"] for row in rows}) == len(short_names)
            and all(time.time() - row["updated_at"] <= max_age for row in rows)
        )
    except (OSError, sqlite3.Error):
        return False


def has_unverified(short_names: set[str], mime_type: str | None, size: int | None,
                   is_video: bool, *, path: Path = INDEX_PATH) -> bool:
    if not short_names or not path.exists():
        return False
    try:
        with _database(path) as connection:
            placeholders = ",".join("?" for _ in short_names)
            row = connection.execute(
                f"""SELECT 1 FROM stickers s JOIN packs p USING(pack_id)
                    WHERE p.short_name IN ({placeholders}) AND s.verified=0
                    AND s.mime_type=? AND s.size=? AND s.is_video=? LIMIT 1""",
                (*tuple(short_names), mime_type, size, int(bool(is_video))),
            ).fetchone()
        return row is not None
    except (OSError, sqlite3.Error):
        return True


def snapshot_is_usable(user_id: int, *, max_age: float = 7 * 86400,
                       path: Path = INDEX_PATH) -> bool:
    if not path.exists():
        return False
    try:
        with _database(path) as connection:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        return (
            metadata.get("user_id") == str(user_id)
            and metadata.get("complete") == "1"
            and time.time() - float(metadata.get("refreshed_at", "0")) <= max_age
        )
    except (OSError, sqlite3.Error, ValueError):
        return False


def find_match(unique_id: str | None, fingerprint, is_video: bool, mime_type: str | None,
               size: int | None, candidate_short_names: set[str], *, path: Path = INDEX_PATH):
    """Return a matching cached record, filtered to the selected destination family."""
    if not candidate_short_names or not path.exists():
        return None
    try:
        with _database(path) as connection:
            placeholders = ",".join("?" for _ in candidate_short_names)
            args = tuple(candidate_short_names)
            if unique_id:
                row = connection.execute(
                    f"""SELECT p.short_name,s.* FROM stickers s JOIN packs p USING(pack_id)
                        WHERE p.short_name IN ({placeholders})
                        AND s.file_unique_id=? AND s.mime_type=? AND s.size=?
                        AND s.is_video=? ORDER BY p.updated_at DESC LIMIT 1""",
                    (*args, unique_id, mime_type, size, int(bool(is_video))),
                ).fetchone()
                if row is not None:
                    return dict(row)
            if fingerprint is None:
                return None
            kind = "sha256" if is_video else "phash64"
            if is_video:
                rows = connection.execute(
                    f"""SELECT p.short_name,s.* FROM stickers s JOIN packs p USING(pack_id)
                        WHERE p.short_name IN ({placeholders}) AND p.is_archived=0
                        AND s.verified=1 AND s.fingerprint_kind=? AND s.fingerprint=?
                        AND s.mime_type=? AND s.size=? AND s.is_video=1""",
                    (*args, kind, _fingerprint_text(fingerprint), mime_type, size),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""SELECT p.short_name,s.* FROM stickers s JOIN packs p USING(pack_id)
                        WHERE p.short_name IN ({placeholders}) AND p.is_archived=0
                        AND s.verified=1 AND s.fingerprint_kind=?
                        AND s.mime_type=? AND s.size=? AND s.is_video=0""",
                    (*args, kind, mime_type, size),
                ).fetchall()
                rows = [
                    row for row in rows
                    if (int(_fingerprint_value(row["fingerprint_kind"], row["fingerprint"]))
                        ^ int(fingerprint)).bit_count() <= 4
                ]
            return dict(rows[0]) if rows else None
    except (OSError, sqlite3.Error, ValueError):
        return None


def metadata_unchanged(previous: dict, document: dict) -> bool:
    return all(previous.get(key) == document.get(key) for key in (
        "document_id", "file_unique_id", "mime_type", "size", "is_video"
    ))


def invalidate(path: Path = INDEX_PATH) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def cache_size(path: Path = INDEX_PATH) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
