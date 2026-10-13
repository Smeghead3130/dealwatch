# Dealwatch

A small, local-first OzBargain steal detector for Mat's TrueNAS 25.10.7 installation.
One container uses the existing Ollama gpu-guard proxy and ntfy. It does not buy anything,
scrape comments, perform web searches or require a wishlist.

## Deployment boundary

The build produces `ghcr.io/smeghead3130/dealwatch:v0.2.0` through GitHub Actions. See
`HANDOVER.md` for the actual release/test status and live verification and deployment steps.
NAS deployment is intentionally left to Claude. Do not assume that a successful image build
means the application has been tested on the NAS or that a phone received a notification.

## How it decides

Every 120 seconds, the poller makes one conditional feed request. Judging and notification
delivery run independently in the same asyncio process. Slow inference does not pause polling.
Expired items are skipped. Upcoming items with a start time are held until then; items with
no start time remain held until a later feed update removes the upcoming flag.

Ollama extracts product facts and source excerpts. Python checks monetary arithmetic and
thresholds. The model is never a source of normal prices. Comparison identity includes brand,
model, variant, condition, quantity, bundle, import status and price/eligibility terms.

- **Evidence-backed:** effective price is at most 70% of the median of at least three earlier
  comparable offers from distinct OzBargain nodes in 180 days; or at most 75% of a named-source
  comparison explicitly quoted in the post. Historical offers are not current market prices.
  Quoted prices are described as claims in the post, not independently verified.
- **Possible — unverified:** a supported explicit price-error, clearance, sourced-lowest-ever,
  or free-item-with-small-delivery signal, capped at five per local calendar day. A claim of
  clearance is a weak signal: this tier intentionally accepts some noise and can be disabled
  by setting `POSSIBLE_DAILY_LIMIT=0`.
- RRP alone and votes alone never trigger alerts. Wishlist rules are described below. Deposits, subscriptions,
  excluded categories and accessory listings are rejected. Cashback is not subtracted.

Price is the explicitly advertised upfront price after a stated coupon, plus stated shipping.
Unknown shipping is disclosed and comparisons must use compatible price bases. Missing facts
can cause false negatives. No model/prompt can guarantee correct interpretation of every post;
the source checks, conservative validation and evaluation reports expose that limitation.
Obvious instruction-bearing sentences are quarantined before inference, and extracted evidence
must occur in the remaining post. This is defence in depth, not a proof against every injection.

First installation has no historical archive. The service only knows collected offers, and a
long outage can miss items that have left the 30-item feed. History improves over time.

## TrueNAS installation (Claude)

1. Create `/mnt/Apps/dealwatch_config`, writable by UID/GID `568:568` using suitable dataset ACLs.
2. Confirm the image is public and anonymously pullable; package visibility can require a
   one-time change under GitHub → profile → Packages → dealwatch → Package settings.
3. In Apps → Discover Apps → menu → Install via YAML, paste `compose.truenas.yaml`.
4. Retain `DRY_RUN: "true"`. It is one service, has no inbound port, no privileged mode and
   no Docker socket. It is capped at 1 CPU and 512 MiB, with log rotation at 5 MiB × 2.
5. The existing services must be reachable from the container network:
   `OLLAMA_URL=http://192.168.50.157:30069` and `NTFY_URL=http://192.168.50.157:30184`.
   **Never use Ollama port 30068.** Configuration rejects that port to protect gpu-guard.
6. Inspect the logs and run `python -m dealwatch status` in the app shell. Verify ingestion,
   pending jobs, model latency and errors, then restart once and check persistent state.
7. Review a day of dry-run decisions before enabling live alerts. Dry-run decisions reserve
   delivery identities and are not resent automatically when live mode is enabled. New deals
   will alert; replay is inspection only. This avoids a burst of old alerts when switching on.

The model is `qwen3.5:9b`, with `think=false`, `stream=false`, JSON-schema output and the server's
default keep-alive. A request may take up to 300 seconds on CPU. After two timeouts on the same
extraction attempt, try `qwen3.5:4b` once and record the actual model. Invalid responses get one
primary retry. Failed jobs retry with backoff, at most three job attempts by default. No models
are downloaded by Dealwatch.

