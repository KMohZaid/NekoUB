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
_CHUNK_SIZE = 500
_INITIALIZED_DBS: set[tuple[str, int]] = set()


def _chunks(items, size: int = _CHUNK_SIZE):
    items_list = list(items)
    for start in range(0, len(items_list), size):
        yield items_list[start : start + size]


def _db_key(path: Path) -> tuple[str, int] | None:
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_ino)
    except OSError:
        return None


@contextmanager
def _database(path: Path = INDEX_PATH, *, read_only: bool = False):
    connection = _connect(path, read_only=read_only)
    try:
        if read_only:
            yield connection
        else:
            with connection:
                yield connection
    finally:
        connection.close()


def _ensure_schema(connection: sqlite3.Connection) -> None:
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
        CREATE INDEX IF NOT EXISTS stickers_pack_media
            ON stickers(pack_id, is_video, verified, mime_type, size);
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
            version = None
        if version is None:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )


def _connect(path: Path = INDEX_PATH, *, read_only: bool = False) -> sqlite3.Connection:
    key = _db_key(path)
    if key is not None and key in _INITIALIZED_DBS:
        connection = sqlite3.connect(path, timeout=30)
        connection.row_factory = sqlite3.Row
        if not read_only:
            connection.execute("PRAGMA foreign_keys=ON")
        return connection

    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    if read_only and key is not None:
        try:
            version = connection.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            if version is not None and int(version["value"]) == SCHEMA_VERSION:
                _INITIALIZED_DBS.add(key)
                return connection
        except (sqlite3.Error, ValueError):
            pass

    _ensure_schema(connection)
    new_key = _db_key(path)
    if new_key is not None:
        _INITIALIZED_DBS.add(new_key)
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


def upsert_packs_snapshot(packs: list[tuple[dict, list[dict]]], user_id: int,
                          *, path: Path = INDEX_PATH) -> None:
    """Atomically replace only the given packs without wiping unrelated cached packs."""
    now = time.time()
    with _database(path) as connection:
        existing_user = connection.execute(
            "SELECT value FROM metadata WHERE key='user_id'"
        ).fetchone()
        if existing_user is not None and existing_user["value"] != str(user_id):
            connection.execute("DELETE FROM stickers")
            connection.execute("DELETE FROM packs")
        for pack, records in packs:
            pack_id = _as_text(pack.get("id") or pack.get("short_name"))
            if not pack_id:
                continue
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
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('user_id',?)",
            (str(user_id),),
        )
        connection.execute(
            "INSERT OR IGNORE INTO metadata(key,value) VALUES('refreshed_at',?)",
            (str(now),),
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('complete','1')"
        )


def prune_packs(valid_pack_ids: set[str], user_id: int,
                *, path: Path = INDEX_PATH) -> None:
    """Drop packs no longer in the owned inventory and stamp snapshot refresh time."""
    now = time.time()
    with _database(path) as connection:
        existing_user = connection.execute(
            "SELECT value FROM metadata WHERE key='user_id'"
        ).fetchone()
        if existing_user is not None and existing_user["value"] != str(user_id):
            connection.execute("DELETE FROM stickers")
            connection.execute("DELETE FROM packs")
        elif valid_pack_ids:
            connection.execute(
                "CREATE TEMP TABLE IF NOT EXISTS _keep_packs (pack_id TEXT PRIMARY KEY)"
            )
            connection.execute("DELETE FROM _keep_packs")
            connection.executemany(
                "INSERT OR IGNORE INTO _keep_packs(pack_id) VALUES(?)",
                [(pack_id,) for pack_id in valid_pack_ids],
            )
            connection.execute(
                "DELETE FROM packs WHERE pack_id NOT IN (SELECT pack_id FROM _keep_packs)"
            )
            connection.execute("DROP TABLE _keep_packs")
        else:
            connection.execute("DELETE FROM stickers")
            connection.execute("DELETE FROM packs")
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('user_id',?)",
            (str(user_id),),
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('refreshed_at',?)",
            (str(now),),
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('complete','1')"
        )


def get_indexed_pack_short_names(user_id: int, *, path: Path = INDEX_PATH) -> set[str]:
    """Return short names of all packs currently indexed for ``user_id``."""
    if not path.exists():
        return set()
    try:
        with _database(path, read_only=True) as connection:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            if metadata.get("user_id") != str(user_id):
                return set()
            rows = connection.execute("SELECT short_name FROM packs").fetchall()
        return {row["short_name"] for row in rows}
    except (OSError, sqlite3.Error):
        return set()


