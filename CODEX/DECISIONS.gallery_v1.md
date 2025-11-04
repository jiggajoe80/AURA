# Gallery v1 — Final BA (with selections)

## Business acceptance summary
- Global pool only (v1) stored at `data/gallery/gallery.json`.
- Supported media: jpg, jpeg, png, webp, gif, mp4, webm, mov.
- Sources remain remote (Discord attachments or HTTPS links); no mirroring in v1.
- NSFW policy: block entries in non-NSFW channels and from `/gallery_random` everywhere. Allow in NSFW channels only (future-proof — no NSFW channels yet).
- Autopost scaffold installed but disabled by default.
- Size checks deferred.
- Hooks for guild overlays and digest integration arrive in v1.1.

## Implementation decisions
- Public commands surface read-only access with NSFW filtering and embed rendering aligned to media type.
- Admin commands handle CRUD plus diagnostics, writing atomically to avoid partial files.
- Logging mirrors spec with structured stdout payloads and embed relays to channel `1434273148856963072` for admin actions.
- Config toggles (`data/gallery/config.json`) remain disabled pending future scheduler wiring.
