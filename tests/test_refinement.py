import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hpis.codec import F32, encrypt_item, pack, unit_vectors, unpack
from hpis.provision import init
from hpis.sdk import Device
from hpis.services import make_store_app, refine_candidates
from hpis.store import RefinementIndex, Store, cosine_distances


class CertifiedRefinementTest(unittest.TestCase):
    def test_fast_search_ranks_by_sketch_direction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            device = Device(init(root, "encrypted", 2, 2.0), "https://127.0.0.1")
            store = Store(root / "hpis" / "store.sqlite")
            try:
                # The first sketch is farther in Euclidean distance but has closer direction.
                sketches = np.array([[1.4, 0.04], [0.96, 0.2]], dtype=F32)
                rows = []
                for rid, sketch in enumerate(sketches):
                    original = unit_vectors(sketch, 2).tobytes()
                    ct = encrypt_item(device.vector_cipher, b"vector", rid, original)
                    rows.append((rid, b"body", [], sketch.tobytes(), ct))
                store.write(device.aid, "encrypted", 2, rows)
                op = {
                    "kind": "vector",
                    "target": np.array([1, 0], dtype=F32).tobytes(),
                    "k": 1,
                }
                result = store.search(device.aid, "encrypted", 2, 2.0, op)
                self.assertEqual(result["rows"], [(0, b"body")])
            finally:
                store.close()

    def test_certificates_against_exhaustive_cosine(self):
        rng = np.random.default_rng(1107)
        cipher = AESGCM(os.urandom(32))
        for dim in (1, 2, 8, 100):
            for beta in (0.01, 0.25, 2.0):
                radius = beta / 4
                base = unit_vectors(rng.normal(size=(240, dim)), dim)
                q = base[0].copy()
                # Genuine ties, near ties, and adversarially aligned boundary noises.
                base[2:5] = q
                base[5:8] = -q
                base[8] = unit_vectors(q + 1.0e-8 * rng.normal(size=dim), dim)

                def noise(shape):
                    direction = rng.normal(size=shape)
                    return (
                        radius
                        * direction
                        / np.linalg.norm(direction, axis=-1, keepdims=True)
                    )

                sketches = (base + noise(base.shape)).astype(F32)
                target = (q + noise(q.shape)).astype(F32)
                ids = rng.choice(1000000, len(base), replace=False).astype("<i8")
                raw = b"".join(
                    encrypt_item(cipher, b"vector", int(i), v.tobytes())
                    for i, v in zip(ids, base)
                )
                sealed = np.frombuffer(raw, dtype=np.uint8).reshape(len(ids), -1)
                index = RefinementIndex(sketches, sealed, beta)
                for positions in (slice(None), np.arange(0, len(ids), 3)):
                    elig = ids[positions]
                    truth = cosine_distances(base[positions], q)
                    for k in (1, 8, 33, 300):
                        with self.subTest(dim=dim, beta=beta, k=k):
                            bundle = index.candidates(
                                elig, sketches[positions], positions, target, k
                            )
                            got, opened = refine_candidates(bundle, q, cipher, k)
                            want = elig[np.lexsort((elig, truth))[:k]].tolist()
                            self.assertEqual(got, want)
                            self.assertGreaterEqual(opened, len(got))
                            self.assertLessEqual(opened, len(bundle["ids"]) // 8)


class RefinementConsistencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_write_between_metadata_and_payload_restarts_search(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = init(root, "encrypted", 3, 0.25)
            sdk = Device(folder, "https://127.0.0.1")
            async with TestClient(TestServer(make_store_app(root / "hpis"))) as client:

                async def execute(op, grant):
                    _, signed = sdk.envelope(op, grant)
                    nonce, _, sealed, _ = unpack(sdk.holder.verify_key.verify(signed))
                    resp = await client.post("/execute", data=pack([nonce, sealed]))
                    self.assertEqual(resp.status, 200)
                    return unpack(await resp.read())

                sdk.request = execute
                await sdk.put(
                    [
                        dict(id=1, vector=[1, 0, 0], content="old"),
                        dict(id=2, vector=[0, 1, 0], content="b"),
                    ]
                )
                res = await sdk.vector([1, 0, 0], 1, refine=True)
                ids = np.frombuffer(res["candidates"]["ids"], dtype="<i8").tolist()
                self.assertIn(1, ids)
                self.assertEqual(res["stats"]["body_rows"], 0)
                self.assertEqual(
                    res["stats"]["candidate_bytes"], len(res["candidates"]["sealed"])
                )
                await sdk.put([dict(id=1, vector=[-1, 0, 0], content="new")])
                stale = await client.post("/fetch", data=pack([res["handle"], [1]]))
                self.assertEqual(stale.status, 409)
                new = await sdk.vector([1, 0, 0], 1, refine=True)
                winners, _ = refine_candidates(
                    new["candidates"],
                    np.array([1, 0, 0], dtype=F32),
                    sdk.vector_cipher,
                    1,
                )
                self.assertEqual(winners, [2])
                good = await client.post("/fetch", data=pack([new["handle"], winners]))
                self.assertEqual(good.status, 200)
                self.assertEqual([r[0] for r in unpack(await good.read())["rows"]], [2])
                await sdk.delete([1, 2])
                empty = await sdk.vector([1, 0, 0], 5, refine=True)
                self.assertEqual(empty["rows"], [])
                self.assertEqual(empty["stats"]["body_rows"], 0)


class ExactResponseProtocolTest(unittest.TestCase):
    def test_selected_bodies_or_full_public_stream(self):
        import asyncio
        from bench.cluster import running

        async def exercise(path, url):
            # The first ten vectors lie in one tight cluster, including a true
            # tie. Their membership is certain for +x, but uncertain for +y.
            vectors = [[1, (i % 4 - 2) * 0.0001, 0] for i in range(10)]
            vectors += [[-1, (i % 3 - 1) * 0.0001, 0] for i in range(10)]
            rows = [
                {
                    "id": i,
                    "content": f"mail {i}",
                    "keywords": ["focus" if i < 10 else "other"],
                    "vector": vec,
                }
                for i, vec in enumerate(vectors)
            ]
            rows.append({"id": 100, "content": "text only", "keywords": ["focus"]})
            async with Device(path, url) as client:
                await client.put(rows)
                for groups, query, expected_mode in (
                    ([], [1, 0, 0], "certified_one_round"),
                    ([["word", "focus"]], [0, 1, 0], "certified_one_round"),
                    ([], [0, 1, 0], "buffered_candidates"),
                ):
                    result = await client.vector(
                        query, k=10, refine=True, predicates=groups
                    )
                    ids = np.arange(10 if groups else len(vectors))
                    values = unit_vectors(np.asarray(vectors)[ids], 3)
                    truth = cosine_distances(values, unit_vectors(query, 3))
                    expected = ids[np.lexsort((ids, truth))[:10]].tolist()
                    self.assertEqual(
                        result["records"], [[i, f"mail {i}"] for i in expected]
                    )
                    stats = result["stats"]
                    self.assertEqual(stats["body_rows"], 10)
                    if expected_mode == "certified_one_round":
                        self.assertTrue(stats[expected_mode])
                        self.assertEqual(stats["candidate_rows"], 10)
                    else:
                        self.assertNotIn("candidate_stream_bytes", stats)
                        self.assertGreater(stats["candidate_wire_data_bytes"], 0)
                        self.assertLessEqual(
                            stats["refined_rows"], stats["candidate_rows"]
                        )
                # The certified path still requires the authorized VEK.
                key = client.vek
                client.vek = os.urandom(32)
                import aiohttp

                with self.assertRaises(aiohttp.ClientResponseError) as error:
                    await client.vector([1, 0, 0], 10, refine=True)
                self.assertEqual(error.exception.status, 403)
                client.vek = key

        with (
            tempfile.TemporaryDirectory() as tmp,
            running(Path(tmp), dim=3) as endpoints,
        ):
            asyncio.run(exercise(*endpoints))


class ExactOrderingEdges(unittest.TestCase):
    def test_stream_and_legacy_vector_representation(self):
        import asyncio
        from itertools import permutations
        from bench.cluster import running

        async def exercise(folder, url):
            async with Device(folder, url) as client:
                # This valid owner-authenticated older/custom record keeps an independent
                # canonical vector in the encrypted index, different from the body's copy.
                source = unit_vectors([1, 0, 0], 3).tobytes()
                other = unit_vectors([-1, 0, 0], 3).tobytes()
                await client.put(
                    [
                        {
                            "id": 2,
                            "content": "ordinary",
                            "vector": [0, 1, 0],
                            "keywords": ["v"],
                        }
                    ]
                )
                sealed = encrypt_item(client.vector_cipher, b"vector", 1, source)
                body = encrypt_item(
                    client.body_cipher,
                    b"body",
                    1,
                    pack(
                        {
                            "fields": {},
                            "keywords": ["v"],
                            "content": "legacy",
                            "vector": other,
                        }
                    ),
                )
                sketch = client.vectors.sketch(
                    np.frombuffer(source, dtype=F32)
                ).tobytes()
                tags = client.labels.index({}, ["v"])
                await client.request(
                    {"kind": "put", "records": [[1, body, tags, sketch, sealed]]}, {}
                )
                res = await client.vector(
                    [1, 0, 0], 2, refine=True, predicates=[["word", "v"]]
                )
                self.assertEqual(res["records"], [[1, "legacy"], [2, "ordinary"]])
                self.assertFalse(res["stats"]["certified_one_round"])
                self.assertEqual(res["stats"]["candidate_bytes"], 2 * (28 + 3 * 4 + 28))

                # The mathematical cosine ties of permuted float32 coordinates are real;
                # every component's marginal norm and the exact dot with (1,1,1) are equal.
                vectors = list(permutations([1, 0.5, -0.25]))
                await client.put(
                    [
                        {
                            "id": 10 + j,
                            "content": str(j),
                            "vector": v,
                            "keywords": ["tie"],
                        }
                        for j, v in enumerate(vectors)
                    ]
                )
                for k in (4, 6):
                    reply = await client.vector(
                        [1, 1, 1], k, refine=True, predicates=[["word", "tie"]]
                    )
                    self.assertEqual(
                        [r[0] for r in reply["records"]], list(range(10, 10 + k))
                    )

        async def large(folder, url):
            async with Device(folder, url) as client:
                # Near-orthogonal scores make the public candidate set exceed one network chunk.
                vals = np.zeros((272, 100), dtype=F32)
                vals[:, 0] = 1
                vals[:, 1] = np.arange(len(vals), dtype=F32) * 1e-6
                await client.put(
                    [{"id": i, "content": i, "vector": v} for i, v in enumerate(vals)]
                )
                q = np.zeros(100, dtype=F32)
                q[1] = 1
                reply = await client.vector(q, 10, refine=True)
                self.assertEqual(
                    [r[0] for r in reply["records"]], list(range(271, 261, -1))
                )
                self.assertEqual(reply["stats"]["body_rows"], 10)
                self.assertGreater(
                    reply["stats"]["candidate_stream_bytes"],
                    reply["stats"]["candidate_wire_data_bytes"],
                )

        with (
            tempfile.TemporaryDirectory() as tmp,
            running(Path(tmp), dim=3) as endpoints,
        ):
            asyncio.run(exercise(*endpoints))
        with (
            tempfile.TemporaryDirectory() as tmp,
            running(Path(tmp), dim=100) as endpoints,
        ):
            asyncio.run(large(*endpoints))
