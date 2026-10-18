"""Keep the long-lived HTTP parent free of expanded historical game objects."""
from datetime import datetime
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
import threading
import logging
import zipfile
from match_response_snapshot import load, save

from match_api import KST, PreparedMatchResponses


class IsolatedMatchResponses(PreparedMatchResponses):
    def __init__(self, views, snapshot_path=None):
        super().__init__(views)
        self.refresh_lock = threading.Lock()
        self.snapshot_path = snapshot_path
        if snapshot_path is not None:
            try:
                ready, revisions, revision, games = load(snapshot_path, datetime.now(KST).date().isoformat())
                self.ready, self.published_revisions = ready, revisions
                self.views.revision, self.views.games = revision, games
                self.requested.update(ready)
            except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
                # Missing/corrupt/incompatible cache must not prevent startup.
                logging.getLogger(__name__).info('No compatible restart response cache')

    def refresh(self):
        with self.refresh_lock:
            revision = self.views.current_revision()
            day = datetime.now(KST).date().isoformat()
            with self.lock:
                scopes = sorted(self.requested)
                if all(scope in self.ready and self.ready[scope][0] == day
                       and self.published_revisions.get(scope) == revision
                       for scope in scopes):
                    return
            # TemporaryDirectory has a task-private location. Only our own child
            # writes this pickle; never deserialize network/DB/user-provided data.
            with tempfile.TemporaryDirectory(prefix='match-views-') as directory:
                output = Path(directory) / 'responses.pickle'
                subprocess.run([sys.executable, '-u', str(Path(__file__).with_name(
                    'match_response_worker.py')), str(self.views.database.path.resolve()),
                    str(output), *scopes], check=True, timeout=180)
                with output.open('rb') as stream:
                    ready, revisions, actual_revision, games = pickle.load(stream)
            # Publish only after a complete successful child exit. Failure keeps
            # the old bodies AND their original timestamps/detail revision.
            with self.views.lock:
                with self.lock:
                    self.views.games = games
                    self.views.revision = actual_revision
                    self.ready = ready
                    self.published_revisions = revisions
            if self.snapshot_path is not None:
                try:
                    save(self.snapshot_path, ready, revisions, actual_revision, games)
                except OSError:
                    # Disk failure must not undo a successfully published response.
                    logging.getLogger(__name__).warning('Restart response cache save failed')
