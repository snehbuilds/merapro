"""A minimal SMTP server for tests: accepts every message and keeps it, parsed.

Just enough of RFC 5321 for ``smtplib`` (EHLO/HELO, MAIL, RCPT, DATA, RSET, NOOP, QUIT),
so the real ``SmtpNotifier`` is exercised over a real socket.
"""

from __future__ import annotations

import asyncio
import email
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from email.message import Message


@dataclass
class SmtpSink:
    port: int = 0
    messages: list[Message] = field(default_factory=list)

    def bodies(self) -> list[str]:
        out = []
        for m in self.messages:
            part = m.get_body(preferencelist=("plain",)) if hasattr(m, "get_body") else m
            out.append(part.get_content() if part is not None else "")  # type: ignore[union-attr]
        return out

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async def say(line: str) -> None:
            writer.write((line + "\r\n").encode())
            await writer.drain()

        await say("220 sink ready")
        try:
            while line := await reader.readline():
                cmd = line.decode(errors="replace").strip().upper()
                if cmd.startswith(("EHLO", "HELO")):
                    await say("250 sink")
                elif cmd.startswith(("MAIL", "RCPT", "RSET", "NOOP")):
                    await say("250 OK")
                elif cmd == "DATA":
                    await say("354 end with .")
                    raw = bytearray()
                    while (chunk := await reader.readline()) not in (b".\r\n", b""):
                        raw += chunk[1:] if chunk.startswith(b"..") else chunk
                    self.messages.append(
                        email.message_from_bytes(bytes(raw), policy=email.policy.default)
                    )
                    await say("250 queued")
                elif cmd == "QUIT":
                    await say("221 bye")
                    break
                else:
                    await say("502 not implemented")
        finally:
            writer.close()


@asynccontextmanager
async def smtp_sink() -> AsyncIterator[SmtpSink]:
    sink = SmtpSink()
    server = await asyncio.start_server(sink._handle, "127.0.0.1", 0)
    sink.port = server.sockets[0].getsockname()[1]
    async with server:
        yield sink
