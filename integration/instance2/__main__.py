import argparse
import base64
import json
import os
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from nacl.signing import SigningKey

from hpis.codec import pack, unpack
from hpis.provision import read_config, save
from hpis.services import owner_binding
from . import Binding, Verifier, prepare, redeem


def update(folder, **changes):
    data = {**read_config(folder), **changes}
    with tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
        output.write(pack(data))
        src = output.name
    os.replace(src, folder / "config.msgpack")


def install(folder, holder_seed, ticket):
    # Detect mixing two devices' files before replacing a working credential.
    # HPIC verifies the issuer's signature when the ticket is presented.
    cfg = read_config(folder)
    audience, expiry, holder, commitment = unpack(ticket[64:])
    owner = bytes(SigningKey(cfg["owner"]).verify_key)
    expected_holder = bytes(SigningKey(holder_seed).verify_key)
    if (audience != "hpic-owner-v1" or holder != expected_holder or expiry <= time.time()
            or commitment != owner_binding(holder, cfg["aid"], owner)):
        raise ValueError("this ticket and holder do not match the current device")
    update(folder, holder=holder_seed, ticket=ticket)


def main():
    parser = argparse.ArgumentParser(description="HPIS2 Instance-2 ticket exchange")
    cmds = parser.add_subparsers(dest="command", required=True)
    cmd = cmds.add_parser("bind", help="register an independently authorized service SubAID")
    cmd.add_argument("database", type=Path)
    cmd.add_argument("aid_hex")
    cmd.add_argument("service")
    cmd.add_argument("update")
    cmd.add_argument("subaid_b64")
    cmd.add_argument("binding", type=Path)
    cmd = cmds.add_parser("prepare", help="create signed device exchange request and holder key")
    cmd.add_argument("device", type=Path)
    cmd.add_argument("bundle_json", type=Path)
    cmd.add_argument("service")
    cmd.add_argument("update")
    cmd.add_argument("request", type=Path)
    cmd.add_argument("holder", type=Path)
    cmd = cmds.add_parser("redeem", help="issue a short-lived HPIC ticket using pinned keys")
    for field in ("archive", "rsa_pem", "mac_pem", "issuer", "binding", "request", "ticket"):
        cmd.add_argument(field, type=Path)
    cmd = cmds.add_parser("install", help="install the exchanged ticket on its device")
    for field in ("device", "holder", "ticket"):
        cmd.add_argument(field, type=Path)
    cmd = cmds.add_parser("require-bound", help="require owner-bound tickets on this HPIC after restart")
    cmd.add_argument("hpic", type=Path)
    args = parser.parse_args()
    if args.command == "bind":
        subaid = base64.urlsafe_b64decode(args.subaid_b64 + "=" * (-len(args.subaid_b64) % 4))
        binding = Binding.from_store(args.database, bytes.fromhex(args.aid_hex), args.service, args.update, subaid)
        save(args.binding, pack(asdict(binding)))
    elif args.command == "prepare":
        request, holder = prepare(read_config(args.device), json.loads(args.bundle_json.read_bytes()),
                                  args.service, args.update)
        save(args.holder, holder)
        save(args.request, pack(request))
    elif args.command == "redeem":
        verify = Verifier(args.archive, args.rsa_pem.read_bytes(), args.mac_pem.read_bytes())
        ticket = redeem(unpack(args.request.read_bytes()), Binding(**unpack(args.binding.read_bytes())),
                        verify, SigningKey(read_config(args.issuer)["sign"]))
        save(args.ticket, ticket)
    elif args.command == "install":
        install(args.device, args.holder.read_bytes(), args.ticket.read_bytes())
    else:
        update(args.hpic, owner_ticket_only=True)


if __name__ == "__main__":
    main()
