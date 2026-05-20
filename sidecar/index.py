"""Index a folder: walk → embed → upsert into SQLite + sqlite-vec.

Run as a module for the CLI:
    python -m sidecar.index /path/to/folder
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import imagehash
from PIL import Image

from . import clip_model, db, exif, thumb

BATCH_SIZE = 16


@dataclass
class IndexProgress:
    total: int = 0
    seen: int = 0
    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    current_path: str | None = None
    done: bool = False
    started_at: float = field(default_factory=time.time)
    error: str | None = None

    def snapshot(self) -> dict:
        return {
            "total": self.total,
            "seen": self.seen,
            "indexed": self.indexed,
            "skipped": self.skipped,
            "failed": self.failed,
            "current_path": self.current_path,
            "done": self.done,
            "started_at": self.started_at,
            "error": self.error,
        }


ProgressCb = Callable[[IndexProgress], None]


def _walk(root: Path):
    for p in root.rglob("*"):
        if p.is_file() and not any(part.startswith(".") for part in p.parts):
            if thumb.is_supported(p):
                yield p


def _existing(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, mtime FROM images WHERE path = ?", (path,)
    ).fetchone()


def _upsert_folder(conn: sqlite3.Connection, root: Path) -> int:
    conn.execute(
        "INSERT INTO folders(path, added_at) VALUES(?, ?) ON CONFLICT(path) DO NOTHING",
        (str(root), time.time()),
    )
    row = conn.execute("SELECT id FROM folders WHERE path = ?", (str(root),)).fetchone()
    conn.commit()
    return row["id"]


def _process_batch(
    conn: sqlite3.Connection,
    bundle,
    folder_id: int,
    items: list[tuple[Path, Image.Image]],
    progress: IndexProgress,
) -> None:
    if not items:
        return
    paths = [p for p, _ in items]
    images = [im for _, im in items]
    feats = clip_model.encode_images(bundle, images)

    now = time.time()
    for (path, img), feat in zip(items, feats):
        try:
            ph = str(imagehash.phash(img))
        except Exception:
            ph = None
        ex = exif.read_exif(path)
        thumb_p = thumb.make_thumb(path, img)
        mtime = path.stat().st_mtime
        existing = _existing(conn, str(path))

        if existing:
            image_id = existing["id"]
            conn.execute(
                """UPDATE images SET folder_id=?, mtime=?, w=?, h=?, taken_at=?,
                   camera=?, lat=?, lon=?, phash=?, thumb_path=?, indexed_at=?
                   WHERE id=?""",
                (
                    folder_id, mtime, img.width, img.height, ex.taken_at,
                    ex.camera, ex.lat, ex.lon, ph, str(thumb_p), now, image_id,
                ),
            )
            conn.execute("DELETE FROM image_vecs WHERE id = ?", (image_id,))
        else:
            cur = conn.execute(
                """INSERT INTO images(path, folder_id, mtime, w, h, taken_at,
                   camera, lat, lon, phash, thumb_path, indexed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(path), folder_id, mtime, img.width, img.height, ex.taken_at,
                    ex.camera, ex.lat, ex.lon, ph, str(thumb_p), now,
                ),
            )
            image_id = cur.lastrowid

        conn.execute(
            "INSERT INTO image_vecs(id, embedding) VALUES(?, ?)",
            (image_id, db.pack_embedding(feat.tolist())),
        )
        progress.indexed += 1
    conn.commit()


def index_folder(
    root: Path,
    progress: IndexProgress | None = None,
    on_progress: ProgressCb | None = None,
    model_name: str = clip_model.DEFAULT_MODEL,
    pretrained: str = clip_model.DEFAULT_PRETRAINED,
    device: str | None = None,
) -> IndexProgress:
    progress = progress or IndexProgress()

    bundle = clip_model.get_model(model_name, pretrained, device)
    conn = db.connect()
    db.init_db(conn, bundle.dim)
    db.set_setting(conn, "model_name", model_name)
    db.set_setting(conn, "pretrained", pretrained)
    db.set_setting(conn, "embedding_dim", str(bundle.dim))

    folder_id = _upsert_folder(conn, root)

    files = list(_walk(root))
    progress.total = len(files)
    if on_progress:
        on_progress(progress)

    batch: list[tuple[Path, Image.Image]] = []
    for path in files:
        progress.seen += 1
        progress.current_path = str(path)
        try:
            existing = _existing(conn, str(path))
            if existing and abs(existing["mtime"] - path.stat().st_mtime) < 1e-3:
                progress.skipped += 1
                if on_progress:
                    on_progress(progress)
                continue
            img = thumb.load_image(path)
        except Exception as e:
            progress.failed += 1
            sys.stderr.write(f"[index] failed: {path}: {e}\n")
            if on_progress:
                on_progress(progress)
            continue

        batch.append((path, img))
        if len(batch) >= BATCH_SIZE:
            try:
                _process_batch(conn, bundle, folder_id, batch, progress)
            except Exception as e:
                progress.failed += len(batch)
                sys.stderr.write(f"[index] batch failed: {e}\n")
            batch = []
            if on_progress:
                on_progress(progress)

    if batch:
        try:
            _process_batch(conn, bundle, folder_id, batch, progress)
        except Exception as e:
            progress.failed += len(batch)
            sys.stderr.write(f"[index] final batch failed: {e}\n")

    progress.done = True
    progress.current_path = None
    if on_progress:
        on_progress(progress)
    return progress


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Index a folder of images.")
    p.add_argument("folder", type=Path, help="Folder to index recursively.")
    p.add_argument("--model", default=clip_model.DEFAULT_MODEL)
    p.add_argument("--pretrained", default=clip_model.DEFAULT_PRETRAINED)
    p.add_argument("--device", default=None, help="cuda | mps | cpu | auto")
    args = p.parse_args(argv)

    root = args.folder.expanduser().resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 2

    def log(prog: IndexProgress) -> None:
        if prog.total:
            done = prog.indexed + prog.skipped + prog.failed
            pct = 100 * done / prog.total
            print(
                f"\r[{done}/{prog.total} {pct:5.1f}%] "
                f"indexed={prog.indexed} skipped={prog.skipped} failed={prog.failed}",
                end="",
                flush=True,
            )

    result = index_folder(
        root,
        on_progress=log,
        model_name=args.model,
        pretrained=args.pretrained,
        device=args.device,
    )
    print()
    print(
        f"Done. total={result.total} indexed={result.indexed} "
        f"skipped={result.skipped} failed={result.failed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