## Android notification setup

Install ntfy for Android. In the TrueNAS app shell run `python -m dealwatch status` to obtain
the topic. With empty `NTFY_TOPIC`, a random `dealwatch-` plus 12-character topic is generated
once and stored in SQLite. Subscribe using the configured ntfy base URL and this exact topic.
Topic obscurity is not authentication; configure ntfy credentials when required by its policy.

`python -m dealwatch test-notify` sends one real message to **dealwatch-codex-test**, even in
dry-run mode. Subscribe to that test topic to validate delivery, then subscribe to the production
topic from `status`. `--topic NAME` explicitly selects another topic. No purchase action occurs.
Check a notification with the phone locked. Server acceptance alone does not prove phone delivery.

If ntfy rejects publication with 401/403, configure either `NTFY_TOKEN`, or both `NTFY_USERNAME`
and `NTFY_PASSWORD`, in the application's environment. Do not commit credentials to YAML in Git.
Authentication must also permit the phone to read its topic. Without auth configured in Dealwatch,
the code sends anonymously; that works only if the server permits it.

Mat's server now denies anonymous access. Deployment credentials are already stored privately
in `~/.config/ntfy-nas` (mode 600, shell `KEY=value` format). Claude should load `dealwatch_token`
and `dealwatch_topic` into `NTFY_TOKEN` and `NTFY_TOPIC` in the actual TrueNAS app configuration.
The alternative is `dealwatch_user` plus `dealwatch_password`. Do not use `mat_*` admin entries.
The publisher can write only to `dealwatch-*` topics. Never copy these values into the repository.
The committed YAML deliberately contains empty credential placeholders and requires this deployment step.

Mat has working remote Android delivery through authenticated `https://ntfy.nethos.org`.
Keep the app’s `NTFY_URL` on the LAN address. Local router DNS may block the public hostname;
that does not affect the app. iOS is out of scope.

## All environment variables

No `.env` is read. The TrueNAS YAML lists every setting explicitly. Secrets remain environment
variables and are not printed by status or logs.

| Variable | Default | Meaning |
| --- | --- | --- |
| DATA_DIR | `/data` | SQLite, heartbeat, service lock and optional wishlist |
| TZ | `Australia/Brisbane` | Quiet hours and daily possible-alert cap |
| POLL_SECONDS | `120` | Poll interval, minimum 60 |
| FEED_URL | `https://www.ozbargain.com.au/deals/feed` | RSS URL |
| USER_AGENT | `Mozilla/5.0 (compatible; Dealwatch/0.1; personal deal monitor)` | HTTP user agent |
| OLLAMA_URL | `http://192.168.50.157:30069` | gpu-guard proxy; direct port 30068 rejected |
| LLM_MODEL | `qwen3.5:9b` | Extraction and wishlist matching model |
| FALLBACK_MODEL | `qwen3.5:4b` | After two primary timeouts |
| LLM_TIMEOUT | `300` | Seconds per inference request |
| NTFY_URL | `http://192.168.50.157:30184` | ntfy base URL |
| NTFY_TOPIC | empty | Empty means generated and persisted topic |
| NTFY_TOKEN | empty | Optional bearer token; mutually exclusive with basic auth |
| NTFY_USERNAME | empty | Optional basic-auth username |
| NTFY_PASSWORD | empty | Optional basic-auth password; requires username |
| DRY_RUN | `true` | Record would-be alerts without publishing |
| QUIET_HOURS | empty | Optional `22:00-07:00`; lowers priority to 2 |
| HISTORY_RATIO | `0.70` | Maximum current/reference ratio for local history |
| QUOTED_RATIO | `0.75` | Maximum ratio for a named comparison in the post |
| KEEP_EMAIL | empty | Dedicated Google account; empty with token empty selects file mode |
| KEEP_MASTER_TOKEN | empty | Secret from desktop helper; never use a personal account |
| KEEP_NOTE_TITLE | `Wishlist` | Case-insensitive trimmed title; latest edited duplicate wins |
| KEEP_SYNC_MINUTES | `15` | Independent sync interval; retries back off to one hour |
| WISHLIST_HISTORY_RATIO | `0.85` | Matched-item local-history ratio |
| WISHLIST_QUOTED_RATIO | `0.85` | Matched-item quoted-comparison ratio |
| MIN_OBSERVATIONS | `3` | Minimum distinct earlier nodes; cannot be below 3 |
| HISTORY_DAYS | `180` | Comparison window, at most available retained history |
| POSSIBLE_DAILY_LIMIT | `5` | Per local calendar day; 0 disables possible alerts |
| STALE_HOURS | `6` | Suppress delivery older than this since first seen |
| MAX_JOB_ATTEMPTS | `3` | Bounded persistent job attempts |
| EXCLUDE_CATEGORIES | `Financial,Credit Card,Bank Account,Mobile Plans,Internet,Internet Plans,Gambling,Subscription` | Case-insensitive exact category matches; additional title guards cover plan/signup/subscription offers |
| LOG_LEVEL | `INFO` | Application JSON logs |

