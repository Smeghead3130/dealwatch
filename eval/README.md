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
