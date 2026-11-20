"""Rehearsal: run every preset once per model so answers are recorded for offline replay.

    python -m demo.rehearse [--models qwen gemma] [--port 8780]

Requires the demo server to be running.  Each successful answer is cached under
the runtime folder; with HPIS_DEMO_OFFLINE=1 (or when the model endpoint fails
during a presentation) the recorded answer is replayed and labelled as replay.
"""

import argparse
import json
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=["qwen", "gemma"])
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    status = json.load(urllib.request.urlopen(base + "/api/status"))
    for model in args.models:
        for preset in status.get("presets", []):
            body = json.dumps({"question": preset["question"], "model": model}).encode()
            req = urllib.request.Request(base + "/api/run", data=body, headers={"Content-Type": "application/json"})
            lanes, verify, text, evid, search = {}, None, {}, {}, None
            with urllib.request.urlopen(req, timeout=600) as resp:
                for line in resp:
                    ev = json.loads(line)
                    if ev["type"] == "token":
                        text[ev["lane"]] = text.get(ev["lane"], "") + ev["text"]
                    elif ev["type"] == "finish":
                        lanes[ev["lane"]] = (ev["ttft_ms"], ev["total_ms"])
                    elif ev["type"] == "verify":
                        verify = ev
                    elif ev["type"] == "evidence":
                        evid[ev["lane"]] = [c["id"] for c in ev["items"]]
                    elif ev["type"] == "search":
                        search = ev
            summary = " ".join(f"{k}:{v[0]:.0f}/{v[1]:.0f}ms" for k, v in sorted(lanes.items()) if v[0])
            extra = f" verify {verify['overhead_pct']:+.1f}% identical={verify['identical']}" if verify else ""
            same = evid.get("plain") == evid.get("v2")
            cloud = f" cloud-embed {search['candidates']} in {search['embed_ms']:.0f}ms" if search else ""
            print(f"[{model}] {preset['chip']}: {summary}{extra} evidence-same={same}{cloud}", flush=True)
            print("   v2:", " ".join(text.get("v2", "").split())[:160], flush=True)


if __name__ == "__main__":
    main()
