"""Run with: python -m unittest integration.mapped_index.tests -v"""

import asyncio
import itertools
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest

import aiohttp
import numpy as np
from nacl.signing import SigningKey

from bench.run import oracle
from hpis.codec import pack
from hpis.provision import enroll
from .bench import running
from .index import MappedImage, image_path
from .protocol import MappedDevice


class MappedProtocolTest(unittest.TestCase):
    def test_numerical_equalities(self):
        async def check(root, dim, inputs, query, want):
            with running(root, "plain", dim, joint=True) as pair:
                for folder, url in pair:
                    async with MappedDevice(folder, url) as sdk:
                        await sdk.put(
                            [
                                {"id": i, "vector": v, "content": i}
                                for i, v in enumerate(inputs)
                            ]
                        )
                        got = await sdk.vector(query, len(want), refine=True)
                        self.assertEqual([i for i, _ in got["records"]], want)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in (3, 4):
                terms = [1.0, 0.5, -0.25, 0.125][:d]
                asyncio.run(
                    check(
                        root / str(d),
                        d,
                        list(itertools.permutations(terms)),
                        [1.0] * d,
                        [0, 1, 2, 3],
                    )
                )
            asyncio.run(
                check(
                    root / "narrow",
                    2,
                    [[1.0, 2**-30], [1.0, 2**-31]],
                    [1.0, 0.0],
                    [1, 0],
                )
            )

    def test_exact_scope_ciphertext_and_updates(self):
        async def go(base, endpoint):
            folder, url = endpoint
            other = enroll(base, "other")
            rng = np.random.default_rng(19)
            x = rng.standard_normal((160, 9))
            x[0] = x[1] = x[2]
            q = np.vstack([x[0], rng.standard_normal((36, 9))])
            gold = oracle(x, q, 10)
            async with MappedDevice(folder, url) as a, MappedDevice(other, url) as b:
                await a.put(
                    [
                        {
                            "id": i,
                            "content": f"record-{i}",
                            "vector": v,
                            "fields": {"parity": str(i % 2)},
                        }
                        for i, v in enumerate(x)
                    ]
                )
                await b.put([{"id": 0, "content": "only in B", "vector": x[0]}])
                await a.put([{"id": 9999, "content": "no vector"}])
                for v, expected in zip(q, gold):
                    res = await a.vector(v, 10, refine=True)
                    self.assertEqual([t[0] for t in res["records"]], expected)
                    self.assertEqual(res["stats"]["body_rows"], 10)
                res = await b.vector(x[0], 10, refine=True)
                self.assertEqual(res["records"], [[0, "only in B"]])
                good = (await a.vector(x[0], 10, refine=True))["stats"]["revision"]
                path = image_path(base / "shared", a.index_id)
                image = MappedImage(path)
                with sqlite3.connect(base / "hpis/store.sqlite") as db:
                    blobs = [
                        row[0]
                        for row in db.execute(
                            "SELECT original FROM vectors WHERE aid=? ORDER BY rid",
                            (a.aid,),
                        )
                    ]
                self.assertEqual(bytes(image.sealed), b"".join(blobs))
                original_vek = a.vek
                a.vek = os.urandom(32)
                with self.assertRaises(aiohttp.ClientResponseError) as e:
                    await a.vector(q[0], 10, refine=True)
                self.assertEqual(e.exception.status, 403)
                a.vek = original_vek
                # The HPIS body read must compare the same revision used for ranking.
                await a.delete([0, 1])
                res = await a.vector(q[0], 10, refine=True)
                self.assertNotEqual(res["stats"]["revision"], good)
                self.assertFalse(set(i for i, _ in res["records"]) & {0, 1})
                await a.put([{"id": 0, "content": "restored", "vector": -x[0]}])
                r = await a.vector(-x[0], 1, refine=True)
                self.assertEqual(r["records"], [[0, "restored"]])
                old_owner = a.owner
                a.owner = SigningKey.generate()
                with self.assertRaises(aiohttp.ClientResponseError) as e:
                    await a.vector(q[0], 10, refine=True)
                self.assertEqual(e.exception.status, 403)
                a.owner = old_owner
                # Same AID and K with a different q must also fail at HPIS.
                previous = a.envelope

                def change_query(op, grant):
                    return previous(
                        op,
                        {
                            **grant,
                            "q": np.asarray(
                                [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                                dtype="<f4",
                            ).tobytes(),
                        },
                    )

                from unittest.mock import patch

                for strategy in (True, False):
                    a.pipeline = strategy
                    with patch.object(a, "envelope", change_query):
                        with self.assertRaises(aiohttp.ClientResponseError) as error:
                            await a.vector(q[0], 1, refine=True)
                        self.assertEqual(error.exception.status, 403)
                    honest = await a.vector(q[0], 1, refine=True)
                    self.assertEqual(honest["stats"]["pipelined"], strategy)
                a.pipeline = True
                # A valid B holder cannot present A's delegated public index.
                # Both tenants have valid credentials. A holder cannot pair
                # B's valid owner-signed HPIS operation with A's rank grant.
                from unittest.mock import patch

                def swap_inner(op, grant):
                    # a holds the ticket, delegation, and both data grants.
                    # Re-build only the signed, sealed inner request as B.
                    nonce, exp = os.urandom(16), int(__import__("time").time()) + 45
                    wrong = {**op, "id": b.index_id}
                    signed = b.owner.sign(pack(["hpis-v1", b.aid, nonce, exp, wrong]))
                    inner = b.box.encrypt(pack([b.aid, bytes(signed)]))
                    return [
                        a.ticket,
                        bytes(a.holder.sign(pack([nonce, exp, inner, grant]))),
                    ]

                with patch.object(a, "envelope", swap_inner):
                    with self.assertRaises(aiohttp.ClientResponseError) as error:
                        await a.vector(q[0], 1, refine=True)
                    self.assertEqual(error.exception.status, 403)
                old_id, old_deleg = b.index_id, b.delegation
                b.index_id, b.delegation = a.index_id, a.delegation
                with self.assertRaises(aiohttp.ClientResponseError) as e:
                    await b.vector(q[0], 10, refine=True)
                self.assertEqual(e.exception.status, 403)
                b.index_id, b.delegation = old_id, old_deleg
                v = await a.vector(
                    x[0], 8, refine=True, predicates=[["field", "parity", "1"]]
                )
                self.assertTrue(all(i % 2 for i, _ in v["records"]))
                await a.delete(list(range(160)))
                r = await a.vector(q[0], 10, refine=True)
                self.assertEqual(r["records"], [])

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with running(base, "encrypted", 9) as end:
                asyncio.run(go(base, end))
