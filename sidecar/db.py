"""SQLite schema + sqlite-vec initialization."""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import Iterable

import sqlite_vec

from .paths import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    added_at REAL NOT NULL,
    watch INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    folder_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    mtime REAL NOT NULL,
    w INTEGER,
    h INTEGER,
    taken_at TEXT,
    camera TEXT,
    lat REAL,
    lon REAL,
    phash TEXT,
    thumb_path TEXT,
    indexed_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS images_folder_idx ON images(folder_id);
CREATE INDEX IF NOT EXISTS images_phash_idx ON images(phash);
CREATE INDEX IF NOT EXISTS images_taken_idx ON images(taken_at);
CREATE INDEX IF NOT EXISTS images_camera_idx ON images(camera);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _vec_table_sql(dim: int) -> str:
    return f"""
    CREATE VIRTUAL TABLE IF NOT EXISTS image_vecs USING vec0(
        id INTEGER PRIMARY KEY,
        embedding float[{dim}]
    );
    """


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def init_db(conn: sqlite3.Connection, embedding_dim: int) -> None:
    conn.executescript(SCHEMA)
    conn.execute(_vec_table_sql(embedding_dim))
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def pack_embedding(values: Iterable[float]) -> bytes:
    arr = list(values)
    return struct.pack(f"{len(arr)}f", *arr)
