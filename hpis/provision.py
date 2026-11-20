"""Offline enrollment and local TLS bootstrap for the Linux deployment."""

from contextlib import closing
import datetime as dt
import os
import sqlite3
import time
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from nacl.public import PrivateKey
from nacl.signing import SigningKey

from .codec import MAX_DIM, pack, rotation, unpack
from .store import SCHEMA


def save(path, value):
    # Exclusive creation keeps existing service identities intact on a retry.
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
        f.write(value)


def read_config(folder):
    return unpack((Path(folder) / "config.msgpack").read_bytes())


def certs(root):
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HPIS local root")])

    def builder(name, key):
        return (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=365))
        )

    def key_usage(*, ca):
        return x509.KeyUsage(
            digital_signature=True,
            content_commitment=False,
            key_encipherment=False,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=ca,
            crl_sign=ca,
            encipher_only=False,
            decipher_only=False,
        )

    ca_id = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    authority = x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_id)
    ca = (
        builder(ca_name, ca_key)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .add_extension(key_usage(ca=True), True)
        .add_extension(ca_id, False)
        .add_extension(authority, False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_bytes = ca.public_bytes(serialization.Encoding.PEM)
    for name in ["hpic", "hpis"]:
        folder = root / name
        key = ec.generate_private_key(ec.SECP256R1())
        dns = [x509.DNSName(name + ".internal")]
        dn = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        use = [ExtendedKeyUsageOID.SERVER_AUTH]
        if name == "hpic":
            use.append(ExtendedKeyUsageOID.CLIENT_AUTH)
        cert = (
            builder(dn, key)
            .add_extension(x509.SubjectAlternativeName(dns), False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(key_usage(ca=False), True)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False
            )
            .add_extension(authority, False)
            .add_extension(x509.ExtendedKeyUsage(use), False)
            .sign(ca_key, hashes.SHA256())
        )
        save(
            folder / "tls.key",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        save(folder / "tls.pem", cert.public_bytes(serialization.Encoding.PEM))
        save(folder / "ca.pem", ca_bytes)
    return ca_bytes


def init(
    root,
    mode,
    dim,
    beta,
    *,
    measurement=None,
    minimum_tcb=None,
    snp_product=None,
    snp_allow_smt=False,
):
    if (
        mode not in ("plain", "encrypted", "trusted-process", "snp")
        or not 1 <= dim <= MAX_DIM
        or not 0 < beta <= 2
    ):
        raise ValueError("invalid mode, dimension, or beta")
    if mode == "snp" and (not measurement or len(bytes.fromhex(measurement)) != 48):
        raise ValueError("hardware mode needs the expected 48-byte launch measurement")
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("choose an empty deployment directory")
    for name in ["issuer", "hpic", "hpis", "devices"]:
        (root / name).mkdir(mode=0o700)
    certs(root)
    issuer, receiver = SigningKey.generate(), PrivateKey.generate()
    save(root / "issuer" / "config.msgpack", pack({"sign": bytes(issuer)}))
    save(root / "hpic" / "config.msgpack", pack({"verify": bytes(issuer.verify_key)}))
    save(
        root / "hpis" / "config.msgpack",
        pack({} if mode == "snp" else {"box": bytes(receiver)}),
    )
    public = dict(mode=mode, dim=dim, beta=beta)
    if mode == "snp":
        public["snp"] = {
            "measurement": measurement,
            "minimum_tcb": minimum_tcb,
            "product": snp_product,
            "allow_smt": snp_allow_smt,
        }
    else:
        public["box"] = bytes(receiver.public_key)
    save(root / "public.msgpack", pack(public))
    db = sqlite3.connect(root / "hpis" / "store.sqlite")
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
    finally:
        db.close()
    return enroll(root, "default")


def mint(issuer, ttl=86400):
    holder = SigningKey.generate()
    claims = pack(["hpic-search-v1", int(time.time()) + ttl, bytes(holder.verify_key)])
    return bytes(issuer.sign(claims)), bytes(holder)


def enroll(root, name):
    root = Path(root)
    if not name or name in (".", "..") or Path(name).name != name:
        raise ValueError("invalid device name")
    public = unpack((root / "public.msgpack").read_bytes())
    folder = root / "devices" / name
    folder.mkdir(mode=0o700)
    owner = SigningKey.generate()
    ticket, holder = mint(SigningKey(read_config(root / "issuer")["sign"]))
    aid = os.urandom(16)
    device = {
        **public,
        "aid": aid,
        "owner": bytes(owner),
        "ticket": ticket,
        "holder": holder,
    }
    if public["mode"] != "plain":
        device.update(sse=os.urandom(32), dek=os.urandom(32), vek=os.urandom(32))
    if public["mode"] == "encrypted":
        device["rotation"] = rotation(public["dim"]).tobytes()
    save(folder / "config.msgpack", pack(device))
    save(folder / "ca.pem", (root / "hpic" / "ca.pem").read_bytes())
    with closing(sqlite3.connect(root / "hpis" / "store.sqlite")) as db, db:
        db.execute(
            "INSERT INTO tenants VALUES(?,?,?,?,?)",
            (
                aid,
                bytes(owner.verify_key),
                public["mode"],
                public["dim"],
                public["beta"],
            ),
        )
    return folder


def renew(root, name):
    folder = Path(root) / "devices" / name
    if name in ("", ".", "..") or Path(name).name != name:
        raise ValueError("invalid device name")
    cfg = read_config(folder)
    cfg["ticket"], cfg["holder"] = mint(
        SigningKey(read_config(Path(root) / "issuer")["sign"])
    )
    temp = folder / (".renew-" + os.urandom(8).hex())
    save(temp, pack(cfg))
    os.replace(temp, folder / "config.msgpack")
