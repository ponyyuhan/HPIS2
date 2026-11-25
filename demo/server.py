"""Web server for the demo (面向个人敏感数据的智能问答): static UI + JSON/NDJSON API.

Endpoints (PLAN-v3 §3; older fields are kept):
  GET  /api/status                 phase, models (+vision), presets (+kind, device), devices, build …
  POST /api/run                    NDJSON events of one question (plan, notice, stage, …, finish, end)
  POST /api/stop                   stop the run in progress -> {"ok", "stopped"}
  GET  /api/mail/{id}              one email (plaintext, the PC's own copy)
  GET  /api/record/{kind}/{id}     one record of any kind (plaintext, the device's own copy)
  GET  /api/photo/{id}?size=       the phone's own JPEG (thumb | full)
  GET  /api/cipher/{id}?kind=      the same record inside HPIS (AID, ciphertext, tokens, vector)
  GET  /api/cloudview?kind=plain   what the ordinary cloud reads (real name, all kinds)
  GET  /api/cloudview?kind=hpis    what HPIS stores (many anonymous AIDs)
  GET  /api/spy?q=                 an operator greps both clouds for a word
  GET  /api/acceptance             the Linux acceptance results
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import signal
import time
import traceback
import webbrowser

from aiohttp import web

from . import config, records
from .build import build as build_deployments
from .build import current_build
from .cluster import Cluster, deploy_root, device_folder
from .devices import BY_ID, BY_KIND, Catalog
from .engine import Engine
from .mailbox import Mailbox

STATE = web.AppKey("state", dict)


def ndjson(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def jsonify(obj, status: int = 200):
    return web.json_response(obj, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


def model_options():
    try:
        from . import llm

        profiles = getattr(llm, "describe_profiles", None)
        if profiles:
            return profiles()
    except Exception:
        pass
    return [
        {"id": "qwen", "label": "Qwen3.6-27B", "vision": None},
        {"id": "gemma", "label": "Gemma 4 31B", "vision": None},
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
        loop = asyncio.get_running_loop()
        try:
            from .datafiles import ensure_data

            state["phase"] = "unpacking"
            await loop.run_in_executor(None, ensure_data)
            mailbox = Mailbox()
            state["mailbox"] = mailbox
            catalog = await loop.run_in_executor(None, Catalog, mailbox)
            build = await loop.run_in_executor(None, current_build, catalog)
            if build is None:
                state["phase"] = "building"

                def progress(stage, n):
                    state["progress"][stage] = n

                build = await build_deployments(progress=progress)
                catalog = await loop.run_in_executor(None, Catalog, mailbox)
            state["build"] = build
            state["phase"] = "starting-services"
            cluster = await Cluster().start()
            state["cluster"] = cluster
            engine = await Engine(cluster, mailbox, build, catalog).open()
            state["catalog"] = engine.catalog
            state["phase"] = "warming"
            await engine.warmup()
            state["store"] = await loop.run_in_executor(None, store_view, engine)
            state["engine"] = engine
            state["phase"] = "ready"
            state["warm"] = asyncio.create_task(warm_models())
            state["keepalive"] = asyncio.create_task(keepalive(state))
            state["spy_warm"] = asyncio.create_task(prime_spy(state))
        except Exception as exc:
            state["phase"] = "error"
            state["error"] = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()

    state["boot"] = asyncio.create_task(boot())


SPY_DEFAULT = "体检报告"  # the word the 云端视角 search box starts with (index.html)


async def prime_spy(state):
    """Answer the page's default operator search once in the background after start-up.

    The first search reads both stores from disk (about 1.6 GB, 10 s or more when cold); doing it
    here leaves the answer cached and the files in the OS cache, so the first search on stage is instant."""
    view, catalog = state.get("store"), state.get("catalog")
    if view is None or catalog is None:
        return
    warm = state.get("warm")
    if warm is not None:
        # after the model warm-up: this reading holds the interpreter for seconds and must not delay
        # the vision probe (a probe that times out leaves photos as captions until it is asked again)
        await asyncio.wait([warm])
    loop = asyncio.get_running_loop()
    try:
        (plain_hits, samples), scan = await asyncio.gather(
            loop.run_in_executor(None, _plain_hits, catalog, SPY_DEFAULT),
            loop.run_in_executor(None, view.scan, SPY_DEFAULT),
        )
        state.setdefault("spy", {})[SPY_DEFAULT] = {"plain_hits": plain_hits, "samples": samples, "scan": scan}
    except Exception:  # only a warm-up; a search on stage still works without it
        traceback.print_exc()


def store_view(engine: Engine):
    from .storeview import StoreView

    mine = {}
    for (key, tenant), device in engine.tenants.items():
        if key == "tee":
            info = next((d for d in BY_ID.values() if d.tenant == tenant), None)
            if info is not None:
                mine[device.aid] = info.id
    return StoreView(deploy_root("tee") / "hpis" / "store.sqlite", mine, cache=config.RUNTIME / "storeview.npz")


TICKET_RENEW_SECONDS = 6 * 3600
MODEL_WARM_SECONDS = 600


async def warm_models():
    """Ollama unloads a model after keep_alive without requests; load both answer
    models now and every few minutes so a question never waits for a model load.
    Also asks each model server whether its model accepts images."""
    from . import llm

    try:
        await llm.probe_vision_all(refresh=True)  # renewed with every warm-up (10 min)
    except Exception:
        traceback.print_exc()
    try:
        for result in await llm.warm_all():
            if not result.get("ok"):
                print(f"model warm-up: {result.get('profile')}: {result.get('message')}", flush=True)
    except Exception:
        traceback.print_exc()


async def keepalive(state):
    """Long-running deployments: renew the one-day anonymous tickets and watch the workers."""
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
                cluster.renew_tickets()
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


# ------------------------------------------------------------------ status

EXPOSURE = {
    "mail": "普通云可读取：全部 {n} 封邮件及附件的正文、收发人和时间",
    "photo": "普通云可读取：全部 {n} 张照片及拍摄地点、时间",
    "sport": "普通云可读取：全部 {n} 条运动与健康记录及时间",
    "car": "普通云可读取：全部 {n} 条用车记录及行程起止地点、时间",
}


def device_rows(state) -> list[dict]:
    """The user's devices: one anonymous AID each, what they hold, what a plain cloud would read."""
    from hpis.provision import read_config

    catalog: Catalog | None = state.get("catalog")
    build = state.get("build") or {}
    if catalog is None:
        return []
    uploads = build.get("uploads") or {"mail": build.get("upload") or {}}
    rows = []
    for info in catalog.devices:
        kind = info.kind
        try:
            aid = read_config(device_folder("tee", info.tenant))["aid"].hex()
        except (OSError, KeyError, ValueError):
            aid = ""
        n = catalog.count(kind)
        summary = catalog.mailbox.summary if kind == "mail" else catalog.sets[kind].summary
        size = (uploads.get(kind) or {}).get("bytes") or summary.get("bytes") or summary.get("text_bytes") or 0
        rows.append(
            {
                "id": info.id,
                "label": info.label,
                "kind": kind,
                "kind_label": info.kind_label,
                "unit": records.UNIT[kind],
                "aid": aid,
                "aid_short": aid[:8],
                "records": n,
                "bytes": int(size),
                "first": summary.get("first"),
                "last": summary.get("last"),
                "exposure": EXPOSURE[kind].format(n=f"{n:,}"),
            }
        )
    return rows


