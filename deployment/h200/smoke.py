"""Exercise the showcase's actual model client on one synthetic attachment citation."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from showcase import corpus
from showcase.local_model import model_answer


PROVIDERS = {
    "qwen": ("qwen/qwen3.6-27b", "hpis-qwen-27b", 18081),
    "gemma": ("google/gemma-4-31b-it", "hpis-gemma-4", 18082),
}


async def check(profile: str, openrouter: bool, timeout: float):
    remote_id, local_alias, port = PROVIDERS[profile]
    prefix = "HPIS_SHOWCASE_" + profile.upper()
    if openrouter:
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise SystemExit("OPENROUTER_API_KEY is required for --openrouter")
        os.environ[prefix + "_BASE_URL"] = "https://openrouter.ai/api/v1"
        os.environ[prefix + "_MODEL"] = remote_id
        os.environ[prefix + "_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
    else:
        os.environ.setdefault(prefix + "_BASE_URL", f"http://127.0.0.1:{port}/v1")
        os.environ.setdefault(prefix + "_MODEL", local_alias)

    question = "星澜项目最终的上线时间是什么？"
    plan = corpus.plan_query(question)
    evidence = [
        corpus.message_content(row)
        for row in corpus.CASES
        if corpus.matches_plan(row, plan)
    ]
    reference = corpus.make_answer(question, evidence, plan)
    answer, meta = await model_answer(
        question, evidence, reference, profile=profile, timeout=timeout
    )
    verified = meta["status"] == "verified"
    sections = answer["sections"] if verified else []
    quotes = [quote for point in sections for quote in point["quotes"]]
    correct = verified and all(
        any(
            fact["id"] == quote["id"]
            and fact["attachment"] == quote["attachment"]
            and fact["text"] in quote["text"]
            for quote in quotes
        )
        for point in reference["sections"]
        for fact in point["quotes"]
    )
    return {
        "profile": profile,
        "requested_model": os.environ[prefix + "_MODEL"],
        "requested_question": question,
        "retrieved_synthetic_email_ids": [row["id"] for row in evidence],
        "verified_model_answer": bool(correct),
        "verified_model_quotes": quotes,
        "model_call": meta,
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profiles", nargs="+", choices=tuple(PROVIDERS))
    parser.add_argument(
        "--openrouter",
        action="store_true",
        help="send one synthetic mail to each requested OpenRouter model",
    )
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    record = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint_type": "OpenRouter" if args.openrouter else "operator-configured",
        "scope": "one real chat-completions request per listed profile; synthetic email and attachment only",
        "observations": [
            await check(profile, args.openrouter, args.timeout)
            for profile in args.profiles
        ],
        "finished_utc": datetime.now(timezone.utc).isoformat(),
    }
    output = json.dumps(record, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return (
        0 if all(row["verified_model_answer"] for row in record["observations"]) else 1
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
