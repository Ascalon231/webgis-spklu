# Continue from here

## Done
- 68 skills installed in `.opencode/skills/`
- MCP GitHub & Supabase configured in `.opencode/opencode.json`
- Endpoint `GET /api/analytics/distance-stats` added in `app.py`
- analytics.html fixed (undefined functions removed, download buttons shown)
- GiST index for `jalan_nasional.geom` added in `schema.sql`
- GiST index executed in Supabase (~3,306 rows)
- Distance stats query optimized: removed `::geography` cast, uses `ST_DistanceSphere` + `<->` KNN
- `unit_count` & `provider` added to `/api/spklu` GeoJSON response
- Popup marker now shows "Unit: X unit(s)" and provider (if known)

## Still needs to be done (optional)
1. Set `GITHUB_TOKEN` env var (create PAT at https://github.com/settings/personal-access-tokens)
2. Run Supabase OAuth flow: `opencode mcp auth supabase`
3. Data stale (DATA-05) — snapshot 16 Dec 2025 needs refresh
4. Consider self-host OSRM routing server
