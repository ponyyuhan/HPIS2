"""Build the optional fused exact scorer against the local OpenSSL development package."""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    flags = shlex.split(
        subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "libcrypto"], text=True
        )
    )
    subprocess.run(
        [
            os.getenv("CC", "cc"),
            "-O3",
            "-std=c11",
            "-fPIC",
            "-ffp-contract=off",
            "-dynamiclib" if sys.platform == "darwin" else "-shared",
            str(Path(__file__).with_name("rank.c")),
            "-o",
            str(a.output),
            *flags,
            "-lm",
        ],
        check=True,
    )
    print(a.output.resolve())


if __name__ == "__main__":
    main()
