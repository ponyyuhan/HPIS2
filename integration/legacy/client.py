"""Originate indexed HPIC requests using the standard HPIS2 Device search APIs."""

import json
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

from hpis.codec import pack
from hpis.sdk import Device

from .bridge import C2_INFO, PROFILE, binding


class InferenceDevice(Device):
    """Use .terms() and .vector() to return the original HPIC's decrypted C4 answer."""

    def __init__(
        self,
        folder,
        hpic_url,
        *,
        bridge_url,
        crypto,
        pic_public,
        bridge_public,
        token,
        prompt,
    ):
        super().__init__(folder, hpic_url)
        scheme, host = urlsplit(bridge_url).scheme, urlsplit(bridge_url).hostname
        if scheme != "http" or host not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError(
                "use the trusted HPIC loopback adapter or a local SSH forward"
            )
        self.bridge_url = bridge_url.rstrip("/")
        self.crypto, self.pic_public, self.bridge_public = (
            crypto,
            pic_public,
            bridge_public,
        )
        self.token, self.prompt = token, prompt

    def make_inference(self, op, grant):
        if op["kind"] not in ("terms", "vector"):
            raise ValueError("the compatibility API accepts retrieval operations")
        crypto = self.crypto
        eph = X25519PrivateKey.generate()
        public = eph.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        key = crypto.hkdf_sha256(
            eph.exchange(X25519PublicKey.from_public_bytes(self.pic_public)),
            b"PAE2E-v1",
        )
        c1 = {
            "epk": crypto.b64e(public),
            **crypto.aead_encrypt(
                key, json.dumps({"prompt": self.prompt, "at": self.token}).encode()
            ),
        }
        packet = self.envelope(op, grant)
        c2 = crypto.ecies_encrypt_for(
            self.bridge_public, pack(packet), aad=binding(c1), info=C2_INFO
        )
        return {"C1": c1, "C2": {"profile": PROFILE, **c2}}, key

    async def request(self, op, grant):
        data, key = self.make_inference(op, grant)
        async with self.session.post(
            self.bridge_url + "/inference", json=data, allow_redirects=False
        ) as response:
            response.raise_for_status()
            if response.status != 200:
                raise ValueError("unexpected HPIC status")
            result = await response.json()
        return self.crypto.aead_decrypt(key, result["C4"]).decode("utf8")
