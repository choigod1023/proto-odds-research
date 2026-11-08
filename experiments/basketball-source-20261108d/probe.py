"""Bounded official-source probe; original bytes and receipts stay outside git."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path('C:/Users/user/AppData/Local/Temp/basketball-source-20261108d')
spec = importlib.util.spec_from_file_location('collector', ROOT/'scripts/collect_basketball_comparison_sources.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

SOURCES = {
    'nba-cdn-0022500274': 'https://cdn.nba.com/static/json/liveData/boxscore/boxscore_0022500274.json',
    'nba-stats-0022500274': 'https://stats.nba.com/stats/boxscoretraditionalv3?GameID=0022500274&StartPeriod=0&EndPeriod=0&StartRange=0&EndRange=0&RangeType=0',
    'kbl-box-S45G01N116': 'https://api.kbl.or.kr/match/S45G01N116/player-stat',
    'wkbl-04601072': 'https://www.wkbl.or.kr/m/game/result.asp?game_no=72&game_type=01&season_gu=046&ym=202602',
    'nba-injury-20261104': 'https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-04_12_15AM.pdf',
}


def main():
    with collector.singleflight(EVIDENCE):
        for label, url in SOURCES.items():
            meta, _ = collector.capture(label, url, EVIDENCE)
            print(json.dumps({k: meta.get(k) for k in ('label','status','bytes','body_sha256','received_at','error')}))


if __name__ == '__main__':
    main()
