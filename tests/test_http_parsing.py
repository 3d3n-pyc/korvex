"""Raw-socket tests: the built-in server against malformed and ambiguous HTTP/1.1."""

import contextlib
import re
import socket
import sys
import time

import pytest
from test_server import free_port, start

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the built-in server needs Linux or macOS")

HOST = b"Host: x\r\n"


@pytest.fixture(scope="module")
def port():
    number = free_port()
    process = start(number)
    yield number
    process.kill()


def read_all(sock, wait=2.0):
    """Reads until the server closes the connection or `wait` seconds pass."""
    sock.settimeout(wait)
    data = b""
    try:
        while chunk := sock.recv(65536):
            data += chunk
    except (socket.timeout, ConnectionResetError):
        pass
    return data


def raw(port, data, wait=2.0):
    """Sends raw bytes and returns everything the server answered."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            sock.sendall(data)
        return read_all(sock, wait)


def statuses(response):
    """The status codes of every response found in the raw bytes, in order."""
    return [int(code) for code in re.findall(rb"HTTP/1\.[01] (\d{3}) ", response)]


def test_content_length_with_chunked_is_read_as_chunked(port):
    """Smuggling: CL and TE together must not leave a hidden request to be served."""
    hidden = b"GET /smuggled HTTP/1.1\r\nHost: x\r\n\r\n"
    chunked = b"%x\r\n" % len(hidden) + hidden + b"\r\n0\r\n\r\n"
    request = b"POST /echo HTTP/1.1\r\n" + HOST + b"Content-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\n" + chunked
    response = raw(port, request)
    assert statuses(response) in ([200], [400])
    assert b"404" not in response.split(b"\r\n")[0] and len(statuses(response)) == 1


def test_two_different_content_lengths_are_rejected(port):
    """Smuggling: conflicting Content-Length values must not pick one silently."""
    request = b"POST /echo HTTP/1.1\r\n" + HOST + b"Content-Length: 3\r\nContent-Length: 5\r\n\r\nhello"
    assert statuses(raw(port, request))[:1] == [400]


def test_transfer_encoding_gzip_is_not_a_body(port):
    """Smuggling: a Transfer-Encoding not ending in chunked must be refused (400 or 501)."""
    request = b"POST /echo HTTP/1.1\r\n" + HOST + b"Transfer-Encoding: gzip\r\n\r\nhello"
    assert statuses(raw(port, request))[:1][0] in (400, 501)


@pytest.mark.parametrize("bad", [b"X-A: a\x00b", b"X-A: a\rb", b"X-A: a\x7fb", b"X\x00A: a", b"X-A\r: a"])
def test_invalid_header_bytes_are_rejected(port, bad):
    """Header injection: NUL or bare CR in a header name or value must give 400."""
    request = b"GET /users/1 HTTP/1.1\r\n" + HOST + bad + b"\r\n\r\n"
    assert statuses(raw(port, request))[:1] == [400]


def test_bare_lf_in_header_is_rejected_or_harmless(port):
    """Header injection: a bare LF must not let a smuggled header or request through."""
    request = b"GET /users/1 HTTP/1.1\r\n" + HOST + b"X-A: a\nX-B: b\r\n\r\n"
    assert statuses(raw(port, request)) in ([400], [200])


def test_obsolete_line_folding_is_rejected(port):
    """Smuggling: obs-fold lets proxies and servers disagree on headers; must give 400."""
    request = b"GET /users/1 HTTP/1.1\r\n" + HOST + b"X-A: a\r\n b\r\n\r\n"
    assert statuses(raw(port, request))[:1] == [400]


def test_more_than_100_headers_is_rejected(port):
    """Resource exhaustion: over 100 headers is refused with 400 or 431."""
    headers = b"".join(b"X-%d: v\r\n" % i for i in range(150))
    request = b"GET /users/1 HTTP/1.1\r\n" + HOST + headers + b"\r\n"
    assert statuses(raw(port, request))[:1][0] in (400, 431)


def test_huge_header_line_is_rejected_and_closed(port):
    """Resource exhaustion: a header line beyond the buffer gives 431/400, then closes."""
    request = b"GET /users/1 HTTP/1.1\r\n" + HOST + b"X-A: " + b"a" * 1_000_000 + b"\r\n\r\n"
    response = raw(port, request)
    assert statuses(response)[:1][0] in (400, 431)
    assert len(statuses(response)) == 1


def test_bad_chunk_size_is_not_a_server_error(port):
    """Parser robustness: a non-hex chunk size must give 400 or a closed connection, never 500."""
    request = b"POST /echo HTTP/1.1\r\n" + HOST + b"Transfer-Encoding: chunked\r\n\r\nzz\r\nhello\r\n0\r\n\r\n"
    assert set(statuses(raw(port, request))) <= {400}


def test_pipelined_requests_are_answered_in_order(port):
    """Pipelining: three requests in one write must give three responses, in order."""
    request = b"".join(b"GET /users/%d HTTP/1.1\r\n" % i + HOST + b"\r\n" for i in (1, 2, 3))
    response = raw(port, request, wait=1.0)
    assert statuses(response) == [200, 200, 200]
    assert [response.index(b'{"id": %d}' % i) for i in (1, 2, 3)] == sorted(
        response.index(b'{"id": %d}' % i) for i in (1, 2, 3)
    )


def test_keep_alive_serves_two_requests_on_one_connection(port):
    """Keep-alive: a second request on the same connection must be answered."""
    request = b"GET /users/1 HTTP/1.1\r\n" + HOST + b"\r\n"
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        for _ in range(2):
            sock.sendall(request)
            sock.settimeout(2)
            assert sock.recv(65536).startswith(b"HTTP/1.1 200")


def test_idle_keep_alive_connection_is_closed_after_read_timeout():
    """Slowloris: an idle keep-alive connection is closed once read_timeout passes."""
    number = free_port()
    process = start(number, TIMEOUT="1")
    try:
        with socket.create_connection(("127.0.0.1", number), timeout=5) as sock:
            sock.sendall(b"GET /users/1 HTTP/1.1\r\n" + HOST + b"\r\n")
            started = time.monotonic()
            read_all(sock, wait=5)
            assert time.monotonic() - started < 4
    finally:
        process.kill()


def test_http_1_0_without_host_is_answered_then_closed(port):
    """HTTP/1.0: no Host is legal; the answer comes and the connection is closed after."""
    started = time.monotonic()
    response = raw(port, b"GET /users/1 HTTP/1.0\r\n\r\n", wait=5)
    assert statuses(response) == [200]
    assert time.monotonic() - started < 3


@pytest.mark.parametrize(
    "body",
    [
        b"--B\r\n\r\n\r\n--B--",  # a part without headers
        b'--B\r\nContent-Disposition: form-data; name="a"\r\nbad header\r\n\r\nv\r\n--B--',
    ],
)
def test_malformed_multipart_part_headers_give_400(port, body):
    """Fuzz regressions: a bad part header line used to answer 500."""
    head = b"POST /form HTTP/1.1\r\n" + HOST + b'Content-Type: multipart/form-data; boundary="B"\r\n'
    request = head + b"Content-Length: %d\r\n\r\n" % len(body) + body
    assert statuses(raw(port, request))[:1] == [400]


@pytest.mark.parametrize("line", [b"POSE /echo", b"PST /", b"GE%c0%afT /"])
def test_unknown_method_is_501_and_server_keeps_working(port, line):
    """An unknown method token is 501 (RFC 9110), never a 500."""
    response = raw(port, line + b" HTTP/1.1\r\n" + HOST + b"\r\n")
    assert statuses(response) == [501]
    ok = raw(port, b"GET /users/1 HTTP/1.1\r\n" + HOST + b"\r\n")
    assert statuses(ok) == [200]
