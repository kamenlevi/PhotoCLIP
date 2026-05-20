"""Text → embed → top-K cosine via sqlite-vec.

CLI:
    python -m sidecar.search "sunset over water"
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any

from . import clip_model, db


@dataclass
class SearchResult:
    id: int
    score: float
    path: str
    thumb_path: str | None
    w: int | None
    h: int | None
    taken_at: str | None
    camera: str | None
    lat: float | None
    lon: float | None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _model_settings(conn: sqlite3.Connection) -> tuple[str, str]:
    name = db.get_setting(conn, "model_name", clip_model.DEFAULT_MODEL)
    pretrained = db.get_setting(conn, "pretrained", clip_model.DEFAULT_PRETRAINED)
    return name or clip_model.DEFAULT_MODEL, pretrained or clip_model.DEFAULT_PRETRAINED


def _build_filter_sql(
    folder: str | None,
    camera: str | None,
    date_from: str | None,
    date_to: str | None,
    has_gps: bool | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if folder:
        clauses.append("images.path LIKE ?")
        params.append(folder.rstrip("/") + "/%")
    if camera:
        clauses.append("images.camera = ?")
        params.append(camera)
    if date_from:
        clauses.append("images.taken_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("images.taken_at <= ?")
        params.append(date_to)
    if has_gps is True:
        clauses.append("images.lat IS NOT NULL AND images.lon IS NOT NULL")
    elif has_gps is False:
        clauses.append("(images.lat IS NULL OR images.lon IS NULL)")
    return (" AND ".join(clauses), params) if clauses else ("", params)


def search_by_vector(
    conn: sqlite3.Connection,
    query_vec: list[float],
    *,
    top_k: int = 50,
    offset: int = 0,
    folder: str | None = None,
    camera: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_gps: bool | None = None,
    exclude_id: int | None = None,
) -> list[SearchResult]:
    top_k = max(1, min(int(top_k), 500))
    where, params = _build_filter_sql(folder, camera, date_from, date_to, has_gps)
    extra = []
    if exclude_id is not None:
        extra.append("images.id != ?")
        params.append(exclude_id)
    if extra:
        where = " AND ".join([w for w in [where] + extra if w])

    # vec_search: order by distance ASC. Use match + k.
    sql = f"""
    SELECT images.id, images.path, images.thumb_path, images.w, images.h,
           images.taken_at, images.camera, images.lat, images.lon,
           image_vecs.distance AS distance
      FROM image_vecs
      JOIN images ON images.id = image_vecs.id
     WHERE image_vecs.embedding MATCH ?
       AND k = ?
       {('AND ' + where) if where else ''}
     ORDER BY image_vecs.distance ASC
     LIMIT ? OFFSET ?
    """
    bound = [db.pack_embedding(query_vec), top_k + offset, *params, top_k, offset]
    rows = conn.execute(sql, bound).fetchall()

    out: list[SearchResult] = []
    for r in rows:
        # sqlite-vec returns L2 distance on normalized vectors; convert to cosine sim.
        # ||a-b||^2 = 2 - 2*cos(a,b)  =>  cos = 1 - d^2 / 2
        d = float(r["distance"])
        score = max(0.0, 1.0 - (d * d) / 2.0)
        out.append(SearchResult(
            id=r["id"], score=score, path=r["path"], thumb_path=r["thumb_path"],
            w=r["w"], h=r["h"], taken_at=r["taken_at"], camera=r["camera"],
            lat=r["lat"], lon=r["lon"],
        ))
    return out


def search_text(query: str, **kwargs) -> list[SearchResult]:
    conn = db.connect()
    name, pretrained = _model_settings(conn)
    bundle = clip_model.get_model(name, pretrained)
    db.init_db(conn, bundle.dim)
    vec = clip_model.encode_text(bundle, query).tolist()
    return search_by_vector(conn, vec, **kwargs)


def similar_to(image_id: int, **kwargs) -> list[SearchResult]:
    conn = db.connect()
    name, pretrained = _model_settings(conn)
    bundle = clip_model.get_model(name, pretrained)
    db.init_db(conn, bundle.dim)
    row = conn.execute(
        "SELECT embedding FROM image_vecs WHERE id = ?", (image_id,)
    ).fetchone()
    if not row:
        return []
    import struct
    blob = row["embedding"]
    floats = list(struct.unpack(f"{len(blob)//4}f", blob))
    kwargs.setdefault("exclude_id", image_id)
    return search_by_vector(conn, floats, **kwargs)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Search indexed photos by text query.")
    p.add_argument("query", help="Natural language query.")
    p.add_argument("-k", "--top-k", type=int, default=20)
    p.add_argument("--folder", default=None)
    p.add_argument("--camera", default=None)
    p.add_argument("--from", dest="date_from", default=None)
    p.add_argument("--to", dest="date_to", default=None)
    p.add_argument("--has-gps", action="store_true", default=None)
    args = p.parse_args(argv)

    results = search_text(
        args.query,
        top_k=args.top_k,
        folder=args.folder,
        camera=args.camera,
        date_from=args.date_from,
        date_to=args.date_to,
        has_gps=args.has_gps,
    )
    if not results:
        print("(no results)")
        return 0
    width = max(len(r.path) for r in results)
    for r in results:
        print(f"{r.score:.3f}  {r.path.ljust(width)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
