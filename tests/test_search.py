import asyncio
import itertools
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import aiohttp
import numpy as np
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nacl.signing import SigningKey

from bench.cluster import running
from hpis.codec import Labels, decrypt_item, encrypt_item, nearest
from hpis.network import post
from hpis.provision import enroll, mint
from hpis.sdk import Device


class IndexTests(unittest.TestCase):
    def test_fuzzy_language(self):
        labels = Labels(os.urandom(32))
        words = [
            "".join(cs) for n in range(4) for cs in itertools.product("甲乙", repeat=n)
        ]
        for a, b in itertools.product(words, repeat=2):
            got = set(labels.predicate("fuzzy", a)).intersection(labels.index({}, [b]))
            short, long = sorted((a, b), key=len)
            if len(short) == len(long):
                expected = sum(x != y for x, y in zip(a, b)) <= 1
            else:
                expected = any(
                    short == long[:i] + long[i + 1 :] for i in range(len(long))
                )
            self.assertEqual(bool(got), expected, (a, b))

    def test_ties_and_body_binding(self):
        np.testing.assert_array_equal(
            nearest(np.array([4, 2, 1]), np.array([1.0, 1.0, 1.0]), 2), [2, 1]
        )
        cipher = AESGCM(os.urandom(32))
        ct = encrypt_item(cipher, b"body", 42, b"private")
        for purpose, rid in [(b"body", 41), (b"vector", 42)]:
            with self.assertRaises(InvalidTag):
                decrypt_item(cipher, purpose, rid, ct)

    def test_network(self):
        async def exercise(folder, url):
            other = enroll(root, "second")
            async with Device(folder, url) as sdk, Device(other, url) as peer:
                rows = [
                    {
                        "id": 1,
                        "fields": {"姓名": "张三"},
                        "keywords": ["小艺", "café"],
                        "vector": [1, 0, 0],
                        "content": "private-sentinel",
                    },
                    {
                        "id": 2,
                        "fields": {"姓名": "李四"},
                        "keywords": ["小艺"],
                        "vector": [0, 1, 0],
                        "content": "second",
                    },
                    {
                        "id": 3,
                        "fields": {"姓名": "张三"},
                        "keywords": ["公园"],
                        "content": "no vector",
                    },
                ]
                await sdk.put(rows)
                await peer.put([{**rows[0], "content": "different account"}])

                def ids(r):
                    return [i for i, _ in r["records"]]

                self.assertEqual(
                    ids(await sdk.terms([["field", "姓名", "张三"]])), [1, 3]
                )
                self.assertEqual(ids(await sdk.terms([["word", "CAFÉ"]])), [1])
                self.assertEqual(ids(await sdk.terms([["fuzzy", "小义"]])), [1, 2])
                self.assertEqual(
                    ids(await sdk.terms([["field", "姓名", "张三"], ["word", "小艺"]])),
                    [1],
                )
                self.assertEqual(
                    ids(await sdk.terms([["word", "café"], ["word", "公园"]], "any")),
                    [1, 3],
                )
                self.assertEqual(ids(await sdk.terms([["word", "空"]])), [])
                self.assertEqual(
                    ids(await sdk.vector([1, 0, 0], 8, refine=True)), [1, 2]
                )
                self.assertEqual(
                    ids(
                        await sdk.vector(
                            [1, 0, 0], 1, predicates=[["field", "姓名", "李四"]]
                        )
                    ),
                    [2],
                )
                self.assertEqual(ids(await peer.terms([["word", "小艺"]])), [1])
                self.assertEqual(
                    (await peer.terms([["word", "小艺"]]))["records"][0][1],
                    "different account",
                )
                scan = await sdk.full_scan(vector=[0.98, 0.02, 0], k=1)
                self.assertEqual((ids(scan), scan["stats"]["body_rows"]), ([1], 3))
                await sdk.put([{**rows[0], "keywords": ["换词"], "vector": [-1, 0, 0]}])
                self.assertEqual(ids(await sdk.terms([["word", "小艺"]])), [2])
                self.assertEqual(ids(await sdk.vector([1, 0, 0], 1, refine=True)), [2])
                await sdk.delete([2])
                self.assertEqual(ids(await sdk.vector([1, 0, 0], 1, refine=True)), [1])
                self.assertEqual(ids(await sdk.terms([["word", "小艺"]])), [])
                with self.assertRaises(ValueError):
                    await sdk.vector([0, 0, 0], 2)
                # The batch fails after its first write. The old row and hot index must survive.
                row = [1, b"bad", [], None, None]
                with self.assertRaises(aiohttp.ClientResponseError):
                    await sdk.request(
                        {"kind": "put", "records": [row, [-1, b"x", [], None, None]]},
                        {},
                    )
                self.assertEqual(
                    (await sdk.terms([["word", "换词"]]))["records"][0][1],
                    "private-sentinel",
                )
                op = {
                    "kind": "terms",
                    "combine": "all",
                    "groups": sdk.groups([["word", "换词"]]),
                }
                envelope = sdk.envelope(op, {"dek": sdk.dek})
                await post(sdk.session, url, "/request", envelope, "hpic")
                with self.assertRaises(aiohttp.ClientResponseError) as e:
                    await post(sdk.session, url, "/request", envelope, "hpic")
                self.assertEqual(e.exception.status, 403)
                sdk.owner = peer.owner
                with self.assertRaises(aiohttp.ClientResponseError) as e:
                    await sdk.request(op, {"dek": sdk.dek})
                self.assertEqual(e.exception.status, 403)
                sdk.ticket, secret = mint(SigningKey.generate())
                sdk.holder = SigningKey(secret)
                with self.assertRaises(aiohttp.ClientResponseError) as e:
                    await sdk.request(op, {})
                self.assertEqual(e.exception.status, 403)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with running(root) as (folder, url):
                asyncio.run(exercise(folder, url))
            with sqlite3.connect(root / "hpis" / "store.sqlite") as db:
                for (body,) in db.execute("SELECT body FROM docs"):
                    self.assertNotIn(b"private-sentinel", body)
                self.assertTrue(
                    all(
                        size == 32
                        for (size,) in db.execute("SELECT length(tag) FROM postings")
                    )
                )
            with running(root, existing=True) as (folder, url):

                async def reopen():
                    async with Device(folder, url) as sdk:
                        self.assertEqual(
                            (await sdk.vector([1, 0, 0], 1, refine=True))["records"],
                            [[1, "private-sentinel"]],
                        )

                asyncio.run(reopen())


if __name__ == "__main__":
    unittest.main()
