<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { api, type Folder, type IndexProgress } from "$lib/ipc";
  import { open as openDialog } from "@tauri-apps/plugin-dialog";

  let folders = $state<Folder[]>([]);
  let progress = $state<Record<string, IndexProgress>>({});
  let err = $state<string | null>(null);
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let manualPath = $state("");

  async function refresh() {
    try {
      folders = await api.listFolders();
      const all = await api.indexStatus();
      progress = all as Record<string, IndexProgress>;
    } catch (e) {
      err = (e as Error).message;
    }
  }

  async function add() {
    let chosen: string | null = null;
    try {
      const picked = await openDialog({ directory: true, multiple: false });
      if (typeof picked === "string") chosen = picked;
    } catch {
      // dialog plugin not available — fall back to text input.
    }
    if (!chosen) chosen = manualPath.trim() || null;
    if (!chosen) return;
    try {
      await api.addFolder(chosen);
      manualPath = "";
      await api.indexStart(chosen);
      await refresh();
    } catch (e) {
      err = (e as Error).message;
    }
  }

  async function reindex(path: string) {
    try {
      await api.indexStart(path);
      await refresh();
    } catch (e) {
      err = (e as Error).message;
    }
  }

  async function remove(path: string) {
    if (!confirm(`Remove ${path} and forget its embeddings?`)) return;
    try {
      await api.removeFolder(path);
      await refresh();
    } catch (e) {
      err = (e as Error).message;
    }
  }

  async function toggleWatch(path: string, current: number) {
    try {
      await api.setWatch(path, !current);
      await refresh();
    } catch (e) {
      err = (e as Error).message;
    }
  }

  async function prune(path: string) {
    try {
      const r = await api.indexPrune(path);
      await refresh();
      msg = `Pruned ${r.pruned} dead row${r.pruned === 1 ? "" : "s"}.`;
    } catch (e) {
      err = (e as Error).message;
    }
  }

  let msg = $state<string | null>(null);

  onMount(() => {
    refresh();
    pollTimer = setInterval(refresh, 1000);
  });
  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer);
  });

  function pct(p: IndexProgress): number {
    if (!p.total) return 0;
    return Math.round((100 * (p.indexed + p.moved + p.skipped + p.failed)) / p.total);
  }
</script>

<section class="space-y-4 p-4">
  <header class="flex flex-wrap items-center gap-2">
    <h1 class="text-base font-semibold">Indexed folders</h1>
    <div class="flex-1"></div>
    <input
      type="text"
      bind:value={manualPath}
      placeholder="/home/me/Pictures"
      class="rounded border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-sm" />
    <button
      type="button"
      on:click={add}
      class="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500">
      Add folder
    </button>
  </header>

  {#if err}
    <div class="rounded border border-red-900/60 bg-red-950/40 p-2 text-sm text-red-300">{err}</div>
  {/if}
  {#if msg}
    <div class="rounded border border-emerald-900/60 bg-emerald-950/40 p-2 text-sm text-emerald-300">{msg}</div>
  {/if}

  {#if folders.length === 0}
    <p class="text-sm text-neutral-500">No folders yet. Add one to start indexing.</p>
  {/if}

  <ul class="space-y-2">
    {#each folders as f (f.id)}
      {@const p = progress[f.path]}
      <li class="rounded border border-neutral-800 bg-neutral-900 p-3">
        <div class="flex items-center gap-3">
          <div class="min-w-0 flex-1">
            <div class="truncate font-mono text-sm">{f.path}</div>
            <div class="text-xs text-neutral-500">{f.image_count} indexed images</div>
          </div>
          <label class="flex items-center gap-1.5 text-xs text-neutral-300">
            <input
              type="checkbox"
              checked={!!f.watch}
              on:change={() => toggleWatch(f.path, f.watch)}
              class="h-3.5 w-3.5 accent-indigo-500" />
            Watch
          </label>
          <button on:click={() => reindex(f.path)} class="rounded bg-neutral-800 px-2 py-1 text-xs hover:bg-neutral-700">
            Re-index
          </button>
          <button on:click={() => prune(f.path)} class="rounded bg-neutral-800 px-2 py-1 text-xs hover:bg-neutral-700">
            Prune
          </button>
          <button on:click={() => remove(f.path)} class="rounded bg-neutral-800 px-2 py-1 text-xs text-red-300 hover:bg-red-900/30">
            Remove
          </button>
        </div>
        {#if p && !p.done}
          <div class="mt-2">
            <div class="h-1.5 w-full overflow-hidden rounded bg-neutral-800">
              <div class="h-full bg-indigo-500 transition-[width]" style="width: {pct(p)}%"></div>
            </div>
            <div class="mt-1 flex justify-between text-[11px] text-neutral-500">
              <span>
                {p.indexed} indexed · {p.moved} moved · {p.skipped} skipped · {p.failed} failed
              </span>
              <span>{p.seen}/{p.total}</span>
            </div>
            {#if p.current_path}
              <div class="mt-0.5 truncate text-[11px] text-neutral-600">{p.current_path}</div>
            {/if}
          </div>
        {:else if p?.error}
          <div class="mt-2 text-xs text-red-300">{p.error}</div>
        {/if}
      </li>
    {/each}
  </ul>
</section>