def preset_rows(state) -> list[dict]:
    catalog: Catalog | None = state.get("catalog")
    mailbox: Mailbox | None = state.get("mailbox")
    presets = catalog.presets() if catalog is not None else [
        {**p, "kind": "mail", "device": "pc"} for p in (mailbox.presets if mailbox else [])
    ]
    return [
        {
            "id": p["id"],
            "chip": p.get("chip", p["id"]),
            "question": p["question"],
            "gold": p.get("gold", {}),
            "kind": p.get("kind", "mail"),
            "device": p.get("device", "pc"),
        }
        for p in presets
    ]


async def status(request):
    state = request.app[STATE]
    mailbox = state.get("mailbox")
    build = state.get("build") or {}
    engine = state.get("engine")
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
        "evidenceKByKind": {k: config.evidence_k(k) for k in records.KINDS},
        "limits": {
            "runS": config.RUN_LIMIT_S,
            "firstTokenS": config.FIRST_TOKEN_S,
            "answerS": config.ANSWER_LIMIT_S,
            "cloudEmbedBudgetS": config.CLOUD_EMBED_BUDGET_S,
            "cloudShortlist": config.CLOUD_SHORTLIST,
        },
        "running": bool(engine and engine.active and not engine.active.done.is_set()),
        "build": {k: v for k, v in build.items() if k not in ("signature",)},
        "devices": device_rows(state),
    }
    if mailbox is not None:
        try:
            body["mailbox"] = mailbox.summary
            body["presets"] = preset_rows(state)
        except FileNotFoundError:
            body["mailbox"] = None
    return jsonify(body)


