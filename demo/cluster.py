"""Device-side client and the local process cluster (HPIS, HPIC, HPIC 1.0).

Two HPIS deployments are provisioned from the same device data:
  tee    HPIS 2.0 tenants: AES-256-GCM bodies, HMAC-SHA256 SSE labels, AES-GCM
         vector index opened only inside HPIS' trusted compute region.
  plain  the same service stack with unencrypted storage (baseline for the
         encrypted-vs-常规检索 overhead check, never used by the race lanes).

Each deployment holds one tenant per device (folder ``devices/<tenant>``:
default = PC mail, phone, watch, car) plus decoy tenants (``decoy-NN``).
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

import aiohttp

from hpis.network import tls_client
from hpis.provision import renew
from hpis.sdk import Device

from . import config, retrieval

DEPLOYMENTS = {"tee": "trusted-process", "plain": "plain"}


def deploy_root(name: str) -> Path:
    return config.RUNTIME / "deploy" / name


def device_folder(name: str, tenant: str = "default") -> Path:
    return deploy_root(name) / "devices" / tenant


def device_tenants(name: str) -> list[str]:
    """Tenants of the user's own devices in one deployment (decoys never query)."""
    folder = deploy_root(name) / "devices"
    if not folder.exists():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_dir() and not p.name.startswith("decoy-"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DemoDevice(Device):
    """hpis SDK device plus helpers to build envelopes for the demo routes."""

    def groups(self, plan):  # type: ignore[override]
        return retrieval.sse_groups(self.labels, plan)

    def vector_op(self, unit, k: int, plan: dict) -> dict:
        op = {
            "kind": "vector",
            "target": self.vectors.sketch(unit).tobytes(),
            "k": k,
            "refine": False,
            "groups": self.groups(plan),
            "combine": "all",
        }
        if self.mode in ("snp", "trusted-process"):
            op["vek"] = self.vek  # sealed to HPIS; HPIC never sees it
        return op

    def scan_op(self) -> dict:
        return {"kind": "scan"}

    def ask_envelope(self, op: dict, ask: dict):
        return self.envelope(op, {"dek": self.dek, "ask": ask})


async def wait_port(port: int, timeout: float = 60.0, proc=None):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if proc is not None and proc.returncode is not None:
            raise RuntimeError(f"worker exited with {proc.returncode}")
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.1)
    raise TimeoutError(f"port {port} not ready")


class Cluster:
    def __init__(self, deployments=("tee", "plain"), *, cloud=True):
        self.deployments = deployments
        self.with_cloud = cloud
        self.procs = []
        self.hpic_url: dict[str, str] = {}
        self.cloud_url = None
        self.log_dir = config.RUNTIME / "logs"

    async def _spawn(self, *args):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log = open(self.log_dir / f"{len(self.procs):02d}-{args[0]}-{Path(args[1]).parent.name}.log", "wb")
        env = dict(os.environ)
        if args[0] == "cloud":
            from .llm import lane_env

            env = lane_env("plain", env)  # HPIC 1.0 may have its own model server
        env["PYTHONPATH"] = str(config.ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "demo.worker", *map(str, args), cwd=str(config.ROOT), env=env, stdout=log, stderr=log
            )
        finally:
            log.close()  # the child keeps its own descriptor
        self.procs.append(proc)
        return proc

    async def _pair(self, name: str, key: str):
        """One HPIS process + one HPIC process for a lane (lanes never share a process)."""
        root = deploy_root(name)
        store_port, hpic_port = free_port(), free_port()
        p1 = await self._spawn("hpis", root / "hpis", store_port)
        await wait_port(store_port, proc=p1)
        p2 = await self._spawn("hpic", root / "hpic", hpic_port, "--store-url", f"https://127.0.0.1:{store_port}")
        await wait_port(hpic_port, proc=p2)
        self.hpic_url[key] = f"https://127.0.0.1:{hpic_port}"

    def renew_tickets(self):
        for name in self.deployments:
            for tenant in device_tenants(name):
                renew(deploy_root(name), tenant)  # fresh one-day anonymous ticket

    async def start(self):
        self.renew_tickets()
        for name in self.deployments:
            await self._pair(name, name)
            if name == "tee" and self.with_cloud and os.environ.get("HPIS_DEMO_V1") == "1":
                # The HPIS 1.0 lane reads the same encrypted store through its own
                # HPIS/HPIC processes, so its full-fetch load never queues 2.0 requests.
                await self._pair(name, "tee-v1")
        if self.with_cloud:
            port = free_port()
            p3 = await self._spawn("cloud", deploy_root(self.deployments[0]) / "hpic", port)
            await wait_port(port, proc=p3)
            self.cloud_url = f"https://127.0.0.1:{port}"
        return self

    async def stop(self):
        for proc in self.procs:
            if proc.returncode is None:
                proc.terminate()
        for proc in self.procs:
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill()
        self.procs.clear()

    def device(self, key: str, tenant: str = "default") -> DemoDevice:
        return DemoDevice(device_folder(key.split("-")[0], tenant), self.hpic_url[key])

    def cloud_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls_client(device_folder(self.deployments[0]))),
            timeout=aiohttp.ClientTimeout(total=900),
        )
