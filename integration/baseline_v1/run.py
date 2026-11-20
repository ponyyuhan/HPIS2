"""Run unmodified HPIS 1.0 Auth, HPIS, PIC and the original network client."""
import argparse
import base64
from contextlib import ExitStack
import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import random
import re
import socket
import subprocess
import sys
import tempfile
import time

import h5py
import numpy as np
import requests

from source import source_files


def start(stack, root, role, route):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    log = stack.enter_context((root / f"{role}.log").open("wb"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", role + ":app", "--host", "127.0.0.1",
         "--port", str(port), "--no-access-log"], cwd=root, stdout=log, stderr=log
    )

    def stop():
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    stack.callback(stop)
    for _ in range(130):
        if proc.poll() is not None:
            raise RuntimeError(f"{role} exited during startup")
        try:
            if requests.get(url + route, timeout=.4).status_code == 200:
                return url
        except requests.ConnectionError:
            pass
        time.sleep(.05)
    raise RuntimeError(f"{role} did not become ready")


def f32bytes(row):
    return np.ascontiguousarray(row, dtype="<f4").tobytes()


def vector_json(row):
    return base64.b64encode(f32bytes(row)).decode("ascii")


def unit(rows):
    values = np.asarray(rows, dtype=np.float64)
    return values / np.linalg.norm(values, axis=-1, keepdims=True)


def corpus(base):
    rows = []
    for i, v in enumerate(unit(base)):
        rows.append(dict(id=i, fields={"ref": f"item-{i}"}, keywords=[f"g{i // 10}"],
                         vector_f32le_b64=vector_json(v),
                         content=f"Reminder {i}: review item {i % 73} at the next meeting."))
    return {"ContextData": {"domain": "personal", "records": rows}}


def measured(values):
    return dict(mean=float(np.mean(values)), p50=float(np.median(values)),
                p95=float(np.percentile(values, 95)), n=len(values))


def check_full_c3(client, root, mk, counter, at, context, base, queries, cases, k):
    # Out-of-timer check, using precisely the original /resolve request and PIC's C3 opener.
    # It advances the demo's rotation counter once more.
    import crypto_primitives as crypto
    old_aid = crypto.anonym_id(mk, counter)
    next_aid = crypto.anonym_id(mk, counter + 1)
    old_key = crypto.k_data(mk, counter)
    next_key = crypto.k_data(mk, counter + 1)
    payload = dict(anonym_id=old_aid, anonym_id_new=next_aid, k_data_b64=crypto.b64e(old_key),
                   k_data_new_b64=crypto.b64e(next_key), at=at)
    c2 = crypto.ecies_encrypt_for((root / "secrets/server_x25519_pub.bin").read_bytes(),
                                  json.dumps(payload).encode(), info=b"HPIS-C2")
    response = requests.post(client.HPIS_URL + "/resolve", json={"C2": c2}, timeout=30)
    response.raise_for_status()
    c3 = response.json()["C3"]
    plain = crypto.ecies_decrypt_with((root / "secrets/pic_x25519_priv.bin").read_bytes(),
                                      c3, info=b"PIC-C3")
    doc = json.loads(plain)
    if doc != context:
        raise RuntimeError("C3 does not contain the original full context")
    records = doc["ContextData"]["records"]
    matrix = np.frombuffer(b"".join(base64.b64decode(r["vector_f32le_b64"]) for r in records),
                           dtype="<f4").reshape(len(records), base.shape[1]).astype(np.float64)
    base_oracle = unit(base)
    query_unit = unit(queries)
    checks = []
    for case in cases:
        value = case["selector"]
        if case["workload"] == "exact":
            ids = [r["id"] for r in records if r["fields"].get("ref") == f"item-{value}"]
            gold = [value]
        else:
            # Both validation paths have distinct data origins: decrypted C3 vs original HDF5.
            ids = np.argsort(-(matrix @ query_unit[value]), kind="stable")[:k].tolist()
            gold = np.argsort(-(base_oracle @ query_unit[value]), kind="stable")[:k].tolist()
        if ids != gold:
            raise RuntimeError("full-context reference answer differs from independent ground truth")
        checks.append(dict(workload=case["workload"], selector=value, ids=ids))
    dbfile = root / "hpis_db.json"
    db = json.loads(dbfile.read_bytes())
    if set(db) != {next_aid} or crypto.aead_decrypt(next_key, db[next_aid]["C"]) != plain:
        raise RuntimeError("rotated AID or re-encrypted full context differs")
    return dict(c3_ref_check=dict(received_records=len(records), all_record_values_match=True,
                                  matches=checks, returned_natively_by_pic=False),
                old_aid_absent=True, new_aid_dek_open_full_context=True, final_db_file_bytes=dbfile.stat().st_size,
                context_json_utf8_bytes=len(plain), c3_ct_bytes=len(crypto.b64d(c3["ct"])),
                c3_nonce_bytes=len(crypto.b64d(c3["nonce"])), c3_ephemeral_public_key_bytes=len(crypto.b64d(c3["epk"])),
                c3_ct_b64_chars=len(c3["ct"]), resolve_http_response_body_bytes=len(response.content),
                stored_full_ciphertext_bytes=len(crypto.b64d(db[next_aid]["C"]["ct"])))


