<script lang="ts">
  import { goto } from "$app/navigation";
  import { api, type SearchResult } from "$lib/ipc";

  let { results, showScore = true }: { results: SearchResult[]; showScore?: boolean } = $props();

  function open(r: SearchResult) {
    goto(`/photo/${r.id}/`);
  }
</script>

{#if results.length === 0}
  <div class="p-8 text-center text-sm text-neutral-500">No matches.</div>
{:else}
  <div class="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
    {#each results as r (r.id)}
      <button
        type="button"
        on:click={() => open(r)}
        class="group relative aspect-square overflow-hidden rounded-md bg-neutral-900 ring-1 ring-neutral-800 hover:ring-neutral-600">
        <img
          src={api.photoThumbUrl(r.id)}
          alt={r.path}
          loading="lazy"
          class="h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.02]" />
        {#if showScore}
          <span class="absolute left-1.5 top-1.5 rounded bg-black/70 px-1.5 py-0.5 text-[10px] font-mono text-neutral-200">
            {r.score.toFixed(2)}
          </span>
        {/if}
        <span
          class="absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/80 to-transparent px-2 py-1 text-left text-[11px] text-neutral-200">
          {r.path.split("/").pop()}
        </span>
      </button>
    {/each}
  </div>
{/if}
