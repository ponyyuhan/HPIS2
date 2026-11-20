"""Run the actual archived HPIC, OAuth, storage and client against fresh test keys."""

import argparse
import asyncio
import gzip
import json
import os
import platform
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import AsyncExitStack, ExitStack, contextmanager
from pathlib import Path

import aiohttp
import numpy as np
from aiohttp import web
from threadpoolctl import threadpool_limits

from bench.cluster import free_port, running
from hpis.codec import pack, unpack
from hpis.provision import enroll, init
from hpis.sdk import Device

from .bridge import gateway, keygen, primitives
from .client import InferenceDevice
from .import_context import import_context


def stage(source, target):
    target.mkdir()
    required = [
        "client_pic.py",
        "crypto_primitives.py",
        "hpis_server.py",
        "pic_server.py",
        "oauth_server.py",
        "scripts/gen_keys.py",
    ]
    if (source / "demo_logging.py").is_file():
        required.append("demo_logging.py")
    for name in required:
        dst = target / name
        dst.parent.mkdir(exist_ok=True)
        shutil.copyfile(source / name, dst)
    subprocess.run(
        [sys.executable, "scripts/gen_keys.py"],
        cwd=target,
        stdout=subprocess.DEVNULL,
        check=True,
    )


@contextmanager
def archived_server(name, port, root, env):
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                name + ":app",
                "--port",
                str(port),
                "--host",
                "127.0.0.1",
                "--no-access-log",
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            stop = time.monotonic() + 30
            while time.monotonic() < stop:
                if proc.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(
                        f"{name} startup: "
                        + log.read()[-2000:].decode(errors="replace")
                    )
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.04)
            else:
                raise TimeoutError(name)
            yield
        finally:
            proc.terminate()
            try:
                proc.wait(4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def independent_c3(cipher, crypto, pic_private):
    packet = json.loads(cipher)["C3"]
    return json.loads(crypto.ecies_decrypt_with(pic_private, packet, info=b"PIC-C3"))


def record(i, vector):
    return {
        "id": i,
        "fields": {"ref": "item-" + str(i)},
        "keywords": ["g" + str(i // 10)],
        "vector": vector,
        "content": "context-" + str(i),
    }


async def old_client(origin, env, rows, master):
    script = """
import json, sys
import client_pic as original
request = json.load(sys.stdin)
master = bytes.fromhex(request["mk"])
token = original.token_demo()
original.pre_store(master, 1, 1, {"ContextData":{"records":request["rows"]}})
print(original.call_infer(master, 1, 1, "read my context", token))
"""

    def call():
        res = subprocess.run(
            [sys.executable, "-c", script],
            cwd=origin,
            env=env,
            input=json.dumps({"mk": master.hex(), "rows": rows}),
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        if res.returncode:
            raise RuntimeError("upstream client failed: " + res.stderr[-450:])
        return res.stdout

    assert "ContextData" in await asyncio.to_thread(call)


async def run(args, root):
    old = root / "original"
    stage(args.legacy_source, old)
    oldkeys = old / "secrets"
    crypto = primitives(old)
    bridge_folder = root / "adapter"
    keygen(bridge_folder)
    search = root / "search"
    init(search, "encrypted", args.dim, args.beta)
    encrypted = search / "devices/default"
    spare = enroll(search, "second-owner")
    # Register a plaintext control in the same temporary store, using precisely
    # the same endpoints, issuer and HPIC as the encrypted account.
    public_file = search / "public.msgpack"
    public = public_file.read_bytes()
    try:
        public_file.write_bytes(pack({**unpack(public), "mode": "plain"}))
        plain = enroll(search, "plain-control")
    finally:
        public_file.write_bytes(public)
    port_set = set()
    while len(port_set) < 4:
        port_set.add(free_port())
    auth_port, old_store_port, pic_port, gw_port = sorted(port_set)

    def url(port):
        return f"http://127.0.0.1:{port}"

    env = {
        **os.environ,
        "OAUTH_URL": url(auth_port),
        "PIC_URL": url(pic_port),
        "HPIS_URL": url(old_store_port),
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    pic_private = (oldkeys / "pic_x25519_priv.bin").read_bytes()
    pic_public = (oldkeys / "pic_x25519_pub.bin").read_bytes()
    copies = []
    report = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "source": args.source_label,
        "upstream": ["oauth_server", "pic_server", "hpis_server", "client_pic"],
        "topology": "SDK and adapter share a Linux process, with a real TCP hop. Archived services and new HPIC/HPIS run in separate processes.",
        "forwarding": "new and legacy C1/C3/C4 consumed by original pic_server.py",
        "synthetic_keys": True,
    }
    async with AsyncExitStack() as async_stack:
        stack = async_stack.enter_context(ExitStack())
        _, search_url = stack.enter_context(running(search, existing=True))
        for name, port in [
            ("oauth_server", auth_port),
            ("hpis_server", old_store_port),
            ("pic_server", pic_port),
        ]:
            service_env = (
                {**env, "HPIS_URL": url(gw_port)} if name == "pic_server" else env
            )
            stack.enter_context(archived_server(name, port, old, service_env))
        app = gateway(
            crypto=crypto,
            private_key=(bridge_folder / "private.bin").read_bytes(),
            pic_public=pic_public,
            pic_url=url(pic_port),
            old_store_url=url(old_store_port),
            search_url=search_url,
            search_ca=encrypted / "ca.pem",
        )

        @web.middleware
        async def observe_c3(request, handler):
            response = await handler(request)
            if request.path == "/resolve" and response.status == 200:
                copies.append(bytes(response.body))
            return response

        app.middlewares.insert(0, observe_c3)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        async_stack.push_async_callback(runner.cleanup)
        await web.TCPSite(runner, "127.0.0.1", gw_port).start()
        session = await async_stack.enter_async_context(aiohttp.ClientSession())
        async with session.post(
            url(auth_port) + "/oauth/token_demo",
            json={"anon_id": "test-user", "domain": "personal"},
        ) as res:
            res.raise_for_status()
            token = (await res.json())["access_token"]
        common = {
            "bridge_url": url(gw_port),
            "crypto": crypto,
            "pic_public": pic_public,
            "bridge_public": (bridge_folder / "public.bin").read_bytes(),
            "token": token,
            "prompt": "retrieve my context",
        }
        master = os.urandom(32)
        eye = np.eye(2, args.dim)
        rows = [record(i, x.tolist()) for i, x in enumerate(eye)]
        await old_client(old, env, rows, master)
        dbfile = old / "hpis_db.json"
        archive_db = json.loads(dbfile.read_text())
        old_aid, new_aid = [crypto.anonym_id(master, j) for j in [1, 2]]
        assert old_aid not in archive_db and new_aid in archive_db
        decoded = json.loads(
            crypto.aead_decrypt(crypto.k_data(master, 2), archive_db[new_aid]["C"])
        )
        assert decoded["ContextData"]["records"] == rows
        assert independent_c3(copies.pop(), crypto, pic_private) == decoded
        assert not copies
        dev = await async_stack.enter_async_context(Device(encrypted, search_url))
        count = await import_context(
            dev,
            database=dbfile,
            crypto=crypto,
            master_key=master,
            id_counter=2,
            data_counter=2,
        )
        assert count == len(rows)
        client = await async_stack.enter_async_context(
            InferenceDevice(encrypted, search_url, **common)
        )
        peer = await async_stack.enter_async_context(
            InferenceDevice(spare, search_url, **common)
        )

        async def checked_query(call, expected_ids):
            answer = await call
            assert (
                "ContextData" in answer and "retrieval" in answer and len(copies) == 1
            )
            context = independent_c3(copies.pop(), crypto, pic_private)
            assert context["ContextData"]["records"] == [
                [i, "context-" + str(i)] for i in expected_ids
            ]
            assert context["retrieval"]["body_rows"] == len(expected_ids)

        await checked_query(client.terms([["field", "ref", "item-1"]]), [1])
        await checked_query(client.vector(eye[0], 1), [0])
        await checked_query(peer.terms([["field", "ref", "item-1"]]), [])
        negative = {}

        async def expect_http(name, obj, path="/inference"):
            async with session.post(url(gw_port) + path, json=obj) as res:
                assert res.status >= 400, (name, res.status)
                negative[name] = res.status
            assert not copies

        op = {
            "kind": "terms",
            "groups": client.groups([["word", "g0"]]),
            "combine": "all",
        }
        grant = {"dek": client.dek}
        packet, _ = client.make_inference(op, grant)
        async with session.post(url(gw_port) + "/inference", json=packet) as res:
            res.raise_for_status()
            assert "C4" in await res.json()
        copies.pop()
        await expect_http("exact_request_replay", packet)
        unrelated, _ = client.make_inference(op, grant)
        await expect_http("different_C1", {"C1": unrelated["C1"], "C2": packet["C2"]})
        await expect_http("direct_resolve", {"C2": unrelated["C2"]}, "/resolve")
        previous_owner, client.owner = client.owner, peer.owner
        wrong, _ = client.make_inference(op, grant)
        client.owner = previous_owner
        await expect_http("wrong_owner", wrong)
        client.token = "invalid-test-token"
        bad, _ = client.make_inference(op, grant)
        client.token = token
        await expect_http("invalid_legacy_AT", bad)
        report["functional"] = {
            "legacy_rotation_count": 1,
            "migrated_rows": count,
            "exact_ids": [1],
            "vector_ids": [0],
            "other_tenant_ids": [],
            "rejections": negative,
        }
        if args.dataset:
            control = await async_stack.enter_async_context(Device(plain, search_url))
            reference = await async_stack.enter_async_context(
                InferenceDevice(plain, search_url, **common)
            )
            report["benchmark"] = await benchmark(
                args,
                [("plain", control, reference), ("encrypted", dev, client)],
                copies,
                crypto,
                pic_private,
            )
    return report


async def benchmark(args, modes, copies, crypto, pic_private):
    import h5py

    with h5py.File(args.dataset, "r") as dataset:
        base, queries = dataset["train"][: args.rows], dataset["test"][: args.queries]
    if (
        len(base) != args.rows
        or len(queries) != args.queries
        or base.shape[1] != args.dim
        or args.k > len(base)
    ):
        raise ValueError("dataset dimensions or row counts do not match workload")
    v = base.astype(np.float64)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    q = queries.astype(np.float64)
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    gold = [np.argsort(-(v @ x), kind="stable")[: args.k].tolist() for x in q]
    for _, device, _ in modes:
        for start in range(0, len(base), 128):
            await device.put(
                record(i, base[i]) for i in range(start, min(start + 128, len(base)))
            )
    rng = random.Random(20261120)
    samples = []
    for rnd in range(-1, args.rounds):
        n = min(8, len(queries)) if rnd < 0 else len(queries)
        tasks = [
            (i, kind, rng.randrange(len(base)))
            for i in range(n)
            for kind in ("field", "vector")
        ]
        rng.shuffle(tasks)
        for qi, kind, rid in tasks:
            pair = list(modes)
            rng.shuffle(pair)
            for order, (mode, _, sdk) in enumerate(pair):
                start = time.perf_counter_ns()
                if kind == "field":
                    answer = await sdk.terms([["field", "ref", f"item-{rid}"]])
                else:
                    answer = await sdk.vector(queries[qi], args.k)
                us = (time.perf_counter_ns() - start) / 1000
                assert "ContextData" in answer and len(copies) == 1
                ctx = independent_c3(copies.pop(), crypto, pic_private)
                records, stats = ctx["ContextData"]["records"], ctx["retrieval"]
                ids = [r[0] for r in records]
                assert all(body == "context-" + str(i) for i, body in records)
                oracle = gold[qi] if kind == "vector" else [rid]
                if mode == "plain" or kind == "field":
                    assert ids == oracle, (mode, kind, qi, ids, oracle)
                assert len(set(ids)) == stats["body_rows"] == len(oracle)
                if rnd >= 0:
                    samples.append(
                        {
                            "round": rnd,
                            "kind": kind,
                            "mode": mode,
                            "order": order,
                            "query": qi,
                            "id": rid,
                            "us": us,
                            "ids": ids,
                            "gold": oracle,
                            "stats": stats,
                        }
                    )
    result = {
        "rows": len(base),
        "queries": len(queries),
        "dim": args.dim,
        "rounds": args.rounds,
        "k": args.k,
        "beta": args.beta,
        "source": "ANN-Benchmarks GloVe first train/test rows",
        "timing": "SDK target generation through decrypted C4 over loopback, distinct original OAuth/PIC and HPIS2 HPIC/store services; oracle check outside interval",
        "workloads": {},
    }
    for kind in ("field", "vector"):
        groups = {}
        for mode in ("plain", "encrypted"):
            group = [s for s in samples if s["kind"] == kind and s["mode"] == mode]
            times = [s["us"] for s in group]
            groups[mode] = {
                "n": len(group),
                "mean_us": float(np.mean(times)),
                "p50_us": float(np.median(times)),
                "p95_us": float(np.percentile(times, 95)),
                "recall": float(
                    np.mean(
                        [
                            len(set(s["ids"]) & set(s["gold"])) / len(s["gold"])
                            for s in group
                        ]
                    )
                ),
                "body_rows": float(np.mean([s["stats"]["body_rows"] for s in group])),
            }
        groups["overhead_pct"] = {
            label: 100 * (groups["encrypted"][label] / groups["plain"][label] - 1)
            for label in ["mean_us", "p50_us", "p95_us"]
        }
        ratios = []
        by_query = {}
        for m in ("plain", "encrypted"):
            by_query[m] = np.zeros(args.queries)
            for sample in samples:
                if sample["mode"] == m and sample["kind"] == kind:
                    by_query[m][sample["query"]] += sample["us"]
        boot = np.random.default_rng(20261120)
        for _ in range(2000):
            ix = boot.integers(0, args.queries, size=args.queries)
            ratios.append(
                100
                * (by_query["encrypted"][ix].sum() / by_query["plain"][ix].sum() - 1)
            )
        groups["mean_overhead_bootstrap95_pct"] = np.percentile(
            ratios, [2.5, 97.5]
        ).tolist()
        result["workloads"][kind] = groups
    with gzip.open(args.out / "samples.jsonl.gz", "wt", encoding="utf8") as stream:
        for sample in samples:
            stream.write(json.dumps(sample, separators=(",", ":")) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-source", type=Path, required=True)
    parser.add_argument("--source-label", default="provided local mcp_agent checkout")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--rows", type=int, default=20000)
    parser.add_argument("--queries", type=int, default=128)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--dim", type=int, default=100)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--beta", type=float, default=0.25)
    args = parser.parse_args()
    args.legacy_source = args.legacy_source.resolve()
    if min(args.rows, args.queries, args.rounds, args.k) < 1 or args.dim < 2:
        parser.error("positive workload sizes and dimension >=2 required")
    args.out.mkdir(parents=True, exist_ok=True)
    with (
        threadpool_limits(limits=1, user_api="blas"),
        tempfile.TemporaryDirectory(prefix="hpis-legacy-") as tmp,
    ):
        report = asyncio.run(run(args, Path(tmp)))
    (args.out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
