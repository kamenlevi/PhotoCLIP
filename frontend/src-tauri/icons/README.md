# Icons

Tauri's bundler expects:

- `32x32.png`
- `128x128.png`
- `128x128@2x.png`
- `icon.icns` (macOS)
- `icon.ico` (Windows)

Generate them once you have artwork:

```
pnpm tauri icon path/to/icon.png
```

Until then `pnpm tauri dev` works without them; only the production
bundler (`tauri build`) requires the full set.
