"""Opt-in SEV-SNP interoperability tests for the real attest/check executables.

HPIS_SNP_MILAN_REPORT points to the published VirTEE Milan measurement fixture
in binary format. HPIS_TEST_NO_SNP=1 exercises a Linux machine without a quote
provider. The normal unit suite does not require these external resources.
"""

import asyncio
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

import aiohttp

from bench.cluster import running
from hpis import attestation
from hpis.provision import init
from hpis.sdk import Device


# Values in virtee/sev, tests/certs_data/report_milan.hex (see docs/snp.md).
MEASUREMENT = "7a1e5c266c0108dbc9bb94fa926951320940915d0aafb42464bd88b579ea158d3e1a0dc39b2c60bd95b9c480cd81841f"
CHALLENGE = bytes.fromhex(
    "d447b55d197491bfe15cf298f9de9986b7a7c4be2468b4f6e2d53b71d7c64581"
)
PUBLIC_DATA = bytes.fromhex(
    "0b0f2cdfca0040433be063fc1a8293f0f3f8dae7b79fecb3d1cd82bd6a93ebfd"
)


def change_one_bit(value):
    return bytes([value[0] ^ 1]) + value[1:]


class SnpIntegration(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("HPIS_SNP_MILAN_REPORT"),
        "needs the published Milan fixture and check",
    )
    def test_public_milan_report(self):
        report = Path(os.environ["HPIS_SNP_MILAN_REPORT"]).read_bytes()
        policy = {"measurement": MEASUREMENT, "product": "Milan", "allow_smt": True}
        evidence = [PUBLIC_DATA, report]

        async def run():
            await attestation.verify(evidence, CHALLENGE, policy)
            # These values exercise the numerical exit codes of the real check CLI.
            cases = (
                ("challenge", evidence, change_one_bit(CHALLENGE), policy, 5),
                (
                    "recipient",
                    [change_one_bit(PUBLIC_DATA), report],
                    CHALLENGE,
                    policy,
                    5,
                ),
                (
                    "measurement",
                    evidence,
                    CHALLENGE,
                    {
                        **policy,
                        "measurement": change_one_bit(bytes.fromhex(MEASUREMENT)).hex(),
                    },
                    5,
                ),
                (
                    "SMT disallowed",
                    evidence,
                    CHALLENGE,
                    {**policy, "allow_smt": False},
                    5,
                ),
                (
                    "signature",
                    [PUBLIC_DATA, report[:672] + change_one_bit(report[672:])],
                    CHALLENGE,
                    policy,
                    2,
                ),
            )
            for name, supplied, challenge, expected, code in cases:
                with self.subTest(name=name):
                    with self.assertRaisesRegex(
                        PermissionError, f"check exited with status {code}"
                    ):
                        await attestation.verify(supplied, challenge, expected)

        asyncio.run(run())

    @unittest.skipUnless(
        os.environ.get("HPIS_TEST_NO_SNP") == "1",
        "needs Linux without an SNP quote provider and attest",
    )
    def test_device_aborts_before_query_without_hardware(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init(
                root,
                "snp",
                3,
                0.25,
                measurement=MEASUREMENT,
                snp_product="Milan",
                snp_allow_smt=True,
            )
            with running(root, existing=True) as (folder, url):

                async def run():
                    # A missing executable is an installation error; demand the
                    # specific hardware rejection from the installed collector.
                    with self.assertRaisesRegex(
                        PermissionError, "no supported SEV-SNP QuoteProvider"
                    ):
                        await attestation.evidence(os.urandom(32), os.urandom(32))
                    sdk = Device(folder, url)
                    with self.assertRaises(aiohttp.ClientResponseError) as error:
                        async with sdk:
                            self.fail(
                                "device obtained a query session without SNP evidence"
                            )
                    self.assertEqual(error.exception.status, 403)
                    self.assertIsNone(sdk.box)
                    self.assertIsNone(sdk.session)

                asyncio.run(run())
            with sqlite3.connect(root / "hpis" / "store.sqlite") as conn:
                self.assertEqual(
                    conn.execute("SELECT count(*) FROM docs").fetchone()[0], 0
                )


if __name__ == "__main__":
    unittest.main()
