"""Sample pipeline that exercises @probe across the three modes.

Run:

    PROBE_SERVER_URL=http://localhost:8000 python main.py

Switch modes from the dashboard (off / trace / shadow) and rerun.
"""

import time
import uuid

from probe_agent import add_entity, probe, probe_context, set_candidate

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


# replay_capture=True (Issue #242 Phase A) opts this pure component into
# structured, JSON round-trip-able input capture so later replay phases can
# mechanically restore its inputs. Purely additive: traces gain
# input_capture / replayability / replay_reasons fields.
@probe(component_id="json-normalizer", replay_capture=True)
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
    for i, s in enumerate(SAMPLES):
        # Group the summarize + classify probes for this document into one
        # logical flow so lineage can be queried by document / correlation.
        with probe_context(correlation_id=str(uuid.uuid4()), flow_id="ingest-doc"):
            add_entity("document", f"doc-{i}", role="source")
            print("summary :", run_summarize(s))
            print("label   :", run_classify(s))
        time.sleep(0.1)
    for j in JSON_SAMPLES:
        print("normal  :", run_normalize(j))


if __name__ == "__main__":
    main()
