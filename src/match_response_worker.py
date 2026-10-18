"""Build compressed HTTP views in a disposable process, never in supervisor.

The private temporary output is IPC only, not a replacement prediction artifact.
No expanded picks/lineup tree crosses the process boundary.
"""
import argparse
import pickle
from pathlib import Path

from match_api import MatchViews, PreparedMatchResponses
from runtime_db import RuntimeDatabase


def build(database, output, scopes):
    prepared = PreparedMatchResponses(MatchViews(RuntimeDatabase(database)))
    prepared.requested = set(scopes)
    prepared.refresh()
    if set(prepared.published_revisions.values()) != {prepared.views.revision}:
        raise RuntimeError('prediction revision changed while preparing scopes; retry')
    with Path(output).open('wb') as stream:
        pickle.dump((prepared.ready, prepared.published_revisions,
                     prepared.views.revision, prepared.views.games), stream,
                    protocol=pickle.HIGHEST_PROTOCOL)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('database')
    parser.add_argument('output')
    parser.add_argument('scopes', nargs='+', choices=['recent', 'all'])
    args = parser.parse_args()
    build(args.database, args.output, args.scopes)
