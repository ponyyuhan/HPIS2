"""Launch independent HPIC and HPIS processes on Linux or a local workstation."""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from hpis.provision import init

MODES = ("plain", "encrypted", "trusted-process")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def running(
    root, *, existing=False, mode="encrypted", dim=3, beta=0.25, hpic_port=None
):
    root = Path(root)
    if not existing:
        init(root, mode, dim, beta)
    processes = []
    url = None
    env = {
        **os.environ,
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    with (root / "servers.log").open("a+") as log:
        try:
            for role in ("hpis", "hpic"):
                port = hpic_port if role == "hpic" and hpic_port else free_port()
                while role == "hpis" and port == hpic_port:
                    port = free_port()
                cmd = [
                    sys.executable,
                    "-m",
                    "hpis",
                    "serve",
                    role,
                    str(root / role),
                    "--port",
                    str(port),
                ]
                if role == "hpic":
                    cmd += ["--store-url", url]
                proc = subprocess.Popen(cmd, stdout=log, stderr=log, env=env)
                processes.append(proc)
                end = time.monotonic() + 30
                while time.monotonic() < end:
                    if proc.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read()[-3000:])
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.03)
                else:
                    raise RuntimeError(f"{role} failed to listen on port {port}")
                url = f"https://127.0.0.1:{port}"
            yield root / "devices" / "default", url
        finally:
            for proc in reversed(processes):
                if proc.poll() is None:
                    proc.terminate()
            for proc in processes:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


@contextmanager
def fleet(root, *, existing, dim=None, beta=0.25, base_port=None, modes=MODES):
    with ExitStack() as stack:
        endpoints = {}
        for mode in modes:
            port = None if base_port is None else base_port + MODES.index(mode)
            endpoints[mode] = stack.enter_context(
                running(
                    root / mode,
                    existing=existing,
                    mode=mode,
                    dim=dim,
                    beta=beta,
                    hpic_port=port,
                )
            )
        yield endpoints


def serve_prepared(root, base_port):
    done = threading.Event()
    previous = {
        s: signal.signal(s, lambda *_: done.set())
        for s in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        with fleet(root, existing=True, base_port=base_port) as endpoints:
            for mode, (_, url) in endpoints.items():
                print(f"{mode} {url}", flush=True)
            done.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
