<script lang="ts">
  import { onMount } from "svelte";
  import { page } from "$app/stores";
  import { api, type PhotoDetail, type SearchResult } from "$lib/ipc";
  import ResultGrid from "$lib/components/ResultGrid.svelte";

  let photo = $state<PhotoDetail | null>(null);
  let similar = $state<SearchResult[]>([]);
  let err = $state<string | null>(null);

  $effect(() => {
    const id = Number($page.params.id);
    if (!id) return;
    photo = null;
    similar = [];
    api.photo(id).then((p) => (photo = p)).catch((e) => (err = e.message));
    api.photoSimilar(id, 24).then((r) => (similar = r.results)).catch((e) => (err = e.message));
  });

  function fmtCoord(lat: number | null, lon: number | null) {
    if (lat == null || lon == null) return "—";
    return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  }
</script>

<section class="space-y-4 p-4">
  {#if err}
    <div class="rounded border border-red-900/60 bg-red-950/40 p-2 text-sm text-red-300">{err}</div>
  {/if}

  {#if photo}
    <div class="grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <div class="overflow-hidden rounded-md bg-black ring-1 ring-neutral-800">
        <img
          src={api.photoFileUrl(photo.id)}
          alt={photo.path}
          class="mx-auto max-h-[70vh] w-full object-contain" />
      </div>
      <dl class="space-y-2 text-sm">
        <div>
          <dt class="text-xs uppercase tracking-wide text-neutral-500">Path</dt>
          <dd class="break-all font-mono text-xs">{photo.path}</dd>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <div>
            <dt class="text-xs uppercase tracking-wide text-neutral-500">Dimensions</dt>
            <dd>{photo.w ?? "?"} × {photo.h ?? "?"}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-neutral-500">Taken</dt>
            <dd>{photo.taken_at ?? "—"}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-neutral-500">Camera</dt>
            <dd>{photo.camera ?? "—"}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-neutral-500">GPS</dt>
            <dd class="font-mono text-xs">{fmtCoord(photo.lat, photo.lon)}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-neutral-500">pHash</dt>
            <dd class="font-mono text-xs">{photo.phash ?? "—"}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-neutral-500">Indexed</dt>
            <dd>{new Date(photo.indexed_at * 1000).toLocaleString()}</dd>
          </div>
        </div>
      </dl>
    </div>

    <section>
      <h2 class="px-3 pt-2 text-xs uppercase tracking-wide text-neutral-500">Similar photos</h2>
      <ResultGrid results={similar} showScore={false} />
    </section>
  {:else}
    <div class="p-8 text-center text-sm text-neutral-500">Loading…</div>
  {/if}
</section>