def run(args):
    with h5py.File(args.dataset, "r") as hf:
        if not (1 <= args.k <= args.rows <= len(hf["train"]) and 1 <= args.pairs <= len(hf["test"])):
            raise ValueError("invalid corpus, k, or test size")
        base, queries = hf["train"][:args.rows], hf["test"][:args.pairs]
    context = corpus(base)
    query_unit = unit(queries)
    rng = random.Random(args.seed)
    cases = [dict(workload=kind, selector=(rng.randrange(len(base)) if kind == "exact" else j))
             for j in range(args.pairs) for kind in ("exact", "vector")]
    rng.shuffle(cases)
    samples = []
    with tempfile.TemporaryDirectory(prefix="hpis-v1-") as tmp, ExitStack() as stack:
        root = Path(tmp)
        provenance = source_files(root, args.source)
        subprocess.run([sys.executable, "scripts/gen_keys.py"], cwd=root, check=True,
                       stdout=subprocess.DEVNULL)
        os.environ["OAUTH_URL"] = start(stack, root, "oauth_server", "/oauth/pubkey")
        os.environ["HPIS_URL"] = start(stack, root, "hpis_server", "/pubkey")
        os.environ["PIC_URL"] = start(stack, root, "pic_server", "/pubkey")
        prev = Path.cwd()
        os.chdir(root)
        stack.callback(os.chdir, prev)
        sys.path.insert(0, str(root))
        import client_pic as client

        mk = os.urandom(32)
        at = client.token_demo()
        client.pre_store(mk, 1, 1, context)
        counter = 1
        for index, case in enumerate([{"workload":"exact","selector":0}] * args.warmup + cases):
            sel = case["selector"]
            if case["workload"] == "exact":
                prompt = f"Return the record with ref=item-{sel}"
            else:
                prompt = f"Return the {args.k} nearest records by cosine to f32le_b64={vector_json(query_unit[sel])}"
            started = time.perf_counter_ns()
            ans = client.call_infer(mk, counter, counter, prompt, at)
            elapsed = (time.perf_counter_ns() - started) / 1e6
            if ans != f"[PIC-LLM] prompt={prompt!r}; context_keys=['ContextData']":
                raise RuntimeError("unexpected decrypted C4 answer")
            if index >= args.warmup:
                samples.append(dict(**case, sequence=len(samples), elapsed_ms=elapsed,
                                    rotation_counter_in=counter, rotation_counter_out=counter+1))
            counter += 1

        validation = check_full_c3(client, root, mk, counter, at, context, base, queries, cases, args.k)
        # Extract sizes and success counts, never publish token/AID/nonces or the upstream logs.
        hp_log = (root / "hpis_server.log").read_text()
        pic_log = (root / "pic_server.log").read_text()
        native_calls = args.warmup + len(samples)
        lengths = [int(v) for v in re.findall(r"PIC<-HPIS C3 [^\n]*?ct_len=([0-9]+)", pic_log)]
        if len(lengths) != native_calls or set(lengths) != {validation["c3_ct_b64_chars"]}:
            raise RuntimeError("native PIC C3 trace does not match the decrypted full-context envelope")
        if pic_log.count("PIC.C3.dec |") != native_calls or hp_log.count("HPIS.rotate |") != native_calls + 1:
            raise RuntimeError("unexpected number of decryptions or rotations")
        validation.update(native_c3_received_decrypted_and_c4_opened=native_calls,
                          hpis_full_rotations_including_out_of_timer_check=native_calls+1,
                          native_c3_sizes_all_match_reference=True)
    return dict(upstream=dict(**provenance,
                              source="original server functions and client_pic.token_demo/pre_store/call_infer"),
                started_at_utc=args.started_at_utc, dataset=dict(name="glove-100-angular",train_first=len(base),
                test_first=len(queries),dim=base.shape[1],k=args.k),
                environment=dict(os=platform.platform(),arch=platform.machine(),python=platform.python_version(),
                                 versions={n:importlib.metadata.version(n) for n in
                                           ("fastapi","uvicorn","requests","pyjwt","numpy","h5py","cryptography")}),
                method=dict(warmup=args.warmup, seed=args.seed,transport="unchanged upstream HTTP loopback",
                            timing="original call_infer entry through C4 decryption/answer; token issuance, pre-store and reference filtering excluded",
                            context="one complete JSON blob per AID; normalized float32 vectors encoded as base64 per record",
                            pid_roles="three distinct upstream processes, plus client",native_search=False),
                latency_ms={kind:measured([r["elapsed_ms"] for r in samples if r["workload"]==kind])
                            for kind in ("exact","vector")}, verification=validation), samples


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, help="directory containing the selected original upstream files")
    p.add_argument("--dataset", required=True, type=Path)
    p.add_argument("--rows", type=int, default=20000)
    p.add_argument("--pairs", type=int, default=12)
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--warmup", type=int, default=2)
    p.add_argument("--seed", type=int, default=20261120)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    if args.source is not None:
        args.source = args.source.resolve()
    args.started_at_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    summary, samples = run(args)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/"summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    (args.out/"samples.jsonl").write_text("".join(json.dumps(row)+"\n" for row in samples))
    print(json.dumps(summary["latency_ms"]))


if __name__ == "__main__":
    main()
