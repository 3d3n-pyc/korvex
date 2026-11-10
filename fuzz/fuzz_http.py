"""Mutation fuzzer of the built-in HTTP/1.1 server.

python fuzz/fuzz_http.py --duration 60 [--seed N] [--workers K]
"""

from __future__ import annotations

import json
import random

from harness import fuzz_main

H = b"Host: x\r\n"
BODY = b"a=1&b=two&c=%41"
MP_BODY = b'--B\r\nContent-Disposition: form-data; name="a"\r\n\r\n1\r\n--B--\r\n'
CHUNKED = b"5\r\nhello\r\n3\r\nabc\r\n0\r\n\r\n"
POST = b"POST %s HTTP/1.1\r\n" + H


def post(path: bytes, headers: bytes, body: bytes) -> bytes:
    """A POST request with the given extra headers and body."""
    return POST % path + headers + b"\r\n" + body


def seeds() -> list[bytes]:
    """Valid requests to every route, plus the shapes the parsing tests use."""
    form = b"Content-Type: application/x-www-form-urlencoded\r\nContent-Length: %d\r\n" % len(BODY)
    multi = b"Content-Type: multipart/form-data; boundary=B\r\nContent-Length: %d\r\n" % len(MP_BODY)
    js = json.dumps({"a": [1, 2, {"b": None}]}).encode()
    get = b"GET /users/bob?x=1&y=%%20z HTTP/1.1\r\n" + H + b"\r\n"
    hidden = b"GET /smuggled HTTP/1.1\r\nHost: x\r\n\r\n"
    return [
        b"GET / HTTP/1.1\r\n" + H + b"\r\n",
        get,
        b"GET /items/42 HTTP/1.1\r\n" + H + b"\r\n",
        post(b"/form", form, BODY),
        post(b"/form", multi, MP_BODY),
        post(b"/echo", b"Content-Type: application/json\r\nContent-Length: %d\r\n" % len(js), js),
        post(b"/echo", b"Transfer-Encoding: chunked\r\n", CHUNKED),
        post(
            b"/echo",
            b"Content-Length: 5\r\nTransfer-Encoding: chunked\r\n",
            b"%x\r\n" % len(hidden) + hidden + b"\r\n0\r\n\r\n",
        ),
        post(b"/echo", b"Content-Length: 3\r\nContent-Length: 5\r\n", b"hello"),
        post(b"/echo", b"Transfer-Encoding: gzip\r\n", b"hello"),
        post(b"/echo", b"Expect: 100-continue\r\nContent-Length: 5\r\n", b"hello"),
        b"GET /users/1 HTTP/1.1\r\n" + H + b"X-A: a\nX-B: b\r\n\r\n",
        b"GET /users/1 HTTP/1.1\r\n" + H + b"X-A: a\r\n b\r\n\r\n",
        b"GET /users/1 HTTP/1.0\r\n\r\n",
        b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n",
        get * 3,
        get + post(b"/echo", b"Content-Length: 2\r\n", b"hi") + get,
    ]


def flip(r: random.Random, data: bytes) -> bytes:
    """Flip, insert or delete a few bytes."""
    out = bytearray(data)
    for _ in range(r.randint(1, 4)):
        pos = r.randrange(len(out) + 1)
        op = r.randrange(3)
        if op == 0 and out:
            out[min(pos, len(out) - 1)] = r.randrange(256)
        elif op == 1:
            out.insert(pos, r.choice([0, 10, 13, 32, 58, 255, r.randrange(256)]))
        elif out:
            del out[min(pos, len(out) - 1)]
    return bytes(out)


def lines(r: random.Random, data: bytes) -> bytes:
    """Duplicate, remove or reorder header lines."""
    head, sep, rest = data.partition(b"\r\n\r\n")
    parts = head.split(b"\r\n")
    if len(parts) < 2:
        return data
    i = r.randrange(1, len(parts))
    op = r.randrange(3)
    if op == 0:
        parts.insert(i, parts[i])
    elif op == 1:
        del parts[i]
    else:
        parts[1:] = r.sample(parts[1:], len(parts) - 1)
    return b"\r\n".join(parts) + sep + rest


def length(r: random.Random, data: bytes) -> bytes:
    """Replace or add a hostile Content-Length or Transfer-Encoding."""
    value = r.choice([b"-1", b"99999999999999999999", b"abc", b"0x10", b"1 2", b"", b"4294967296", b"+3", b"1e2"])
    extra = r.choice(
        [
            b"Content-Length: " + value,
            b"Transfer-Encoding: chunked",
            b"Transfer-Encoding: chunked, gzip",
            b"Transfer-Encoding : chunked",
        ]
    )
    return data.replace(b"\r\n\r\n", b"\r\n" + extra + b"\r\n\r\n", 1)


