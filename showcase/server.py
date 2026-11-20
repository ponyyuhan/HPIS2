"""Browser-facing presentation server plus local, independent data-plane processes."""

import argparse
import asyncio
from pathlib import Path
import socket
import sys
from urllib.parse import quote

from aiohttp import web

from hpis.network import tls_server

from .backend import Showcase, answering_hpic, json_line, plaintext_app, traced_store
from . import corpus
from .local_model import model_profiles


STATIC = Path(__file__).with_name("static")


def cache_default():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "hpis-showcase"
    import os

    return (
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "hpis-showcase"
    )


def application(engine):
    app = web.Application(client_max_size=16 << 10)

    async def lifecycle(_):
        engine.preparing = asyncio.create_task(engine.start())
        try:
            yield
        finally:
            await engine.close()

    async def bootstrap(_):
        return web.json_response(engine.bootstrap())

    async def run(request):
        if not engine.ready:
            return web.json_response(
                {"error": engine.error or "演示邮箱正在准备中"}, status=503
            )
        try:
            payload = await request.json()
            question = payload["question"]
            if not isinstance(question, str) or not 1 <= len(question.strip()) <= 600:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return web.json_response(
                {"error": "请输入 1 至 600 个字符的问题"}, status=400
            )
        profile = payload.get("model", "extractive")
        profiles = {item["id"]: item for item in model_profiles()}
        if not isinstance(profile, str) or profile not in profiles:
            return web.json_response(
                {"error": "请选择本地证据抽取、Qwen 27B 或 Gemma 4"}, status=400
            )
        if not profiles[profile]["configured"]:
            return web.json_response(
                {
                    "error": f"{profiles[profile]['label']} 尚未配置服务地址和模型名。",
                    "code": "model-unconfigured",
                    "model": profile,
                },
                status=409,
            )
        resp = web.StreamResponse(
            headers={
                "Content-Type": "application/x-ndjson; charset=utf-8",
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            }
        )
        await resp.prepare(request)

        events = asyncio.Queue()

        async def enqueue(item):
            # Browser playback and a slow display connection should not enter path timings.
            events.put_nowait(item)

        async def produce():
            try:
                data = await engine.compare(question, enqueue, profile)
                await enqueue({"type": "complete", "run": data})
            except asyncio.CancelledError:
                raise
            except Exception:
                import logging

                logging.getLogger(__name__).exception("showcase comparison failed")
                await enqueue(
                    {"type": "error", "message": "比较未完成，请检查本地服务后重试。"}
                )

        worker = asyncio.create_task(produce())
        try:
            while True:
                item = await events.get()
                await resp.write(json_line(item))
                if item["type"] in ("complete", "error"):
                    break
            await resp.write_eof()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        return resp

    async def evidence(request):
        obj = (
            engine.archive.mail(int(request.match_info["rid"]))
            if engine.archive
            else None
        )
        if not obj:
            raise web.HTTPNotFound(text="没有这封邮件")
        return web.json_response(obj)

    async def attachment(request):
        obj = (
            engine.archive.mail(int(request.match_info["rid"]))
            if engine.archive
            else None
        )
        idx = int(request.match_info["idx"])
        if not obj or idx >= len(obj["attachments"]):
            raise web.HTTPNotFound()
        item = obj["attachments"][idx]
        return web.Response(
            body=item["text"].encode(),
            headers={
                "Content-Type": item["mime"]
                + ("; charset=utf-8" if item["mime"].startswith("text/") else ""),
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(item["name"], safe=""),
            },
        )

    async def library(request):
        if not engine.archive:
            return web.json_response(
                {"items": [], "total": 0, "offset": 0, "limit": 12}
            )
        try:
            offset = max(0, int(request.query.get("offset", 0)))
            limit = min(60, max(1, int(request.query.get("limit", 12))))
        except ValueError:
            raise web.HTTPBadRequest(text="分页参数无效")
        q = request.query.get("q", "").strip()
        return web.json_response(engine.archive.library(q, offset, limit))

    async def index(_):
        return web.FileResponse(
            STATIC / "index.html", headers={"Cache-Control": "no-cache"}
        )

    async def assets(request):
        path = (STATIC / request.match_info["filename"]).resolve()
        if not path.is_relative_to(STATIC.resolve()) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "no-cache"})

    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/api/bootstrap", bootstrap)
    app.router.add_post("/api/run", run)
    app.router.add_get(r"/api/evidence/{rid:\d+}", evidence)
    app.router.add_get(r"/api/evidence/{rid:\d+}/attachment/{idx:\d+}", attachment)
    app.router.add_get("/api/library", library)
    app.router.add_get("/", index)
    app.router.add_get("/{filename:.*}", assets)
    return app


def main():
    parser = argparse.ArgumentParser(description="HPIS 个人邮件密态问答演示")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--cache", type=Path, default=cache_default())
    parser.add_argument("--rows", type=int, default=corpus.TOTAL)
    parser.add_argument(
        "--worker", choices=["plain", "hpis", "hpic"], help=argparse.SUPPRESS
    )
    parser.add_argument("--socket-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--folder", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--upstream", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if args.worker == "plain":
            app = plaintext_app()
        elif args.worker == "hpis":
            app = traced_store(args.folder)
        else:
            app = answering_hpic(args.folder, args.upstream)
        sock = socket.socket(fileno=args.socket_fd)
        web.run_app(
            app,
            sock=sock,
            ssl_context=tls_server(args.folder, args.worker == "hpis"),
            access_log=None,
            handler_cancellation=True,
        )
    else:
        app = application(Showcase(args.cache, args.rows))
        web.run_app(
            app,
            host="127.0.0.1",
            port=args.port,
            access_log=None,
            handler_cancellation=True,
        )


if __name__ == "__main__":
    main()
