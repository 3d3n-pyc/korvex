"""Black-box WebSocket fuzzer: mutated handshakes and frame streams against the built-in server.

python fuzz/fuzz_ws.py --duration 60 [--seed N] [--workers K]
"""

from __future__ import annotations

import base64
import os
import random
import struct

from harness import fuzz_main


def handshake(r: random.Random, mutate: bool) -> bytes:
    """An Upgrade request for /ws, with one random flaw when `mutate`."""
    key = base64.b64encode(os.urandom(16)).decode()
    headers = [
        ("Host", "x"),
        ("Upgrade", "websocket"),
        ("Connection", "Upgrade"),
        ("Sec-WebSocket-Version", "13"),
        ("Sec-WebSocket-Key", key),
    ]
    path = "/ws"
    if mutate:
        flaw = r.randrange(7)
        index = r.randrange(len(headers))
        if flaw == 0:
            del headers[r.randrange(1, len(headers))]
        elif flaw == 1:
            headers[4] = ("Sec-WebSocket-Key", r.choice(["", "abc", "!!!!", key * 3, "\x00" * 8]))
        elif flaw == 2:
            headers[3] = ("Sec-WebSocket-Version", r.choice(["8", "0", "99", "13, 13", ""]))
        elif flaw == 3:
            headers[r.choice([1, 2])] = (headers[r.choice([1, 2])][0], r.choice(["", "foo", "close", "websocket, x"]))
        elif flaw == 4:
            headers.append(headers[index])
        elif flaw == 5:
            path = r.choice(["/", "/nope", "/ws/", "/ws?x=" + "a" * 5000, "ws"])
        else:
            headers.append(("Sec-WebSocket-Protocol", r.choice(["", "x" * 3000, "a,b,,c"])))
    lines = "".join(f"{name}: {value}\r\n" for name, value in headers)
    return f"GET {path} HTTP/1.1\r\n{lines}\r\n".encode("latin-1", "replace")


def xor(mask: bytes, payload: bytes) -> bytes:
    """The payload masked with the 4-byte key (fast: one big integer XOR)."""
    key = (mask * (len(payload) // 4 + 1))[: len(payload)]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(key, "big")).to_bytes(len(payload), "big")


def frame(r: random.Random, opcode: int, payload: bytes, *, fin: bool = True, mutate: bool = False) -> bytes:
    """One client frame; `mutate` may break the mask, reserved bits or length."""
    first = (0x80 if fin else 0) | opcode
    masked = True
    lie = None
    if mutate:
        flaw = r.randrange(6)
        if flaw == 0:
            first |= r.choice([0x40, 0x20, 0x10, 0x70])
        elif flaw == 1:
            masked = False
        elif flaw == 2:
            first = (first & 0xF0) | r.choice([3, 4, 5, 6, 7, 11, 12, 13, 14, 15])
        elif flaw == 3:
            lie = r.choice([len(payload) + r.randint(1, 70000), 2**63, 2**64 - 1, 0])
        elif flaw == 4:
            masked = r.random() < 0.5
            payload = payload[: r.randrange(len(payload) + 1)]
    size = len(payload) if lie is None else lie
    bit = 0x80 if masked else 0
    if size < 126 and lie is None:
        head = struct.pack("!BB", first, bit | size)
    elif size < 65536 and r.random() < 0.9:
        head = struct.pack("!BBH", first, bit | 126, size)
    else:
        head = struct.pack("!BBQ", first, bit | 127, size)
    if not masked:
        return head + payload
    mask = os.urandom(4)
    return head + mask + xor(mask, payload)


def payload_for(r: random.Random, opcode: int) -> bytes:
    """A payload fitting the opcode: text, bytes, invalid UTF-8, close codes, long control data."""
    if opcode == 8:
        code = r.choice([1000, 1001, 1002, 1005, 1006, 1015, 999, 1004, 2999, 3000, 4999, 5000, 0, 65535])
        return r.choice([b"", b"\x03", struct.pack("!H", code) + r.choice([b"", b"bye", b"\xff\xfe", b"x" * 200])])
    if opcode in (9, 10):
        return os.urandom(r.choice([0, 1, 10, 125, 126, 200]))
    if opcode == 1 and r.random() < 0.4:
        return r.choice([b"\xff", b"\xc0\x80", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"ok\xe2\x82"])
    return os.urandom(r.choice([0, 1, 5, 100, 1000, 70000]))


def frames(r: random.Random, mutate: bool) -> bytes:
    """A random sequence of frames, ending most of the time with a close."""
    out = b""
    for _ in range(r.randint(1, 8)):
        kind = r.choice(["text", "bin", "ping", "pong", "close", "frag", "cont", "split8", "huge"])
        bad = mutate and r.random() < 0.5
        if kind == "frag":
            out += frame(r, 1, b"he", fin=False) + frame(r, r.choice([9, 10]), b"p") + frame(r, 0, b"llo", mutate=bad)
        elif kind == "split8":
            out += frame(r, 1, b"a\xe2", fin=False) + frame(r, 0, b"\x82\xac")
            out += frame(r, 1, b"\xe2\x82", fin=False) + frame(r, 0, b"x", mutate=bad)
        elif kind == "cont":
            out += frame(r, 0, b"orphan", fin=r.random() < 0.5, mutate=bad)
        elif kind == "huge":
            out += frame(r, 2, os.urandom(1024) * r.choice([1024, 4096]), mutate=bad)
        else:
            opcode = {"text": 1, "bin": 2, "ping": 9, "pong": 10, "close": 8}[kind]
            fin = not (opcode >= 8 and bad and r.random() < 0.3)
            out += frame(r, opcode, payload_for(r, opcode), fin=fin, mutate=bad)
    if r.random() < 0.7:
        out += frame(r, 8, struct.pack("!H", 1000))
    if r.random() < 0.15:
        out += frame(r, 1, b"after close")
    if r.random() < 0.15:
        out = out[: r.randrange(len(out) + 1)]
    return out


def make_case(r: random.Random) -> bytes:
    """A handshake followed by frames, either part sometimes mutated."""
    return handshake(r, r.random() < 0.3) + frames(r, r.random() < 0.6)


def case_at(r: random.Random, index: int) -> bytes:
    """A new case; the index is not used."""
    return make_case(r)


if __name__ == "__main__":
    fuzz_main("ws", __doc__ or "", case_at)
