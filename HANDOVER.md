# Dealwatch v0.2.0 — handover to Claude

Built for Mat on 14 October 2026. This adds the Google Keep wishlist to the existing steal-first app.
**Build only: Codex has not deployed or changed the running TrueNAS app.**

- Repository: https://github.com/Smeghead3130/dealwatch
- Desktop checkout: `/home/mjet/src/dealwatch`
- Release/image: `v0.2.0` / `ghcr.io/smeghead3130/dealwatch:v0.2.0`
- Verified image digest: `sha256:d9c41c498fd0fe99d2f374fb7c8906d542e2bfa69da2d34e73a098f47ca7ad7a`.
- Published by the green release workflow: https://github.com/Smeghead3130/dealwatch/actions/runs/38457583286
- Anonymous pull with an empty Docker credential configuration succeeded. No registry login required.
- Released source commit: `71071c7`; final publication evidence is in this follow-up handover.
- Install file: `compose.truenas.yaml`, still `DRY_RUN=true`.
- Existing mount: **`/mnt/Apps/dealwatch_config:/data`**. Do not create the old `/mnt/Apps/dealwatch/data` path.

## What changed

Pinned `gkeepapi==0.17.1` and `gpsoauth==2.0.0`; Google calls run in a bounded child process in an
independent sync task. Title selects the newest edited matching note; lists exclude checked items,
text notes split into lines. Keep replaces file mode when either Keep credential is configured.
Missing notes, bad credentials and outages retain the last-good SQLite list. Cached incremental
Keep state is `/data/keep_state.json` (mode 600). Source identity includes account and note title,
so changing those cannot silently use another account/note's saved list.

A warning is queued once after six continuous hours of failure; one recovery notice follows a
warned outage. Short outages recover silently. Episode state survives restart. All notices respect
DRY_RUN and the existing outbox; uncertain ntfy acceptance still has the usual at-least-once risk.

Only a schema-checked `same_product` match applies wishlist rules. Wrong capacities, conditions,
variants and accessories do not qualify. The default matched thresholds are 0.85 history / 0.85
quoted. Existing ordinary thresholds remain 0.70 / 0.75. Targets are deterministic: `<=` includes
equality, `under` is strict. Known landed cost must meet a target; unknown delivery cannot prove
one. A target-constrained item above its target does not relax thresholds, but may still pass the
ordinary steal rules. Target alerts say **Wishlist target hit**, priority 4 before quiet hours.
Matched possible alerts still consume the existing possible quota. Wishlist details and reasons
are in decisions and notification bodies. A failed matcher leaves the ordinary steal path available.

## Verification

- **83 offline pytest tests pass**, including the original 41; Ruff lint and formatting pass.
- Keep list/text notes, checked items, latest-title selection, incremental cache/permissions,
  corrupt-cache recovery, file precedence, target parsing, variants/accessories/injection,
  relaxed ratios, target class, existing exclusions, quota and matcher-failure fallback pass.
- Outage tests retain last-good items across restart, back off, and reserve exactly one warning
  plus one recovery notice per warned episode; mocked delivery and DRY_RUN both pass.
- Child-process cancellation/nonblocking polling and private token-file creation/no-echo pass.
- **40/40 non-wishlist decision regressions match v0.1.0** on identical live-extracted facts.
  Label accuracy is 39/40: case 15 omitted a quoted comparison in extraction, so both versions
  missed that deal. No false-positive alerts. Existing extraction code/prompt is unchanged.
- **14/14 live wishlist cases pass** for both match/no-match and final alert class on qwen3.5:9b.
  This is a small synthetic acceptance set, not a real-world accuracy guarantee.
- Docker Compose validation and local container/migration checks pass (details below).

Raw reports: `eval/reports/v020-*`; rerun instructions and limitations: `eval/README.md`.

The local Docker smoke test used a local mocked feed/Ollama/ntfy, UID/GID 568, read-only filesystem,
1 CPU and 512 MiB. It recorded a wishlist target in dry-run, restarted without rejudging, kept its
topic, passed liveness and sent zero notifications. A separate network-disabled container test
created a real v0.1.0 database, migrated it with v0.2.0, preserved its topic and confirmed that
v0.1.0 refuses schema 2 cleanly.

Live inference uses only gpu-guard `http://192.168.50.157:30069`, `qwen3.5:9b`, `think:false`.
No model downloads, direct port 30068 calls, production notifications or NAS app changes were made.
**Live Google Keep and the real token exchange are untested**: `~/.config/dealwatch-keep` was not
present. Mocked tests cover the adapter/helper. Long-running Google sessions, NAS runtime/soak and
actual phone receipt for new wishlist/service notices remain Claude's deployment checks.

## Claude: deployment sequence

1. Help Mat create a dedicated Google account and share the existing Wishlist note with it.
   Never use Mat's personal account: this master token grants account-level access, not a
   note-only permission. The family keeps its existing Keep widget and note.
2. On the desktop, run `.venv/bin/python scripts/keep_token.py` from the checkout. README has the
   short private-browser cookie steps. The helper saves `KEEP_EMAIL` / `KEEP_MASTER_TOKEN` in
   `~/.config/dealwatch-keep`, mode 600, without printing the token. Source/load the file privately;
   do not paste its contents into chat. Do not put secrets into committed YAML.
3. **Stop the app and snapshot Apps/dealwatch_config before opening schema 2**, including via CLI.
   Then deploy the v0.2.0 image with the existing mount and UID/GID 568. Do not create duplicate
   Ollama or ntfy services. Keep `DRY_RUN=true` initially.
4. Preserve existing ntfy topic/auth. Publisher settings are in `~/.config/ntfy-nas`: use
   `dealwatch_token` / `dealwatch_topic`, or `dealwatch_user` / `dealwatch_password`; ignore `mat_*`.
   Keep `NTFY_URL=http://192.168.50.157:30184`. Public `https://ntfy.nethos.org` and Android delivery
   already work per Mat/Claude. Router DNS sinkholing the public hostname is irrelevant to app LAN access.
5. Insert `KEEP_EMAIL` / `KEEP_MASTER_TOKEN` privately; set `KEEP_NOTE_TITLE` if not Wishlist.
   Defaults: `KEEP_SYNC_MINUTES=15`, `WISHLIST_HISTORY_RATIO=0.85`, `WISHLIST_QUOTED_RATIO=0.85`.
   Partial credentials deliberately produce a sync error, not a fallback to the file.
6. After first sync, run `python -m dealwatch wishlist` and `python -m dealwatch status` in the app
   shell. Check source `keep`, items, `last_sync`, and `keep_sync_degraded=false`. Edit/check an item,
   wait for sync and verify the cached list changes; Dealwatch never writes back. Restart once.
7. Use `judge <node>` / `replay --hours 24` to inspect match reasons and dry-run decisions. Inspect
   failed matches conservatively; do not enable alerts just because the image started. Review the
   dry-run period before live alerts. Old dry-run reservations are not replayed when switching live.

To disable Keep, clear **both** Keep fields and use `/data/wishlist.txt` (now using the same semantic
matching/target rules). To roll back to v0.1.0, stop, restore the **pre-upgrade dataset snapshot**, pin
v0.1.0 and restart. Old code refuses schema 2; changing only the image is insufficient. Snapshot
restoration also rewinds post-upgrade delivery history, so consider that before enabling live alerts.

Future token-heavy implementation should go back to Codex; tell Mat before undertaking it in Claude.
