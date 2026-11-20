"""Validate demo/data: schema, dates, sizes, presets (facts + retrieval top-5).

    python -m demo.corpus.validate [--no-embed]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter

import numpy as np

from . import bible as B

DATA = __import__("pathlib").Path(__import__("os").environ.get("CORPUS_OUT") or B.DATA)
CATS = {"work", "client", "notification", "finance", "travel", "health", "family", "school", "newsletter", "promotion", "system"}
FOLDERS = {"inbox", "sent", "archive"}
KINDS = {"doc", "sheet", "pdf", "slides", "image"}
LO, HI = "2024-01-01T00:00:00+08:00", "2026-11-20T18:00:00+08:00"
ID_RE = __import__("re").compile(r"(?<![\dA-Za-z])\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![\dA-Za-z])")


def load():
    mails = []
    with open(DATA / "mailbox.jsonl", encoding="utf-8") as fh:
        for line in fh:
            mails.append(json.loads(line))
    return mails


def full_text(m):
    return m["subject"] + "\n" + m["body"] + "\n" + "\n".join(a["name"] + "\n" + (a["text"] or "") for a in m["attachments"])


def window_text(m):
    """What the demo's model_text() shows the LLM: body[:1800] + first 3 attachments[:1600]."""
    return m["subject"] + "\n" + m["body"][:1800] + "\n" + "\n".join(a["name"] + "\n" + (a["text"] or "")[:1600] for a in m["attachments"][:3])


def check_schema(mails):
    errs = Counter()
    prev = ""
    for i, m in enumerate(mails, 1):
        if m.get("id") != i:
            errs["id"] += 1
        d = m.get("date", "")
        try:
            t = dt.datetime.fromisoformat(d)
            if t.utcoffset() != dt.timedelta(hours=8):
                errs["tz"] += 1
        except Exception:
            errs["date_parse"] += 1
        if not (LO <= d <= HI):
            errs["date_range"] += 1
        if d < prev:
            errs["order"] += 1
        prev = d
        if m.get("folder") not in FOLDERS:
            errs["folder"] += 1
        if m.get("category") not in CATS:
            errs["category"] += 1
        if not str(m.get("thread", "")).startswith("t-"):
            errs["thread"] += 1
        f = m.get("from") or {}
        if not (f.get("name") and "@" in f.get("email", "") and "org" in f):
            errs["from"] += 1
        if not isinstance(m.get("to"), list) or not m["to"] or any("@" not in x.get("email", "") for x in m["to"]):
            errs["to"] += 1
        if not isinstance(m.get("cc"), list):
            errs["cc"] += 1
        if not m.get("subject") or not m.get("body"):
            errs["empty"] += 1
        for a in m.get("attachments", []):
            if not (a.get("name") and a.get("mime") and isinstance(a.get("size"), int) and a["size"] > 0 and a.get("kind") in KINDS and isinstance(a.get("text"), str)):
                errs["attachment"] += 1
        fc = m.get("facets") or {}
        if not ("org" in fc and "project" in fc and isinstance(fc.get("people"), list)):
            errs["facets"] += 1
        if ID_RE.search(m["body"]) or any(ID_RE.search(x["text"] or "") for x in m.get("attachments", [])):
            errs["id_card_unmasked"] += 1
    return errs


