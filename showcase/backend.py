"""Local terminal, ordinary cloud and HPIC/HPIS comparison for the presentation."""

import asyncio
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from time import perf_counter, time
import uuid

import aiohttp
from aiohttp import web
import msgpack

from hpis.codec import pack, unpack
from hpis.network import tls_client
from hpis.provision import init as enroll_demo
from hpis.provision import read_config, renew
from hpis.sdk import Device
from hpis.services import make_hpic_app, make_store_app

from . import corpus
from .local_model import model_answer, model_profiles


INGEST_BATCH = 400
SEND_BATCH = 2500
ENGINE_LABEL = "本地证据抽取"


def timestamp():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def milliseconds(start):
    return (perf_counter() - start) * 1000


def json_line(obj):
    return (
        json.dumps(
            obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
        + b"\n"
    )


def answer_view(question, messages, plan):
    result = corpus.make_answer(question, messages, plan)
    sections = result.get("sections", [])
    return {
        "headline": result.get("lead", ""),
        "points": sections,
        "sourceIds": result.get("sourceIds", []),
        "mode": result.get("mode", "extractive"),
        "matched": result.get("matched", len(messages)),
    }


def evidence_view(mail, *, full=True):
    """Public fields only. Search facets belong to the terminal index."""
    value = {
        k: mail.get(k)
        for k in (
            "id",
            "subject",
            "sender",
            "email",
            "role",
            "date",
            "time",
            "recipients",
            "project",
            "category",
            "status",
        )
    }
    if full:
        value["body"] = mail.get("body", "")
    attachments = []
    for i, a in enumerate(mail.get("attachments", [])):
        info = {
            "name": a["name"],
            "mime": a.get("mime", "text/plain"),
            "downloadUrl": f"/api/evidence/{mail['id']}/attachment/{i}",
        }
        if full:
            info["text"] = a.get("content", a.get("text", ""))
        attachments.append(info)
    value["attachments"] = attachments
    value["attachmentCount"] = len(attachments)
    return value


def preset_quality(question, answer):
    def clean(text):
        return "".join(text.split()).strip("？?。.!！")

    preset = next(
        (p for p in corpus.PRESETS if clean(p["question"]) == clean(question)), None
    )
    if not preset or not preset.get("expectedIds"):
        return None
    expected = set(preset["expectedIds"])
    cited = set(answer["sourceIds"])
    return {
        "matched": len(expected & cited),
        "total": len(expected),
        "cited": len(cited),
        "metric": "referenceEvidenceCoverage",
    }


def plaintext_app():
    async def ask(request):
        # Only the initial synthetic demo setup keeps mail on the terminal.
        # This independent ordinary-cloud worker receives a fresh full body every time.
        payload = await request.read()
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
        await response.prepare(request)
        await response.write(json_line({"stage": "search"}))
        start = perf_counter()
        stream = msgpack.Unpacker(io.BytesIO(payload), raw=False)
        header = next(stream)
        question = header["question"]
        plan = corpus.plan_query(question)
        matches = []
        read_rows = batches = 0
        for group in stream:
            batches += 1
            read_rows += len(group)
            for mail in group:
                if corpus.matches_plan(mail, plan):
                    matches.append(corpus.message_content(mail))
        search_ms = milliseconds(start)
        await response.write(
            json_line(
                {"stage": "answer", "readRows": read_rows, "matchedRows": len(matches)}
            )
        )
        start = perf_counter()
        answer, model = await model_answer(
            question,
            matches,
            answer_view(question, matches, plan),
            profile=header["model"],
        )
        await response.write(
            json_line(
                {
                    "answer": answer,
                    "model": model,
                    "readRows": read_rows,
                    "batches": batches,
                    "seenBytes": len(payload),
                    "cloudBytes": 0,
                    "cloudWireBytes": 0,
                    "searchMs": search_ms,
                    "answerMs": milliseconds(start),
                    "matchedRows": len(matches),
                }
            )
        )
        await response.write_eof()
        return response

    app = web.Application(client_max_size=512 << 20)
    app.router.add_post("/ask", ask)
    return app


def traced_store(folder):
    app = make_store_app(folder)

    @web.middleware
    async def count(request, handler):
        response = await handler(request)
        if request.path == "/execute" and response.status == 200:
            data = unpack(response.body)
            if "stats" in data:
                # Include the two counters themselves in the genuine packed response size.
                stats = data["stats"]
                rows = data.get("rows", [])
                if rows:
                    stats["cipherBodySample"] = rows[0][1][12:28].hex()
                stats["cloudRequestBytes"] = len(await request.read())
                stats["cloudReplyBytes"] = 0
                body = pack(data)
                while stats["cloudReplyBytes"] != len(body):
                    stats["cloudReplyBytes"] = len(body)
                    body = pack(data)
                return web.Response(body=body, content_type="application/msgpack")
        return response

    app.middlewares.insert(0, count)
    return app


def answering_hpic(folder, store_url):
    app = make_hpic_app(folder, store_url)

    @web.middleware
    async def extract(request, handler):
        if (
            request.path != "/request"
            or request.headers.get("X-HPIS-Showcase") != "ask"
        ):
            return await handler(request)
        wire = await request.read()
        stream = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
        await stream.prepare(request)
        await stream.write(json_line({"stage": "search"}))
        try:
            start = perf_counter()
            native = await handler(request)
            if native.status != 200:
                raise ValueError("HPIC 拒绝了检索请求")
            result = unpack(native.body)
            stats = result["stats"]
            # Authentication and ownership have succeeded inside the real HPIC/HPIS handlers.
            # The terminal signed the HPIC grant containing the question. HPIS saw only blind labels.
            _, signed = unpack(wire)
            _, _, _, grant = unpack(signed[64:])
            question = grant["showcaseQuestion"]
            messages = [item for _, item in result["records"]]
            search_ms = milliseconds(start)
            await stream.write(
                json_line(
                    {
                        "stage": "answer",
                        "readRows": stats["body_rows"],
                        "matchedRows": len(messages),
                    }
                )
            )
            start = perf_counter()
            answer, model = await model_answer(
                question,
                messages,
                answer_view(question, messages, corpus.plan_query(question)),
                profile=grant["showcaseModel"],
            )
            await stream.write(
                json_line(
                    {
                        "answer": answer,
                        "model": model,
                        "readRows": stats["body_rows"],
                        "batches": 1,
                        "cipherBodySample": stats.get("cipherBodySample"),
                        "seenBytes": len(wire),
                        "cloudBytes": stats["body_bytes"],
                        "cloudWireBytes": stats["cloudRequestBytes"]
                        + stats["cloudReplyBytes"],
                        "searchMs": search_ms,
                        "answerMs": milliseconds(start),
                        "matchedRows": len(messages),
                    }
                )
            )
        except (web.HTTPException, ValueError, TypeError, KeyError, IndexError):
            await stream.write(json_line({"error": "HPIC 未能完成已授权的检索或回答"}))
        await stream.write_eof()
        return stream

    app.middlewares.insert(0, extract)
    return app


class MailArchive:
    """Presentation terminal's original mailbox and the completed ingestion cursor."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS mails (
                id INTEGER PRIMARY KEY, subject TEXT NOT NULL, sender TEXT NOT NULL,
                search TEXT NOT NULL, attachments INTEGER NOT NULL, message BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS setup (
                singleton INTEGER PRIMARY KEY, elapsed_ms REAL NOT NULL, sent INTEGER NOT NULL,
                indexed INTEGER NOT NULL, raw_bytes INTEGER NOT NULL, attachments INTEGER NOT NULL,
                built_at TEXT
            );
            INSERT OR IGNORE INTO setup VALUES (1,0,0,0,0,0,NULL);
        """)

    def state(self):
        ms, sent, count, raw, attachments, date = self.db.execute(
            "SELECT elapsed_ms,sent,indexed,raw_bytes,attachments,built_at FROM setup WHERE singleton=1"
        ).fetchone()
        return {
            "setupMs": ms,
            "setupBytes": sent,
            "indexed": count,
            "plainBytes": raw,
            "attachments": attachments,
            "builtAt": date,
        }

    def commit(self, group, ms, wire_bytes, *, final):
        rows = []
        for msg in group:
            search = "\n".join(
                [
                    str(msg.get(key, ""))
                    for key in (
                        "subject",
                        "sender",
                        "date",
                        "body",
                        "project",
                        "category",
                    )
                ]
                + [a["name"] for a in msg.get("attachments", [])]
            )
            rows.append(
                (
                    msg["id"],
                    msg["subject"],
                    msg["sender"],
                    search.casefold(),
                    len(msg.get("attachments", [])),
                    pack(msg),
                )
            )
        with self.db:
            self.db.executemany("INSERT INTO mails VALUES(?,?,?,?,?,?)", rows)
            self.db.execute(
                """UPDATE setup SET elapsed_ms=elapsed_ms+?, sent=sent+?, indexed=indexed+?,
                               raw_bytes=raw_bytes+?, attachments=attachments+?, built_at=? WHERE singleton=1""",
                (
                    ms,
                    wire_bytes,
                    len(group),
                    sum(len(r[-1]) for r in rows),
                    sum(r[-2] for r in rows),
                    timestamp() if final else None,
                ),
            )

    def batches(self):
        cursor = self.db.execute("SELECT message FROM mails ORDER BY id")
        packer = msgpack.Packer(use_bin_type=True)
        try:
            while rows := cursor.fetchmany(SEND_BATCH):
                yield (
                    len(rows),
                    packer.pack_array_header(len(rows)) + b"".join(r[0] for r in rows),
                )
        finally:
            cursor.close()

    def mail(self, rid):
        row = self.db.execute("SELECT message FROM mails WHERE id=?", (rid,)).fetchone()
        return evidence_view(unpack(row[0])) if row else None

    def library(self, query, offset, limit):
        where, args = (
            (" WHERE instr(search,?)>0", (query.casefold(),)) if query else ("", ())
        )
        count = self.db.execute("SELECT COUNT(*) FROM mails" + where, args).fetchone()[
            0
        ]
        rows = self.db.execute(
            "SELECT message FROM mails" + where + " ORDER BY id LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        return {
            "items": [evidence_view(unpack(row[0]), full=False) for row in rows],
            "total": count,
            "offset": offset,
            "limit": limit,
        }


