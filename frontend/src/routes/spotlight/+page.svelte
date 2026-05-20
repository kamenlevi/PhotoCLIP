<script lang="ts">
  import { onDestroy, onMount, tick } from "svelte";

  type InvokeFn = <T = unknown>(cmd: string, args?: Record<string, unknown>) => Promise<T>;
  type ListenFn = (event: string, cb: (e: { payload: unknown }) => void) => Promise<() => void>;
  let invoke: InvokeFn | null = null;
  let listen: ListenFn | null = null;

  let query = $state("");
  let inputEl: HTMLInputElement | null = $state(null);
  let unlistenShow: (() => void) | null = null;

  async function close() {
    if (!invoke) return;
    try { await invoke("hide_spotlight"); } catch { /* ignore */ }
  }

  async function submit() {
    const q = query.trim();
    if (!q) return;
    if (invoke) {
      try {
        await invoke("search_in_main", { query: q });
      } catch { /* fall back: just close */ }
    }
    query = "";
    await close();
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  }

  onMount(async () => {
    try {
      const core = await import("@tauri-apps/api/core");
      invoke = core.invoke as unknown as InvokeFn;
      const ev = await import("@tauri-apps/api/event");
      listen = ev.listen as unknown as ListenFn;
    } catch { /* dev browser fallback */ }

    if (listen) {
      unlistenShow = await listen("spotlight://show", async () => {
        query = "";
        await tick();
        inputEl?.focus();
        inputEl?.select();
      });
    }

    await tick();
    inputEl?.focus();
  });

  onDestroy(() => {
    if (unlistenShow) unlistenShow();
  });
</script>

<svelte:window on:keydown={onKeydown} />

<div class="spotlight">
  <input
    bind:this={inputEl}
    bind:value={query}
    placeholder=""
    spellcheck="false"
    autocomplete="off"
    autocapitalize="off" />
</div>

<style>
  .spotlight {
    height: 100%;
    width: 100%;
    background: rgba(28, 28, 32, 0.93);
    border-radius: 16px;
    box-shadow:
      0 30px 80px rgba(0, 0, 0, 0.55),
      0 0 0 0.5px rgba(255, 255, 255, 0.10) inset;
    color: #f3f3f3;
    overflow: hidden;
    display: flex;
    align-items: center;
    padding: 0 26px;
    -webkit-font-smoothing: antialiased;
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
  }
  input {
    flex: 1;
    background: transparent;
    border: none;
    color: inherit;
    outline: none;
    font-size: 24px;
    font-weight: 300;
    letter-spacing: -0.01em;
    min-width: 0;
    caret-color: #818cf8;
  }
  input::placeholder { color: rgba(255, 255, 255, 0.20); }
</style>
