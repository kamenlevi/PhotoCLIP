<script lang="ts">
  import { onMount } from "svelte";
  import { api, type Settings } from "$lib/ipc";

  let settings = $state<Settings | null>(null);
  let saving = $state(false);
  let err = $state<string | null>(null);
  let msg = $state<string | null>(null);

  const MODELS = [
    { model_name: "ViT-B-32", pretrained: "laion2b_s34b_b79k", label: "ViT-B/32 · laion2b (default, ~150MB, fast)" },
    { model_name: "ViT-L-14", pretrained: "laion2b_s32b_b82k", label: "ViT-L/14 · laion2b (~890MB, slower, better)" },
  ];
  let modelKey = $state(`${MODELS[0].model_name}|${MODELS[0].pretrained}`);
  let device = $state("auto");

  onMount(async () => {
    try {
      settings = await api.getSettings();
      modelKey = `${settings.model_name}|${settings.pretrained}`;
      device = settings.device || "auto";
    } catch (e) {
      err = (e as Error).message;
    }
  });

  async function save() {
    if (!settings) return;
    const [model_name, pretrained] = modelKey.split("|");
    const changingModel = model_name !== settings.model_name || pretrained !== settings.pretrained;
    if (changingModel && !confirm("Switching the model invalidates existing embeddings. You will need to re-index every folder. Continue?")) {
      return;
    }
    saving = true;
    err = null;
    msg = null;
    try {
      settings = await api.setSettings({ model_name, pretrained, device });
      msg = "Saved.";
    } catch (e) {
      err = (e as Error).message;
    } finally {
      saving = false;
    }
  }
</script>

<section class="max-w-2xl space-y-4 p-4">
  <h1 class="text-base font-semibold">Settings</h1>

  {#if err}
    <div class="rounded border border-red-900/60 bg-red-950/40 p-2 text-sm text-red-300">{err}</div>
  {/if}
  {#if msg}
    <div class="rounded border border-emerald-900/60 bg-emerald-950/40 p-2 text-sm text-emerald-300">{msg}</div>
  {/if}

  {#if settings}
    <label class="block space-y-1 text-sm">
      <span class="text-neutral-400">CLIP model</span>
      <select bind:value={modelKey} class="w-full rounded border border-neutral-700 bg-neutral-950 px-2 py-2">
        {#each MODELS as m}
          <option value="{m.model_name}|{m.pretrained}">{m.label}</option>
        {/each}
      </select>
    </label>

    <label class="block space-y-1 text-sm">
      <span class="text-neutral-400">Device</span>
      <select bind:value={device} class="w-full rounded border border-neutral-700 bg-neutral-950 px-2 py-2">
        <option value="auto">auto (CUDA → MPS → CPU)</option>
        <option value="cuda">CUDA</option>
        <option value="mps">MPS (Apple Silicon)</option>
        <option value="cpu">CPU</option>
      </select>
    </label>

    <div class="rounded border border-neutral-800 bg-neutral-900 p-3 text-xs text-neutral-400">
      <div><span class="text-neutral-500">Data dir:</span> <span class="font-mono">{settings.data_dir}</span></div>
    </div>

    <button
      on:click={save}
      disabled={saving}
      class="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50">
      {saving ? "Saving…" : "Save"}
    </button>
  {/if}
</section>