def check_packs_ready(
    pack_counts: dict[str, int],
    *,
    user_id: int,
    max_age: float = 7 * 86400,
    path: Path = INDEX_PATH,
) -> tuple[bool, list[str]]:
    """Run snapshot, coverage, count, and freshness checks in one read-only DB pass."""
    if not pack_counts:
        return True, []
    if not path.exists():
        return False, ["snapshot", "packs_indexed", "counts_match"]
    try:
        now = time.time()
        with _database(path, read_only=True) as connection:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            snapshot_ok = (
                metadata.get("user_id") == str(user_id)
                and metadata.get("complete") == "1"
            )
            by_name: dict[str, sqlite3.Row] = {}
            for chunk in _chunks(pack_counts.keys()):
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""SELECT p.short_name, p.updated_at,
                               COUNT(s.document_id) AS sticker_count
                        FROM packs p LEFT JOIN stickers s USING(pack_id)
                        WHERE p.short_name IN ({placeholders})
                        GROUP BY p.pack_id""",
                    tuple(chunk),
                ).fetchall()
                for row in rows:
                    by_name[row["short_name"]] = row
        indexed_ok = snapshot_ok and len(by_name) == len(pack_counts)
        counts_ok = len(by_name) == len(pack_counts) and all(
            by_name[name]["sticker_count"] == expected
            for name, expected in pack_counts.items()
        )
        fresh_ok = len(by_name) == len(pack_counts) and all(
            now - by_name[name]["updated_at"] <= max_age
            for name in pack_counts
        )
        failed = [
            label
            for label, ok in (
                ("snapshot", snapshot_ok),
                ("packs_indexed", indexed_ok),
                ("counts_match", counts_ok),
                ("packs_fresh", fresh_ok),
            )
            if not ok
        ]
        return not failed, failed
    except (OSError, sqlite3.Error, ValueError):
        return False, ["snapshot"]


def packs_needing_refresh(
    pack_counts: dict[str, int],
    *,
    user_id: int,
    max_age: float = 24 * 3600,
    require_verified: bool = False,
    path: Path = INDEX_PATH,
) -> set[str]:
    """Return requested pack short names that are missing, stale, mismatched, or unverified."""
    if not pack_counts:
        return set()
    if not path.exists():
        return set(pack_counts)
    try:
        now = time.time()
        with _database(path, read_only=True) as connection:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            if metadata.get("user_id") != str(user_id) or metadata.get("complete") != "1":
                return set(pack_counts)
            by_name: dict[str, sqlite3.Row] = {}
            for chunk in _chunks(pack_counts.keys()):
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""SELECT p.short_name, p.updated_at,
                               COUNT(s.document_id) AS sticker_count,
                               SUM(CASE WHEN s.verified=0 THEN 1 ELSE 0 END) AS unverified_count
                        FROM packs p LEFT JOIN stickers s USING(pack_id)
                        WHERE p.short_name IN ({placeholders})
                        GROUP BY p.pack_id""",
                    tuple(chunk),
                ).fetchall()
                for row in rows:
                    by_name[row["short_name"]] = row
        needing: set[str] = set()
        for short_name, expected_count in pack_counts.items():
            row = by_name.get(short_name)
            if row is None:
                needing.add(short_name)
                continue
            if row["sticker_count"] != expected_count:
                needing.add(short_name)
                continue
            if max_age is not None and now - row["updated_at"] > max_age:
                needing.add(short_name)
                continue
            if require_verified and (row["unverified_count"] or 0) > 0:
                needing.add(short_name)
        return needing
    except (OSError, sqlite3.Error):
        return set(pack_counts)


def get_pack_records(short_name: str, *, path: Path = INDEX_PATH) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        with _database(path, read_only=True) as connection:
            rows = connection.execute(
                """SELECT s.* FROM stickers s JOIN packs p USING(pack_id)
                   WHERE p.short_name=?""",
                (short_name,),
            ).fetchall()
        return {row["document_id"]: dict(row) for row in rows}
    except (OSError, sqlite3.Error):
        return {}


def packs_indexed(short_names: set[str], *, path: Path = INDEX_PATH) -> bool:
    """Return whether every requested pack is present in the last complete snapshot."""
    if not short_names or not path.exists():
        return False
    try:
        with _database(path, read_only=True) as connection:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            if metadata.get("complete") != "1":
                return False
            found: set[str] = set()
            for chunk in _chunks(short_names):
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT short_name FROM packs WHERE short_name IN ({placeholders})",
                    tuple(chunk),
                ).fetchall()
                found.update(row["short_name"] for row in rows)
        return len(found) == len(short_names)
    except (OSError, sqlite3.Error):
        return False


def packs_match_counts(pack_counts: dict[str, int], *, path: Path = INDEX_PATH) -> bool:
    """Return whether cached sticker counts match the owned-pack inventory."""
    if not pack_counts or not path.exists():
        return not pack_counts
    try:
        with _database(path, read_only=True) as connection:
            actual: dict[str, int] = {}
            for chunk in _chunks(pack_counts.keys()):
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""SELECT p.short_name,COUNT(s.document_id) AS sticker_count
                        FROM packs p LEFT JOIN stickers s USING(pack_id)
                        WHERE p.short_name IN ({placeholders}) GROUP BY p.pack_id""",
                    tuple(chunk),
                ).fetchall()
                for row in rows:
                    actual[row["short_name"]] = row["sticker_count"]
        return actual == pack_counts
    except (OSError, sqlite3.Error):
        return False


def packs_usable(short_names: set[str], *, max_age: float = 7 * 86400,
                 path: Path = INDEX_PATH) -> bool:
    if not short_names or not path.exists():
        return True
    try:
        now = time.time()
        with _database(path, read_only=True) as connection:
            found: set[str] = set()
            for chunk in _chunks(short_names):
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""SELECT short_name,updated_at FROM packs
                        WHERE short_name IN ({placeholders})""",
                    tuple(chunk),
                ).fetchall()
                for row in rows:
                    if now - row["updated_at"] > max_age:
                        return False
                    found.add(row["short_name"])
        return len(found) == len(short_names)
    except (OSError, sqlite3.Error):
        return False


