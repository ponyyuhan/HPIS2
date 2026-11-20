"""Single-request authorization across the gap between the header and the selected IDs."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from hpis.codec import decrypt_item, pack, unit_vectors, unpack
from hpis.provision import init
from hpis.store import Store
from integration.mapped_index import protocol
from integration.mapped_index.index import PROFILE, current_revision


def frame(data):
    body = pack(data)
    return len(body).to_bytes(4, "big") + body


class StreamedAuthorizationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        device_dir = init(root, "encrypted", 3, 0.25)
        self.sdk = protocol.MappedDevice(device_dir, "https://hpic.internal")
        self.db = Store(root / "hpis" / "store.sqlite")
        self.addCleanup(self.db.close)
        app = protocol.mapped_store(root / "hpis", root / "shared")
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

        async def store_records(op, grant):
            self.db.write(self.sdk.aid, self.sdk.mode, self.sdk.dim, op["records"])

        self.sdk.request = store_records
        await self.sdk.put([
            {"id": 4, "vector": [1.0, 0.0, 0.0], "content": "original"},
            {"id": 7, "vector": [0.0, 1.0, 0.0], "content": "unselected"},
        ])

    def header(self):
        q = unit_vectors([1, 0, 0], self.sdk.dim)
        rq = (q @ self.sdk.vectors.matrix).tobytes()
        idx = self.sdk.index_id
        target = protocol.target_binding(os.urandom(32), idx, 1, q.tobytes(), rq)
        operation = {"kind": PROFILE, "id": idx, "k": 1, "target_binding": target}
        _, holder_message = self.sdk.envelope(operation, {})
        nonce, _, sealed, _ = unpack(self.sdk.holder.verify_key.verify(holder_message))
        revision = current_revision(self.db.db, self.sdk.aid)
        return frame([nonce, sealed, [], idx, 1, target, revision])

    async def send(self, data):
        return await self.client.post("/mapped/fetch", data=data,
                                      headers={"Content-Type": "application/octet-stream"})

    async def test_only_selected_body_and_one_use(self):
        data = self.header() + frame([4])
        async with await self.send(data) as response:
            self.assertEqual(response.status, 200)
            rows = unpack(await response.read())["rows"]
        self.assertEqual([rid for rid, _ in rows], [4])
        content = decrypt_item(self.sdk.body_cipher, b"body", 4, rows[0][1])
        self.assertEqual(unpack(content)["content"], "original")
        async with await self.send(data) as response:
            self.assertEqual(response.status, 403)

    async def between_frames(self, after_header, *, expire=False):
        """Coordinate the upload at the HPIS authorization point without a wall-clock wait."""
        checked = asyncio.Event()
        header = self.header()
        original_check = protocol.check_deadline

        def check(nonce, exp):
            now = exp + 1 if checked.is_set() and expire else exp - 1
            with patch("hpis.services.time.time", return_value=now):
                original_check(nonce, exp)
            checked.set()

        async def body():
            yield header
            await asyncio.wait_for(checked.wait(), timeout=5)
            if after_header is not None:
                await after_header()
            yield frame([4])

        with patch.object(protocol, "check_deadline", check):
            return await self.send(body())

    async def test_expiry_between_frames(self):
        async with await self.between_frames(None, expire=True) as response:
            self.assertEqual(response.status, 403)

    async def test_write_between_frames(self):
        async def update():
            await self.sdk.put([{"id": 4, "vector": [1, 0, 0], "content": "updated"}])

        async with await self.between_frames(update) as response:
            self.assertEqual(response.status, 200)
            result = unpack(await response.read())
        self.assertTrue(result["retry"])
        self.assertNotIn("rows", result)
        async with await self.send(self.header() + frame([4])) as response:
            self.assertEqual(response.status, 200)
            rows = unpack(await response.read())["rows"]
        self.assertEqual([rid for rid, _ in rows], [4])
        content = decrypt_item(self.sdk.body_cipher, b"body", 4, rows[0][1])
        self.assertEqual(unpack(content)["content"], "updated")
