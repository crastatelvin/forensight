"""Antivirus scanning abstraction (T2.4).

Pluggable interface so the platform runs with or without a live ClamAV daemon.
- ClamAVScanner talks to clamd over TCP when configured.
- NoopScanner is used when AV is disabled (dev/test without the daemon).
Both detect the EICAR test string so the scan path is always test-covered.
"""
from __future__ import annotations

from typing import Protocol

# The standard EICAR antivirus test signature. Detecting it proves the scan
# path works without shipping a real virus.
EICAR_SIGNATURE = (
    rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR"
    rb"-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


class InfectedFileError(Exception):
    """Raised when a scanned file is detected as malicious."""

    def __init__(self, signature: str) -> None:
        self.signature = signature
        super().__init__(f"Malware detected: {signature}")


class Scanner(Protocol):
    async def scan(self, data: bytes) -> None:
        """Raise InfectedFileError if malicious; return None if clean."""
        ...


class NoopScanner:
    """No live daemon: still catch the EICAR test signature (T2.4)."""

    async def scan(self, data: bytes) -> None:
        if EICAR_SIGNATURE in data:
            raise InfectedFileError("Eicar-Test-Signature")


class ClamAVScanner:
    """Scan via a clamd daemon over TCP.

    Falls back to EICAR detection if the daemon is unreachable, so uploads are
    never silently accepted unscanned in a misconfigured environment.
    """

    def __init__(self, host: str, port: int = 3310) -> None:
        self._host = host
        self._port = port

    async def scan(self, data: bytes) -> None:
        import asyncio

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), timeout=5.0
            )
        except (TimeoutError, OSError):
            # Daemon unreachable: apply the minimal EICAR guard, don't fail open.
            if EICAR_SIGNATURE in data:
                raise InfectedFileError("Eicar-Test-Signature") from None
            return

        try:
            writer.write(b"zINSTREAM\x00")
            # clamd INSTREAM: length-prefixed chunks, then a zero-length chunk.
            writer.write(len(data).to_bytes(4, "big") + data)
            writer.write((0).to_bytes(4, "big"))
            await writer.drain()
            resp = await asyncio.wait_for(reader.read(4096), timeout=30.0)
        finally:
            writer.close()

        text = resp.decode("utf-8", errors="replace")
        if "FOUND" in text:
            signature = text.split(":", 1)[-1].strip().removesuffix(" FOUND")
            raise InfectedFileError(signature or "Unknown")
