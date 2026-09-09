# K1 다음 경기 슈팅 예측: 첫 관문

과거 재사용 탐색 결과. 승패 적중률 실험이 아니며 운영 반영 금지.

```json
{
  "input_sha256": "eae45623ea74c6bc15c75dbcd510cd7a1bb6d0949774ea84b0e583e71e025672",
  "config": {
    "league": "K1",
    "window": 10,
    "minimum_history": 5,
    "history_lag_days": 2,
    "train_before": "2025-01-01",
    "ridge": 32,
    "bootstrap": 3000,
    "seed": 20260909
  },
  "code_sha256": "d78f953f2a4d08354a169bb20ecd7fd863023b03d9c567babf2dba4f65b4cd1e",
  "evaluation": "reused historical data; exploratory; no pregame capture proof",
  "source_games": 804,
  "excluded_playoffs": 12,
  "skipped": 0,
  "train_games": 421,
  "test_games": 338,
  "test_dates": 128,
  "status": "exploratory_only",
  "production_allowed": false,
  "test_from": "2025-02-15",
  "test_to": "2026-07-26",
  "metrics": {
    "shots": {
      "candidate_mse": 17.941875622974166,
      "comparisons": {
        "baseline": {
          "mse": 19.70857285815104,
          "gain": 1.766697235176876,
          "descriptive_99pct_day_bootstrap": [
            0.06306277273503168,
            3.5435129358318505
          ]
        },
        "opponent": {
          "mse": 18.326152504568974,
          "gain": 0.3842768815948067,
          "descriptive_99pct_day_bootstrap": [
            -0.4377811058536699,
            1.2083891068645631
          ]
        }
      },
      "next_stage_candidate": false
    },
    "sog": {
      "candidate_mse": 7.541504337980448,
      "comparisons": {
        "baseline": {
          "mse": 8.318954262863375,
          "gain": 0.7774499248829257,
          "descriptive_99pct_day_bootstrap": [
            0.24800605987707125,
            1.3114667137803397
          ]
        },
        "opponent": {
          "mse": 7.632917863243671,
          "gain": 0.09141352526322155,
          "descriptive_99pct_day_bootstrap": [
            -0.19625112824075538,
            0.3923810975993548
          ]
        }
      },
      "next_stage_candidate": false
    }
  },
  "predicted_sog_exceeds_shots": 0,
  "by_year": {
    "2025": {
      "games": 223,
      "shots": {
        "candidate_mse": 18.84942890831748,
        "baseline_mse": 20.880392458594464,
        "opponent_mse": 19.531110646555792
      },
      "sog": {
        "candidate_mse": 7.470265687969328,
        "baseline_mse": 8.38602946129872,
        "opponent_mse": 7.725845021626405
      }
    },
    "2026": {
      "games": 115,
      "shots": {
        "candidate_mse": 16.182011426177997,
        "baseline_mse": 17.4362618068564,
        "opponent_mse": 15.989581498803227
      },
      "sog": {
        "candidate_mse": 7.679645372349839,
        "baseline_mse": 8.188886704158314,
        "opponent_mse": 7.4527199822058465
      }
    }
  }
}
```

날짜 +2일 이후에만 과거 기록을 사용했다. 실제 공개시각은 검증되지 않았다. 99% 구간은 날짜 묶음 재표집의 기술적 구간이며 반복 탐색 보정이나 독립 재현을 대체하지 않는다. SOG 정의와 연도별 품질 감사 전에는 다음 단계로 승격하지 않는다.