# ------------------------------------------------------------------ runs


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
    lanes = tuple(lane for lane in payload.get("lanes", ["plain", "v2"]) if lane in allowed)
    net = config.Network.from_dict(payload.get("net"))
    response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-store"})
    await response.prepare(request)
    try:
        # Closing the stream (page reloaded, connection lost) stops the run it started.
        async with contextlib.aclosing(engine.run(question, model, lanes, net)) as events:
            async for event in events:
                await response.write(ndjson(event))
    except (ConnectionResetError, asyncio.CancelledError):
        raise
    except Exception as exc:
        traceback.print_exc()
        await response.write(ndjson({"type": "error", "message": f"{type(exc).__name__}: {exc}"}))
        await response.write(ndjson({"type": "end", "stopped": True, "timed_out": False, "reason": "error"}))
    await response.write_eof()
    return response


async def stop(request):
    """End the run in progress: every lane gets finish {stopped}, the stream end {stopped}."""
    engine: Engine | None = request.app[STATE].get("engine")
    stopped = await engine.stop() if engine is not None else False
    return jsonify({"ok": True, "stopped": stopped})


# ------------------------------------------------------------------ records


async def mail(request):
    mailbox: Mailbox = request.app[STATE]["mailbox"]
    try:
        value = mailbox.get(int(request.match_info["id"]))
    except (KeyError, ValueError):
        raise web.HTTPNotFound()
    return jsonify(value)


def _record_or_404(state, kind: str, rid: str) -> dict:
    catalog: Catalog | None = state.get("catalog")
    if catalog is None or not catalog.has(kind):
        raise web.HTTPNotFound(text="unknown kind")
    try:
        return catalog.get(kind, int(rid))
    except (KeyError, ValueError):
        raise web.HTTPNotFound()


async def record(request):
    """One record as the device holds it (for the drawer)."""
    state = request.app[STATE]
    kind = request.match_info["kind"]
    rec = _record_or_404(state, kind, request.match_info["id"])
    if kind == "mail":
        return jsonify(rec)
    out = {k: v for k, v in rec.items() if k != "image"}
    info = BY_KIND[kind]
    out.update(kind=kind, device=info.id, device_label=info.label, kind_label=info.kind_label)
    if kind == "photo":
        out["thumb_url"] = records.photo_url(rec["id"], "thumb")
        out["full_url"] = records.photo_url(rec["id"], "full")
        out["place_text"] = records.place_text(rec)
    return jsonify(out)


async def photo(request):
    """The phone's own copy of a picture (thumb: long side 360 px, full: 960 px)."""
    state = request.app[STATE]
    rec = _record_or_404(state, "photo", request.match_info["id"])
    size = "thumb" if request.query.get("size", "thumb") == "thumb" else "full"
    path = state["catalog"].sets["photo"].image_path(rec["id"], size)
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": "image/jpeg", "Cache-Control": "private, max-age=600"})


