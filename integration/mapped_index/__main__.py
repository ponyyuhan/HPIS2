"""Serve the mapped profile, or issue one exact vector query."""

import argparse
import asyncio
import json
from pathlib import Path

from .protocol import MappedDevice, serve


async def query(args):
    async with MappedDevice(args.device, args.url, pipeline=not args.serial) as device:
        result = await device.vector(json.loads(args.vector), args.k, refine=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    for role in ("hpis", "hpic"):
        service = subs.add_parser(role)
        service.add_argument("folder", type=Path)
        service.add_argument("--shared", required=True, type=Path)
        service.add_argument("--port", required=True, type=int)
        service.add_argument("--store-url", default="https://127.0.0.1:8444")
    client = subs.add_parser("query")
    client.add_argument("device", type=Path)
    client.add_argument("--vector", required=True, help="JSON array")
    client.add_argument(
        "--serial",
        action="store_true",
        help="wait for ranking before sending the HPIS request",
    )
    client.add_argument("-k", type=int, default=10)
    client.add_argument("--url", default="https://127.0.0.1:8443")
    args = parser.parse_args()
    if args.action == "query":
        asyncio.run(query(args))
    else:
        serve(args.action, args.folder, args.shared, args.port, args.store_url)


if __name__ == "__main__":
    main()
