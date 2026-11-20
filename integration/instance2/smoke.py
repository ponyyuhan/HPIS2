"""Exercise unmodified archived issuance and the live HPIS2 TLS services."""
import argparse
import asyncio
import base64
import copy
import importlib
import json
import os
import shutil
import platform
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import aiohttp
import requests
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey

from bench.cluster import free_port, running
from hpis.codec import unpack
from hpis.network import post
from hpis.provision import enroll, init, read_config
from hpis.sdk import Device
from . import Binding, Verifier, prepare, redeem, selected_subaid
from .__main__ import install, update


@contextmanager
def archived_issuer(source, work):
    pkg = work / "hpic_rag"
    pkg.mkdir()
    for name in ("__init__.py", "aid_subaid.py", "anon_verify.py", "anon_token.py", "oauth_auth_server.py"):
        shutil.copyfile(source / name, pkg / name)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "ANON_MAC_MODE": "ed25519"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "hpic_rag.oauth_auth_server:app",
                             "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--log-level", "error"],
                            cwd=work, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sys.path.insert(0, str(work))
    try:
        start = time.monotonic()
        while time.monotonic() - start < 20:
            if proc.poll() is not None:
                raise RuntimeError("archived issuer stopped before becoming ready")
            try:
                if requests.get(url + "/health", timeout=.3).status_code == 200:
                    break
            except requests.ConnectionError:
                pass
            time.sleep(.03)
        else:
            raise RuntimeError("archived issuer did not become ready")
        client = importlib.import_module("hpic_rag.anon_token")
        yield pkg, url, client
    finally:
        sys.path.remove(str(work))
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def rejected(case, callback, results):
    try:
        callback()
    except (PermissionError, BadSignatureError):
        results.append(case)
    else:
        raise AssertionError(f"expected rejection: {case}")


async def forbidden(case, awaitable, results):
    try:
        await awaitable
    except aiohttp.ClientResponseError as error:
        if error.status != 403:
            raise
        results.append(case)
    else:
        raise AssertionError(f"expected HTTP 403: {case}")


def smoke(archive):
    rejected_cases = []
    with tempfile.TemporaryDirectory(prefix="hpis-instance2-") as path:
        work = Path(path)
        root = work / "hpis2"
        devices = [init(root, "encrypted", 3, .25), enroll(root, "peer")]
        issuer = SigningKey(read_config(root / "issuer")["sign"])
        update(root / "hpic", owner_ticket_only=True)
        with archived_issuer(archive, work) as (pkg, url, upstream):
            bundles = [upstream.issue_anon_token(url, services=[("hpis", "v1")]) for _ in devices]
            keys = pkg / "data" / "anonv2_keys"
            verifier = Verifier(pkg, (keys / "issuer_rsa_pub.pem").read_bytes(),
                                (keys / "mac_ed25519_pub.pem").read_bytes())
            issued = []
            for folder, bundle in zip(devices, bundles):
                config = read_config(folder)
                selected = selected_subaid(bundle, "hpis", "v1")
                # Test enrollment authorizes this synthetic service pseudonym.
                sub = base64.urlsafe_b64decode(selected + "=" * (-len(selected) % 4))
                binding = Binding.from_store(root / "hpis" / "store.sqlite", config["aid"], "hpis", "v1", sub)
                request, holder = prepare(config, bundle, "hpis", "v1")
                ticket = redeem(request, binding, verifier, issuer)
                issued.append((ticket, SigningKey(holder)))
                if unpack(issuer.verify_key.verify(ticket))[:1] != ["hpic-owner-v1"]:
                    raise AssertionError("wrong ticket audience")
            wrong_pin = copy.deepcopy(bundles[0])
            wrong_pin["issuer"]["n_b64"] = bundles[0]["issuer"]["n_b64"][1:]
            rejected("untrusted upstream RSA key", lambda: verifier.verify(wrong_pin), rejected_cases)
            wrong_attr = copy.deepcopy(bundles[0])
            wrong_attr["attributes"]["level"] = "changed"
            rejected("changed signed attributes", lambda: verifier.verify(wrong_attr), rejected_cases)
            wrong_sub = copy.deepcopy(bundles[0])
            wrong_sub["subaid"] = bundles[1]["subaid"]
            rejected("changed blind-signed SubAID", lambda: verifier.verify(wrong_sub), rejected_cases)
            wrong_request, _ = prepare(read_config(devices[1]), bundles[0], "hpis", "v1")
            rejected("independently valid token for a different enrollment",
                     lambda: redeem(wrong_request, binding, verifier, issuer), rejected_cases)
            wrong_owner, _ = prepare(read_config(devices[0]), bundles[1], "hpis", "v1")
            rejected("wrong HPIS owner signature", lambda: redeem(wrong_owner, binding, verifier, issuer), rejected_cases)
            expired, _ = prepare(read_config(devices[1]), bundles[1], "hpis", "v1", lifetime=-1)
            rejected("expired redemption proof", lambda: redeem(expired, binding, verifier, issuer), rejected_cases)

        async def exercise(hpic):
            pred = [["word", "终端"]]
            async with Device(devices[0], hpic) as first:
                await forbidden("native ticket in owner-only deployment", first.terms(pred), rejected_cases)
            for folder, (ticket, key) in zip(devices, issued):
                install(folder, bytes(key), ticket)
            async with Device(devices[0], hpic) as first, Device(devices[1], hpic) as peer:
                for dev, value in [(first, "alpha"), (peer, "beta")]:
                    await dev.put([{"id": 7, "keywords": ["终端"], "vector": [1,0,0], "content": value}])
                    assert (await dev.terms(pred))["records"] == [[7, value]]
                    assert (await dev.vector([1,0,0], 1, refine=True))["records"] == [[7, value]]
                peer.ticket, peer.holder = issued[0]
                await forbidden("tenant A ticket with tenant B valid query and owner proof", peer.terms(pred),
                                rejected_cases)
                op = {"kind": "terms", "groups": first.groups(pred), "combine":"all"}
                envelope = first.envelope(op, {"dek":first.dek})
                await post(first.session, first.url, "/request", envelope, "hpic")
                await forbidden("replayed query", post(first.session, first.url, "/request", envelope, "hpic"),
                                rejected_cases)
        with running(root, existing=True) as (_, hpic):
            asyncio.run(exercise(hpic))
    return {"platform": platform.platform(), "python": platform.python_version(), "issued_by_archive": 2, "accepted_by_hpis2": 2, "isolated_tenants": 2,
            "verified_queries": ["encrypted keyword", "exact refined vector"],
            "rejected_cases": rejected_cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="archived hpic_rag source directory")
    print(json.dumps(smoke(parser.parse_args().archive), ensure_ascii=False, indent=2))
