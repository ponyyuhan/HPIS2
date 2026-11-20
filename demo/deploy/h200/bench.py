"""Run the demo's answer models on fixed email fixtures and grade the answers.

Uses the demo's own client (demo/llm.py) and prompt (demo/prompts.py), so the
numbers describe exactly what the demo will do.  Endpoints come from the usual
HPIS_LLM_* environment (OpenRouter by default, the H200 with
HPIS_LLM_BACKEND=h200).

    .venv-demo/bin/python -m demo.deploy.h200.bench qwen gemma --runs 5 \
        --output demo/deploy/h200/results/openrouter.json

Requests run one at a time so latency is not distorted by our own load.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import re
import statistics
import sys
import unicodedata
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from demo import llm, prompts

HERE = Path(__file__).resolve().parent
_THOUSANDS = re.compile(r"(?<=\d)[,，](?=\d{3})")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _THOUSANDS.sub("", text)
    return re.sub(r"\s+", "", text)


def _hit(text: str, needles: list[str]) -> bool:
    return any(normalize(n) in text for n in needles)


def run_check(text: str, check: dict[str, Any]) -> bool:
    if "if_any" in check and not _hit(text, check["if_any"]):
        return True
    if "then_any" in check and not _hit(text, check["then_any"]):
        return False
    if "all" in check and not all(normalize(n) in text for n in check["all"]):
        return False
    if "any" in check and not _hit(text, check["any"]):
        return False
    if "none" in check and _hit(text, check["none"]):
        return False
    if "groups" in check:
        hits = sum(_hit(text, group) for group in check["groups"])
        if hits < check.get("min_hits", len(check["groups"])):
            return False
    return True


def grade(answer: str, fixture: dict[str, Any]) -> dict[str, Any]:
    text = normalize(answer)
    failed = [c["name"] for c in fixture.get("checks", []) if not run_check(text, c)]
    soft = {c["name"]: run_check(text, c) for c in fixture.get("soft", [])}
    report = prompts.citation_report(answer, fixture["evidence"])
    missing = [n for n in fixture.get("must_cite", []) if n not in report["cited"]]
    forbidden = [n for n in fixture.get("forbid_cite", []) if n in report["cited"]]
    return {
        "correct": not failed,
        "failed": failed,
        "soft": soft,
        "citations": report["cited"],
        "citations_valid": report["ok"],
        "citations_expected": report["ok"] and not missing and not forbidden,
        "missing_citations": missing,
        "forbidden_citations": forbidden,
        "chars": len(answer),
        "within_length": len(answer) <= fixture.get("max_chars", 10**9),
    }


def load_fixtures(path: Path, names: list[str] | None = None) -> list[dict[str, Any]]:
    data = json.loads(path.read_text("utf-8"))["fixtures"]
    by_name = {f["name"]: f for f in data}
    for fixture in data:
        if "evidence_from" in fixture:
            fixture["evidence"] = by_name[fixture["evidence_from"]]["evidence"]
    return [f for f in data if not names or f["name"] in names]


async def run_one(profile: str, fixture: dict[str, Any], args: argparse.Namespace, session) -> dict[str, Any]:
    messages = prompts.build_messages(fixture["question"], fixture["evidence"])
    final: dict[str, Any] = {}
    tokens = 0
    async for event in llm.stream_answer(
        profile,
        messages,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        timeout=args.timeout,
        session=session,
        extra_body=args.extra_body,
    ):
        if event["type"] == "token":
            tokens += 1
        else:
            final = event
    answer = final.get("text") or final.get("partial_text") or ""
    row = {
        "profile": profile,
        "fixture": fixture["name"],
        "status": final.get("type"),
        "token_events": tokens,
        "prompt_tokens_estimated": round(sum(prompts.estimate_tokens(m["content"]) for m in messages)),
        **{k: v for k, v in final.items() if k not in ("type", "text", "partial_text")},
        "answer": answer,
    }
    row.update(grade(answer, fixture) if final.get("type") == "done" else {"correct": False})
    return row


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def _column(rows: list[dict[str, Any]], name: str) -> list[float]:
    return [r[name] for r in rows if isinstance(r.get(name), (int, float))]


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for key in dict.fromkeys((r["profile"], r.get("model") or r["profile"]) for r in rows):
        group = [r for r in rows if (r["profile"], r.get("model") or r["profile"]) == key]
        done = [r for r in group if r["status"] == "done"]

        def col(name: str, done: list[dict[str, Any]] = done) -> list[float]:
            return _column(done, name)

        out.append(
            {
                "profile": key[0],
                "model": key[1],
                "runs": len(group),
                "ok": len(done),
                "ttft_p50": pct(col("ttft_ms"), 0.5),
                "ttft_p90": pct(col("ttft_ms"), 0.9),
                "ttft_max": max(col("ttft_ms"), default=None),
                "total_p50": pct(col("total_ms"), 0.5),
                "tps_p50": pct(col("tokens_per_s"), 0.5),
                "out_tokens_mean": round(statistics.mean(col("output_tokens")), 1) if col("output_tokens") else None,
                "in_tokens_mean": round(statistics.mean(col("input_tokens")), 1) if col("input_tokens") else None,
                "reasoning_tokens_max": max(col("reasoning_tokens"), default=0),
                "reasoning_chars_max": max(col("reasoning_chars"), default=0),
                "correct": sum(bool(r.get("correct")) for r in group),
                "citations_valid": sum(bool(r.get("citations_valid")) for r in group),
                "citations_expected": sum(bool(r.get("citations_expected")) for r in group),
                "providers": sorted({str(r.get("provider")) for r in done}),
            }
        )
    return out


def markdown(summary: list[dict[str, Any]]) -> str:
    head = (
        "| model | runs ok | TTFT p50 / p90 / max (ms) | total p50 (ms) | tok/s p50 | out tok | "
        "reasoning tok | correct | citations valid / as expected | providers |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    )
    lines = [head]
    for s in summary:
        lines.append(
            f"| `{s['model']}` | {s['ok']}/{s['runs']} | {s['ttft_p50']} / {s['ttft_p90']} / {s['ttft_max']} | "
            f"{s['total_p50']} | {s['tps_p50']} | {s['out_tokens_mean']} | {s['reasoning_tokens_max']} | "
            f"{s['correct']}/{s['runs']} | {s['citations_valid']} / {s['citations_expected']} | "
            f"{', '.join(s['providers'])} |"
        )
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    fixtures = load_fixtures(args.fixtures, args.fixture)
    rows: list[dict[str, Any]] = []
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    endpoints = {p: llm.resolve_endpoint(p).describe() for p in args.profiles}
    import aiohttp

    async with aiohttp.ClientSession() as session:
        for run in range(args.runs):
            for profile in args.profiles:
                for fixture in fixtures:
                    row = await run_one(profile, fixture, args, session if args.reuse_connection else None)
                    row["run"] = run + 1
                    rows.append(row)
                    flag = "ok " if row.get("correct") else "BAD"
                    print(
                        f"[{flag}] {profile:5s} {fixture['name']:22s} run {run + 1}: "
                        f"ttft={row.get('ttft_ms')} ms total={row.get('total_ms')} ms "
                        f"tok/s={row.get('tokens_per_s')} provider={row.get('provider')} "
                        f"cites={row.get('citations')} failed={row.get('failed') or row.get('message')}",
                        flush=True,
                    )
    summary = summarize(rows)
    table = markdown(summary)
    print()
    print(table)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "started_utc": started,
            "finished_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "endpoints": endpoints,
            "settings": {
                "runs": args.runs,
                "max_tokens": args.max_tokens,
                "temperature": args.temperature,
                "extra_body": args.extra_body,
                "reuse_connection": args.reuse_connection,
            },
            "summary": summary,
            "table": table,
            "rows": rows,
        }
        args.output.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", "utf-8")
        print(f"\nsaved {args.output}")
    return 0 if all(r.get("correct") for r in rows) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("profiles", nargs="+", choices=sorted(llm.PROFILES))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--fixtures", type=Path, default=HERE / "fixtures.json")
    parser.add_argument("--fixture", action="append", help="only this fixture (repeatable)")
    parser.add_argument("--max-tokens", type=int, default=600)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--extra-body", type=json.loads, default=None, help="JSON merged into each request")
    parser.add_argument("--reuse-connection", action="store_true", help="share one HTTP session (keep-alive)")
    parser.add_argument("--output", type=Path)
    return asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
