"""Exercise admission and cleanup with loopback TCP sockets, never production."""
import socket
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bounded_http import BoundedHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/error':
            raise RuntimeError('injected handler failure')
        body = b'x' * (8 * 1024 * 1024) if self.path == '/large' else b'ok'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.do_GET()

    def log_message(self, *args):
        pass


class ObservedServer(BoundedHTTPServer):
    def __init__(self, *args, **kwargs):
        self.changed = threading.Condition()
        self.active = self.started = self.peak = 0
        self.errors = []
        super().__init__(*args, **kwargs)

    def process_request_thread(self, *args):
        with self.changed:
            self.active += 1
            self.started += 1
            self.peak = max(self.peak, self.active)
            self.changed.notify_all()
        try:
            super().process_request_thread(*args)
        finally:
            with self.changed:
                self.active -= 1
                self.changed.notify_all()

    def handle_error(self, request, client_address):
        with self.changed:
            self.errors.append(sys.exc_info()[1])
            self.changed.notify_all()

    def wait_for(self, predicate):
        with self.changed:
            assert self.changed.wait_for(predicate, timeout=4)


@contextmanager
def running(timeout=0.25):
    server = ObservedServer(('127.0.0.1', 0), Handler)
    server.socket_timeout = timeout
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={'poll_interval': 0.01})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        assert not thread.is_alive()
        server.wait_for(lambda: server.active == 0)


def connect(server):
    return socket.create_connection(server.server_address, timeout=3)


def receive(sock):
    parts = []
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            return b''.join(parts)
        parts.append(chunk)


def assert_free_slots(server):
    acquired = 0
    try:
        for _ in range(8):
            assert server._worker_slots.acquire(blocking=False)
            acquired += 1
        assert not server._worker_slots.acquire(blocking=False)
    finally:
        for _ in range(acquired):
            server._worker_slots.release()


def assert_healthy(server):
    assert_free_slots(server)
    with connect(server) as sock:
        sock.sendall(b'GET / HTTP/1.0\r\n\r\n')
        response = receive(sock)
    assert response.startswith(b'HTTP/1.0 200 ')
    assert response.endswith(b'ok')
    server.wait_for(lambda: server.active == 0)
    assert_free_slots(server)


def test_defaults():
    assert BoundedHTTPServer.max_workers == 8
    assert BoundedHTTPServer.socket_timeout == 15
    assert BoundedHTTPServer.request_queue_size == 8


def test_saturation_rejects_without_starting_or_queueing_threads():
    with running(timeout=15) as server:
        stalled = [connect(server) for _ in range(8)]
        try:
            server.wait_for(lambda: server.active == 8)
            for _ in range(20):
                # No request bytes: rejection must precede parsing/worker creation.
                with connect(server) as sock:
                    response = receive(sock)
                assert response.startswith(b'HTTP/1.1 503 ')
                for header in (b'Retry-After: 1', b'Cache-Control: no-store',
                               b'Access-Control-Allow-Origin: *',
                               b'Access-Control-Allow-Headers: Content-Type, Cache-Control',
                               b'Access-Control-Expose-Headers: Retry-After',
                               b'Connection: close'):
                    assert header in response
            assert server.started == server.peak == 8
        finally:
            for sock in stalled:
                sock.close()
        server.wait_for(lambda: server.active == 0)
        assert_healthy(server)
        assert server.started == 9  # rejected requests never run later


@pytest.mark.parametrize('partial', [
    b'',
    b'GET / HTTP/1.1\r\nHost:',
    b'POST / HTTP/1.0\r\nContent-Length: 100\r\n\r\nx',
])
def test_idle_header_and_body_reads_timeout_and_release_slot(partial):
    with running() as server:
        with connect(server) as sock:
            if partial:
                sock.sendall(partial)
            server.wait_for(lambda: server.started == 1)
            assert receive(sock) == b''
        server.wait_for(lambda: server.active == 0)
        assert_healthy(server)


def test_handler_exception_and_client_disconnect_release_slots():
    with running() as server:
        with connect(server) as sock:
            sock.sendall(b'GET /error HTTP/1.0\r\n\r\n')
            assert receive(sock) == b''
        server.wait_for(lambda: server.active == 0)
        assert any(isinstance(error, RuntimeError) for error in server.errors)
        with connect(server) as sock:
            sock.sendall(b'GET / HTTP/1.1\r\n')
            server.wait_for(lambda: server.started == 2)
        server.wait_for(lambda: server.active == 0)
        assert_healthy(server)


def test_blocked_response_write_times_out_and_releases_slot():
    with running() as server:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            sock.settimeout(3)
            sock.connect(server.server_address)
            sock.sendall(b'GET /large HTTP/1.0\r\n\r\n')
            server.wait_for(lambda: server.started == 1)
            # Keep TCP open but do not drain the response.
            server.wait_for(lambda: server.active == 0)
        assert_healthy(server)


def test_thread_start_failure_returns_slot_and_closes_socket(monkeypatch):
    with running() as server:
        original = threading.Thread.start
        failed = threading.Event()

        def fail_once(thread):
            if getattr(thread._target, '__self__', None) is server and not failed.is_set():
                failed.set()
                raise RuntimeError('injected thread start failure')
            return original(thread)

        monkeypatch.setattr(threading.Thread, 'start', fail_once)
        with connect(server) as sock:
            assert receive(sock) == b''
        assert failed.is_set()
        server.wait_for(lambda: bool(server.errors))
        assert any(isinstance(error, RuntimeError) for error in server.errors)
        assert_healthy(server)
