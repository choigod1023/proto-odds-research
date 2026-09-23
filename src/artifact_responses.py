"""Compressed-only responses with a single non-blocking rebuild slot.

Keep the previous wire payload during refresh/failure. Concurrent requests do
not allocate another copy of a large artifact or wait behind its compression.
"""
import gzip
import threading


class ArtifactResponses:
    def __init__(self, database):
        self.database = database
        self.ready = {}
        self.lock = threading.Lock()
        self.rebuild = threading.Lock()

    def get_bytes(self, name, compressed=True):
        with self.lock:
            cached = self.ready.get(name)
        if self.rebuild.acquire(blocking=False):
            try:
                metadata = self.database.artifact_metadata(name, include_size=False)
                if metadata is not None and (cached is None or cached[0] != metadata['stored_at']):
                    stored = self.database.get_artifact_json(name)
                    if stored is not None:
                        payload, revision = stored
                        body = gzip.compress(payload.encode('utf-8'), compresslevel=3)
                        cached = (revision, body)
                        with self.lock:
                            self.ready[name] = cached
            except Exception:
                if cached is None:
                    raise
            finally:
                self.rebuild.release()
        if cached is None:
            raise KeyError('artifact preparing')
        return cached[1] if compressed else gzip.decompress(cached[1])
