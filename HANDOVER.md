# Dealwatch v0.3.0 — review handover

Prepared on 14 October 2026 for review on branch `v0.3.0`. **No release tag, image publication,
NAS deployment, real ntfy message, or live model evaluation was performed.** Claude owns live
checks, review, merge, tag and deployment.

User-reported live state: v0.2.0 is deployed in DRY_RUN, Keep is syncing the note `Dealwatch`,
and phone delivery is confirmed end to end. This cloud task has not independently checked
that state. Preserve existing credentials, topic and `/mnt/Apps/dealwatch_config:/data` mount.
The YAML now proposes the future v0.3.0 image and note title; that image does not exist yet.

## What changed

- Persistent wishlist backfill for new/edited Keep item ID + normalized text. The first
  successful sync after upgrade is a baseline: **no backfill of the existing list**. Text
  notes and file mode use normalized-text identity. Re-adding identical text/ID is not new.
- Cached schema interpretation as specific product or description. Description requirements
  are shown by `wishlist`, checked with source quotes, and rendered with ✓/?/✗. Missing
  evidence becomes unknown, contradiction rejects, subjective criteria say model judgement.
  Specific products retain exact identity and target behavior. Accessory needs such as
  X-mount lenses can qualify; a solar panel cannot stand in for a camping battery.
- Description alerts need a fit plus a Python-checked value signal: 20% discount, 15 net votes
  within six hours, or known landed price within target. Unknown-only partial fits use the
  possible tier with the unknown requirements disclosed.
- Backfill uses stored current-revision extractions, separate decision revisions, atomic
  outbox reservations, three individual alerts plus one overflow summary per sync. Rank:
  evidence class, measured saving when available, then effective price. Daily caps take
  precedence; suppressed-by-cap matches are not queued for a future flood.
- Old backfill deals bypass original age suppression, carry age/availability warnings,
  and retain expiry/revision checks. Canonical node-page availability checks have a 10-second
  timeout/512KB bound. Failure is disclosed. Delivery retries expire six hours after reservation.
- Literal title prices now handle main prices followed by parenthesized was-prices and
  alternative `$4/$5` prices (conservatively choose the higher upfront option). RRP, cashback,
  foreign currency and ambiguous prices are not promoted into fabricated price evidence.
  Identity quotes can use the actual title, preserving model/variant source-token checks.
- Seller title was-prices at ≥25% off support **possible**, never evidence-backed, alerts.
  Forty net votes within two hours are a labelled community signal. RRP alone still fails.
  Vote signals use the vote counts available at judgement; ordinary vote-only feed updates
  still do not re-extract/requeue existing decisions.
- `export --decisions --since 24` reads durable SQLite decision history, including backfill
  triggers. Existing retention is 30 days, so it is not an unlimited archive.
- Schema 3 adds interpretation/backfill state and outbox metadata. No dependency changes.

New defaults: `BACKFILL_DAYS=7`, `BACKFILL_MAX_ALERTS=3`, `WISHLIST_DESC_MIN_DISCOUNT=0.20`,
`WISHLIST_DESC_MIN_VOTES=15`, `WISHLIST_DAILY_LIMIT=5`, `ALERT_DAILY_LIMIT=5`. The global budget
covers deal notifications and backfill summaries, not Keep service health notices. Dry-run
reservations count. Zero backfill max disables backfill. Explicit live enabling is unchanged.

## Offline evidence

See `eval/reports/v030-offline.json` and `v030-replay.json`. Commands:

```sh
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/python scripts/evaluate_v030.py --output eval/reports/v030-offline.json
.venv/bin/python scripts/replay_v030.py
```

**131 offline tests pass**, including the original 83 unchanged. Ruff lint and formatting pass.
The v0.3.0 Python wheel also built successfully (local artifact only).
The actual service passed local RSS polling, schema-3 initialization, heartbeat, clean shutdown
and restart/topic-persistence checks using a temporary data directory. New tests cover price parsing, source quote recovery,
weak prices/votes, fit evidence, accessories, target boundaries, durable classification,
baseline/edit detection, backfill cap/summary/dry-run, old/expired/already-alerted deals,
interrupted work, restart deduplication, changed offers, quotas, export filtering and migration.
The actual frozen v0.2.0 Store is used to create schema 2 and prove it refuses schema 3.

- 18 authored description cases: 18/18 class labels, 7 alerts, no misses or false positives.
  **These use mocked schema responses, not live model results or an accuracy estimate.**
- 40 recorded v0.2.0 extractions: 17 alerts before/after, zero changed classes, precision
  17/17, no false-positive examples; the existing miss is case 15 (missing quoted reference).
  Exact label match remains 39/40. New fields and title normalization are not live inference.

## Replay alerts for Mat

A **fresh 30-item public feed** was captured at 2026-10-14 05:29:43 UTC (15:29:43 Brisbane),
HTTP 200, and saved as `tests/fixtures/ozb_feed_2026-10-14.xml` alongside the original. The
four named node pages were also fetched; only their deal titles were parsed, not comments.
Captured titles are in `tests/fixtures/ozb_nodes_2026-10-14.json`. The initial cloud 403 was
resolved after adding the required network domains; fetch metadata/checksum are recorded.

The 34-input replay uses those real captured titles with explicitly authored extraction
responses, and an assumed publication time for the four standalone nodes (no vote signal
is derived for them). It is a deterministic rule replay, not live inference or a complete
reconstruction of the historical 130 decisions. Each input records its provenance.

