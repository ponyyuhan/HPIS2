"""Run the HPIC-side adapter for the original /inference and /resolve JSON protocol."""

import argparse
import importlib.util
import json
import os
import ssl
import sys
from pathlib import Path

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from hpis.codec import pack, unpack
from hpis.network import errors, post

PROFILE = "hpis2-search/1"
C2_INFO = b"HPIS2-HPIC-search-C2-v1"
CALLBACK = "_pic_callback"


def primitives(source):
    path = Path(source) / "crypto_primitives.py"
    spec = importlib.util.spec_from_file_location("archived_hpic_crypto", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.resolve().parent))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def binding(c1):
    # The complete original C1 must accompany this search token.
    return pack([PROFILE, c1["epk"], c1["nonce"], c1["ct"]])


class UpstreamStatus(Exception):
    def __init__(self, status):
        self.status = status


@web.middleware
async def upstream_errors(request, handler):
    try:
        return await handler(request)
    except UpstreamStatus as exc:
        return web.Response(status=exc.status)
    except (aiohttp.ClientConnectionError, TimeoutError):
        raise web.HTTPBadGateway(text="upstream unavailable")


async def json_post(session, url, value):
    async with session.post(url, json=value, allow_redirects=False) as response:
        if response.status != 200:
            raise UpstreamStatus(
                response.status if 400 <= response.status <= 599 else 502
            )
        return await response.json()


def gateway(
    *, crypto, private_key, pic_public, pic_url, old_store_url, search_url, search_ca
):
    if not search_url.startswith("https://"):
        raise ValueError("HPIS2 requires its enrolled TLS endpoint")
    tls = ssl.create_default_context(cafile=str(search_ca))
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    app = web.Application(
        middlewares=[upstream_errors, errors], client_max_size=64 << 20
    )
    pending = {}

    async def clients(_):
        nonlocal old, hp2
        async with (
            aiohttp.ClientSession() as old,
            aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=tls)) as hp2,
        ):
            yield

    old = hp2 = None

    async def infer(request):
        obj = await request.json()
        c2 = obj["C2"]
        if CALLBACK in c2:
            raise PermissionError("reserved callback")
        if c2.get("profile") is None:
            return web.json_response(await json_post(old, pic_url + "/inference", obj))
        if c2["profile"] != PROFILE:
            raise ValueError("unsupported search profile")
        packet = unpack(
            crypto.ecies_decrypt_with(
                private_key, c2, aad=binding(obj["C1"]), info=C2_INFO
            )
        )
        # Original HPIC validates the C1 token before calling /resolve. Only that
        # local callback receives the private one-use handle. The client cannot
        # attach its own C1 to somebody else's sealed grant.
        handle = os.urandom(32).hex()
        pending[handle] = packet
        try:
            forwarded = {"C1": obj["C1"], "C2": {**c2, CALLBACK: handle}}
            return web.json_response(
                await json_post(old, pic_url + "/inference", forwarded)
            )
        finally:
            pending.pop(handle, None)

    async def resolve(request):
        obj = await request.json()
        c2 = obj["C2"]
        if c2.get("profile") is None:
            return web.json_response(
                await json_post(old, old_store_url + "/resolve", obj)
            )
        if c2.get("profile") != PROFILE:
            raise ValueError("unsupported search profile")
        packet = pending.pop(c2.get(CALLBACK), None)
        if packet is None:
            raise PermissionError("no authenticated HPIC callback")
        result = await post(hp2, search_url, "/request", packet, "hpic")
        if "records" not in result:
            raise ValueError("a retrieval response is required")
        context = {
            "ContextData": {"records": result["records"]},
            "retrieval": result["stats"],
        }
        ciphertext = crypto.ecies_encrypt_for(
            pic_public, json.dumps(context, ensure_ascii=False).encode(), info=b"PIC-C3"
        )
        return web.json_response({"C3": ciphertext})

    app.cleanup_ctx.append(clients)
    app.router.add_post("/inference", infer)
    app.router.add_post("/resolve", resolve)
    return app


def keygen(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    key = X25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    (folder / "private.bin").write_bytes(private)
    (folder / "private.bin").chmod(0o600)
    (folder / "public.bin").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cmd = parser.add_subparsers(dest="cmd", required=True)
    p = cmd.add_parser("keygen")
    p.add_argument("folder", type=Path)
    p = cmd.add_parser("serve")
    p.add_argument("--legacy-source", required=True, type=Path)
    p.add_argument("--private", required=True, type=Path)
    p.add_argument("--pic-public", required=True, type=Path)
    p.add_argument("--pic-url", required=True)
    p.add_argument("--legacy-store-url", required=True)
    p.add_argument("--search-url", required=True)
    p.add_argument("--search-ca", required=True, type=Path)
    p.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if args.cmd == "keygen":
        keygen(args.folder)
    else:
        app = gateway(
            crypto=primitives(args.legacy_source),
            private_key=args.private.read_bytes(),
            pic_public=args.pic_public.read_bytes(),
            pic_url=args.pic_url.rstrip("/"),
            old_store_url=args.legacy_store_url.rstrip("/"),
            search_url=args.search_url.rstrip("/"),
            search_ca=args.search_ca,
        )
        web.run_app(app, host="127.0.0.1", port=args.port, access_log=None)


if __name__ == "__main__":
    main()
