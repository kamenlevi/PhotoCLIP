// HTTP client for the Python sidecar.
//
// The Tauri shell spawns the sidecar, reads PHOTOCLIP_PORT from stdout, and
// stores it in window.__PHOTOCLIP_PORT before the SvelteKit app hydrates.
// In `pnpm dev` (browser), fall back to an env var or 8765.

import { browser } from "$app/environment";

declare global {
  interface Window {
    __PHOTOCLIP_PORT?: number;
  }
}

let cachedBase: string | null = null;

export function resetSidecarBase() {
  cachedBase = null;
}

export function sidecarBase(): string {
  if (cachedBase) return cachedBase;
  let port: number | undefined;
  if (browser && window.__PHOTOCLIP_PORT) port = window.__PHOTOCLIP_PORT;
  if (!port) {
    const envPort = (import.meta as any).env?.VITE_SIDECAR_PORT;
    if (envPort) port = Number(envPort);
  }
  if (!port) port = 8765;
  const base = `http://127.0.0.1:${port}`;
  // Only cache once we have a confirmed port from the injected window var or env.
  // Without this guard the fallback (8765) gets locked in before the sidecar
  // announces its real port, causing all API calls to permanently fail.
  if (port !== 8765 || !browser) cachedBase = base;
  return base;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(sidecarBase() + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = j.detail ?? detail;
    } catch { /* ignore */ }
    throw new Error(`${r.status} ${detail}`);
  }
  return r.json() as Promise<T>;
}

// ---------- types ----------

export interface Folder {
  id: number;
  path: string;
  added_at: number;
  watch: number;
  image_count: number;
}

export interface SearchResult {
  id: number;
  score: number;
  path: string;
  thumb_path: string | null;
  w: number | null;
  h: number | null;
  taken_at: string | null;
  camera: string | null;
  lat: number | null;
  lon: number | null;
}

export interface PhotoDetail {
  id: number;
  path: string;
  w: number | null;
  h: number | null;
  taken_at: string | null;
  camera: string | null;
  lat: number | null;
  lon: number | null;
  phash: string | null;
  thumb_path: string | null;
  indexed_at: number;
  mtime: number;
}

export interface IndexProgress {
  total: number;
  seen: number;
  indexed: number;
  skipped: number;
  failed: number;
  current_path: string | null;
  done: boolean;
  started_at: number;
  error: string | null;
}

export interface Settings {
  model_name: string;
  pretrained: string;
  device: string;
  data_dir: string;
}

export interface SearchOptions {
  query: string;
  top_k?: number;
  offset?: number;
  folder?: string | null;
  camera?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  has_gps?: boolean | null;
}

// ---------- api ----------

export const api = {
  health: () => req<{ ok: boolean; data_dir: string; db: string }>("/healthz"),

  listFolders: () => req<Folder[]>("/library/folders"),
  addFolder: (path: string) =>
    req<{ ok: boolean; path: string }>("/library/folders", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  removeFolder: (path: string) =>
    req<{ ok: boolean }>(`/library/folders?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    }),

  indexStart: (path: string) =>
    req<{ ok: boolean; folder: string }>("/index/start", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  indexStatus: (folder?: string) =>
    req<IndexProgress | Record<string, IndexProgress>>(
      folder ? `/index/status?folder=${encodeURIComponent(folder)}` : "/index/status"
    ),

  search: (opts: SearchOptions) =>
    req<{ results: SearchResult[] }>("/search", {
      method: "POST",
      body: JSON.stringify(opts),
    }),

  photo: (id: number) => req<PhotoDetail>(`/photo/${id}`),
  photoSimilar: (id: number, k = 20) =>
    req<{ results: SearchResult[] }>(`/photo/${id}/similar?k=${k}`),
  photoThumbUrl: (id: number) => `${sidecarBase()}/photo/${id}/thumb`,
  photoFileUrl: (id: number) => `${sidecarBase()}/photo/${id}/file`,

  getSettings: () => req<Settings>("/settings"),
  setSettings: (s: Partial<Settings>) =>
    req<Settings>("/settings", { method: "POST", body: JSON.stringify(s) }),
};
