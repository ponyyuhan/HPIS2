"""Send the public RAP candidate set in full while overlapping its suffix with the final fetch."""

import asyncio
import hmac
import struct
from time import perf_counter_ns

import numpy as np
from aiohttp import web

from .codec import F32, context_key, decrypt_item, pack, unpack
from .exact_order import exact_nearest, order_margin
from .network import post
from .store import cosine_distances

MEDIA_TYPE = "application/x-hpis-vector-candidates-v2"


def opened_vectors(ids, ciphertexts, q, cipher):
    width = 4 * len(q) + 28
    if len(ciphertexts) != width * len(ids):
        raise ValueError("invalid vector candidate framing")
    values = b"".join(
        decrypt_item(
            cipher, b"vector", int(rid), ciphertexts[j * width : (j + 1) * width]
        )
        for j, rid in enumerate(ids)
    )
    return np.frombuffer(values, dtype=F32).reshape(-1, len(q))


def authenticate_body_vectors(records, proofs, dim, cipher):
    """Verify the body's vector under the separate VEK using the original AEAD tag."""
    if len(proofs) != 28 * len(records):
        raise ValueError("invalid certified vector proof framing")
    raw = []
    for j, (rid, obj) in enumerate(records):
        value = obj.get("vector")
        if not isinstance(value, bytes) or len(value) != 4 * dim:
            return None
        proof = proofs[j * 28 : (j + 1) * 28]
        sealed = cipher.encrypt(proof[:12], value, b"vector" + struct.pack(">Q", rid))
        if not hmac.compare_digest(proof[12:], sealed[-16:]):
            return None
        raw.append(value)
    return np.frombuffer(b"".join(raw), dtype=F32).reshape(-1, dim)


def needed_prefix(lower, seed_distances, k, dim):
    seed = len(seed_distances)
    rank = min(k, seed) - 1
    threshold = np.partition(seed_distances, rank)[rank]
    return max(
        seed,
        int(
            np.searchsorted(
                lower.astype(np.float64),
                threshold + max(1e-12, order_margin(dim)),
                side="right",
            )
        ),
    )


def finish_ranking(ids, prefix, first_distances, remainder, query, k):
    if remainder is None:
        return ids[exact_nearest(ids, first_distances, k, query, prefix)].tolist()
    scores = np.concatenate((first_distances, cosine_distances(remainder, query)))

    def row(position):
        if position < len(prefix):
            return prefix[position]
        return remainder[position - len(prefix)]

    return ids[exact_nearest(ids, scores, k, query, row)].tolist()


def buffered_ranking(bundle, q, cipher, k):
    ids = np.frombuffer(bundle["ids"], dtype="<i8")
    lower = np.frombuffer(bundle["lower"], dtype=F32)
    width = len(q) * 4 + 28
    sealed = bundle["sealed"]
    if len(ids) != len(lower) or len(sealed) != width * len(ids):
        raise ValueError("invalid vector candidate framing")
    seed = min(len(ids), max(32, k))
    prefix = opened_vectors(ids[:seed], sealed[: width * seed], q, cipher)
    first = cosine_distances(prefix, q)
    stop = needed_prefix(lower, first, k, len(q))
    remaining = None
    if stop > seed:
        remaining = opened_vectors(
            ids[seed:stop], sealed[width * seed : width * stop], q, cipher
        )
    return finish_ranking(ids[:stop], prefix, first, remaining, q, k), stop


async def send_candidates(request, result):
    bundle = result["candidates"]
    ciphertexts = bundle.pop("sealed")
    header = pack(result)
    response = web.StreamResponse(headers={"Content-Type": MEDIA_TYPE})
    response.content_length = 4 + len(header) + len(ciphertexts)
    await response.prepare(request)
    await response.write(len(header).to_bytes(4, "big") + header)
    await response.write(ciphertexts)
    await response.write_eof()
    return response


def apply_fetch(result, fetched):
    rows = fetched["rows"]
    result["rows"] = rows
    stats = result["stats"]
    stats["store_us"] += fetched["store_us"]
    stats.update(body_rows=len(rows), body_bytes=sum(len(value) for _, value in rows))


async def streamed_request(session, url, envelope, grant):
    async with session.post(
        url.rstrip("/") + "/execute",
        data=pack(envelope),
        server_hostname="hpis.internal",
        allow_redirects=False,
        headers={"Content-Type": "application/msgpack", "Accept": MEDIA_TYPE},
    ) as resp:
        resp.raise_for_status()
        if resp.status != 200:
            raise ValueError("unexpected HTTP status")
        if resp.content_type != MEDIA_TYPE:
            return unpack(await resp.read())
        stream = resp.content
        length = int.from_bytes(await stream.readexactly(4), "big")
        if resp.content_length is None or length > resp.content_length - 4:
            raise ValueError("truncated candidate header")
        result = unpack(await stream.readexactly(length))
        b = result.pop("candidates")
        q = np.frombuffer(grant["q"], dtype=F32)
        cipher, k = context_key(grant["vek"]), grant["k"]
        ids = np.frombuffer(b["ids"], dtype="<i8")
        lower = np.frombuffer(b["lower"], dtype=F32)
        width = len(q) * 4 + 28
        if len(ids) != len(lower) or resp.content_length != 4 + length + width * len(
            ids
        ):
            raise ValueError("invalid vector candidate framing")
        seed = min(len(ids), max(k, 32))
        block = await stream.readexactly(width * seed)
        start = perf_counter_ns()
        prefix = opened_vectors(ids[:seed], block, q, cipher)
        first = cosine_distances(prefix, q)
        stop = needed_prefix(lower, first, k, len(q))
        cpu_ns = perf_counter_ns() - start
        remaining = None
        if stop > seed:
            block = await stream.readexactly(width * (stop - seed))
            start = perf_counter_ns()
            remaining = opened_vectors(ids[seed:stop], block, q, cipher)
            cpu_ns += perf_counter_ns() - start
        start = perf_counter_ns()
        top = finish_ranking(ids[:stop], prefix, first, remaining, q, k)
        cpu_ns += perf_counter_ns() - start
        result["stats"].update(
            refine_us=cpu_ns / 1000,
            refined_rows=stop,
            candidate_stream_bytes=resp.content_length,
        )

        async def drain():
            # All publicly selected ciphertexts cross this link on every request.
            total = 0
            async for chunk in stream.iter_chunked(65536):
                total += len(chunk)
            if total != (len(ids) - stop) * width:
                raise ValueError("incomplete public candidate response")

        fetch = asyncio.create_task(
            post(session, url, "/fetch", [result["handle"], top], "hpis")
        )
        try:
            await drain()
            fetched = await fetch
        finally:
            if not fetch.done():
                fetch.cancel()
            await asyncio.gather(fetch, return_exceptions=True)
        apply_fetch(result, fetched)
        return result
