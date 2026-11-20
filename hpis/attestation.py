"""SEV-SNP hardware quote exchange, using Google's attest/check executables."""

import asyncio

from nacl.public import PublicKey, SealedBox


async def command(argv, stdin):
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(stdin), timeout=45)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        reason = stderr.decode(errors="replace").strip()[-500:]
        raise PermissionError(
            f"{argv[0]} exited with status {proc.returncode}: {reason}"
        )
    return stdout


async def evidence(challenge, public_key):
    if not isinstance(challenge, bytes) or len(challenge) != 32:
        raise ValueError("the attestation challenge must be 32 bytes")
    # The SNP report has exactly 64 bytes of user data. Bind both values directly.
    # VLEK clouds provide the endorsement certificate in the guest's certificate table.
    report = await command(
        ["attest", "-extended", "-infile=-", "-inform=bin", "-outform=bin"],
        challenge + public_key,
    )
    return [public_key, report]


async def verify(evidence_value, challenge, policy):
    pub, report = evidence_value
    if not isinstance(pub, bytes) or len(pub) != 32:
        raise ValueError("invalid attested public key")
    measurement = bytes.fromhex(policy["measurement"])
    if len(measurement) != 48:
        raise ValueError("SNP launch measurement must be 48 bytes")
    guest_policy = "0x30000" if policy.get("allow_smt", False) else "0x20000"
    args = [
        "check",
        "-in=-",
        "-inform=bin",
        "-guest_policy=" + guest_policy,
        "-check_crl=true",
        "-measurement=" + measurement.hex(),
        "-report_data=" + (challenge + pub).hex(),
    ]
    if policy.get("product"):
        args.append("-product=" + policy["product"])
    if policy.get("minimum_tcb"):
        args.extend(
            [
                "-minimum_tcb=" + policy["minimum_tcb"],
                "-minimum_launch_tcb=" + policy["minimum_tcb"],
            ]
        )
    await command(args, report)
    return SealedBox(PublicKey(pub))
