<script lang="ts">
  import "../../app.css";
  let { children } = $props();
</script>

<div class="spotlight-root">
  {@render children()}
</div>

<style>
  :global(html), :global(body), :global(.spotlight-root) {
    height: 100%;
    width: 100%;
    background: transparent;
    overflow: hidden;
  }
  :global(body) {
    /* Reset the main app's dark background — the spotlight floats. */
    background: transparent !important;
  }
</style>
