"""FastAPI server on 127.0.0.1. Picks a random free port and prints it on stdout
as the first line, so the Tauri shell can read it.

    PHOTOCLIP_PORT=12345  (line on stdout, then a JSON line with details)
    {"port": 12345, "pid": 4242, "data_dir": "/.../photoclip"}
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import clip_model, db
from .index import IndexProgress, index_folder
from .paths import app_data_dir, db_path
from .search import search_by_vector, search_text, similar_to


app = FastAPI(title="PhotoCLIP sidecar", version="0.1.0")

# Tauri serves the UI from a custom scheme in production and from
# http://localhost:1420 in dev; allow any localhost origin.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|tauri://localhost)$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- shared state ----------

_progress_lock = threading.Lock()
_progress: dict[str, IndexProgress] = {}  # keyed by folder path
_index_thread: threading.Thread | None = None


def _set_progress(folder: str, p: IndexProgress) -> None:
    with _progress_lock:
        _progress[folder] = p


def _get_progress(folder: str) -> IndexProgress | None:
    with _progress_lock:
        return _progress.get(folder)


# ---------- models ----------

class FolderIn(BaseModel):
    path: str


class SearchIn(BaseModel):
    query: str
    top_k: int = 50
    offset: int = 0
    folder: str | None = None
    camera: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    has_gps: bool | None = None


class SettingsIn(BaseModel):
    model_name: str | None = None
    pretrained: str | None = None
    device: str | None = None


# ---------- endpoints ----------

@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "data_dir": str(app_data_dir()),
        "db": str(db_path()),
        "pid": os.getpid(),
    }


@app.get("/library/folders")
def list_folders() -> list[dict[str, Any]]:
    conn = db.connect()
    # init_db requires a dim — only initialize if we know one.
    dim = db.get_setting(conn, "embedding_dim")
    if dim:
        db.init_db(conn, int(dim))
    rows = conn.execute(
        """SELECT folders.id, folders.path, folders.added_at, folders.watch,
                  COUNT(images.id) AS image_count
             FROM folders
        LEFT JOIN images ON images.folder_id = folders.id
         GROUP BY folders.id
         ORDER BY folders.added_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/library/folders")
def add_folder(body: FolderIn) -> dict[str, Any]:
    root = Path(body.path).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {root}")
    conn = db.connect()
    bundle = clip_model.get_model()  # ensures dim is known
    db.init_db(conn, bundle.dim)
    conn.execute(
        "INSERT INTO folders(path, added_at) VALUES(?, ?) ON CONFLICT(path) DO NOTHING",
        (str(root), time.time()),
    )
    conn.commit()
    return {"ok": True, "path": str(root)}


@app.delete("/library/folders")
def remove_folder(path: str) -> dict[str, Any]:
    conn = db.connect()
    row = conn.execute("SELECT id FROM folders WHERE path = ?", (path,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Folder not tracked")
    # Vec rows are not cascaded; clean up explicitly.
    image_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM images WHERE folder_id = ?", (row["id"],)
    )]
    if image_ids:
        qs = ",".join("?" * len(image_ids))
        conn.execute(f"DELETE FROM image_vecs WHERE id IN ({qs})", image_ids)
    conn.execute("DELETE FROM folders WHERE id = ?", (row["id"],))
    conn.commit()
    return {"ok": True}


@app.post("/index/start")
def index_start(body: FolderIn) -> dict[str, Any]:
    global _index_thread
    root = Path(body.path).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {root}")
    if _index_thread and _index_thread.is_alive():
        raise HTTPException(status_code=409, detail="An index job is already running")

    prog = IndexProgress()
    _set_progress(str(root), prog)

    def runner() -> None:
        try:
            index_folder(root, progress=prog, on_progress=lambda p: _set_progress(str(root), p))
        except Exception as e:
            prog.error = str(e)
            prog.done = True
            _set_progress(str(root), prog)

    _index_thread = threading.Thread(target=runner, daemon=True)
    _index_thread.start()
    return {"ok": True, "folder": str(root)}


@app.get("/index/status")
def index_status(folder: str | None = None) -> dict[str, Any]:
    with _progress_lock:
        if folder:
            p = _progress.get(folder)
            return p.snapshot() if p else {"done": True, "total": 0}
        return {k: v.snapshot() for k, v in _progress.items()}


@app.post("/search")
def search(body: SearchIn) -> dict[str, Any]:
    results = search_text(
        body.query,
        top_k=body.top_k,
        offset=body.offset,
        folder=body.folder,
        camera=body.camera,
        date_from=body.date_from,
        date_to=body.date_to,
        has_gps=body.has_gps,
    )
    return {"results": [r.to_dict() for r in results]}


@app.get("/photo/{photo_id}")
def photo_detail(photo_id: int) -> dict[str, Any]:
    conn = db.connect()
    row = conn.execute(
        """SELECT id, path, w, h, taken_at, camera, lat, lon, phash,
                  thumb_path, indexed_at, mtime
             FROM images WHERE id = ?""",
        (photo_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return dict(row)


@app.get("/photo/{photo_id}/similar")
def photo_similar(photo_id: int, k: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    results = similar_to(photo_id, top_k=k)
    return {"results": [r.to_dict() for r in results]}


@app.get("/photo/{photo_id}/file")
def photo_file(photo_id: int):
    conn = db.connect()
    row = conn.execute("SELECT path FROM images WHERE id = ?", (photo_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(row["path"])


@app.get("/photo/{photo_id}/thumb")
def photo_thumb(photo_id: int):
    conn = db.connect()
    row = conn.execute("SELECT thumb_path FROM images WHERE id = ?", (photo_id,)).fetchone()
    if not row or not row["thumb_path"]:
        raise HTTPException(status_code=404, detail="No thumbnail")
    p = Path(row["thumb_path"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="Thumb missing")
    return FileResponse(p, media_type="image/jpeg")


@app.get("/settings")
def get_settings() -> dict[str, Any]:
    conn = db.connect()
    return {
        "model_name": db.get_setting(conn, "model_name", clip_model.DEFAULT_MODEL),
        "pretrained": db.get_setting(conn, "pretrained", clip_model.DEFAULT_PRETRAINED),
        "device": db.get_setting(conn, "device", "auto"),
        "data_dir": str(app_data_dir()),
    }


@app.post("/settings")
def set_settings(body: SettingsIn) -> dict[str, Any]:
    conn = db.connect()
    bundle = clip_model.get_model()
    db.init_db(conn, bundle.dim)
    if body.model_name:
        db.set_setting(conn, "model_name", body.model_name)
    if body.pretrained:
        db.set_setting(conn, "pretrained", body.pretrained)
    if body.device:
        db.set_setting(conn, "device", body.device)
    return get_settings()


# ---------- bootstrap ----------

def _pick_port() -> int:
    env_port = os.environ.get("PHOTOCLIP_PORT")
    if env_port:
        return int(env_port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    port = _pick_port()
    # First line: a parseable port. Second line: structured info. Both stdout, flushed.
    print(f"PHOTOCLIP_PORT={port}", flush=True)
    print(json.dumps({
        "port": port,
        "pid": os.getpid(),
        "data_dir": str(app_data_dir()),
    }), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