def plan_filter(mails, plan):
    orgs = set(plan.get("orgs") or [])
    cats = set(plan.get("categories") or [])
    ppl = set(plan.get("people") or [])
    lo = (plan.get("date_from") or "0000") + "T00:00:00"
    hi = (plan.get("date_to") or "9999") + "T23:59:59"
    idx = []
    for i, m in enumerate(mails):
        if orgs and (m["facets"].get("org") or m["from"].get("org")) not in orgs:
            continue
        if cats and m["category"] not in cats:
            continue
        if ppl and not (ppl & set(m["facets"].get("people") or [])):
            continue
        if not (lo <= m["date"][:19] <= hi):
            continue
        idx.append(i)
    return np.asarray(idx, dtype=np.int64)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-embed", action="store_true")
    args = ap.parse_args()
    mails = load()
    ok = True
    print(f"emails: {len(mails)}")
    errs = check_schema(mails)
    print("schema errors:", dict(errs) or "none")
    ok &= not errs
    body_b = sum(len(m["body"].encode()) for m in mails)
    att_b = sum(len(a["text"].encode()) for m in mails for a in m["attachments"])
    n_att = sum(len(m["attachments"]) for m in mails)
    print(f"text bytes: body {body_b / 1e6:.1f} MB + attachments {att_b / 1e6:.1f} MB = {(body_b + att_b) / 1e6:.1f} MB; "
          f"attachments {n_att} on {sum(1 for m in mails if m['attachments'])} emails ({100 * sum(1 for m in mails if m['attachments']) / len(mails):.1f}%)")
    print("date range:", mails[0]["date"], "→", mails[-1]["date"])
    print("categories:", dict(Counter(m["category"] for m in mails).most_common()))
    presets = json.loads((DATA / "presets.json").read_text("utf-8"))
    byid = {m["id"]: m for m in mails}

    emb = None
    if not args.no_embed:
        from demo.embed import Embedder, load_embeddings

        emb = load_embeddings(DATA)
        assert emb.shape[0] == len(mails), (emb.shape, len(mails))
        norms = np.linalg.norm(emb[: min(2000, len(emb))], axis=1)
        print(f"embeddings: {emb.shape}, norm≈{norms.mean():.4f}")
        model = Embedder()

    rows = []
    for p in presets:
        ev = p["gold"]["evidence_ids"]
        exists = all(i in byid for i in ev) and len(ev) >= 2
        dis_ok = all(i in byid for i in p["distractor_ids"]) and len(p["distractor_ids"]) >= 2
        blob = "\n".join(full_text(byid[i]) for i in ev if i in byid)
        win = "\n".join(window_text(byid[i]) for i in ev if i in byid)
        facts_ok = all(any(x in blob for x in f["match"]) for f in p["gold"]["facts"])
        miss = [f["label"] for f in p["gold"]["facts"] if not any(x in blob for x in f["match"])]
        miss_w = [f["label"] for f in p["gold"]["facts"] if not any(x in win for x in f["match"])]
        cand = plan_filter(mails, p["plan"])
        in_cand = all((i - 1) in set(cand.tolist()) for i in ev)
        ranks = "-"
        top5 = None
        qranks = "-"
        if emb is not None:
            q = model.encode_query(p["plan"]["semantic"])
            sc = emb[cand] @ q
            order = cand[np.argsort(-sc, kind="stable")]
            pos = {int(j) + 1: r + 1 for r, j in enumerate(order)}
            rk = [pos.get(i, 10**9) for i in ev]
            ranks = ",".join(str(r) if r < 10**9 else "x" for r in rk)
            top5 = max(rk) <= 5
            q2 = model.encode_query(p["question"])
            sc2 = emb[cand] @ q2
            order2 = cand[np.argsort(-sc2, kind="stable")]
            pos2 = {int(j) + 1: r + 1 for r, j in enumerate(order2)}
            qranks = ",".join(str(pos2.get(i, "x")) for i in ev)
            p["_top"] = [int(j) + 1 for j in order[:8]]
        rows.append((p["id"], len(ev), exists, facts_ok, len(miss_w) == 0, dis_ok, len(cand), in_cand, ranks, top5, qranks, miss, miss_w))
        ok &= exists and facts_ok and dis_ok and in_cand and (top5 is not False)
    print()
    hdr = f"{'preset':9} {'gold':>4} {'exist':>5} {'facts':>5} {'win':>4} {'distr':>5} {'cands':>6} {'filt':>4} {'ranks(plan)':>12} {'top5':>5} {'ranks(question)':>15}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r[0]:9} {r[1]:>4} {str(r[2]):>5} {str(r[3]):>5} {str(r[4]):>4} {str(r[5]):>5} {r[6]:>6} {str(r[7]):>4} {r[8]:>12} {str(r[9]):>5} {r[10]:>15}"
              + (f"  missing={r[11]}" if r[11] else "") + (f"  outside-window={r[12]}" if r[12] else ""))
    if emb is not None:
        print()
        for p in presets:
            print(f"[{p['id']}] top-8 (plan):")
            for i in p["_top"]:
                m = byid[i]
                tag = "GOLD" if i in p["gold"]["evidence_ids"] else ("dist" if i in p["distractor_ids"] else "    ")
                print(f"   {tag} #{i:<6} {m['date'][:10]} {m['category']:8} {m['subject'][:60]}")
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
