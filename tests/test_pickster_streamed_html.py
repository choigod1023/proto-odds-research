import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pytest
import pickster_watch as w


def streamed(selection='TB ML +115 1u', header='MIL 8:09 PM ET TB'):
    # Minimal public DOM structure only; no publisher article text or JS eval.
    return f'''<section class="scroll-mt-10"><div></div><div>{header}</div>
    <div class="grid-cols-[auto_minmax(0,1fr)]">
      <a href="/cappers/alpha"></a><template id="P:5"></template><template id="P:6"></template>
    </div></section>
    <div hidden id="S:5"><a href="/cappers/alpha">@alpha</a></div>
    <script>$RS("S:5","P:5")</script>
    <div hidden id="S:6"><span>5h {selection}</span></div>
    <script>$RS("S:6","P:6")</script>'''


def test_public_mlb_route_does_not_collect_default_nba_board():
    assert w.SLATE_URL == 'https://tailslips.com/slate?sport=mlb'


def test_streamed_pregame_and_result_keep_v4_identity():
    pre = w.parse_slate(streamed(), '2026-10-28T14:00:00Z')[0][0]
    final = w.parse_slate(streamed('TB ML +115 W +1.15u', 'MIL 3 FINAL TB 4'), '2026-10-29T02:00:00Z')[0][0]
    # Same ET slate date; observation and source outcome stay distinct.
    assert pre['pick_id'] == final['pick_id']
    assert pre['identity_version'] == 4 and pre['selection'] == 'TB ML +115'
    assert pre['game_started'] is False and pre['result'] is None
    assert final['game_started'] is True and final['result'] == 'W'
    assert pre['american_odds'] == final['american_odds'] == 115


def test_streamed_parlay_is_still_excluded():
    row = w.parse_slate(streamed('TB ML +115 1u in 5 -leg'), '2026-10-28T14:00:00Z')[0][0]
    assert row['is_parlay_leg'] is True


def test_unknown_rows_fail_instead_of_silently_collecting_zero():
    with pytest.raises(ValueError, match='0건'):
        w.parse_slate(streamed().replace('grid-cols-[auto_minmax(0,1fr)]', 'unknown-row'), '2026-10-28T14:00:00Z')
