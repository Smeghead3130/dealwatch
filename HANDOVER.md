# Dealwatch v0.1.0 — build handover

Implementation is complete; release verification is in progress. This file will be updated
with final test, image and live-check results before the handover is delivered.

No NAS deployment has been performed. Claude owns dataset creation and deployment.
Use the existing gpu-guard endpoint on port 30069; never call direct Ollama port 30068.

Authentication changed after the original brief: ntfy now denies anonymous access.
Claude must load the private `~/.config/ntfy-nas` file and set `NTFY_TOKEN` from
`dealwatch_token`, and `NTFY_TOPIC` from `dealwatch_topic`, in the actual app environment.
Ignore all `mat_*` admin entries. The committed YAML intentionally contains no credentials.
Mat reports authenticated token/basic publication succeeded, anonymous access returned 403,
and out-of-scope publication returned 403. Codex was told not to repeat that live check.
