"""Index a folder: walk → embed → upsert into SQLite + sqlite-vec.

Run as a module for the CLI:
    python -m sidecar.index /path/to/folder

Tuning knobs (env vars):
    PHOTOCLIP_BATCH_SIZE      images per CLIP forward pass (default 16;
                              GPU: 32-64 is faster, CPU: 16-32)
    PHOTOCLIP_LOAD_WORKERS    image-decode worker threads (default 4;
                              raise on HEIC/RAW-heavy libraries)
"""

from __future__ import annotations

import argparse
import os
import queue
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import imagehash
from PIL import Image

from . import clip_model, db, exif, thumb


def _batch_size() -> int:
    return int(os.environ.get("PHOTOCLIP_BATCH_SIZE", "16"))


def _load_workers() -> int:
    return max(1, int(os.environ.get("PHOTOCLIP_LOAD_WORKERS", "4")))


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


def _load_one(path: Path) -> tuple[Path, Image.Image | None, str | None, str | None]:
    """Worker-side: decode image and compute pHash. Returns (path, img, phash, err)."""
    try:
        img = thumb.load_image(path)
    except Exception as e:
        return (path, None, None, str(e))
    try:
        ph: str | None = str(imagehash.phash(img))
    except Exception:
        ph = None
    return (path, img, ph, None)


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
    """Incrementally index a specific list of files. Image decoding runs in a
    worker pool so CLIP's forward pass stays busy on the main thread."""
    progress = progress or IndexProgress()
    bundle = clip_model.get_model(model_name, pretrained, device)
    conn = db.connect()
    db.init_db(conn, bundle.dim)

    batch_size = _batch_size()
    workers = _load_workers()

    # Cheap pre-filter on the main thread: skip files whose mtime hasn't
    # changed. Avoids handing untouched files to the decoder workers.
    to_load: list[tuple[Path, int | None]] = []
    for path in paths:
        progress.seen += 1
        progress.current_path = str(path)
        try:
            existing = _existing(conn, str(path))
            if existing and abs(existing["mtime"] - path.stat().st_mtime) < 1e-3:
                progress.skipped += 1
                if on_progress:
                    on_progress(progress)
                continue
        except OSError as e:
            progress.failed += 1
            sys.stderr.write(f"[index] stat failed: {path}: {e}\n")
            if on_progress:
                on_progress(progress)
            continue
        fid = folder_id if folder_id is not None else _folder_id_for(conn, path)
        to_load.append((path, fid))

    progress.total = max(progress.total, len(paths))
    if on_progress:
        on_progress(progress)

    if not to_load:
        return progress

    # Producer: parallel image decode → bounded queue.
    # Consumer (main thread): drain queue, do move detection + CLIP batch.
    loaded_q: "queue.Queue[tuple | None]" = queue.Queue(maxsize=batch_size * 4)
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="phc-load")

    def producer() -> None:
        try:
            for (path, fid), result in zip(
                to_load, pool.map(_load_one, [p for p, _ in to_load])
            ):
                loaded_q.put((fid, *result))
        finally:
            loaded_q.put(None)

    threading.Thread(target=producer, daemon=True, name="phc-producer").start()

    batch: list[tuple[int, Path, Image.Image, str | None]] = []
    while True:
        item = loaded_q.get()
        if item is None:
            break
        fid, path, img, ph, err = item
        if err is not None or img is None:
            progress.failed += 1
            sys.stderr.write(f"[index] failed: {path}: {err}\n")
            if on_progress:
                on_progress(progress)
            continue

        existing = _existing(conn, str(path))
        if not existing and ph:
            move_id = _find_move_candidate(conn, ph, str(path))
            if move_id is not None:
                _handle_move(conn, move_id, fid, path, img, ph, progress)
                if on_progress:
                    on_progress(progress)
                continue

        batch.append((fid or 0, path, img, ph))
        if len(batch) >= batch_size:
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

    pool.shutdown(wait=False)
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
    p.add_argument(
        "--limit", type=int, default=None,
        help="Only process the first N images. Handy for benchmarking before "
             "a multi-hour run.",
    )
    args = p.parse_args(argv)

    root = args.folder.expanduser().resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 2

    started = time.time()

    def log(prog: IndexProgress) -> None:
        if not prog.total:
            return
        done = prog.indexed + prog.skipped + prog.failed + prog.moved
        pct = 100 * done / prog.total
        elapsed = max(1e-3, time.time() - started)
        rate = (prog.indexed + prog.moved) / elapsed
        eta = (prog.total - done) / max(rate, 1e-3)
        eta_s = "?" if rate < 0.01 else _fmt_dur(eta)
        print(
            f"\r[{done}/{prog.total} {pct:5.1f}%] "
            f"indexed={prog.indexed} moved={prog.moved} "
            f"skipped={prog.skipped} failed={prog.failed} "
            f"@ {rate:5.2f} img/s ETA {eta_s}",
            end="",
            flush=True,
        )

    if args.limit is not None:
        files = []
        for p in _walk(root):
            files.append(p)
            if len(files) >= args.limit:
                break
        conn = db.connect()
        bundle = clip_model.get_model(args.model, args.pretrained, args.device)
        db.init_db(conn, bundle.dim)
        db.set_setting(conn, "model_name", args.model)
        db.set_setting(conn, "pretrained", args.pretrained)
        db.set_setting(conn, "embedding_dim", str(bundle.dim))
        fid = _upsert_folder(conn, root)
        prog = IndexProgress()
        result = index_paths(
            files, folder_id=fid, progress=prog, on_progress=log,
            model_name=args.model, pretrained=args.pretrained, device=args.device,
        )
        result.done = True
    else:
        result = index_folder(
            root, on_progress=log,
            model_name=args.model, pretrained=args.pretrained, device=args.device,
        )

    elapsed = time.time() - started
    print()
    print(
        f"Done in {_fmt_dur(elapsed)}. "
        f"total={result.total} indexed={result.indexed} "
        f"moved={result.moved} skipped={result.skipped} "
        f"failed={result.failed} pruned={result.pruned}"
    )
    n = result.indexed + result.moved
    if n and elapsed > 0:
        print(f"Throughput: {n / elapsed:.2f} img/s")
    return 0


def _fmt_dur(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m:02d}m"
    d, h = divmod(h, 24)
    return f"{d}d{h:02d}h"


if __name__ == "__main__":
    raise SystemExit(main())
