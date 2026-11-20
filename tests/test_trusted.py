import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
import numpy as np
from nacl.public import PrivateKey, SealedBox

from bench.cluster import running
from hpis import attestation
from hpis.codec import VectorCodec, rotation, unit_vectors
from hpis.provision import init
from hpis.sdk import Device


class TrustedSearch(unittest.TestCase):
    def test_confidential_index_software_reference(self):
        async def go(folder, url):
            async with Device(folder, url) as sdk:
                vectors = [[3, 4, 0], [-3, -4, 0], [0, -1, 0], [3, 4, 0]]
                await sdk.put(
                    {"id": i, "content": str(i), "vector": v}
                    for i, v in enumerate(vectors)
                )
                for _ in range(2):
                    got = await sdk.vector([3, 4, 0], 99)
                    self.assertEqual([r[0] for r in got["records"]], [0, 3, 2, 1])
                with self.assertRaises(aiohttp.ClientResponseError) as err:
                    await sdk.request(
                        {
                            "kind": "vector",
                            "target": unit_vectors([3, 4, 0], 3).tobytes(),
                            "k": 1,
                            "vek": os.urandom(32),
                        },
                        {"dek": sdk.dek},
                    )
                self.assertEqual(err.exception.status, 403)
                await sdk.delete([0])
                self.assertEqual(
                    (await sdk.vector([3, 4, 0], 1))["records"], [[3, "3"]]
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with running(root, mode="trusted-process") as (folder, url):
                asyncio.run(go(folder, url))
            db = sqlite3.connect(root / "hpis" / "store.sqlite")
            try:
                self.assertEqual(
                    db.execute(
                        "SELECT DISTINCT length(sketch),length(original) FROM vectors"
                    ).fetchall(),
                    [(0, 40)],
                )
            finally:
                db.close()

    def test_snp_binding_and_no_software_fallback(self):
        async def check():
            sk = PrivateKey.generate()
            pub, challenge = bytes(sk.public_key), os.urandom(32)
            policy = {"measurement": os.urandom(48).hex(), "minimum_tcb": "0x123"}
            with patch.object(
                attestation, "command", new=AsyncMock(return_value=b"report")
            ) as cmd:
                evidence = await attestation.evidence(challenge, pub)
                self.assertEqual(cmd.await_args.args[1], challenge + pub)
                box = await attestation.verify(evidence, challenge, policy)
                self.assertEqual(SealedBox(sk).decrypt(box.encrypt(b"data")), b"data")
                argv, stdin = cmd.await_args.args
                self.assertIn("-report_data=" + (challenge + pub).hex(), argv)
                self.assertIn("-measurement=" + policy["measurement"], argv)
                self.assertEqual(stdin, b"report")
            with patch.object(
                attestation, "command", new=AsyncMock(side_effect=PermissionError)
            ):
                with self.assertRaises(PermissionError):
                    await attestation.verify(evidence, challenge, policy)

        asyncio.run(check())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                init(root, "snp", 3, 0.25)
            folder = init(root, "snp", 3, 0.25, measurement=os.urandom(48).hex())
            device = Device(folder, "https://127.0.0.1:1")
            self.assertIsNone(device.box)

    def test_extreme_finite_vectors(self):
        with np.errstate(over="ignore", under="ignore"):
            units = unit_vectors([[1e308, 1e308], [1e-300, 1e-300]], 2)
        np.testing.assert_allclose(units, np.full((2, 2), 2.0**-0.5), rtol=1e-6)
        for invalid in ([0, 0], [0, float("inf")], [float("nan"), 0]):
            with self.assertRaises(ValueError):
                unit_vectors(invalid, 2)

    def test_single_use_noise_within_bound(self):
        dim, beta = 12, 0.25
        m = rotation(dim)
        codec = VectorCodec(dim, beta, m)
        q = unit_vectors(np.ones(dim), dim)
        draws = [codec.sketch(q) for _ in range(100)]
        self.assertEqual(len({a.tobytes() for a in draws}), 100)
        self.assertTrue(
            all(np.linalg.norm(a - q @ m) <= beta / 4 + 1e-6 for a in draws)
        )
