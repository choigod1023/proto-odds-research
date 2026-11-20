"""Local admission simulation: no collector, network, or database is used."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy import supervisor

for mem in (468, 479, 511, 512):
    events = []
    with patch.object(supervisor, '_available_memory_mb', return_value=mem), \
         patch.object(supervisor, '_release_match_working_set', lambda: events.append('release_views')), \
         patch.object(supervisor, 'release_unused_heap', lambda: events.append('trim')), \
         patch.object(supervisor, '_publish_match_responses', lambda: events.append('publish')), \
         patch.object(supervisor.subprocess, 'run', lambda *a, **k: events.append('child') or SimpleNamespace(returncode=0)), \
         patch.object(supervisor, 'log', lambda *a: None):
        rc = supervisor._run_market_refresh().returncode
    assert (rc, 'child' in events, 'publish' in events) == ((1, False, False) if mem < 512 else (0, True, True))
    print(f'available_mb={mem} rc={rc} events={events}')
