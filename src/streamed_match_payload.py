"""Incremental picks projection: at most one full game object is decoded.

The caller compresses each detail immediately; the unneeded performance ledger
is consumed as parser events without building its Python object graph.
"""
import ijson
from ijson.common import ObjectBuilder


def _value(events, first, keep=True):
    builder = ObjectBuilder() if keep else None
    depth = 0
    current = first
    while True:
        _, event, value = current
        if builder is not None:
            builder.event(event, value)
        depth += (event in ('start_map', 'start_array')) - (event in ('end_map', 'end_array'))
        if depth == 0:
            return builder.value if builder is not None else None
        current = next(events)


def projected_payload(stream, prepare_game):
    try:
        return _projected_payload(stream, prepare_game)
    except (ijson.JSONError, StopIteration) as exc:
        raise ValueError('Invalid picks JSON') from exc


def _projected_payload(stream, prepare_game):
    events = iter(ijson.parse(stream, use_float=True))
    if next(events)[1] != 'start_map':
        raise ValueError('Expected picks object')
    payload = {}
    for _, event, key in events:
        if event == 'end_map':
            # Consume through EOF to reject truncated/trailing malformed input.
            if next(events, None) is not None:
                raise ValueError('Trailing JSON')
            return payload
        if event != 'map_key':
            raise ValueError('Expected field')
        first = next(events)
        if key == 'prediction_performance':
            _value(events, first, keep=False)
        elif key in ('live', 'past'):
            if first[1] != 'start_array':
                raise ValueError('Expected games array')
            games = []
            while True:
                first = next(events)
                if first[1] == 'end_array':
                    break
                game = _value(events, first)
                if not isinstance(game, dict):
                    raise ValueError('Expected game object')
                games.append(prepare_game(game))
            payload[key] = games
        else:
            payload[key] = _value(events, first)
    raise ValueError('Unclosed picks object')
