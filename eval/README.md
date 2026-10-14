# Evaluation

`cases.jsonl` contains 40 **synthetic** labelled offers, including 10 held-out cases (IDs 31–40).
These prices are fixtures, not claims about real offers. Tune only against IDs 1–30. The live
`evaluate` command sends only the post text/categories to Ollama; expected labels and supplied
history are not included in the prompt. History entries represent earlier comparable observations.

Run `dealwatch evaluate --split heldout` against the configured Ollama endpoint. It never sends
notifications or writes production state. Output is JSONL per case, followed by precision, misses,
errors and exact alert-class accuracy. Precision is null when there are no positive predictions.
For independent model selection retain a fresh second held-out set after using this one to tune.

The offline tests separately check reference provenance, identity matching, numeric boundaries,
restrictions, persistence and transport failures. They do not claim to measure live LLM quality.

## v0.2.0 wishlist and regression evaluation

Run `.venv/bin/python scripts/evaluate_v020.py` from the desktop checkout. It makes live
Ollama calls; it does not open the production DB, read Keep credentials or send notifications.
`wishlist.jsonl` contains 14 labelled synthetic cases covering matches, target boundaries,
wrong models/capacities/numeric sizes/conditions, accessories, relaxed ratios and injection.
Expected answers and history fixtures are never sent to the model. These are acceptance cases,
not an independent estimate of real-world precision.

For the 40 original cases, the script obtains one live extraction, then evaluates the identical
facts with the frozen v0.1.0 decision engine (`baseline/value_v010.py`, imports made absolute)
and v0.2.0 without a wishlist. It compares all business decision fields, excluding only runtime
model/latency and new empty wishlist metadata. Offline tests replay the captured inputs against
the stored old outputs, so future builds retain the regression check without requiring Ollama.
This tests decision parity on identical facts, not reproducibility of separate LLM generations.
The original extraction code/prompt is unchanged.

The raw inputs, extractions, decisions, timing, labels and comparison results are in
`reports/v020-nonwishlist-regression.jsonl` and `reports/v020-wishlist-live.jsonl`.
The 40-case run matched the v0.1.0 decision engine in all 40 cases; label-class accuracy was
39/40. Case 15 missed its quoted comparison because the model extracted no reference; both
versions consequently returned no alert. There were no false-positive alerts in that run.
The 10 previously held-out cases matched their classes 10/10; the test is no longer fresh holdout
validation after repeated evaluation. The wishlist run passed 14/14 match labels and final alert classes, with no errors.

## v0.3.0 descriptions and replay

`wishlist_descriptions.jsonl` adds 18 labelled synthetic cases using Mat's three real need
phrases. Embedded `mock_*` fields are authored fixtures, **not recorded model results**.
`scripts/evaluate_v030.py` defaults to mocked HTTP responses and also runs the 40-case saved
extraction regression. `--live` sends only source text and wishlist data to Ollama; labels,
mocked extractions and expected fit results are never sent. It never sends notifications.
The ordinary `dealwatch evaluate --cases eval/wishlist_descriptions.jsonl --split all` also
supports wishlist/vote fields. Reports distinguish live inference from mocked rules checks.

`v030-offline.json`: 18/18 mocked classes, 7 alerts, zero false positives/misses. Recorded
regression: 17 alerts before/after, precision 1.0, existing miss 15, 39/40 classes. These do not
establish live-model accuracy. All old eval cases are previously seen acceptance fixtures.

`scripts/build_replay_v030.py` builds explicit mocked replay inputs from the fresh captured public
feed plus four captured node titles. Use `--feed PATH --captured-at ISO_TIMESTAMP` for a new
capture. It reuses prior recorded extractions only where node and title match; review all
facts if the underlying offer has changed. `scripts/replay_v030.py` applies current rules and
outbox caps in a temporary database. The report states provenance and coverage limitations.

The initial cloud HTTP 403 was resolved after the required domain additions. A fresh 30-item
feed was captured on 14 October at 05:29:43 UTC, HTTP 200, along with the four named node-page
titles. Captures and fetch metadata/checksum are under `tests/fixtures`. No comment text was
parsed. The 34-input mocked replay yields possible alerts for 978865 and 978890, no other
alerts: 2 on the sampled Brisbane date. Node publication times and extraction responses are
authored inputs, not recorded inference. This partial snapshot does not establish daily
production rate. Claude must still perform live inference checks before merge.
