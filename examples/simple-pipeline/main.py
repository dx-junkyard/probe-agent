"""Sample pipeline that exercises @probe across the three modes.

Run:

    PROBE_SERVER_URL=http://localhost:8000 python main.py

Switch modes from the dashboard (off / trace / shadow) and rerun.
"""

import time

from probe_agent import probe, set_candidate

from components import (
    classify,
    classify_v2,
    normalize_json,
    summarize,
    summarize_v2,
)

# Register candidates so 'shadow' mode has something to compare against.
set_candidate("summarizer", summarize_v2)
set_candidate("classifier", classify_v2)


@probe(component_id="summarizer")
def run_summarize(text: str) -> str:
    return summarize(text)


@probe(component_id="classifier")
def run_classify(text: str) -> str:
    return classify(text)


@probe(component_id="json-normalizer")
def run_normalize(payload: str) -> str:
    return normalize_json(payload)


SAMPLES = [
    "Probe-agent makes it easy to trace components. It supports shadow execution too.",
    "Add new dashboard feature for shadow comparison.",
    "Fix crash when policy fetch fails.",
    "Update README with example usage.",
]

JSON_SAMPLES = [
    '{"b": 1, "a": 2}',
    '{"name": "probe", "tags": ["trace", "shadow"]}',
]


def main() -> None:
    for s in SAMPLES:
        print("summary :", run_summarize(s))
        print("label   :", run_classify(s))
        time.sleep(0.1)
    for j in JSON_SAMPLES:
        print("normal  :", run_normalize(j))


if __name__ == "__main__":
    main()
