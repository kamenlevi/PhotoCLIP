"""Filesystem watcher: keeps the index in sync with disk.

A `WatcherManager` runs one `FolderWatcher` per folder you've opted in to.
Each watcher batches inotify events for a short debounce window so a big
rsync doesn't trigger 10k separate index calls.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from . import thumb as thumb_mod
from .index import IndexProgress, index_paths
from .prune import prune_paths

DEBOUNCE_SECONDS = 3.0


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "FolderWatcher") -> None:
        self._w = watcher

    def on_created(self, event):
        if not event.is_directory:
            self._w._enqueue("upsert", event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._w._enqueue("upsert", event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._w._enqueue("delete", event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        self._w._enqueue("delete", event.src_path)
        self._w._enqueue("upsert", event.dest_path)


class FolderWatcher:
    """Watch one folder. Debounces inotify events and runs the indexer
    on the affected paths in a background thread."""

    def __init__(self, folder_id: int, root: Path, debounce: float = DEBOUNCE_SECONDS) -> None:
        self.folder_id = folder_id
        self.root = Path(root)
        self.debounce = debounce
        self._observer = Observer()
        self._handler = _Handler(self)
        self._pending: dict[str, str] = {}  # path -> "upsert" | "delete"
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def start(self) -> None:
        if not self.root.is_dir():
            sys.stderr.write(f"[watcher] not a dir, skipping: {self.root}\n")
            return
        self._observer.schedule(self._handler, str(self.root), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        try:
            self._observer.stop()
            self._observer.join(timeout=2.0)
        except Exception:
            pass
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _enqueue(self, action: str, raw_path: str) -> None:
        path = str(Path(raw_path))
        # Only consider supported image extensions; ignore .tmp, .swp, etc.
        if action == "upsert" and not thumb_mod.is_supported(Path(path)):
            return
        with self._lock:
            # Most recent action wins (e.g. created then deleted -> deleted).
            self._pending[path] = action
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            pending = dict(self._pending)
            self._pending.clear()
            self._timer = None
        if not pending:
            return

        upserts: list[Path] = []
        deletes: list[str] = []
        for p, action in pending.items():
            if action == "upsert" and Path(p).is_file():
                upserts.append(Path(p))
            else:
                deletes.append(p)

        try:
            if upserts:
                # Wait a beat in case a file is still being written.
                time.sleep(0.5)
                index_paths(upserts, folder_id=self.folder_id, progress=IndexProgress())
            if deletes:
                prune_paths(deletes)
        except Exception as e:
            sys.stderr.write(f"[watcher] flush failed: {e}\n")


class WatcherManager:
    """Owns one FolderWatcher per indexed folder that has watch=1."""

    def __init__(self) -> None:
        self._watchers: dict[int, FolderWatcher] = {}
        self._lock = threading.Lock()

    def start(self, folder_id: int, root: Path) -> None:
        with self._lock:
            if folder_id in self._watchers:
                return
            w = FolderWatcher(folder_id, root)
            self._watchers[folder_id] = w
        w.start()

    def stop(self, folder_id: int) -> None:
        with self._lock:
            w = self._watchers.pop(folder_id, None)
        if w:
            w.stop()

    def stop_all(self) -> None:
        with self._lock:
            watchers = list(self._watchers.values())
            self._watchers.clear()
        for w in watchers:
            w.stop()

    def is_watching(self, folder_id: int) -> bool:
        with self._lock:
            return folder_id in self._watchers


_manager = WatcherManager()


def manager() -> WatcherManager:
    return _manager
