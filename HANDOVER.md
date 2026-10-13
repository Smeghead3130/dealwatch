# Dealwatch v0.1.0 — handover to Claude

Built for Mat on 14 October 2026. **Do not rebuild from the old wishlist-first spec.**
No NAS deployment or production notification has been performed.

- Repository: https://github.com/Smeghead3130/dealwatch
- Source: `/home/mjet/src/dealwatch`
- Release: `v0.1.0` (commit `52b3c2d`)
- Image: `ghcr.io/smeghead3130/dealwatch:v0.1.0`
- Verified image digest: `sha256:2c4005ed7e22e2114f3018e0394bc35065365bb3879a2f2942fe9fa535e94717`
- Installation file: `compose.truenas.yaml`
- Green release workflow: https://github.com/Smeghead3130/dealwatch/actions/runs/38412136241

## Tested

- 41 offline pytest tests pass; Ruff checks and formatting pass.
- Real 30-item RSS fixture, namespaces, expired/upcoming handling, conditional 304,
  revision detection, queue/crash recovery, atomic decision/outbox writes, notification
  deduplication, replay isolation, stale suppression, auth, quotas and timeout fallback.
- Numeric and evidence checks cover the specified misleading-offer cases and injection example.
- Local Docker build and Compose validation pass. A local mock-service container check passed
  with UID/GID 568, read-only filesystem, 1 CPU and 512 MiB. It ingested, judged, recorded a
  dry-run alert, passed liveness, restarted without another judgement, and kept its topic/state.
- GitHub Actions test/build/publish jobs are green. A pull using an empty Docker credential
  configuration succeeded. **No GHCR visibility click or NAS registry credential is needed.**

## Live-tested

Feed fetch succeeded. Five real-feed judgements used only gpu-guard at
`http://192.168.50.157:30069` and `qwen3.5:9b`:

| OzB node | Latency |
| --- | --- |
| 978855 | 5.404 s |
| 978852 | 6.146 s |
| 978849 | 5.849 s |
| 978848 | 6.284 s |
| 978845 | 7.461 s |

All five returned no alert: missing comparable evidence, uncertain identity, accessory or
non-AUD pricing. Raw decisions/extractions are in `eval/reports/live-five-results.jsonl`.

There are 40 labelled synthetic evaluation cases, 10 held out from prompt tuning. The held-out
live run had 5 true positives, 0 false positives, 0 missed steals, 0 errors: precision 100%,
exact-class accuracy 10/10. This small synthetic sample does not establish real-world accuracy.
See `eval/reports/heldout-results.jsonl`. Conservative identity matching still misses some offers.

## ntfy: updated facts from Mat

The brief's anonymous-access assumption is outdated. Codex's initial unauthenticated test
failed; a read-only topic check returned 403. Mat subsequently confirmed token and basic-auth
publication return 200, anonymous access returns 403, and the restricted token cannot publish
outside `dealwatch-*`. Mat explicitly said **do not repeat the live notification check**.
Successful authenticated publication is therefore user-verified, not independently repeated.

Credentials are in `~/.config/ntfy-nas`, mode 600, shell KEY=value format. Load only the
publisher values into the actual app configuration:

- `NTFY_TOKEN` ← `dealwatch_token` (preferred).
- `NTFY_TOPIC` ← `dealwatch_topic`.
- Alternative: `NTFY_USERNAME` / `NTFY_PASSWORD` from `dealwatch_user` / `dealwatch_password`.
- Ignore `mat_*` admin entries. Never put credential values in Git or logs.

Keep `NTFY_URL=http://192.168.50.157:30184`; the public URL is not live yet. The committed YAML
has empty credential placeholders. Configure them during deployment. No credentials were read
or copied into the repository or handover archive by Codex.

## Claude's remaining deployment work

1. Create `/mnt/Apps/dealwatch/data`, writable by 568:568.
2. Install the single-service YAML as a TrueNAS Custom App with `DRY_RUN=true`, inserting the
   publisher credential and topic privately. Reuse existing Ollama/ntfy; do not install duplicates.
3. Check ingestion, model responses, restart persistence and status on the actual NAS.
4. Have Mat subscribe on Android; locked-phone receipt has not been tested by Codex.
5. Review a day of dry-run decisions before switching to live alerts. Dry-run alerts are not
   replayed on enabling live mode; new qualifying deals will notify.

Never call Ollama port 30068 or pull new models. Actual gaming/CPU fallback, NAS installation,
phone delivery, long soak behaviour and remote notification access remain untested here.
Delivery is at-least-once: uncertain ntfy acceptance can cause a rare duplicate after a crash.
Future token-heavy implementation should go back to Codex; tell Mat before undertaking it in Claude.