## Shared Google Keep wishlist

Use a **dedicated Google account** for Dealwatch and share the wishlist note with it. The master
token is an account credential, not a note-scoped permission: do not use a personal account.
[gkeepapi](https://gkeepapi.readthedocs.io/en/latest/) is unofficial and may stop working after
Google changes its service. Dealwatch is read-only and keeps its last successful list on failure.

Desktop setup (Mat, with Claude helping):

1. Create the dedicated account, then share your existing Keep note with it. Keep the title
   `Wishlist`, or tell Claude the title you chose. Your partner can keep using the same note/widget.
2. After installing the development dependencies below, run `.venv/bin/python scripts/keep_token.py`.
3. Follow its three short prompts: private browser, sign into the dedicated account at
   [Google EmbeddedSetup](https://accounts.google.com/EmbeddedSetup), then copy the `oauth_token`
   cookie from F12 → Application/Storage → Cookies into the hidden terminal prompt.
4. The helper exchanges the cookie using [gpsoauth](https://github.com/simon-weber/gpsoauth) and
   saves `KEEP_EMAIL` and `KEEP_MASTER_TOKEN` in `~/.config/dealwatch-keep`, mode 600. It never
   prints the token. Close the private browser. Claude loads the file privately when deploying;
   **do not paste its contents into chat or commit it**. `--replace` renews an existing credential.

List notes use only unchecked items; text notes use one item per line. Duplicate note titles
select the most recently edited note and log a warning. Matching is title-based, regardless of
who owns the note. There is no writeback or automatic checking-off.

Examples: `Sony WH-1000XM6`, `Sony WH-1000XM6 <= 350`, `Samsung T7 2TB under $170`.
Targets are parsed in Python. `<=` includes equality; literal `under` is strict. Malformed price
suffixes have no target. Explicit model, capacity and condition requirements must be met.
Normalized product tokens select candidates, then a separate JSON-schema Ollama call checks
identity. Only `same_product` qualifies; uncertain variants and accessories do not. Match reasons
are stored in decisions. Instruction-like wishlist entries are skipped before matching.

A verified match permits the 85% historical/quoted ratios above. A target item also requires
known delivery and a landed price meeting its target before it can relax thresholds. A target hit
can alert without a comparison price, with **Wishlist target hit** in the title and priority 4;
it is not a steal claim. Above-target items can still qualify under the ordinary steal rules.
Membership/coupons/condition remain disclosed, cashback is never subtracted, and all existing
expiry, stale, category, evidence and possible-alert limits remain in force. Quiet hours still
reduce delivery priority. Wishlist membership alone cannot trigger a possible alert.

The sync task runs independently of judging/polling. Synchronous Google I/O runs in a child
process with a 120-second limit. Incremental state is cached privately in `/data/keep_state.json`;
the usable last-good list and sync status persist in SQLite. Missing notes/auth failures preserve
the last-good list and set `keep_sync_degraded`. After six continuous hours of failure, one warning
is queued; after recovery, one recovery notice is queued. Short failures recover silently.
Notice episodes survive restarts and respect `DRY_RUN`; delivery has the same at-least-once
transport limitation as deal alerts. These notices are independent of deal expiry.

If both Keep credentials are empty, `/data/wishlist.txt` supplies the same matching/target rules.
Blank lines and `#`, `//`, `~` comments are ignored. If either Keep field is set, Keep takes
precedence; incomplete credentials are a sync error rather than silently switching sources.
`python -m dealwatch wishlist` prints source, items, last sync and degraded state; it reads the
cached Keep list, so allow the service’s first sync to finish. A missing unconfigured file is empty.

## CLI and development

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-test.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
```

```sh
python -m dealwatch run
python -m dealwatch status
python -m dealwatch wishlist
python -m dealwatch judge 978852 --refresh
python -m dealwatch replay --hours 24
python -m dealwatch evaluate --split heldout
python -m dealwatch export
python -m dealwatch test-notify --topic dealwatch-codex-test
```

For development set `DATA_DIR` to a scratch directory. `judge` inspects a node in the local
database/current feed; it never notifies. `replay` rejudges collected deals without changing
decisions, observations or delivery state. `evaluate` runs the synthetic labelled dataset live
against Ollama and reports precision and misses. `export` emits retained decisions as JSONL.
Live commands require explicit access to the configured endpoints; ordinary `pytest` is offline.

## Persistence, recovery and monitoring

SQLite uses WAL and versioned migrations. Jobs are unique per node and meaningful revision;
vote changes alone do not requeue inference. A service process lock prevents two runners sharing
the data directory. Interrupted jobs are recovered on restart. Decisions and pending notifications
commit together. Price observations count each node once, not each poll or revision.

Notifications are unique per node and class. A possible alert may receive one evidence-backed
upgrade. Dealwatch does not repeatedly notify price changes within the same class. Changed offers
invalidate queued old notifications. Unavailable or stale items are suppressed before sending.
Network errors retry with bounded exponential delay until an alert becomes stale.

Delivery is **at least once**, not exactly once: acceptance by ntfy followed by a lost response
or a crash before recording success can cause a rare duplicate. Routine restarts and replay do
not resend recorded deliveries. Daily possible-alert limits include reserved and dry-run alerts.

The healthcheck measures process liveness; feed freshness is separately visible in `status`.
A rate-limited feed backs off (normally capped at 30 minutes, longer if Retry-After requires it)
without deliberately restarting the service. A 304 updates successful-poll status. No feed item
can be guaranteed caught while the service is offline or the source is blocked.

Retain full deals, revisions, jobs and decisions for 30 days, compact price observations for
180 days, and notification identities for 365 days. Pruning runs daily. Source quotes in alerts
are evidence claims, not independently verified prices. A long inference queue can cause stale
deals to be suppressed; monitor pending count/oldest job rather than assuming a latency promise.

## Backup and rollback

Stop the app and copy the entire `/data` directory, including SQLite WAL/SHM files if present,
or take a consistent ZFS snapshot of the dataset. Never copy only a live `.db` while ignoring
its WAL. Model data and ntfy storage are managed by their existing apps.

v0.2.0 migrates SQLite schema 1 to 2 on first open (including CLI commands). Before upgrading,
stop the app and snapshot **Apps/dealwatch_config**. To roll back to v0.1.0, stop the app,
restore that pre-upgrade snapshot, then pin the v0.1.0 image and start it. Startup refuses to
open a database with a schema newer than the application supports. If schema compatibility
blocks rollback, restore the matching pre-upgrade snapshot first. Preserve the production topic
and send history to avoid resubscription and routine duplicate alerts.

## Image pipeline

Pushes to main and pull requests run offline tests/lint. `v*` tags additionally build and publish
to GHCR using GitHub Actions' `GITHUB_TOKEN` with `packages: write`; no personal package token
is required. The container base is `python:3.12-slim-bookworm`; Python dependencies are locked.
Package visibility must be public for anonymous NAS pulls. Release tags should not be moved.

Official references: [TrueNAS Custom Apps](https://apps.truenas.com/managing-apps/installing-custom-apps/),
[Ollama chat](https://docs.ollama.com/api/chat), [ntfy publishing](https://docs.ntfy.sh/publish/),
[GitHub container publishing](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images).
