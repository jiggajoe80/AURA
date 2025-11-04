# Gallery v1

Gallery v1 introduces a global curated media pool for Aura.

## Features
- Public slash commands: `/gallery_random`, `/gallery_show`, `/gallery_tag`, `/gallery_list`.
- Admin slash commands: `/gallery_add`, `/gallery_remove`, `/gallery_reload`, `/gallery_diag`.
- JSON-backed storage at `data/gallery/gallery.json` with atomic writes.
- NSFW-aware routing that blocks entries from non-NSFW channels.

## Data files
- `data/gallery/gallery.json` – curated entries (global pool).
- `data/gallery/config.json` – autopost scaffold (`enabled` remains `false`).

## Usage notes
1. Add entries with `/gallery_add` (tags comma-separated). URLs must be HTTPS and end in jpg/jpeg/png/webp/gif/mp4/webm/mov.
2. `/gallery_remove` accepts either a title (autocomplete) or a 1-based index from `/gallery_list`.
3. `/gallery_diag` reports totals, media mix, tag distribution, and recent additions.

## Integration toggle
To prepare for autopost in a future release, flip `enabled` to `true` in `data/gallery/config.json` and wire the scheduler to read it. Leave it `false` for v1 deployments.
