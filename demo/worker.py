"""Child-process entry points: each cloud role runs in its own process."""

from __future__ import annotations

import argparse
import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from aiohttp import web  # noqa: E402

from hpis.network import tls_server  # noqa: E402
from hpis.services import make_store_app  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["hpis", "hpic", "cloud"])
    parser.add_argument("folder")
    parser.add_argument("port", type=int)
    parser.add_argument("--store-url")
    args = parser.parse_args()
    if args.role == "hpis":
        import hpis.services as services

        from .store import DemoStore

        services.Store = DemoStore  # identical exact path for plaintext and TEE tenants
        app = make_store_app(args.folder)
        ssl = tls_server(args.folder, True)
    elif args.role == "hpic":
        from .hpic import make_demo_hpic_app

        app = make_demo_hpic_app(args.folder, args.store_url)
        ssl = tls_server(args.folder, False)
    else:
        from .cloud import make_cloud_app

        app = make_cloud_app()
        ssl = tls_server(args.folder, False)
    web.run_app(app, host="127.0.0.1", port=args.port, ssl_context=ssl, access_log=None, print=None)


if __name__ == "__main__":
    main()
