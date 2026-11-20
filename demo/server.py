"""Web server for the HPIS 2.0 demo: static UI + JSON/NDJSON API."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
import traceback
import webbrowser

from aiohttp import web

from . import config
from .build import build as build_deployments
from .build import current_build
from .cluster import Cluster, deploy_root
from .engine import Engine
from .mailbox import Mailbox, card

STATE = web.AppKey("state", dict)


def ndjson(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def model_options():
    try:
        from . import llm

        profiles = getattr(llm, "describe_profiles", None)
        if profiles:
            return profiles()
    except Exception:
        pass
    return [
        {"id": "qwen", "label": "Qwen3.6-27B"},
        {"id": "gemma", "label": "Gemma 4 31B"},
    ]


def acceptance_summary():
    path = config.ACCEPTANCE
    if path.exists():
        try:
            return json.loads(path.read_text("utf-8"))
        except ValueError:
            return None
    return None


async def startup(app):
    state = app[STATE]
    state.update(phase="starting", progress={}, error=None)

    async def boot():
        try:
            from .datafiles import ensure_data

            state["phase"] = "unpacking"
            await asyncio.get_running_loop().run_in_executor(None, ensure_data)
            mailbox = Mailbox()
            state["mailbox"] = mailbox
            from .build import UPLOAD_FORMAT

            build = current_build()
            if build is None or build.get("upload", {}).get("format") != UPLOAD_FORMAT:
                state["phase"] = "building"

                def progress(stage, n):
                    state["progress"][stage] = n

                build = await build_deployments(progress=progress)
            state["build"] = build
            state["phase"] = "starting-services"
            cluster = await Cluster().start()
            state["cluster"] = cluster
            engine = await Engine(cluster, mailbox, build).open()
            state["phase"] = "warming"
            await engine.warmup()
            state["engine"] = engine
            state["phase"] = "ready"
        except Exception as exc:
            state["phase"] = "error"
            state["error"] = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()

    state["boot"] = asyncio.create_task(boot())


async def cleanup(app):
    state = app[STATE]
    engine = state.get("engine")
    if engine:
        await engine.close()
    cluster = state.get("cluster")
    if cluster:
        await cluster.stop()


async def index(request):
    return web.FileResponse(config.WEB / "index.html", headers={"Cache-Control": "no-store"})


async def status(request):
    state = request.app[STATE]
    mailbox = state.get("mailbox")
    build = state.get("build") or {}
    body = {
        "phase": state.get("phase"),
        "progress": state.get("progress"),
        "error": state.get("error"),
        "models": model_options(),
        "defaultModel": config.DEFAULT_MODEL,
        "network": config.Network().describe(),
        "asOf": config.AS_OF.isoformat(),
        "evidenceK": config.EVIDENCE_K,
        "build": {k: v for k, v in build.items() if k not in ("signature",)},
    }
    if mailbox is not None:
        try:
            body["mailbox"] = mailbox.summary
            body["presets"] = [
                {"id": p["id"], "chip": p.get("chip", p["id"]), "question": p["question"], "gold": p.get("gold", {})}
                for p in mailbox.presets
            ]
        except FileNotFoundError:
            body["mailbox"] = None
    return web.json_response(body, dumps=lambda o: json.dumps(o, ensure_ascii=False))


async def run(request):
    state = request.app[STATE]
    engine: Engine | None = state.get("engine")
    if engine is None:
        raise web.HTTPServiceUnavailable(text="demo is still starting")
    payload = await request.json()
    question = (payload.get("question") or "").strip()[:300]
    if not question:
        raise web.HTTPBadRequest(text="empty question")
    model = payload.get("model") or config.DEFAULT_MODEL
    allowed = ("plain", "v1", "v2") if "tee-v1" in engine.cluster.hpic_url else ("plain", "v2")
    lanes = tuple(l for l in payload.get("lanes", ["plain", "v2"]) if l in allowed)
    net = config.Network.from_dict(payload.get("net"))
    response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-store"})
    await response.prepare(request)
    try:
        async for event in engine.run(question, model, lanes, net):
            await response.write(ndjson(event))
    except (ConnectionResetError, asyncio.CancelledError):
        raise
    except Exception as exc:
        traceback.print_exc()
        await response.write(ndjson({"type": "error", "message": f"{type(exc).__name__}: {exc}"}))
    await response.write_eof()
    return response


async def mail(request):
    mailbox: Mailbox = request.app[STATE]["mailbox"]
    try:
        value = mailbox.get(int(request.match_info["id"]))
    except (KeyError, ValueError):
        raise web.HTTPNotFound()
    return web.json_response(value, dumps=lambda o: json.dumps(o, ensure_ascii=False))


async def cloudview(request):
    """What an operator of each cloud can read: plaintext uploads vs HPIS storage rows."""
    state = request.app[STATE]
    mailbox: Mailbox = state["mailbox"]
    kind = request.query.get("kind", "hpis")
    limit = min(int(request.query.get("limit", "40")), 200)
    if kind == "plain":
        ids = mailbox.ids
        rng = __import__("random").Random(int(request.query.get("seed", "7")))
        pool = range(len(ids))
        if request.query.get("sensitive") == "1":
            # What a plain-cloud operator would notice first: health, money, family, travel.
            cats = mailbox.categories
            pool = [i for i in range(len(ids)) if cats[i] in ("health", "finance", "family", "school", "travel")] or pool
        picks = sorted(rng.sample(list(pool), min(limit, len(pool))))
        rows = [card(mailbox.get(int(ids[i])), snippet_chars=120) for i in picks]
        return web.json_response({"rows": rows}, dumps=lambda o: json.dumps(o, ensure_ascii=False))
    db = sqlite3.connect(f"file:{deploy_root('tee') / 'hpis' / 'store.sqlite'}?mode=ro", uri=True)
    try:
        offset = int(request.query.get("offset", "0"))
        docs = db.execute(
            "SELECT aid, rid, substr(body,1,48), length(body) FROM docs ORDER BY rid LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        out = []
        for aid, rid, head, size in docs:
            tags = db.execute(
                "SELECT tag FROM postings WHERE aid=? AND rid=? LIMIT 4", (aid, rid)
            ).fetchall()
            vec = db.execute(
                "SELECT substr(original,1,24), length(original) FROM vectors WHERE aid=? AND rid=?", (aid, rid)
            ).fetchone()
            out.append(
                {
                    "aid": aid.hex(),
                    "rid": rid,
                    "body": head.hex(),
                    "bodyBytes": size,
                    "tags": [t[0].hex() for t in tags],
                    "vector": vec[0].hex() if vec else "",
                    "vectorBytes": vec[1] if vec else 0,
                }
            )
        totals = db.execute("SELECT count(*), sum(length(body)) FROM docs").fetchone()
        postings = db.execute("SELECT count(*) FROM postings").fetchone()[0]
    finally:
        db.close()
    return web.json_response({"rows": out, "docs": totals[0], "bytes": totals[1], "postings": postings})


async def acceptance(request):
    from .bench_view import load_view

    view = await asyncio.get_running_loop().run_in_executor(None, load_view, config.ACCEPTANCE)
    return web.json_response(view or {}, dumps=lambda o: json.dumps(o, ensure_ascii=False))


def _store():
    return sqlite3.connect(f"file:{deploy_root('tee') / 'hpis' / 'store.sqlite'}?mode=ro", uri=True)


async def cipher(request):
    """The stored form of one email inside HPIS (read straight from the store file)."""
    rid = int(request.match_info["id"])
    db = _store()
    try:
        row = db.execute("SELECT aid, substr(body,1,192), length(body) FROM docs WHERE rid=? LIMIT 1", (rid,)).fetchone()
        if row is None:
            raise web.HTTPNotFound()
        aid, head, size = row
        tags = [t[0].hex() for t in db.execute("SELECT tag FROM postings WHERE aid=? AND rid=?", (aid, rid))]
        vec = db.execute("SELECT substr(original,1,96), length(original) FROM vectors WHERE aid=? AND rid=?", (aid, rid)).fetchone()
    finally:
        db.close()
    return web.json_response(
        {
            "aid": aid.hex(),
            "rid": rid,
            "body": head.hex(),
            "bodyBytes": size,
            "tags": tags,
            "vector": vec[0].hex() if vec else "",
            "vectorBytes": vec[1] if vec else 0,
        }
    )


def _spy(mailbox: Mailbox, q: str) -> dict:
    raw = q.encode()
    escaped = json.dumps(q)[1:-1].encode()
    hits, samples = 0, []
    for line in mailbox.iter_lines():
        if raw in line or escaped in line:
            hits += 1
            if len(samples) < 24:
                samples.append(card(json.loads(line), snippet_chars=120))
    db = _store()
    hpis_hits = hpis_bytes = 0
    try:
        for (body,) in db.execute("SELECT body FROM docs"):
            hpis_bytes += len(body)
            if raw in body or escaped in body:
                hpis_hits += 1
    finally:
        db.close()
    return {"q": q, "plain_hits": hits, "samples": samples, "hpis_hits": hpis_hits, "hpis_bytes": hpis_bytes}


async def spy(request):
    """An operator greps both clouds for a plaintext word."""
    state = request.app[STATE]
    q = request.query.get("q", "").strip()[:40]
    if not q:
        raise web.HTTPBadRequest()
    cache = state.setdefault("spy", {})
    if q not in cache:
        cache[q] = await asyncio.get_running_loop().run_in_executor(None, _spy, state["mailbox"], q)
    return web.json_response(cache[q], dumps=lambda o: json.dumps(o, ensure_ascii=False))


def make_app():
    app = web.Application()
    app[STATE] = {}
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    app.router.add_get("/", index)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/run", run)
    app.router.add_get("/api/mail/{id}", mail)
    app.router.add_get("/api/cloudview", cloudview)
    app.router.add_get("/api/acceptance", acceptance)
    app.router.add_get("/api/cipher/{id}", cipher)
    app.router.add_get("/api/spy", spy)
    app.router.add_static("/", config.WEB, show_index=False, append_version=False)
    return app


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="HPIS 2.0 密态智能问答演示")
    parser.add_argument("--port", type=int, default=config.WEB_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true", help="open the browser")
    args = parser.parse_args(argv)
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    if args.open:
        import threading

        threading.Timer(1.5, webbrowser.open, args=(f"http://{args.host}:{args.port}/",)).start()
    print(f"HPIS 2.0 demo → http://{args.host}:{args.port}/", flush=True)
    web.run_app(make_app(), host=args.host, port=args.port, access_log=None, print=None)


if __name__ == "__main__":
    main()
