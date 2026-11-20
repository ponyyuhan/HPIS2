import argparse
import asyncio
import json
from pathlib import Path

from threadpoolctl import threadpool_limits

from .provision import enroll, init, renew
from .sdk import Device
from .services import serve


async def client(args):
    device_type = Device
    if getattr(args, "hpic_refine", False):
        from integration.mapped_index.protocol import MappedDevice

        device_type = MappedDevice
    async with device_type(args.device, args.url) as sdk:
        if args.command == "put":
            batch = []
            with args.file.open(encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        batch.append(json.loads(line))
                    if len(batch) == 128:
                        await sdk.put(batch)
                        batch.clear()
                if batch:
                    await sdk.put(batch)
            return
        if args.command == "delete":
            await sdk.delete(args.ids)
            return
        predicates = (
            [["field", *p] for p in args.field]
            + [["word", w] for w in args.word]
            + [["fuzzy", w] for w in args.fuzzy]
        )
        if args.vector is None:
            result = await sdk.terms(predicates, args.combine)
        else:
            result = await sdk.vector(
                json.loads(args.vector),
                args.k,
                refine=args.refine or args.hpic_refine,
                predicates=predicates,
                combine=args.combine,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="HPIS Linux encrypted search")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init")
    p.add_argument("root", type=Path)
    p.add_argument("--dim", type=int, required=True)
    p.add_argument(
        "--mode",
        choices=["encrypted", "plain", "trusted-process", "snp"],
        default="encrypted",
    )
    p.add_argument("--measurement", help="expected SNP launch measurement in hex")
    p.add_argument("--minimum-tcb", help="minimum authorized SNP TCB, e.g. 0x...")
    p.add_argument(
        "--snp-product",
        choices=["Milan", "Genoa", "Turin"],
        help="authorized AMD product line (default: verifier's Milan-B1)",
    )
    p.add_argument(
        "--snp-allow-smt",
        action="store_true",
        help="permit SMT in the SNP guest policy",
    )
    p.add_argument("--beta", type=float, default=0.25)
    for cmd in ["enroll", "renew"]:
        p = sub.add_parser(cmd)
        p.add_argument("root", type=Path)
        p.add_argument("name")
    p = sub.add_parser("serve")
    p.add_argument("role", choices=["hpic", "hpis"])
    p.add_argument("folder", type=Path)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--store-url", default="https://127.0.0.1:8444")
    p.add_argument(
        "--shared-vector-index",
        type=Path,
        metavar="DIR",
        help="enable the opt-in co-located HPIC encrypted vector index",
    )
    for cmd in ["put", "query", "delete"]:
        p = sub.add_parser(cmd)
        p.add_argument("device", type=Path)
        p.add_argument("--url", default="https://127.0.0.1:8443")
        if cmd == "put":
            p.add_argument("file", type=Path, help="UTF-8 JSON Lines")
        elif cmd == "delete":
            p.add_argument("ids", nargs="+", type=int)
        else:
            p.add_argument("--field", nargs=2, action="append", default=[])
            p.add_argument("--word", action="append", default=[])
            p.add_argument("--fuzzy", action="append", default=[])
            p.add_argument("--combine", choices=["all", "any"], default="all")
            p.add_argument("--vector", help="JSON array of numbers")
            p.add_argument(
                "--hpic-refine",
                action="store_true",
                help="exact vector search with the prepositioned HPIC index",
            )
            p.add_argument(
                "--refine",
                action="store_true",
                help="exact cosine top-k using encrypted candidate vectors",
            )
            p.add_argument("-k", type=int, default=10)
    args = parser.parse_args()
    if args.command == "query" and args.hpic_refine and args.vector is None:
        parser.error("--hpic-refine requires --vector")
    with threadpool_limits(limits=1, user_api="blas"):
        if args.command == "init":
            print(
                init(
                    args.root,
                    args.mode,
                    args.dim,
                    args.beta,
                    measurement=args.measurement,
                    minimum_tcb=args.minimum_tcb,
                    snp_product=args.snp_product,
                    snp_allow_smt=args.snp_allow_smt,
                )
            )
        elif args.command == "serve":
            if args.shared_vector_index is None:
                serve(args.role, args.folder, args.host, args.port, args.store_url)
            else:
                if args.host != "127.0.0.1":
                    parser.error("the co-located profile listens on 127.0.0.1")
                from integration.mapped_index.protocol import serve as serve_mapped

                serve_mapped(
                    args.role,
                    args.folder,
                    args.shared_vector_index,
                    args.port,
                    args.store_url,
                )
        elif args.command in ("enroll", "renew"):
            (enroll if args.command == "enroll" else renew)(args.root, args.name)
        else:
            asyncio.run(client(args))


if __name__ == "__main__":
    main()