def chunks(r: random.Random, data: bytes) -> bytes:
    """Send a request body in hostile chunks."""
    size = r.choice([b"zz", b"ffffffffffffffff", b"-1", b"", b"5;ext=1", b"5 ", b"0005", b"7fffffff"])
    end = r.choice([b"\r\n", b"\n", b"", b"\r"])
    tail = r.choice([b"0\r\n\r\n", b"0\r\nX-T: 1\r\n\r\n", b"0\r\n", b"", b"0\r\n\r\nGET / HTTP/1.1\r\n\r\n"])
    return post(b"/echo", b"Transfer-Encoding: chunked\r\n", size + end + b"hello" + end + tail)


def bare(r: random.Random, data: bytes) -> bytes:
    """Replace CRLF by bare LF or CR in some places."""
    out = data.replace(b"\r\n", r.choice([b"\n", b"\r", b"\r\r\n"]), r.randint(1, 4))
    return out


def junk(r: random.Random, data: bytes) -> bytes:
    """Put NUL, high bytes or percent junk in the method, path, or a header."""
    bad = r.choice([b"\x00", b"\xff\xfe", b"%", b"%zz", b"%00", b"%c0%af", b"..%2f", b"\x7f", b"\t", b" "])
    pos = r.randrange(min(len(data), 120) + 1)
    return data[:pos] + bad + data[pos:]


def long(r: random.Random, data: bytes) -> bytes:
    """A very long path, header name or header value."""
    n = r.choice([1000, 9000, 70000, 1_000_000])
    kind = r.randrange(3)
    if kind == 0:
        return data.replace(b" /", b" /" + b"a" * n, 1)
    if kind == 1:
        return data.replace(b"\r\n", b"\r\nX-" + b"a" * n + b": 1\r\n", 1)
    return data.replace(b"\r\n", b"\r\nX-A: " + b"a" * n + b"\r\n", 1)


def multipart(r: random.Random, data: bytes) -> bytes:
    """Break multipart boundaries and parts."""
    part = b'--B\r\nContent-Disposition: form-data; name="f"; filename="x"\r\n\r\ndata\r\n'
    body = r.choice(
        [
            part * r.choice([2, 50, 2000]) + b"--B--\r\n",
            part,
            part + b"--B",
            b"--B\r\n\r\n\r\n--B--",
            b"--B\r\nContent-Disposition: form-data\r\n\r\nx\r\n--B--",
            b"--" + b"B" * 70000 + b"\r\n",
        ]
    )
    boundary = r.choice([b"B", b"", b'"B"', b"B" * 5000, b"B; boundary=C"])
    head = b"Content-Type: multipart/form-data; boundary=%s\r\nContent-Length: %d\r\n" % (boundary, len(body))
    return post(r.choice([b"/form", b"/echo"]), head if boundary else head.replace(b"; boundary=", b""), body)


def version(r: random.Random, data: bytes) -> bytes:
    """Change the HTTP version or add Expect."""
    if r.random() < 0.5:
        return data.replace(
            b"HTTP/1.1", r.choice([b"HTTP/1.0", b"HTTP/2.0", b"HTTP/0.9", b"HTTP/1.", b"http/1.1", b"HTTP/9.9"]), 1
        )
    return data.replace(b"\r\n\r\n", b"\r\nExpect: " + r.choice([b"100-continue", b"nope"]) + b"\r\n\r\n", 1)


def truncate(r: random.Random, data: bytes) -> bytes:
    """Cut the request at a random place."""
    return data[: r.randrange(len(data) + 1)]


MUTATIONS = [flip, lines, length, chunks, bare, junk, long, multipart, version, truncate]


def make_case(r: random.Random, pool: list[bytes]) -> bytes:
    """A seed with one to three mutations."""
    case = r.choice(pool)
    for mutation in r.sample(MUTATIONS, r.randint(1, 3)):
        case = mutation(r, case)
    return case


POOL = seeds()


def case_at(r: random.Random, index: int) -> bytes:
    """The seeds first, one each, then mutated cases."""
    return POOL[index] if index < len(POOL) else make_case(r, POOL)


if __name__ == "__main__":
    fuzz_main("http", __doc__ or "", case_at)
