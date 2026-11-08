"""Compressed-only responses with a single non-blocking rebuild slot.

Keep the previous wire payload during refresh/failure. Concurrent requests do
not allocate another copy of a large artifact or wait behind its compression.
"""
import gzip
import threading
from collections import OrderedDict


class ArtifactResponses:
    def __init__(self, database, memory_lock=None):
        self.database = database
        self.ready = {}
        self.details = OrderedDict()
        self.lock = threading.Lock()
        self.rebuild = threading.Lock()
        self.memory_lock = memory_lock

    def get_bytes(self, name, compressed=True):
        return self.get_response(name, compressed=compressed)[0]

    def get_response(self, name, compressed=True, if_none_match=None, *, view=None, key=None):
        cache = self.details if view == 'detail' else self.ready
        cache_key = (name, view, key) if view is not None else name
        with self.lock:
            cached = cache.get(cache_key)
            if cached is not None and view == 'detail':
                cache.move_to_end(cache_key)
        if self.rebuild.acquire(blocking=False):
            memory_owned = False
            try:
                metadata = self.database.artifact_metadata(name, include_size=False)
                if metadata is not None and (cached is None or cached[0] != metadata['stored_at']):
                    if (name == 'picks_v2' or view is not None) and self.memory_lock is not None:
                        memory_owned = self.memory_lock.acquire(blocking=False)
                        if not memory_owned:
                            raise KeyError('large artifact waiting for memory slot')
                    if view is None:
                        stored = self.database.get_artifact_gzip(name)
                    else:
                        from live_feed_views import get_view_gzip
                        stored = get_view_gzip(self.database, name, view, key)
                    if stored is not None:
                        body, revision, etag = stored
                        cached = (revision, body, etag)
                        with self.lock:
                            cache[cache_key] = cached
                            if view == 'detail':
                                cache.move_to_end(cache_key)
                                if len(cache) > 24:
                                    cache.popitem(last=False)
            except Exception:
                if cached is None:
                    raise
            finally:
                if memory_owned:
                    self.memory_lock.release()
                self.rebuild.release()
        if cached is None:
            raise KeyError('artifact preparing')
        etag = cached[2]
        # Compare against the body actually selected, including stale fallback.
        if if_none_match == etag:
            return None, etag
        return (cached[1] if compressed else gzip.decompress(cached[1])), etag
