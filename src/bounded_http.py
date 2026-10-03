"""HTTP admission without queued workers; socket timeouts bound idle I/O.

The timeout is per socket operation, not a deadline for CPU/DB work or for a
client that continually makes progress. Saturated connections get a best-effort
503 without blocking the accept loop or allocating another handler thread.
"""
import threading
from http.server import ThreadingHTTPServer


class BoundedHTTPServer(ThreadingHTTPServer):
    max_workers = 8
    socket_timeout = 15.0
    request_queue_size = 8
    daemon_threads = True

    _overloaded = (
        b"HTTP/1.1 503 Service Unavailable\r\n"
        b"Connection: close\r\n"
        b"Content-Length: 0\r\n"
        b"Retry-After: 1\r\n"
        b"Cache-Control: no-store\r\n"
        b"Access-Control-Allow-Origin: *\r\n"
        b"Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
        b"Access-Control-Allow-Headers: Content-Type, Cache-Control\r\n"
        b"Access-Control-Expose-Headers: Retry-After\r\n\r\n"
    )

    def __init__(self, *args, **kwargs):
        self._worker_slots = threading.BoundedSemaphore(self.max_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._worker_slots.acquire(blocking=False):
            try:
                request.setblocking(False)
                request.sendall(self._overloaded)
            except OSError:
                # A slow/disconnected client must not stall admission for others.
                pass
            finally:
                request.close()
            return
        try:
            request.settimeout(self.socket_timeout)
            super().process_request(request, client_address)
        except BaseException:
            # No worker owns the slot if thread creation/start failed.
            self._worker_slots.release()
            request.close()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            # Includes handler errors, disconnects and socket cleanup errors.
            self._worker_slots.release()
