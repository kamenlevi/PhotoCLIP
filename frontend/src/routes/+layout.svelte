<script lang="ts">
  import "../app.css";
  import { page } from "$app/stores";

  const tabs = [
    { href: "/search/", label: "Search" },
    { href: "/library/", label: "Library" },
    { href: "/settings/", label: "Settings" },
  ];

  let { children } = $props();
</script>

<div class="flex h-full flex-col">
  <header class="flex items-center gap-6 border-b border-neutral-800 bg-neutral-900 px-4 py-2">
    <div class="text-sm font-semibold tracking-wide text-neutral-200">PhotoCLIP</div>
    <nav class="flex gap-1 text-sm">
      {#each tabs as tab}
        {@const active = $page.url.pathname.startsWith(tab.href)}
        <a
          href={tab.href}
          class="rounded px-3 py-1 transition-colors {active
            ? 'bg-neutral-800 text-white'
            : 'text-neutral-400 hover:bg-neutral-800 hover:text-neutral-100'}">
          {tab.label}
        </a>
      {/each}
    </nav>
  </header>
  <main class="min-h-0 flex-1 overflow-auto">
    {@render children()}
  </main>
</div>