| Node | Would alert | Reason and restrictions |
| --- | --- | --- |
| 978865 | Possible — Leisure Suit Larry: Wet Dreams Saga Bundle, A$4.87 | Seller claimed was-price A$97.45, unverified; Game Pass required; delivery unknown. Public title captured; extraction mocked. |
| 978890 | Possible — Cadbury Jumbo Box 1.56kg, A$29 | Seller claimed was-price A$58 at BIG W, unverified; delivery unknown. Public title captured; extraction mocked. |

978840 (Durex) now has a conservative A$5 upfront price, but RRP alone does not qualify;
Prime/delivery restrictions remain. 978861 (coffee) parses A$30.65 but has no supported value
signal in the supplied brief, so does not alert. The Mercedes A$65,490 arithmetic is preserved.

Observed sample count: **2 alerts from 34 inputs on 14 October (Australia/Brisbane)**. Coverage is
partial, selected and mocked; it cannot establish a real daily rate. The default cap is five
across deal classes, matching the upper end of Mat's requested 1–5/day range without forcing
low-quality alerts. Claude must measure a full live dry-run day before judging usefulness.

## Claude's required checks before merge/release

1. Review the committed fresh feed and four captured node titles. Repeat the capture locally
   if offers have changed before review. `scripts/build_replay_v030.py --feed PATH
   --captured-at ISO_TIMESTAMP` prepares explicitly mocked inputs; review them before
   `scripts/replay_v030.py`. Mocked facts must not be relabelled as live model outputs.
2. Run `.venv/bin/python scripts/evaluate_v030.py --live --output /tmp/v030-live.json` using
   gpu-guard `http://192.168.50.157:30069`, qwen3.5:9b. Never use direct port 30068. The script
   reports precision, misses and false-positive IDs. Alternatively `python -m dealwatch
   evaluate --cases eval/wishlist_descriptions.jsonl --split all` runs these labelled cases.
   Inspect model interpretations and source quotes, including ambiguous large capacity,
   solar input, soft brand criteria, 3.5-inch drives and X-mount compatibility.
3. Review real decisions with `export --decisions --since 24`; compare useful alert volume,
   unknowns, misses and floods. In a scratch copy of the DB, verify baseline sync causes no
   alerts, then add/edit one item, restart during backfill, and inspect individual/summary
   dry-run reservations. Check a page marked OOS and blocked-page disclosure.
4. Preserve Keep/ntfy secrets privately. The actual Keep note is `Dealwatch`. Check cached
   interpretation via `wishlist`; interpretation failures are distinct from Keep sync failure.
   A model outage can leave an interpretation pending until a later sync/judgement. Failed
   backfill batches are visible in `status`; bounded retries do not run forever.
5. Only after review/live checks: merge, tag and build/release using the existing workflow.
   Then follow the snapshot/upgrade sequence below and a full day of DRY_RUN. No real
   notification should be sent as a side effect of reviewing the offline replay.

## Upgrade and rollback

Stop the app and snapshot **Apps/dealwatch_config** before any v0.3.0 CLI or service opens
SQLite. Opening migrates schema 2 to 3; preserve the whole dataset/WAL state, topic and
notification history. Deploy with the existing mount, UID/GID 568, resource limits, gpu-guard
and ntfy addresses. The first successful sync establishes the v0.3 baseline even if v0.2 had
already synced; it deliberately does not replay the current list.

To roll back, stop v0.3, restore the **pre-upgrade snapshot**, pin the v0.2.0 image and restart.
v0.2.0 refuses schema 3 cleanly; swapping only the image is insufficient. Restoring a snapshot
also rewinds delivery history, so account for any notifications sent after the snapshot.

## Claude review, 14 October 2026 (live qwen3.5:9b via gpu-guard)

Codex's 18/18 description result used mocked model answers. **Live, the first run produced 0/7 alerts**:
the model labelled every wishlist item `specific_product` (even "Lenses for Fujifilm XT-5"), so no
description matching ran. Claude fixed this on the branch:

1. `Interpretation` field order is now requirements → accessory_need → kind, so the label is chosen
   after the requirements. The classify prompt has a clearer rule, and its examples no longer
   reuse Mat's real items (those made the eval circular).
2. A description with only soft requirements gets its first requirement forced hard (otherwise
   "chocolate" would fit every deal).
3. Description matching skips the brand/model identity gate (`validate_facts(require_identity=False)`);
   the quoted per-requirement fit check is the identity proof. Specific-product matches are still
   re-gated with full identity checks in `decide()`.
4. The fit call receives `wanted_is_accessory`, and the prompt lets accessory-type needs (lenses) match.
5. A `not_met` row no longer needs a verbatim quote (downgrading it to unknown turned rejections into
   possible alerts, e.g. an SSD matched the 3.5" HDD item). Only `met` needs a quote.
6. An incomplete/duplicate fit result retries once before erroring.

Live description eval after the fixes: **5 alerts, precision 5/5, 0 false positives**. Misses: 5
(camping battery, solar unknown → expected possible) and 17 (lens, mount unknown → expected possible).
Cases 1, 8 and 12 alert as `possible` rather than a full match, because the model paraphrases quotes and
`supported()` downgrades those `met` rows to unknown. Cases 2 and 3 still raise after the retry (final
decision `none`, which is correct). Recorded v0.2 regression: unchanged, 17/17 precision, 0 changed classes.
134 offline tests pass.

**Backlog for a later Codex pass:** quote-matching tolerance for `met` rows (normalise whitespace,
punctuation and case; allow a short fuzzy match); the two remaining misses; the fit-row count errors on
4-requirement items; `description_candidates()` hard-codes Mat's three current item families
(overfit; prefer a general lexical/LLM prefilter); and more realistic eval cases (real OzB titles,
not "@ Test Retailer" with $100 prices).
