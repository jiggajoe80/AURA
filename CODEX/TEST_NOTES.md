# Gallery v1 — DEV acceptance checklist

1. **Seed data**
   - `/gallery_add title:"Sample One" url:<https image>`
   - `/gallery_add title:"Sample Two" url:<https image>`
   - `/gallery_add title:"Sample Three" url:<https image>`
   - `/gallery_add title:"Sample Clip" url:<https mp4>`
2. **Random pick**
   - Run `/gallery_random` in a non-NSFW channel; confirm it serves one of the seeded safe entries with the embed (no NSFW).
3. **NSFW gating**
   - Update an entry via `/gallery_remove` + `/gallery_add` (with `nsfw:true`) or edit JSON manually, then run `/gallery_random` in non-NSFW (should block) and NSFW channel (if available) to allow it.
4. **Tag drill-down**
   - `/gallery_tag tag:sample` (or chosen tag) returns a filtered random entry and respects NSFW policy.
5. **Removal paths**
   - `/gallery_remove title:"Sample One"`
   - `/gallery_remove index:2`
6. **Reload**
   - Manually edit `data/gallery/gallery.json`, run `/gallery_reload`, then `/gallery_list` to confirm the new data is live.

Autopost is intentionally disabled; no scheduler activity should trigger while `data/gallery/config.json.enabled` remains `false`.