async def cipher(request):
    """The stored form of one record inside HPIS (read straight from the store file)."""
    from hpis.provision import read_config

    state = request.app[STATE]
    kind = request.query.get("kind", "mail")
    if kind not in records.KINDS:
        raise web.HTTPBadRequest(text="unknown kind")
    try:
        rid = int(request.match_info["id"])
        aid = read_config(device_folder("tee", BY_KIND[kind].tenant))["aid"]
    except (ValueError, OSError, KeyError):
        raise web.HTTPNotFound()
    view = state.get("store")
    if view is None:
        raise web.HTTPServiceUnavailable(text="demo is still starting")
    row = await asyncio.get_running_loop().run_in_executor(None, view.cipher, aid, rid)
    if row is None:
        raise web.HTTPNotFound()
    row["kind"] = kind
    if kind == "photo":
        path = state["catalog"].sets["photo"].image_path(rid, "full") if "photo" in state["catalog"].sets else None
        row["imageBytes"] = path.stat().st_size if path else 0
    return jsonify(row)


# ------------------------------------------------------------------ cloud views


def _sample_kind(catalog: Catalog, kind: str, n: int, rng: random.Random, sensitive: bool) -> list[dict]:
    if n <= 0:
        return []
    if kind == "mail":
        mailbox = catalog.mailbox
        ids = mailbox.ids
        pool = range(len(ids))
        if sensitive:
            # What a plain-cloud operator would notice first: health, money, family, travel.
            cats = mailbox.categories
            pool = [i for i in range(len(ids)) if cats[i] in ("health", "finance", "family", "school", "travel")] or pool
        picks = sorted(rng.sample(list(pool), min(n, len(pool))))
        return [records.plain_row("mail", mailbox.get(int(ids[i]))) for i in picks]
    rs = catalog.sets[kind]
    picks = rng.sample(range(rs.count), min(n, rs.count))
    rows = [records.plain_row(kind, rs.records[i]) for i in picks]
    return sorted(rows, key=lambda r: r["date"], reverse=True)


def _interleave(groups: list[list[dict]], limit: int) -> list[dict]:
    out = []
    while len(out) < limit and any(groups):
        for group in groups:
            if group and len(out) < limit:
                out.append(group.pop(0))
    return out


def plain_rows(catalog: Catalog, limit: int, seed: int, sensitive: bool, kinds: list[str] | None = None) -> list[dict]:
    """Real-name rows across the user's devices, mixed so every kind shows up."""
    rng = random.Random(seed)
    kinds = [k for k in (kinds or catalog.kinds) if catalog.has(k)] or ["mail"]
    if kinds == ["mail"]:
        shares = {"mail": limit}
    else:
        others = [k for k in kinds if k != "mail"]
        mail_share = limit // 2 if "mail" in kinds else 0
        shares = {"mail": mail_share} if "mail" in kinds else {}
        for i, kind in enumerate(others):
            shares[kind] = (limit - mail_share) // len(others) + (1 if i < (limit - mail_share) % len(others) else 0)
    groups = [_sample_kind(catalog, k, shares.get(k, 0), rng, sensitive) for k in kinds]
    rows = _interleave(groups, limit)
    for row in rows:
        row["uid"] = f"{row['kind']}:{row['id']}"
    return rows


