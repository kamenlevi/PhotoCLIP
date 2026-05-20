<script lang="ts">
  import { api, type SearchResult } from "$lib/ipc";
  import ResultGrid from "$lib/components/ResultGrid.svelte";

  let query = $state("");
  let results = $state<SearchResult[]>([]);
  let loading = $state(false);
  let err = $state<string | null>(null);

  // Filters
  let folder = $state("");
  let camera = $state("");
  let dateFrom = $state("");
  let dateTo = $state("");
  let hasGps = $state<"any" | "yes" | "no">("any");
  let topK = $state(60);

  async function run() {
    if (!query.trim()) return;
    loading = true;
    err = null;
    try {
      const r = await api.search({
        query,
        top_k: topK,
        folder: folder || null,
        camera: camera || null,
        date_from: dateFrom || null,
        date_to: dateTo || null,
        has_gps: hasGps === "any" ? null : hasGps === "yes",
      });
      results = r.results;
    } catch (e) {
      err = (e as Error).message;
      results = [];
    } finally {
      loading = false;
    }
  }
</script>

<section class="flex h-full flex-col">
  <form class="flex flex-wrap items-center gap-2 border-b border-neutral-800 bg-neutral-900 p-3" on:submit|preventDefault={run}>
    <input
      type="text"
      bind:value={query}
      placeholder='try: "sunset over water", "screenshot of code", "person playing guitar"'
      class="min-w-[24rem] flex-1 rounded border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm placeholder:text-neutral-600 focus:border-neutral-500 focus:outline-none" />
    <button
      type="submit"
      disabled={loading}
      class="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50">
      {loading ? "Searching…" : "Search"}
    </button>
  </form>

  <details class="border-b border-neutral-800 bg-neutral-900/60">
    <summary class="cursor-pointer select-none px-4 py-2 text-xs text-neutral-400 hover:text-neutral-200">Filters</summary>
    <div class="grid grid-cols-1 gap-3 px-4 pb-3 text-xs sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5">
      <label class="flex flex-col gap-1">
        <span class="text-neutral-400">Folder prefix</span>
        <input type="text" bind:value={folder} placeholder="/home/me/Pictures" class="rounded border border-neutral-700 bg-neutral-950 px-2 py-1" />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-neutral-400">Camera</span>
        <input type="text" bind:value={camera} placeholder="Apple iPhone 15" class="rounded border border-neutral-700 bg-neutral-950 px-2 py-1" />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-neutral-400">Taken from</span>
        <input type="date" bind:value={dateFrom} class="rounded border border-neutral-700 bg-neutral-950 px-2 py-1" />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-neutral-400">Taken to</span>
        <input type="date" bind:value={dateTo} class="rounded border border-neutral-700 bg-neutral-950 px-2 py-1" />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-neutral-400">Has GPS</span>
        <select bind:value={hasGps} class="rounded border border-neutral-700 bg-neutral-950 px-2 py-1">
          <option value="any">any</option>
          <option value="yes">yes</option>
          <option value="no">no</option>
        </select>
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-neutral-400">Top K</span>
        <input type="number" min="1" max="500" bind:value={topK} class="rounded border border-neutral-700 bg-neutral-950 px-2 py-1" />
      </label>
    </div>
  </details>

  {#if err}
    <div class="border-b border-red-900/60 bg-red-950/40 px-4 py-2 text-sm text-red-300">
      {err}
    </div>
  {/if}

  <div class="min-h-0 flex-1 overflow-auto">
    <ResultGrid {results} />
  </div>
</section>