class IngestDevice(Device):
    sent = 0

    def envelope(self, op, grant):
        envelope = super().envelope(op, grant)
        self.sent += len(pack(envelope))
        return envelope


async def quiet(_event):
    pass


class Showcase:
    def __init__(self, cache_dir, total):
        if not 12 <= total <= corpus.TOTAL:
            raise ValueError(f"corpus size must be between 12 and {corpus.TOTAL}")
        version = getattr(corpus, "VERSION", "mail-v1")
        self.root = Path(cache_dir) / f"{version}-{total}"
        self.total = total
        self.ready = False
        self.error = None
        self.last = None
        self.children = []
        self.archive = None
        self.device = None
        self.plain = None
        self.indexed = 0
        self.setup = {}
        self.ticket_until = 0
        self.preparing = None

    def bootstrap(self):
        presets = [
            {k: p[k] for k in ("id", "label", "question")} for p in corpus.PRESETS
        ]
        return {
            "ready": self.ready,
            "total": self.total,
            "indexed": self.indexed,
            "error": self.error,
            "last": self.last,
            "presets": presets,
            "models": [
                {**item, "available": item["configured"]} for item in model_profiles()
            ],
            "dataset": {"name": "个人演示邮箱", "total": self.total, **self.setup},
            "engine": self.last["engine"] if self.last else ENGINE_LABEL,
        }

    async def worker(self, role, folder, upstream=None):
        # Let the OS select and reserve the actual listening socket until the child inherits it.
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        logfile = (self.root / f"{role}.log").open("ab")
        try:
            args = [
                sys.executable,
                "-m",
                "showcase.server",
                "--worker",
                role,
                "--socket-fd",
                str(sock.fileno()),
                "--folder",
                str(folder),
            ]
            if upstream:
                args += ["--upstream", upstream]
            env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
            if role == "hpis":
                # Only the two answer-generating workers use model server credentials.
                env.pop("HPIS_SHOWCASE_QWEN_API_KEY", None)
                env.pop("HPIS_SHOWCASE_GEMMA_API_KEY", None)
            proc = subprocess.Popen(
                args, pass_fds=(sock.fileno(),), stdout=logfile, stderr=logfile, env=env
            )
            self.children.append(proc)
            deadline = perf_counter() + 30
            while perf_counter() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError(f"{role} 服务启动失败，请查看 {logfile.name}")
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.close()
                    await writer.wait_closed()
                    return f"https://127.0.0.1:{port}"
                except OSError:
                    await asyncio.sleep(0.05)
            raise RuntimeError(f"{role} 服务启动超时")
        finally:
            sock.close()
            logfile.close()

    async def start(self):
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            protected, ordinary = self.root / "protected", self.root / "ordinary"
            for directory, mode in ((protected, "encrypted"), (ordinary, "plain")):
                if not (directory / "public.msgpack").exists():
                    enroll_demo(directory, mode, 1, 0.25)
            renew(protected, "default")
            self.ticket_until = time() + 23 * 3600
            self.archive = MailArchive(self.root / "terminal.sqlite")
            store = await self.worker("hpis", protected / "hpis")
            hp = await self.worker("hpic", protected / "hpic", store)
            self.plain_url = await self.worker("plain", ordinary / "hpic")
            self.plain = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    ssl=tls_client(ordinary / "devices" / "default")
                )
            )
            self.device = IngestDevice(protected / "devices" / "default", hp)
            await self.device.__aenter__()
            self.setup = self.archive.state()
            self.indexed = self.setup["indexed"]
            if self.indexed < self.total:
                await self.build()
            self.last = await self.compare(corpus.PRESETS[0]["question"], quiet)
            self.ready = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
            import traceback

            traceback.print_exc()

    async def build(self):
        # A retried signed upsert replaces any batch acknowledged by HPIS just before a crash.
        group = []
        start = perf_counter()
        for mail in corpus.iter_messages(self.total):
            if mail["id"] <= self.indexed:
                continue
            group.append(mail)
            final = self.indexed + len(group) == self.total
            if len(group) < INGEST_BATCH and not final:
                continue
            before = self.device.sent
            await self.device.put(
                {
                    "id": m["id"],
                    "fields": m["fields"],
                    "keywords": m["keywords"],
                    "content": corpus.message_content(m),
                }
                for m in group
            )
            self.archive.commit(
                group, milliseconds(start), self.device.sent - before, final=final
            )
            self.setup = self.archive.state()
            self.indexed = self.setup["indexed"]
            group = []
            await asyncio.sleep(0)
            start = perf_counter()
        if self.indexed != self.total:
            raise ValueError("未载入完整的演示邮箱")

    def refresh_ticket(self):
        if time() < self.ticket_until:
            return
        renew(self.root / "protected", "default")
        config = read_config(self.device.folder)
        from nacl.signing import SigningKey

        self.device.ticket, self.device.holder = (
            config["ticket"],
            SigningKey(config["holder"]),
        )
        self.ticket_until = time() + 23 * 3600

    async def compare(self, question, notify, profile="extractive"):
        question = question.strip()
        if not question or len(question) > 600:
            raise ValueError("请输入 1 至 600 个字符的问题")
        self.refresh_ticket()
        run_id = uuid.uuid4().hex
        await notify(
            {"type": "start", "id": run_id, "question": question, "model": profile}
        )
        tasks = [
            asyncio.create_task(self.branch(route, question, notify, profile))
            for route in ("plain", "protected")
        ]
        try:
            plain, private = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        run = {
            "id": run_id,
            "question": question,
            "completedAt": timestamp(),
            "model": profile,
            "total": self.total,
            "engine": plain["engine"]
            if plain["engine"] == private["engine"]
            else "两路回答引擎分别记录",
            "plain": plain,
            "protected": private,
        }
        self.last = run
        return run

    async def branch(self, route, question, notify, profile):
        start = perf_counter()
        sent = 0
        token_sample = None

        async def event(stage, **fields):
            await notify(
                {
                    "type": "stage",
                    "route": route,
                    "stage": stage,
                    "elapsedMs": milliseconds(start),
                    **fields,
                }
            )

        async def ordinary_payload():
            nonlocal sent
            header = pack({"question": question, "model": profile})
            sent += len(header)
            yield header
            done = 0
            for count, batch in self.archive.batches():
                sent += len(batch)
                yield batch
                done += count
                await event("upload", completed=done, total=self.total, bytes=sent)

        await event(
            "upload", completed=0, total=self.total if route == "plain" else 1, bytes=0
        )
        if route == "plain":
            session, url, data = self.plain, self.plain_url + "/ask", ordinary_payload()
        else:
            plan = corpus.plan_query(question)
            op = {
                "kind": "terms",
                "groups": self.device.groups(plan["predicates"]),
                "combine": plan.get("combine", "all"),
            }
            envelope = self.device.envelope(
                op,
                {
                    "dek": self.device.dek,
                    "showcaseQuestion": question,
                    "showcaseModel": profile,
                },
            )
            _, _, sealed_query, _ = unpack(envelope[1][64:])
            token_sample = sealed_query[48:64].hex()
            data = pack(envelope)
            sent = len(data)
            session, url = self.device.session, self.device.url + "/request"
        received = 0
        upload_ms = 0
        result = None
        headers = {"Content-Type": "application/msgpack", "X-HPIS-Showcase": "ask"}
        async with session.post(
            url,
            data=data,
            server_hostname="hpic.internal",
            headers=headers,
            allow_redirects=False,
        ) as response:
            response.raise_for_status()
            async for line in response.content:
                received += len(line)
                item = json.loads(line)
                if "error" in item:
                    raise RuntimeError(item["error"])
                if item.get("stage"):
                    if item["stage"] == "search":
                        upload_ms = milliseconds(start)
                    await event(
                        item["stage"],
                        bytes=sent,
                        **{
                            k: item[k] for k in ("readRows", "matchedRows") if k in item
                        },
                    )
                elif "answer" in item:
                    result = item
        overall = milliseconds(start)
        if result is None or result["seenBytes"] != sent:
            raise RuntimeError("服务返回的传输计数不完整")
        answer = result["answer"]
        evidence = [self.archive.mail(rid) for rid in answer["sourceIds"]]
        quality = preset_quality(question, answer)
        await event("done", bytes=sent)
        return {
            "uploadBytes": sent,
            "downloadBytes": received,
            "responseBytes": received,
            "cloudBytes": result["cloudBytes"],
            "cloudWireBytes": result["cloudWireBytes"],
            "modelWireBytes": result["model"].get("requestBytes", 0)
            + result["model"].get("responseBytes", 0),
            "batches": result["batches"],
            "readRows": result["readRows"],
            "matchedRows": result["matchedRows"],
            "elapsedMs": overall,
            "firstAnswerMs": overall,
            "answer": answer,
            "evidence": evidence,
            "quality": quality,
            "engine": result["model"]["engine"],
            "model": result["model"],
            "queryCipherSample": token_sample,
            "bodyCipherSample": result.get("cipherBodySample"),
            "stages": [
                {"id": "upload", "label": "传输", "ms": upload_ms},
                {"id": "search", "label": "检索与取证", "ms": result["searchMs"]},
                {"id": "answer", "label": "生成回答", "ms": result["answerMs"]},
            ],
        }

    async def close(self):
        if self.preparing and not self.preparing.done():
            self.preparing.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.preparing
        if self.device and self.device.session:
            await self.device.__aexit__(None, None, None)
        if self.plain:
            await self.plain.close()
        for proc in self.children:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.children:
            try:
                await asyncio.to_thread(proc.wait, 5)
            except subprocess.TimeoutExpired:
                proc.kill()
                await asyncio.to_thread(proc.wait)
        if self.archive:
            self.archive.db.close()