async def cloudview(request):
    """What an operator of each cloud can read: plaintext uploads vs HPIS storage rows."""
    state = request.app[STATE]
    kind = request.query.get("kind", "hpis")
    try:
        limit = max(1, min(int(request.query.get("limit", "40")), 200))
    except ValueError:
        raise web.HTTPBadRequest(text="limit")
    loop = asyncio.get_running_loop()
    if kind == "plain":
        catalog: Catalog = state.get("catalog") or Catalog(state["mailbox"])
        seed = int(request.query.get("seed", "7"))
        sensitive = request.query.get("sensitive") == "1"
        only = [k for k in request.query.get("kinds", "").split(",") if k] or None
        rows = await loop.run_in_executor(None, plain_rows, catalog, limit, seed, sensitive, only)
        total = sum(catalog.count(k) for k in catalog.kinds)
        return jsonify({"rows": rows, "total": total, "kinds": {k: catalog.count(k) for k in catalog.kinds}})
    view = state.get("store")
    if view is None:
        raise web.HTTPServiceUnavailable(text="demo is still starting")
    seed = request.query.get("seed")
    rows = await loop.run_in_executor(None, view.sample, limit, int(seed) if seed else None)
    return jsonify(
        {
            "rows": rows,
            "docs": view.docs,
            "bytes": view.bytes,
            "postings": view.postings,
            "tenants": view.tenants(),
        }
    )


async def acceptance(request):
    from .bench_view import load_view

    view = await asyncio.get_running_loop().run_in_executor(None, load_view, config.ACCEPTANCE)
    return jsonify(view or {})


def _plain_hits(catalog: Catalog, q: str) -> tuple[int, list[dict]]:
    """Grep the plaintext the ordinary cloud receives (every device's data) for the word."""
    raw = q.encode()
    escaped = json.dumps(q)[1:-1].encode()
    hits, groups = 0, []
    sources = [("mail", catalog.mailbox.iter_lines())] + [(k, rs.iter_lines()) for k, rs in catalog.sets.items()]
    for kind, lines in sources:
        samples = []
        for line in lines:
            if raw in line or escaped in line:
                hits += 1
                if len(samples) < 24:
                    samples.append(records.plain_row(kind, json.loads(line)))
        groups.append(samples)
    rows = _interleave(groups, 24)
    for row in rows:
        row["uid"] = f"{row['kind']}:{row['id']}"
    return hits, rows


async def spy(request):
    """An operator greps both clouds for a plaintext word; HPIS shows a fresh sample of what it scanned."""
    state = request.app[STATE]
    q = request.query.get("q", "").strip()[:40]
    if not q:
        raise web.HTTPBadRequest()
    view = state.get("store")
    catalog = state.get("catalog")
    if view is None or catalog is None:
        raise web.HTTPServiceUnavailable(text="demo is still starting")
    loop = asyncio.get_running_loop()
    cache = state.setdefault("spy", {})
    if q not in cache:
        (plain_hits, samples), scan = await asyncio.gather(  # both clouds are searched at the same time
            loop.run_in_executor(None, _plain_hits, catalog, q),
            loop.run_in_executor(None, view.scan, q),
        )
        cache[q] = {"plain_hits": plain_hits, "samples": samples, "scan": scan}
        if len(cache) > 256:
            cache.pop(next(iter(cache)))
    hit = cache[q]
    rows = await loop.run_in_executor(None, view.probe_rows, q, 24, None)
    scan = hit["scan"]
    tenants = len(view.order)
    return jsonify(
        {
            "q": q,
            "plain_hits": hit["plain_hits"],
            "samples": hit["samples"],
            "hpis_scanned": scan["scanned"],
            "hpis_bytes": scan["bytes"],
            "hpis_hits": scan["hits"],
            "hpis_rows": [{**r, "matched": r["matched"]} for r in rows],
            "tenants": tenants,
            "note": f"HPIS 中 {tenants} 个匿名租户共 {scan['scanned']:,} 条记录，只有 AES-256-GCM 密文、"
            f"HMAC 检索令牌和向量密文，没有可与「{q}」比对的明文：命中 {scan['hits']} 条。",
        }
    )


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
    app.router.add_post("/api/stop", stop)
    app.router.add_get("/api/mail/{id}", mail)
    app.router.add_get("/api/record/{kind}/{id}", record)
    app.router.add_get("/api/photo/{id}", photo)
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
    # A page that goes away (reload, lost connection) cancels its /api/run handler, which stops the run.
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
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
