"""Compressed-only responses with a single non-blocking rebuild slot.

Keep the previous wire payload during refresh/failure. Concurrent requests do
not allocate another copy of a large artifact or wait behind its compression.
"""
import gzip
import threading


class ArtifactResponses:
    def __init__(self, database, memory_lock=None):
        self.database = database
        self.ready = {}
        self.lock = threading.Lock()
        self.rebuild = threading.Lock()
        self.memory_lock = memory_lock

    def get_bytes(self, name, compressed=True):
        with self.lock:
            cached = self.ready.get(name)
        if self.rebuild.acquire(blocking=False):
            memory_owned = False
            try:
                metadata = self.database.artifact_metadata(name, include_size=False)
                if metadata is not None and (cached is None or cached[0] != metadata['stored_at']):
                    if name == 'picks_v2' and self.memory_lock is not None:
                        memory_owned = self.memory_lock.acquire(blocking=False)
                        if not memory_owned:
                            raise KeyError('large artifact waiting for memory slot')
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
                if memory_owned:
                    self.memory_lock.release()
                self.rebuild.release()
        if cached is None:
            raise KeyError('artifact preparing')
        return cached[1] if compressed else gzip.decompress(cached[1])
