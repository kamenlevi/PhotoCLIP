<script lang="ts">
  import { onMount } from "svelte";

  type InvokeFn = <T = unknown>(cmd: string, args?: Record<string, unknown>) => Promise<T>;
  let invoke: InvokeFn | null = null;

  let query = $state("");
  let inputEl: HTMLInputElement | null = $state(null);

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

  function resetInput() {
    query = "";
    if (inputEl) {
      inputEl.value = "";
      inputEl.focus();
    }
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

  // The browser focus event fires reliably every time the Tauri window
  // is shown, even after the webview has been suspended in the background.
  function onWindowFocus() {
    resetInput();
  }

  onMount(async () => {
    try {
      const core = await import("@tauri-apps/api/core");
      invoke = core.invoke as unknown as InvokeFn;
    } catch { /* dev browser fallback */ }

    resetInput();
  });
</script>

<svelte:window on:keydown={onKeydown} on:focus={onWindowFocus} />

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
  :global(html), :global(body) {
    height: 100%;
    width: 100%;
    margin: 0;
    padding: 0;
    background: transparent !important;
    overflow: hidden;
  }
  /* Force-hide the root layout header/nav inside the spotlight window.
     Each Tauri window has its own webview, so this cannot affect main. */
  :global(header) { display: none !important; }
  :global(.flex.h-full.flex-col > main) {
    overflow: hidden !important;
    height: 100% !important;
    flex: none !important;
  }
  .spotlight {
    height: 100vh;
    width: 100vw;
    background: rgba(28, 28, 32, 0.93);
    border-radius: 12px;
    box-shadow:
      0 30px 80px rgba(0, 0, 0, 0.55),
      0 0 0 0.5px rgba(255, 255, 255, 0.10) inset;
    color: #f3f3f3;
    overflow: hidden;
    display: flex;
    align-items: center;
    padding: 0 22px;
    box-sizing: border-box;
    -webkit-font-smoothing: antialiased;
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
  }
  input {
    flex: 1;
    width: 100%;
    height: 100%;
    background: transparent;
    border: none;
    color: inherit;
    outline: none;
    font-size: 21px;
    font-weight: 300;
    letter-spacing: -0.01em;
    min-width: 0;
    caret-color: #818cf8;
  }
  input::placeholder { color: rgba(255, 255, 255, 0.20); }
</style>
