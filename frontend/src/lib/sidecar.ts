import { writable } from "svelte/store";

// Becomes true once Tauri emits 'sidecar://ready' (or immediately in browser dev).
export const sidecarReady = writable(false);
