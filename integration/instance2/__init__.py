"""Exchange an archived Instance-2 credential for a tenant-bound HPIC ticket."""
from __future__ import annotations

import base64
import importlib.util
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from nacl.signing import SigningKey, VerifyKey

from hpis.codec import pack, unpack
from hpis.services import owner_binding


@dataclass(frozen=True)
class Binding:
    """Public enrollment data, maintained by the trusted enrollment operator."""
    aid: bytes
    owner: bytes
    service: str
    update: str
    subaid: bytes

    @classmethod
    def from_store(cls, database, aid, service, update, subaid):
        with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT owner FROM tenants WHERE aid=?", (aid,)).fetchone()
        if row is None or len(subaid) != 32:
            raise ValueError("expected an enrolled AID and an authorized 32-byte SubAID")
        return cls(aid, row[0], service, update, subaid)


def b64u(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def selected_subaid(bundle, service, update):
    matches = [item["subaid_b64"] for item in bundle["subaid"]["items"]
               if item["service_id"] == service and item["update"] == update]
    if len(matches) != 1:
        raise PermissionError("expected exactly one SubAID for this service and update")
    return matches[0]


class Verifier:
    def __init__(self, archive, issuer_pem, mac_pem):
        spec = importlib.util.spec_from_file_location("upstream_instance2_verify",
                                                      Path(archive) / "anon_verify.py")
        self.upstream = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.upstream)
        issuer = serialization.load_pem_public_key(issuer_pem)
        mac_key = serialization.load_pem_public_key(mac_pem)
        if not isinstance(issuer, rsa.RSAPublicKey) or not isinstance(mac_key, ed25519.Ed25519PublicKey):
            raise ValueError("expected the Instance-2 RSA issuer and Ed25519 attribute keys")
        self.rsa = issuer.public_numbers()
        self.mac_pem = mac_pem

    def verify(self, bundle):
        """Verify exactly the archive's canon_json/RSA construction using local trust anchors."""
        try:
            rsa_key = self.rsa
            encoded_n = b64u(rsa_key.n.to_bytes((rsa_key.n.bit_length() + 7) // 8, "big"))
            if bundle["issuer"] != {"n_b64": encoded_n, "e": rsa_key.e}:
                raise PermissionError("unrecognized Instance-2 issuer")
            source = self.upstream
            if not source.verify_mac(bundle["attributes"], bundle["mac1"], "ed25519", self.mac_pem, None):
                raise PermissionError("invalid Instance-2 attribute signature")
            if not source.verify_user_bundle(bundle):
                raise PermissionError("invalid Instance-2 user bundle")
            message = source.compute_M(bundle, rsa_key.n)
            sig = int.from_bytes(source.b64u_dec(bundle["ot_token"]), "big")
            if not 0 < sig < rsa_key.n or pow(sig, rsa_key.e, rsa_key.n) != message:
                raise PermissionError("invalid Instance-2 RSA signature")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise PermissionError("malformed Instance-2 credential") from exc


def prepare(config, bundle, service, update, *, lifetime=300):
    """Device signs its exchange request. Only the device keeps the new holder key."""
    holder = SigningKey.generate()
    expiry = int(time.time()) + lifetime
    proof = SigningKey(config["owner"]).sign(pack([
        "instance2-exchange-v1", config["aid"], service, update, selected_subaid(bundle, service, update),
        bundle["ot_token"], bytes(holder.verify_key), expiry]))
    return {"bundle": bundle, "owner_proof": bytes(proof)}, bytes(holder)


def redeem(request, binding, verifier, hpic_issuer):
    """Run within the HPIC ticket issuer. Binding is from the enrollment registry."""
    bundle = request["bundle"]
    verifier.verify(bundle)
    proof = unpack(VerifyKey(binding.owner).verify(request["owner_proof"]))
    tag, aid, service, update, subaid, signature, holder, expiry = proof
    expected = ("instance2-exchange-v1", binding.aid, binding.service, binding.update,
                b64u(binding.subaid), bundle["ot_token"])
    if (tag, aid, service, update, subaid, signature) != expected:
        raise PermissionError("owner proof does not match the enrolled credential")
    if selected_subaid(bundle, service, update) != subaid:
        raise PermissionError("credential belongs to another SubAID")
    if type(expiry) is not int or not time.time() < expiry <= time.time() + 300:
        raise PermissionError("exchange is expired or exceeds the ticket lifetime")
    VerifyKey(holder)
    claims = ["hpic-owner-v1", expiry, holder, owner_binding(holder, binding.aid, binding.owner)]
    return bytes(hpic_issuer.sign(pack(claims)))
