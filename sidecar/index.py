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
from typing import Callable, Iterable

import imagehash
from PIL import Image

from . import clip_model, db, exif, thumb

BATCH_SIZE = 16


@dataclass
class IndexProgress:
    total: int = 0
    seen: int = 0
    indexed: int = 0
    moved: int = 0
    skipped: int = 0
    failed: int = 0
    pruned: int = 0
    current_path: str | None = None
    done: bool = False
    started_at: float = field(default_factory=time.time)
    error: str | None = None

    def snapshot(self) -> dict:
        return {
            "total": self.total,
            "seen": self.seen,
            "indexed": self.indexed,
            "moved": self.moved,
            "skipped": self.skipped,
            "failed": self.failed,
            "pruned": self.pruned,
            "current_path": self.current_path,
            "done": self.done,
            "started_at": self.started_at,
            "error": self.error,
        }


ProgressCb = Callable[[IndexProgress], None]


def _walk(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if p.is_file() and not any(part.startswith(".") for part in p.parts):
            if thumb.is_supported(p):
                yield p


def _existing(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, mtime FROM images WHERE path = ?", (path,)
    ).fetchone()


def _find_move_candidate(conn: sqlite3.Connection, ph: str, new_path: str) -> int | None:
    """If an existing row has the same pHash but its file is gone, return its id —
    we treat the new file as a move and update the existing row in place."""
    if not ph:
        return None
    rows = conn.execute(
        "SELECT id, path FROM images WHERE phash = ? AND path != ?",
        (ph, new_path),
    ).fetchall()
    for r in rows:
        if not Path(r["path"]).exists():
            return r["id"]
    return None


def _upsert_folder(conn: sqlite3.Connection, root: Path) -> int:
    conn.execute(
        "INSERT INTO folders(path, added_at) VALUES(?, ?) ON CONFLICT(path) DO NOTHING",
        (str(root), time.time()),
    )
    row = conn.execute("SELECT id FROM folders WHERE path = ?", (str(root),)).fetchone()
    conn.commit()
    return row["id"]


def _folder_id_for(conn: sqlite3.Connection, path: Path) -> int | None:
    """Find the indexed folder that contains `path`, longest-prefix wins."""
    s = str(path)
    rows = conn.execute("SELECT id, path FROM folders").fetchall()
    best: tuple[int, int] | None = None  # (length, folder_id)
    for r in rows:
        fp = r["path"].rstrip("/") + "/"
        if s.startswith(fp):
            if best is None or len(fp) > best[0]:
                best = (len(fp), r["id"])
    return best[1] if best else None


def _process_batch(
    conn: sqlite3.Connection,
    bundle,
    items: list[tuple[int, Path, Image.Image, str | None]],
    progress: IndexProgress,
) -> None:
    """Embed and write a batch. Items are (folder_id, path, img, phash)."""
    if not items:
        return
    images = [im for (_, _, im, _) in items]
    feats = clip_model.encode_images(bundle, images)

    now = time.time()
    for (folder_id, path, img, ph), feat in zip(items, feats):
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


def _handle_move(
    conn: sqlite3.Connection,
    move_id: int,
    folder_id: int | None,
    path: Path,
    img: Image.Image,
    ph: str | None,
    progress: IndexProgress,
) -> None:
    """Repoint an existing row to a new path. Embedding stays untouched."""
    ex = exif.read_exif(path)
    thumb_p = thumb.make_thumb(path, img)
    mtime = path.stat().st_mtime
    conn.execute(
        """UPDATE images SET path=?, folder_id=?, mtime=?, w=?, h=?, taken_at=?,
           camera=?, lat=?, lon=?, phash=?, thumb_path=?, indexed_at=?
           WHERE id=?""",
        (
            str(path), folder_id, mtime, img.width, img.height, ex.taken_at,
            ex.camera, ex.lat, ex.lon, ph, str(thumb_p), time.time(), move_id,
        ),
    )
    conn.commit()
    progress.moved += 1


def index_paths(
    paths: list[Path],
    *,
    folder_id: int | None = None,
    progress: IndexProgress | None = None,
    on_progress: ProgressCb | None = None,
    model_name: str = clip_model.DEFAULT_MODEL,
    pretrained: str = clip_model.DEFAULT_PRETRAINED,
    device: str | None = None,
) -> IndexProgress:
    """Incrementally index a specific list of files. Each file's folder is
    looked up automatically unless `folder_id` is given."""
    progress = progress or IndexProgress()
    bundle = clip_model.get_model(model_name, pretrained, device)
    conn = db.connect()
    db.init_db(conn, bundle.dim)

    progress.total = max(progress.total, len(paths))
    batch: list[tuple[int, Path, Image.Image, str | None]] = []

    for path in paths:
        progress.seen += 1
        progress.current_path = str(path)
        fid = folder_id if folder_id is not None else _folder_id_for(conn, path)

        try:
            existing = _existing(conn, str(path))
            if existing and abs(existing["mtime"] - path.stat().st_mtime) < 1e-3:
                progress.skipped += 1
                if on_progress:
                    on_progress(progress)
                continue
            img = thumb.load_image(path)
            try:
                ph = str(imagehash.phash(img))
            except Exception:
                ph = None
        except Exception as e:
            progress.failed += 1
            sys.stderr.write(f"[index] failed: {path}: {e}\n")
            if on_progress:
                on_progress(progress)
            continue

        if not existing and ph:
            move_id = _find_move_candidate(conn, ph, str(path))
            if move_id is not None:
                _handle_move(conn, move_id, fid, path, img, ph, progress)
                if on_progress:
                    on_progress(progress)
                continue

        batch.append((fid or 0, path, img, ph))
        if len(batch) >= BATCH_SIZE:
            try:
                _process_batch(conn, bundle, batch, progress)
            except Exception as e:
                progress.failed += len(batch)
                sys.stderr.write(f"[index] batch failed: {e}\n")
            batch = []
            if on_progress:
                on_progress(progress)

    if batch:
        try:
            _process_batch(conn, bundle, batch, progress)
        except Exception as e:
            progress.failed += len(batch)
            sys.stderr.write(f"[index] final batch failed: {e}\n")

    if on_progress:
        on_progress(progress)
    return progress


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

    index_paths(
        files,
        folder_id=folder_id,
        progress=progress,
        on_progress=on_progress,
        model_name=model_name,
        pretrained=pretrained,
        device=device,
    )

    # Sweep rows whose files vanished while we were walking — but only
    # those belonging to this folder, so unrelated indexes aren't touched.
    from .prune import prune_folder
    progress.pruned += prune_folder(folder_id)

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
            done = prog.indexed + prog.skipped + prog.failed + prog.moved
            pct = 100 * done / prog.total
            print(
                f"\r[{done}/{prog.total} {pct:5.1f}%] "
                f"indexed={prog.indexed} moved={prog.moved} "
                f"skipped={prog.skipped} failed={prog.failed}",
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
        f"moved={result.moved} skipped={result.skipped} "
        f"failed={result.failed} pruned={result.pruned}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
