"""Before/after transcript: the model handling the same failing tools/call.

    python experiments/week9_error_transcript.py before   # server as committed in d4e76e4
    python experiments/week9_error_transcript.py after    # after the docstring/error rewrite

Lap 1 is a replayed call, identical in both runs: get_article("HC-8"), an id
the model plausibly writes and the store does not have (ids are HC-008).
From lap 2 on, the model is on its own: what it does next depends only on
what the server told it, through the tool description it was given and the
error text it got back.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

import agent_runtime  # noqa: E402
from mcp_agent import Host  # noqa: E402

CONFIG = ROOT / "week9" / "mcp_config.json"
QUERY = "Show me the refunds and escalation article, HC-8, and tell me the refund window."
FAILING_CALL = {"name": "get_article", "arguments": {"article_id": "HC-8"}}


def main(label):
    agent_runtime.warm_up()
    host = Host(str(CONFIG))
    try:
        host.connect()
        description = host.tools["get_article"][1].get("description")
        trace = host.run(QUERY, forced_call=FAILING_CALL, verbose=True)
    finally:
        host.close()
    trace["label"] = label
    trace["get_article_description"] = description
    out = ROOT / "week9" / "runs" / f"error_{label}.json"
    out.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "before"))
