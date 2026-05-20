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

The Rust shell launches the Python sidecar, reads the chosen port from
stdout, and points the SvelteKit UI at it.

### What you get

- A **tray icon** lives in the system menu bar / status area. The app
  stays alive in the background even if you close the main window —
  click the tray icon for a menu (Quick search…, Library…, Settings…,
  Quit).
- A **global hotkey** opens a floating Spotlight-style search panel:
  - macOS: **Cmd+Space** (free it from system Spotlight in System
    Settings → Keyboard → Shortcuts → Spotlight)
  - Linux / Windows: **Ctrl+Space**
- The spotlight panel **scopes to the front-most file-manager window**
  if one is open (Finder on macOS, Nautilus / Nemo / Dolphin / Thunar /
  Caja / PCManFM on Linux). Otherwise it searches every indexed folder.
- Type → live results (debounced, top-30). **Arrow keys** to move,
  **Enter** to open in the OS default viewer, **Esc** or click-away to
  dismiss.

### Linux system dependencies

For the desktop build on Ubuntu / Debian:

```bash
sudo apt install -y \
  libwebkit2gtk-4.1-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev \
  xdotool                # for frontmost-folder detection on X11
```

Frontmost-folder detection works on **X11** out of the box. On
**Wayland** the app degrades gracefully — tray, spotlight, and global
hotkey all still work, but the panel won't auto-scope to a folder.

### Building a redistributable bundle

```bash
cd frontend
pnpm tauri build
# Linux:  src-tauri/target/release/bundle/{deb,appimage}/...
# macOS:  src-tauri/target/release/bundle/{dmg,macos}/...
```

Run that on the platform you want to ship for. The resulting .deb /
.AppImage / .dmg installs as a normal desktop app — tray icon, hotkey,
and all.

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

## Indexing at scale (CPU vs. GPU)

Indexing is `O(images) × CLIP forward pass`. On CPU that's the bottleneck:
a 5th-gen Intel laptop manages ~0.3–0.5 images/sec with ViT-B/32, which
makes serious libraries (>100k images) impractical without a GPU.

| Hardware | ViT-B/32 throughput | 100k photos | 1M photos |
|---|---|---|---|
| Old laptop CPU (Broadwell, T450s-class) | ~0.3–0.5 img/s | 2–4 days | 3–6 weeks |
| Modern laptop CPU (M1/M2, Ryzen 6000+) | ~3–8 img/s | 4–10 hrs | 1.5–4 days |
| Apple Silicon GPU (M1/M2/M3, MPS) | ~30–80 img/s | 20–60 min | 4–10 hrs |
| Mid-range NVIDIA (GTX 1070, RTX 3060) | ~150–250 img/s | 7–12 min | 1–2 hrs |
| High-end NVIDIA (RTX 3090/4090) | ~600–1200 img/s | 1.5–3 min | 15–30 min |

The DB and thumb cache are portable: **index on a fast machine, copy
`~/.local/share/photoclip/` over, and search runs the same anywhere.**
Search itself is cheap (one text encode + ~50ms vector scan at 100k), so
the laptop is fine for queries — just not for the initial bulk index.

Tuning knobs:

```bash
PHOTOCLIP_BATCH_SIZE=32  python -m sidecar.index ~/Pictures
PHOTOCLIP_LOAD_WORKERS=8 python -m sidecar.index ~/Pictures
```

- `PHOTOCLIP_BATCH_SIZE` — images per CLIP pass. Default 16. Raise to
  32–64 on a GPU, 16–32 on CPU. Bigger isn't always faster on CPU.
- `PHOTOCLIP_LOAD_WORKERS` — parallel image-decode threads. Default 4.
  Raise on HEIC/RAW-heavy libraries where decode is slow; lower if
  memory pressure is a concern.

To benchmark before committing to a long run:

```bash
python -m sidecar.index ~/Pictures --limit 200
# Done in 1m23s. Throughput: 2.41 img/s
```

The output shows live throughput and ETA so you know what you're in for
before kicking off a multi-hour job.

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
