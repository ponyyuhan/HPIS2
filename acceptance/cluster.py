"""Provision tenants, start one HPIS + one HPIC process on loopback TLS, describe the host."""

import hashlib
import importlib.metadata
import os
import platform
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import aiohttp

from hpis.codec import pack, unit_vectors, unpack
from hpis.network import tls_client
from hpis.provision import enroll, init
from hpis.sdk import Device

ROOT = Path(__file__).resolve().parents[1]
BLAS_ENV = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}


def provision(root, dim, beta, extra_betas=(), *, with_tee=True, second_plain=False):
    """One deployment (issuer, HPIC, HPIS, one SQLite DB) holding several tenants.

    plain   : plaintext labels, plaintext bodies, raw unit vectors
    plain_b : a second, identical plaintext tenant (own matrix), used by the 3-arm
              appendix so that no two arms share a vector matrix / cache footprint
    ppe     : HMAC-SHA256 SSE tags, AES-256-GCM bodies, RAP sketches h = xR + e (beta)
    tee     : HMAC-SHA256 SSE tags, AES-256-GCM bodies, AES-GCM vector index opened
              only inside the trusted HPIS process ("trusted-process" software TEE)
    """
    root = Path(root)
    tenants = {"plain": ("plain", beta)}
    folders = {"plain": init(root, "plain", dim, beta)}
    public_path = root / "public.msgpack"
    original = public_path.read_bytes()
    extra = [("plain_b", "plain", beta)] if second_plain else []
    extra += [("ppe", "encrypted", beta)] + ([("tee", "trusted-process", beta)] if with_tee else [])
    extra += [(beta_name(b), "encrypted", b) for b in extra_betas]
    try:
        for name, mode, b in extra:
            public_path.write_bytes(pack({**unpack(original), "mode": mode, "beta": b}))
            folders[name] = enroll(root, name)
            tenants[name] = (mode, b)
    finally:
        public_path.write_bytes(original)
    return folders, tenants


def beta_name(b):
    return "ppe_b" + f"{b:g}".replace(".", "")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pick_cpus(n=3):
    """Distinct physical cores (Linux); CPU 0 used last. None elsewhere."""
    if not hasattr(os, "sched_getaffinity"):
        return None
    allowed = sorted(os.sched_getaffinity(0))
    if len(allowed) < n:
        return None

    def core(c):
        base = Path(f"/sys/devices/system/cpu/cpu{c}/topology")
        try:
            return (base / "physical_package_id").read_text().strip(), (base / "core_id").read_text().strip()
        except OSError:
            return None, str(c)

    chosen, seen = [], set()
    for c in allowed[1:] + allowed[:1]:
        key = core(c)
        if key in seen:
            continue
        seen.add(key)
        chosen.append(c)
        if len(chosen) == n:
            return chosen
    return None


def _wait(proc, port, log):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.flush()
            raise RuntimeError(f"service exited early (see {log.name})")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"service did not listen on {port}")


@contextmanager
def services(root, cpus=None, mapped_shared=None):
    """HPIS then HPIC, each its own process, real TLS 1.3 (HPIC->HPIS mutual TLS)."""
    root = Path(root)
    env = {**os.environ, **BLAS_ENV}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH")]))
    procs = []
    log = (root / "services.log").open("a+")
    try:
        url = None
        info = {}
        for role, cpu in (("hpis", cpus[2] if cpus else None), ("hpic", cpus[1] if cpus else None)):
            port = free_port()
            cmd = [sys.executable, "-m", "acceptance.server", role, str(root / role), "--port", str(port)]
            if url:
                cmd += ["--store-url", url]
            if cpu is not None:
                cmd += ["--cpu", str(cpu)]
            if mapped_shared:
                cmd += ["--mapped-shared", str(mapped_shared)]
            proc = subprocess.Popen(cmd, stdout=log, stderr=log, env=env, cwd=ROOT)
            procs.append(proc)
            _wait(proc, port, log)
            url = f"https://127.0.0.1:{port}"
            info[role] = {"pid": proc.pid, "port": port, "cpu": cpu}
        info["url"] = url
        yield info
    finally:
        for proc in reversed(procs):
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        log.close()


