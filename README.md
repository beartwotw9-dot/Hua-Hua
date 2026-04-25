# Competition Archive

## How to add a new competition

1. Drop files into any subfolder under `04_競賽作品/`
2. Add one entry to `competitions/index.json`
3. Run: `node competitions/generate.js`
4. Done - new page is live at `competitions/[id].html`

Optional: add `meta.json` inside a competition source folder with:

```json
{ "reflection": "我在這場比賽學到..." }
```

To serve locally: `npx serve .` (or VS Code Live Server)

To auto-watch: `node competitions/watch.js`

## Current archive source

The portfolio now indexes:

- `04_競賽作品/`
- `05_進修學習/`

These are symlinks to the organized folder on Desktop, so the archive can display the files without duplicating or modifying the original materials.

Use `filePatterns` in `competitions/index.json` when multiple portfolio pages share the same source folder but should show different files.
