# PhotoCLIP

Local CLIP-based photo search for Linux and macOS. Index your photo folders, search them with natural language, everything stays on disk.

macOS has built-in semantic search but only for the Photos library; Linux has nothing comparable. CLIP-based search beats keyword indexes on "vibe" queries like *sunset over water*, *screenshot of code*, *person playing guitar*.

## Architecture

- **Shell:** Tauri 2 (Rust) — spawns a Python sidecar at startup
- **Frontend:** SvelteKit + TypeScript + Tailwind
- **ML backend:** Python sidecar with `open_clip_torch`, FastAPI on `127.0.0.1` (random port)
- **Vector store:** SQLite + [sqlite-vec](https://github.com/asg017/sqlite-vec) extension
- **Thumbs:** 256px JPEGs cached under the app data dir
- **Devices:** CUDA (Linux), MPS (macOS), CPU fallback

```
photoclip/
├── frontend/                  # SvelteKit app
│   └── src-tauri/             # Rust shell, spawns sidecar
└── sidecar/                   # Python: index, search, serve
    ├── server.py
    ├── index.py
    ├── search.py
    ├── thumb.py
    ├── exif.py
    └── db.py
```

## Running the sidecar standalone

The sidecar works as a CLI before any UI is involved.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r sidecar/requirements.txt

# Index a folder
python -m sidecar.index ~/Pictures

# Search from the CLI
python -m sidecar.search "sunset over water"

# Run the HTTP server (prints chosen port on stdout)
python -m sidecar.server
```

## Running the desktop app

```bash
cd frontend
pnpm install
pnpm tauri dev
```

The Rust shell launches the Python sidecar, reads the chosen port from stdout, and points the SvelteKit UI at it.

## Models

- **Default:** `ViT-B-32` / `laion2b_s34b_b79k` — ~150MB, fast, good quality
- **Optional:** `ViT-L-14` / `laion2b_s32b_b82k` — ~890MB, slower, noticeably better

Switching the model in Settings triggers a re-index warning. Models are cached under the app data dir.

## Performance and caching

The expensive thing — running each image through CLIP — is done once and
saved to SQLite + `image_vecs`. The cheap things are cached too:

- **Embeddings:** never recomputed unless a file's `mtime` changes
- **Thumbnails:** content-addressed JPEGs under `<data-dir>/thumbs/`
- **CLIP weights:** downloaded once, stored under `<data-dir>/models/`
- **Browser/webview:** the server returns `Cache-Control: immutable` for
  thumbs and ETag headers for originals — the UI doesn't refetch bytes
  every render

For the CLI, importing torch and loading CLIP weights costs ~3-5s per
invocation. To avoid that, start the resident server once:

```bash
python -m sidecar.server &
```

Now `python -m sidecar.search "anything"` discovers the running server
via `<data-dir>/server.port` and uses it over HTTP — queries become
sub-100ms. Add `--no-server` to force in-process search.

## Keeping the index in sync

Two ways:

1. **Manual:** `python -m sidecar.index <folder>` re-walks and updates
   anything whose `mtime` has changed. Followed by an automatic prune
   pass for vanished files. Files moved within an indexed folder are
   detected by perceptual hash and just have their path updated — no
   re-embedding.

2. **Automatic:** flip the **Watch** toggle for a folder in the Library
   tab (or `POST /library/folders/watch`). The sidecar then runs a
   `watchdog` thread per watched folder, debouncing events so a big
   rsync doesn't trigger a thousand mini-index runs.

Other useful commands:

```bash
# Drop DB rows for files that no longer exist (auto-runs after indexing)
python -m sidecar.prune
python -m sidecar.prune --folder /mnt/photos
```

## Non-goals (v1)

- No Apple Photos library integration (private DB, unstable across OS versions)
- No tagging, albums, or editing
- No video
- No remote indexing