def has_unverified(short_names: set[str], mime_type: str | None, size: int | None,
                   is_video: bool, *, path: Path = INDEX_PATH) -> bool:
    if not short_names or not path.exists():
        return False
    try:
        with _database(path, read_only=True) as connection:
            for chunk in _chunks(short_names):
                placeholders = ",".join("?" for _ in chunk)
                row = connection.execute(
                    f"""SELECT 1 FROM stickers s JOIN packs p USING(pack_id)
                        WHERE p.short_name IN ({placeholders}) AND s.verified=0
                        AND s.mime_type=? AND s.size=? AND s.is_video=? LIMIT 1""",
                    (*tuple(chunk), mime_type, size, int(bool(is_video))),
                ).fetchone()
                if row is not None:
                    return True
        return False
    except (OSError, sqlite3.Error):
        return True


def snapshot_is_usable(user_id: int, *, max_age: float | None = 7 * 86400,
                       path: Path = INDEX_PATH) -> bool:
    if not path.exists():
        return False
    try:
        with _database(path, read_only=True) as connection:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        return (
            metadata.get("user_id") == str(user_id)
            and metadata.get("complete") == "1"
            and (
                max_age is None
                or time.time() - float(metadata.get("refreshed_at", "0")) <= max_age
            )
        )
    except (OSError, sqlite3.Error, ValueError):
        return False


def find_match(unique_id: str | None, fingerprint, is_video: bool, mime_type: str | None,
               size: int | None, candidate_short_names: set[str], *, path: Path = INDEX_PATH):
    """Return a matching cached record, filtered to the selected destination family."""
    if not candidate_short_names or not path.exists():
        return None
    try:
        with _database(path, read_only=True) as connection:
            if unique_id:
                best = None
                for chunk in _chunks(candidate_short_names):
                    placeholders = ",".join("?" for _ in chunk)
                    row = connection.execute(
                        f"""SELECT p.short_name,p.updated_at AS _pack_updated_at,s.*
                            FROM stickers s JOIN packs p USING(pack_id)
                            WHERE p.short_name IN ({placeholders})
                            AND s.file_unique_id=? AND s.mime_type=? AND s.size=?
                            AND s.is_video=? ORDER BY p.updated_at DESC LIMIT 1""",
                        (*tuple(chunk), unique_id, mime_type, size, int(bool(is_video))),
                    ).fetchone()
                    if row is not None and (
                        best is None or row["_pack_updated_at"] > best["_pack_updated_at"]
                    ):
                        best = row
                if best is not None:
                    result = dict(best)
                    result.pop("_pack_updated_at", None)
                    return result
            if fingerprint is None:
                return None
            kind = "sha256" if (is_video or isinstance(fingerprint, str)) else "phash64"
            if kind == "sha256":
                fp_text = _fingerprint_text(fingerprint)
                for chunk in _chunks(candidate_short_names):
                    placeholders = ",".join("?" for _ in chunk)
                    row = connection.execute(
                        f"""SELECT p.short_name,s.* FROM stickers s JOIN packs p USING(pack_id)
                            WHERE p.short_name IN ({placeholders}) AND p.is_archived=0
                            AND s.verified=1 AND s.fingerprint_kind=? AND s.fingerprint=?
                            AND s.mime_type=? AND s.size=? AND s.is_video=? LIMIT 1""",
                        (*tuple(chunk), kind, fp_text, mime_type, size, int(bool(is_video))),
                    ).fetchone()
                    if row is not None:
                        return dict(row)
                return None
            target_fp = int(fingerprint)
            for chunk in _chunks(candidate_short_names):
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""SELECT p.short_name,s.* FROM packs p CROSS JOIN stickers s
                        WHERE s.pack_id=p.pack_id
                        AND p.short_name IN ({placeholders}) AND p.is_archived=0
                        AND s.verified=1 AND s.fingerprint_kind=?
                        AND s.mime_type=? AND s.size=? AND s.is_video=0""",
                    (*tuple(chunk), kind, mime_type, size),
                ).fetchall()
                for row in rows:
                    if (
                        int(_fingerprint_value(row["fingerprint_kind"], row["fingerprint"]))
                        ^ target_fp
                    ).bit_count() <= 4:
                        return dict(row)
            return None
    except (OSError, sqlite3.Error, ValueError):
        return None


def metadata_unchanged(previous: dict, document: dict) -> bool:
    return all(previous.get(key) == document.get(key) for key in (
        "document_id", "mime_type", "size", "is_video"
    ))


def invalidate(path: Path = INDEX_PATH) -> None:
    key = _db_key(path)
    if key is not None:
        _INITIALIZED_DBS.discard(key)
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
