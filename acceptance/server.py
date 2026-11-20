"""Service process used by the harness: the production hpis services, with the
AcceptanceStore kernel subclass installed in the HPIS process only.

python -m acceptance.server {hpis,hpic} FOLDER --port N [--store-url URL] [--cpu C]
"""

import argparse
import os

for _name in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_name] = "1"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("role", choices=["hpis", "hpic"])
    p.add_argument("folder")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--store-url")
    p.add_argument("--cpu", type=int, help="pin this process to one CPU (Linux)")
    p.add_argument("--mapped-shared", help="serve the HPIC-mapped profile (appendix)")
    a = p.parse_args()
    if a.cpu is not None and hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {a.cpu})

    from threadpoolctl import threadpool_limits

    from hpis import services

    from .kernel import AcceptanceStore, install_hpic_rerank

    # make_store_app() resolves Store from hpis.services at call time.
    services.Store = AcceptanceStore
    if a.role == "hpic":
        install_hpic_rerank()
    with threadpool_limits(limits=1, user_api="blas"):
        if a.mapped_shared:
            from integration.mapped_index import protocol

            # The mapped body-fetch path opens its own Store; give it the same SQLite settings.
            protocol.Store = AcceptanceStore
            protocol.serve(a.role, a.folder, a.mapped_shared, a.port, a.store_url)
        else:
            services.serve(a.role, a.folder, "127.0.0.1", a.port, a.store_url)


if __name__ == "__main__":
    main()