class BenchDevice(Device):
    """Production SDK device. Every vector op carries the (server-side) kernel selector,
    in all arms, so the signed request bytes are the same shape for every arm.

    rerank = (k, coarse_k): ask HPIS for the PPE Top-coarse_k, and grant HPIC the query
    so it re-ranks the decrypted candidates to an exact Top-k (vec_ppe_rerank)."""

    kernel = "unified"
    rerank = None
    _rerank_q = None

    async def vector(self, vector, k=10, *, refine=False, predicates=(), combine="all"):
        if self.rerank is None:
            self._rerank_q = None
            return await super().vector(vector, k, refine=refine, predicates=predicates, combine=combine)
        final_k, coarse_k = self.rerank
        # The unit query reaches HPIC only (inside the holder-signed grant), like the DEK.
        self._rerank_q = (final_k, unit_vectors(vector, self.dim).tobytes())
        return await super().vector(vector, coarse_k, refine=refine, predicates=predicates, combine=combine)

    async def request(self, op, grant):
        if op.get("kind") == "vector":
            op["acceptance_kernel"] = self.kernel
            if self._rerank_q is not None:
                k, q = self._rerank_q
                grant["scan"] = {"kind": "rerank", "q": q, "k": k}
        return await super().request(op, grant)


def open_devices(folders, url):
    """All tenants share one aiohttp session, hence one persistent TLS connection to HPIC."""
    any_folder = next(iter(folders.values()))
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=tls_client(any_folder)))
    devices = {}
    for name, folder in folders.items():
        dev = BenchDevice(folder, url)
        dev.session = session
        devices[name] = dev
    return devices, session


def code_digest(paths):
    h = hashlib.sha256()
    files = sorted(p for d in paths for p in Path(d).glob("*.py"))
    for p in files:
        h.update(str(p.relative_to(ROOT)).encode() + b"\0" + p.read_bytes())
    return {"sha256": h.hexdigest(), "files": len(files)}


def machine():
    info = {
        "os": platform.platform(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }
    if hasattr(os, "sched_getaffinity"):
        info["cpus_allowed"] = len(os.sched_getaffinity(0))
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        attrs = {}
        for line in cpuinfo.read_text().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                attrs.setdefault(k.strip(), v.strip())
        info["cpu"] = attrs.get("model name") or attrs.get("Hardware") or platform.machine()
        flags = set(attrs.get("flags", attrs.get("Features", "")).split())
        info["cpu_flags"] = sorted(flags & {"avx2", "avx512f", "aes", "vaes", "sha_ni", "fma", "asimd", "pmull"})
        try:
            release = platform.freedesktop_os_release()
            info["distribution"] = release.get("PRETTY_NAME")
        except OSError:
            pass
        mem = Path("/proc/meminfo").read_text().split("\n", 1)[0]
        info["memory"] = mem.split(":", 1)[1].strip()
        quota = Path("/sys/fs/cgroup/cpu.max")
        if quota.is_file():
            info["cgroup_cpu_max"] = quota.read_text().strip()
        virt = subprocess.run(["sh", "-c", "systemd-detect-virt 2>/dev/null || true"], capture_output=True, text=True)
        if virt.stdout.strip():
            info["virtualization"] = virt.stdout.strip()
    elif sys.platform == "darwin":
        def sysctl(name):
            r = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True)
            return r.stdout.strip() if r.returncode == 0 else None

        info["cpu"] = sysctl("machdep.cpu.brand_string")
        mem = sysctl("hw.memsize")
        info["memory"] = f"{int(mem) // 2**20} MiB" if mem else None
        info["distribution"] = "macOS " + platform.mac_ver()[0]
    versions = {}
    for p in ("numpy", "aiohttp", "cryptography", "PyNaCl", "msgpack", "h5py", "threadpoolctl"):
        try:
            versions[p] = importlib.metadata.version(p)
        except importlib.metadata.PackageNotFoundError:
            versions[p] = None
    info["packages"] = versions
    try:
        import ssl

        info["openssl"] = ssl.OPENSSL_VERSION
    except ImportError:
        pass
    try:
        from threadpoolctl import threadpool_info

        info["blas"] = [
            {k: lib.get(k) for k in ("internal_api", "version", "num_threads", "architecture")}
            for lib in threadpool_info()
        ]
    except Exception:  # pragma: no cover - informational only
        info["blas"] = None
    return info
