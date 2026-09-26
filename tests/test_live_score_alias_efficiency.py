from copy import deepcopy
from unittest.mock import patch

from src import live_scores as ls


def test_prepared_keys_preserve_alias_scores():
    names = [None, "", "!!!", "FC Seoul", "서울 FC", "서울", "샬럿FC", "샬럿 FC", "abcd", "bcda"]
    for proto in names:
        for name in names:
            game = {"home": name, "home_alias": [name, "Seoul", "서울"]}
            keys = tuple(dict.fromkeys(ls._team_key(n) for n in [name, *game["home_alias"]]))
            assert ls._best_team_key_similarity(ls._team_key(proto), keys) == ls._team_similarity_with_aliases(proto, game, "home")


def test_normalization_is_linear_not_per_candidate_pair():
    named = [{"sport": "sc", "md": "09.26", "home": "Seoul", "away": "Busan",
              "home_alias": ["Seoul FC"], "away_alias": ["Busan FC"]} for _ in range(20)]
    proto = [{"sport": "sc", "date": f"09.26 {i:02}:00", "home": "Seoul", "away": "Busan"} for i in range(20)]
    original = deepcopy(proto)
    with patch.object(ls, "_team_key", wraps=ls._team_key) as normalize:
        # Identical competing candidates must still be rejected as ambiguous.
        assert ls.add_proto_aliases(named, proto) == 0
    assert normalize.call_count == 20 * 2 + 20 * 4
    assert proto == original
    assert all(g["home_alias"] == ["Seoul FC"] for g in named)


def test_aliases_are_not_cached_across_invocations():
    game = {"sport": "sc", "md": "09.26", "home": "xxx", "away": "yyy",
            "home_alias": [], "away_alias": []}
    proto = [{"sport": "sc", "date": "09.26", "home": "Seoul", "away": "Busan"}]
    assert ls.add_proto_aliases([game], proto) == 0
    game["home_alias"].append("Seoul")
    game["away_alias"].append("Busan")
    assert ls.add_proto_aliases([game], proto) == 1
