"""Fetch the upstream HPIS 1.0 demo source without its stored data or keys."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

REPO = "ponyyuhan/mcp_agent"
REV = "f1207dfa8c3909293e217e2961934c085869263a"
FILES = ("hpis_server.py", "pic_server.py", "client_pic.py", "crypto_primitives.py",
         "oauth_server.py", "demo_logging.py", "scripts/gen_keys.py")


def source_files(destination, checkout=None):
    for name in FILES:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if checkout is None:
            data = subprocess.check_output(
                ["gh", "api", "-H", "Accept: application/vnd.github.raw",
                 f"repos/{REPO}/contents/{name}?ref={REV}"]
            )
            target.write_bytes(data)
        else:
            shutil.copyfile(checkout / name, target)
    if checkout is None:
        return {"repository": REPO, "commit": REV}
    note = checkout / "upstream.json"
    return json.loads(note.read_text()) if note.exists() else {"repository": None, "commit": None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    provenance = source_files(args.out)
    (args.out / "upstream.json").write_text(json.dumps(provenance, indent=2) + "\n")
