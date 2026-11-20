"""Build the Q8 and FP32 entry points in one host-native shared library."""

import argparse
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import tempfile


def build(output):
    base = Path(__file__).resolve().parent
    cc = shlex.split(os.getenv("CC", "cc"))
    cxx = shlex.split(os.getenv("CXX", "c++"))
    target = (
        "-mcpu=native"
        if platform.machine().lower() in ("arm64", "aarch64")
        else "-march=native"
    )
    flags = ["-O3", "-fPIC", target]
    cxx_headers = []
    if sys.platform == "darwin":
        sdk = subprocess.check_output(
            ["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True
        ).strip()
        flags += ["-isysroot", sdk]
        sdk_headers = Path(sdk) / "usr/include/c++/v1"
        if (
            sdk_headers.is_dir()
            and "clang"
            in subprocess.check_output([*cxx, "--version"], text=True).lower()
        ):
            cxx_headers += ["-isystem", str(sdk_headers)]
    crypto = shlex.split(
        subprocess.check_output(["pkg-config", "--cflags", "libcrypto"], text=True)
    )
    libs = shlex.split(
        subprocess.check_output(["pkg-config", "--libs", "libcrypto"], text=True)
    )
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hpis-q8-build-") as scratch:
        rank_obj, q8_obj = Path(scratch) / "rank.o", Path(scratch) / "q8.o"
        subprocess.run(
            [
                *cc,
                *flags,
                "-std=c11",
                "-ffp-contract=off",
                "-DHPIS_MAPPED_Q8",
                *crypto,
                "-c",
                base.parent / "rank.c",
                "-o",
                rank_obj,
            ],
            check=True,
        )
        subprocess.run(
            [
                *cxx,
                *flags,
                "-std=c++17",
                "-fno-math-errno",
                *cxx_headers,
                "-c",
                base / "q8.cc",
                "-o",
                q8_obj,
            ],
            check=True,
        )
        kind = "-dynamiclib" if sys.platform == "darwin" else "-shared"
        subprocess.run(
            [*cxx, kind, rank_obj, q8_obj, "-o", output, *libs, "-lm"], check=True
        )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    print(build(parser.parse_args().output))


if __name__ == "__main__":
    main()
