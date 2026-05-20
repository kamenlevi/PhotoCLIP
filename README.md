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

## Non-goals (v1)

- No Apple Photos library integration (private DB, unstable across OS versions)
- No tagging, albums, or editing
- No video
- No remote indexing
