"""Web server for the demo (面向个人敏感数据的智能问答): static UI + JSON/NDJSON API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
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
            state["warm"] = asyncio.create_task(warm_models())
            state["keepalive"] = asyncio.create_task(keepalive(state))
        except Exception as exc:
            state["phase"] = "error"
            state["error"] = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()

    state["boot"] = asyncio.create_task(boot())


TICKET_RENEW_SECONDS = 6 * 3600
MODEL_WARM_SECONDS = 600


async def warm_models():
    """Ollama unloads a model after keep_alive without requests; load both answer
    models now and every few minutes so a question never waits for a model load."""
    from . import llm

    try:
        for result in await llm.warm_all():
            if not result.get("ok"):
                print(f"model warm-up: {result.get('profile')}: {result.get('message')}", flush=True)
    except Exception:
        traceback.print_exc()


async def keepalive(state):
    """Long-running deployments: renew the one-day anonymous tickets and watch the workers."""
    from hpis.provision import renew

    last_renew = last_warm = time.monotonic()
    warming = None
    while True:
        await asyncio.sleep(5)
        cluster, engine = state.get("cluster"), state.get("engine")
        if cluster is None or engine is None:
            continue
        dead = [p for p in cluster.procs if p.returncode is not None]
        if dead:
            # A crashed HPIS/HPIC/cloud worker would leave the page half broken. Exit so
            # systemd (or the restart loop) brings the whole demo back cleanly.
            print(f"worker exited with {dead[0].returncode}; restarting demo", flush=True)
            os._exit(3)
        if time.monotonic() - last_warm >= MODEL_WARM_SECONDS and (warming is None or warming.done()):
            warming = asyncio.create_task(warm_models())
            last_warm = time.monotonic()
        if time.monotonic() - last_renew >= TICKET_RENEW_SECONDS:
            try:
                for name in cluster.deployments:
                    renew(deploy_root(name), "default")
                engine.refresh_tickets()
                last_renew = time.monotonic()
                print("anonymous tickets renewed", flush=True)
            except Exception:
                traceback.print_exc()


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
        "httpsPort": state.get("https_port"),
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


async def revalidate_static(request, response):
    """Static files: the browser checks for a newer copy on every load (a 304 when unchanged).

    Without a Cache-Control header browsers guess a freshness period and keep using an old file
    after `git pull`, e.g. an icon sprite without the icons the new page asks for.
    """
    if request.method in ("GET", "HEAD") and not request.path.startswith("/api/") and "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"


def make_app():
    app = web.Application()
    app[STATE] = {}
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    app.on_response_prepare.append(revalidate_static)
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


def https_context(folder):
    """TLS for the recording address.

    Browsers let a page record itself (getDisplayMedia) only over https or on
    localhost, and the demo page is opened from other computers by IP.  The
    certificate is self-signed, made once and kept in the runtime folder, so
    the first visit shows a warning to click through.
    """
    import ssl

    cert, key = folder / "cert.pem", folder / "key.pem"
    if not (cert.exists() and key.exists()):
        import datetime
        import ipaddress
        import socket

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        folder.mkdir(parents=True, exist_ok=True)
        ips = {"127.0.0.1"}
        with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))  # picks the outgoing interface; sends nothing
            ips.add(probe.getsockname()[0])
        with contextlib.suppress(OSError):
            ips.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None))
        alt = [x509.DNSName(n) for n in sorted({"localhost", socket.gethostname()})]
        for ip in sorted(ips):
            with contextlib.suppress(ValueError):
                alt.append(x509.IPAddress(ipaddress.ip_address(ip.split("%")[0])))
        pkey = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HPIS demo (self-signed)")])
        now = datetime.datetime.now(datetime.timezone.utc)
        crt = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(pkey.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .sign(pkey, hashes.SHA256())
        )
        key.write_bytes(
            pkey.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        )
        key.chmod(0o600)
        cert.write_bytes(crt.public_bytes(serialization.Encoding.PEM))
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(cert, key)
    return ctx


async def serve(host, port, https_port):
    """The page on http, plus the same page on https for recording; stops on SIGINT/SIGTERM."""
    app = make_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):  # not on Windows
            loop.add_signal_handler(sig, stop.set)
    try:
        await web.TCPSite(runner, host, port).start()
        print(f"demo → http://{host}:{port}/", flush=True)
        if https_port:
            try:
                ctx = await loop.run_in_executor(None, https_context, config.RUNTIME / "https")
                await web.TCPSite(runner, host, https_port, ssl_context=ctx).start()
                app[STATE]["https_port"] = https_port
                shown = "<this host>" if host in ("0.0.0.0", "::") else host
                print(f"recording address → https://{shown}:{https_port}/", flush=True)
            except (OSError, ValueError, ImportError) as exc:
                print(f"https on port {https_port} not started ({exc}); in-page recording needs it", flush=True)
        await stop.wait()
    finally:
        await runner.cleanup()


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="面向个人敏感数据的智能问答 演示")
    parser.add_argument("--port", type=int, default=config.WEB_PORT)
    parser.add_argument(
        "--https-port", type=int, default=config.WEB_HTTPS_PORT,
        help="also serve the page over https here, so it can be recorded from another computer (0: off)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true", help="open the browser")
    args = parser.parse_args(argv)
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    if args.open:
        import threading

        threading.Timer(1.5, webbrowser.open, args=(f"http://{args.host}:{args.port}/",)).start()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(args.host, args.port, args.https_port))


if __name__ == "__main__":
    main()
