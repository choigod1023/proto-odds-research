# Is the Korean Proto Fixed-Odds Market Efficient?

[한국어](README.md) · [日本語](README.ja.md) · **English**

**An empirical study of 553 rounds and 399,150 betting records (2023–2026)**

🌐 [Results site](https://choigod1023.github.io/proto-odds-research/) ·
📁 [28 test write-ups](findings/) · 📄 [Handoff](HANDOFF.md)

---

## Abstract

The question: **can you find an edge that beats market prices** in Korea's Proto (fixed-odds sports
betting) market? Four years — 553 rounds — were collected and 15 hypotheses were tested under pre-registration.

**No edge was found.** The required edge is 6.8pp; everything I could mobilize — information plus
structural choices combined — came to 4.8pp. Even importing the best betting edge in the literature
(+2–5%) leaves you at −7% against Proto's 12% margin.

What I quantified instead is **how to lose less without predicting anything**. Cutting legs saves 8.9pp;
capping the minimum odds lifts the hit rate from 65.9% to 77.6% while also reducing losses.
None of it changes the fact that every measured segment is negative.

As a byproduct, I documented **six false discoveries and four data-processing defects produced during the
research**. That is the most reusable output of this project.

---

## 1. Data and method

### 1.1 Collection

| Source | Size | Used for |
|---|---|---|
| WiseToto round archive | 553 rounds · 177,549 game rows · 399,150 betting records | Odds and results |
| NAVER match detail | 816 K League 1 matches (starting XI, formation, substitutions) | Lineups |
| K League shots | 816 matches (shots, shots on target) | Process metrics |
| KBO/MLB/NPB starters | 228 observations | Starter-information lag |
| Odds snapshots | Continuous, at 15-minute intervals | Line movement |

Collection is for non-commercial research and never touches paths disallowed by `robots.txt`.

### 1.2 Decision rules — fixed before looking at the data

Decide afterwards and any result can be read as a success. Every test used:

- **Temporal split**: train ≤2024 / validate 2025–
- **Cluster bootstrap by match** (selections within a match are not independent), seed 42
- **Year-by-year sign consistency** — the direction relative to that year's mean must hold in all four years
- Bonferroni for multiple testing

### 1.3 Automated checks

After the same function broke twice in one day, I wrote `src/selftest_all.py` (12 checks).
Every run verifies mapping coverage, probability properties, consistency with the rules, team-name
normalization, encoding, and whether the site can render. **All four defects in §4 came from places this
check did not yet cover.**

---

## 2. Results — 15 tests

| # | Question | Result | Document |
|---|---|---|---|
| 1 | Is there a +EV band from odds structure alone? | ❌ 0 of 134 | [Q0](findings/Q0.md) |
| 2 | Do nine team-level variables beat the market? | ❌ The market has priced them | [변수분석](findings/변수분석.md) |
| 3 | What about refining with pi-ratings? | ❌ Only 15% of the gap | [변수분석](findings/변수분석.md) |
| 4 | Is the starter-information lag an edge? | ❌ Failed to replicate | [정보시차_선발](findings/정보시차_선발.md) |
| 5 | Are football lineups an edge? | ❌ **The effect is real but doesn't beat the market** | [라인업_2군투입](findings/라인업_2군투입.md) |
| 6 | Is there opportunity in offshore odds divergence? | ❌ +0.9%, and it uses future information | [해외배당대조](findings/해외배당대조.md) |
| 7 | What about park factors? | ❌ The signal is real; the market knows | [파크팩터](findings/파크팩터.md) |
| 8 | What if the batting order is measured per player? | ❌ Effect size 1.7pp; would need 44 seasons | [라인업타선](findings/라인업타선.md) |
| 9 | Is there a league where Proto is weak? | ❌ 0 of 74 combinations | [리그별시장](findings/리그별시장.md) |
| 10 | Are thin markets an opportunity? | ❌ **The opposite** — first-ever meetings are worse | [세갈래_스캔](findings/세갈래_스캔.md) |
| 11 | If the odds are frozen, is there a lag? | ⚠️ 86.2% frozen is true; the information is worth 2.4pp | [정보시차_전제](findings/정보시차_전제.md) |
| 12 | What if every structural rule is stacked? | ⚠️ −13.9% → −11.5% | [규칙누적](findings/규칙누적.md) |
| 13 | Process metrics **together with** goal difference? | ❌ Process>result replicates; still doesn't beat the market | [과정지표_K리그결합](findings/과정지표_K리그결합.md) |
| 14 | Do unsold selections (drifting odds) win more? | ❌ The two sources have opposite signs | [line_move.py](src/line_move.py) |
| 15 | What is the ceiling in the literature? | 🔴 **Even the best pros make +2–5%** | [문헌_상한](findings/문헌_상한.md) |

**The only test that passed**: starting-pitcher metrics (Brier +0.006; +0.014 in close games). It does not clear the margin.

### 2.1 Why you cannot win

```
Required edge         6.8pp    ← to clear Proto's 12% 2-way margin
Size of the info      2.4pp    ← the sharp market's own 24-hour revision range
Structural gains      2.4pp    ← with all seven rules stacked
                    ─────
Even combined         4.8pp  <  6.8pp
```

| Market | Margin |
|---|---|
| Sharp books (Pinnacle, …) | 2–3% |
| Soft books (bet365, …) | 5.6% |
| **Proto 2-way** | **12.0%** |

The literature confirms it — long-run profitable bettors run around 3%, the CLV professionals target in
efficient markets is +2–3%, and even in soft niches it's +5%
([Aoki et al., KDD 2017](https://arxiv.org/abs/1706.02447);
[PLOS One 2023](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0287601)).

### 2.2 Correction to test 5 — the window existed, and it still failed

```
Proto odds fixed        60 hours before the match (86.2% never move afterwards)
Proto sales close       10 minutes before kickoff
Football lineups out    1 hour before kickoff
```

**You can see the lineup and still buy at the 60-hour-old price.** I had originally closed this as
"lineups come out after the odds, so they're unusable" — that was wrong. The effect is real too:
with 0–1 non-regulars started the win rate is 45.7%, with 6+ it is 30.1%, and within-team comparison
gives −6.08pp for 10 of 13 teams.

But measured against actual Proto odds it runs **−16.79% to −3.27%** — negative throughout
([`src/lineup_edge.py`](src/lineup_edge.py)). A real effect still doesn't clear a 13% margin.

---

## 3. So what did I build? — loss-reduction tools

I gave up on prediction and kept only **what arithmetic gives you**.

### 3.1 Levers, largest first

| Lever | Saves | Basis |
|---|---:|---|
| **Single leg vs. two legs** | **8.85pp** | Same selections: 1 leg −9.81% vs 2 legs −18.66% |
| Odds band (anything → 1.0–1.3) | 4.33pp | −13.58% → −9.25% |
| Structure (3-way handicap → 2-way) | 2.40pp | −15.12% → −12.72% |
| Round payout rate | 2.36pp | **Overlaps the odds band; no additional gain** |

**Removing one leg beats everything else combined.** Combinations multiply the margin.

⚠️ Single-leg (single-match) purchases are only available on matches designated "single match", and the
unit stake is ₩1,000 (versus ₩100 for combinations). [Rules](https://www.sportstoto.co.kr/proto_rules.php)

### 3.2 You raise the hit rate by throwing matches away

| Min-odds cap | Matches you can buy | Hit rate | ROI | 2-leg ticket hit |
|---|---:|---:|---:|---:|
| All | 100% | 65.86% | −10.25% | 43.37% |
| ≤1.5 | 71.6% | 69.76% | −9.95% | 48.67% |
| **≤1.3** | **31.9%** | **77.61%** | **−9.55%** | **60.24%** |
| ≤1.2 | 17.8% | 82.63% | −8.82% | 68.28% |

**This is the only axis where hit rate and ROI improve together.** Every other lever is a trade-off.
Tighten it and there are fewer matches to buy — at ≤1.3 that's three matches in ten, which is roughly the
floor at which you can still build a two-leg ticket that day.

### 3.3 The market matters too

Within the same odds band, markets differ by 1–3pp. Cells that pass the stability gate:

| Market × odds band | n | Hit rate | ROI |
|---|---:|---:|---:|
| **1X2, 1.0–1.3** | 2,709 | **80.1%** | **−6.48%** |
| Win①Loss, 3.0–5.0 | 9,528 | 26.4% | −9.00% |
| Handicap, 1.3–1.5 | 10,850 | 64.1% | −9.12% |
| Under/over, 1.3–1.5 | 4,347 | 62.3% | −9.46% |

Inside 1.0–1.3, 1X2 is −6.48% versus −10.06% for win/loss — a **3.6pp** spread at the same odds.

⚠️ Under/over yields few picks not because it's bad, but because the operator **moves the line to split
the two sides evenly** (a baseball line moving 5.5→11.5 still prices at 1.70–1.78), so the odds never fall
below 1.5. It simply doesn't sell what our rule looks for.

### 3.4 Settled rules

| Rule | Basis |
|---|---|
| **Single leg whenever it's available** | 8.9pp — larger than anything else |
| Otherwise two legs, with as few legs as possible | −6pp per leg |
| Never take odds of 5.0 or above | −33.5% |
| Prefer 2-way | Payout rate 87.8% vs 86.8% for 3-way handicap |
| **No** rule for picking among 3-way selections | 14 of 15 flip sign year to year |

> ⚠️ **All of it is negative.** Even at a 77.6% hit rate the ROI is −9.55%. This is not a tool for winning.

---

## 4. Methodology record — I produced six false findings

**This is the most reusable part of the project.** None of them were statistical problems; all were
data-processing problems, and every one **passed** Bonferroni, the bootstrap, and the temporal split.

| False finding | Apparent | Actual | Cause |
|---|---|---|---|
| KBL 1X2 | ROI **+30.05%** | Proto had the edge | 32% of results coded `⑤` were silently dropped |
| FA Cup higher league | 4/4 aligned | −7.22% | Selection effect + circular reasoning |
| Win①Loss mid band | −3.85% | 2024 alone: −16.52% | A single-year phenomenon |
| Market consistency | ROI **+12.48%** | −8.21% | 2,343 draws excluded |
| Volleyball under/over | Under **100%** | Unit mismatch | A points line applied to a set-based model |
| Lineup edge | ROI **+14.37%** | −16.79% | **Draws excluded (the same bug, again)** |

**The common thread: the sample suddenly shrinks and the result improves.** If the reason it shrinks
correlates with the outcome, it is certainly false.

### And I quietly published wrong numbers for four months

The six above are the ones I **caught**. The four below sat in the results tables **without setting off any
alarm**. All had the same root cause — **more than one copy of the source of truth existed.**

| Defect | Scale | Cause |
|---|---|---|
| Failed to strip decimals from team names | Match keys 44,915 → **71,282** (26,367 phantoms) | Six copies of `-?\d+`; `맨체스C -1.5` became a separate team |
| Odd/even missing from the win/loss mapping | **19,012 rows (10.9%)** dropped out of aggregation | Only one of four copies of `WIN_IDX` was updated |
| Charset guessing failed during collection | **3,429 rows** across 11 rounds were mojibake | `r.text` used chardet guessing — `result` broke too, so all of it was dropped |
| Performance figures hardcoded in the UI | 62.34% → actually **65.32%** | Fixed the pipeline but not the constants in the UI |

**Headline numbers actually moved** — the baseline hit rate went from 62.20% to **65.86%**, and the share of
matches buyable at a ≤1.3 cap from 22.1% to **31.9%**.
The direction of the conclusion (the one axis where hit rate and ROI improve together) is unchanged.

> All four passed the statistical tests. **A bootstrap just summarizes a badly built sample nicely.**

**The fix: force a single source of truth.**
`matches.clean_team` (team names) · `bets._WINNER` (result → hit index) ·
`bets.SEL_NAMES` (selection names) — everything else derives from those, and the UI reads
`loss_grades.json`. The self-tests catch copies reappearing.

### Checking tools

```bash
python3 src/selftest_all.py     # 12 checks. Mandatory after touching the pipeline
```

- [`src/guard.py`](src/guard.py) — z-tests the **hit rate of the rows you discarded** whenever you build a subset
- **Direction flip** — if betting the opposite way also looks good, your filter is wrong
- **Placebo control** — worse than random assignment means it's fake
- **Normalization regression table** — does `clean_team` strip integers, decimals, and leading/trailing
  tokens, and are there zero rows with a numeric token left afterwards? (Schalke 04 and Mainz 05 must be
  kept, so only whitespace-separated tokens are examined)
- **Mapping coverage** — does every (market, selection count) combination in the real data have both a name
  and a probability? Otherwise the site ships a raw `sel0`
- **Mojibake check** — fails if Cyrillic or Latin Extended shows up in team names or results

The lineup false finding was caught by the flip and placebo checks (reverse direction +25.73%, random +19.90%).
The decimal, odd/even, and mojibake defects **triggered no test at all**, which is why the regression table had to be written.

---

## 5. Limitations and what remains

| What's left | What it needs | Expectation |
|---|---|---|
| Line movement (CLV) | 4× the sample (currently 922, MDE 22.2pp) | Time will settle it |
| Per-match xG for the K League | A paid API | Shots on target is a rough proxy |
| Weather, absences, referees | Free, unmeasured | Likely a smaller effect than lineups |

See [무엇이_더_필요한가](findings/무엇이_더_필요한가.md) for the full breakdown.

**An honest forecast**: lineups were the most likely candidate to overturn the result with more data.
I tried it, and it failed. The remaining candidates have lower expected value than that.

---

## 6. Reproducing

```bash
pip install -r requirements.txt

python3 src/collect.py 2023 2024 2025 2026   # round archive (incremental)
python3 src/build_dataset.py                 # → games.csv · bets.csv
python3 src/loss_filter.py                   # loss-reduction grade table
python3 src/combo.py                         # combination design table
python3 src/today_combo.py                   # today's combination

python3 src/selftest_all.py                  # ⚠️ mandatory after code changes
```

Bootstrap seed fixed at 42. Temporal split: train ≤2024 / validate 2025–.

```
src/          collection, model, and test scripts
findings/     28 test write-ups (rationale and traps recorded)
web/          site source (React + Tailwind) → npm run build → docs/
docs/         GitHub Pages
deploy/       always-on collection on fly.io (odds 15 min · starters 30 min · offshore odds 15 min)
HANDOFF.md    for whoever picks this up. Half of it is dead ends
```

---

## ⚠️ Cautions

- **The only legal outlets are physical retailers and [betman.co.kr](https://www.betman.co.kr).**
  Using offshore bookmakers violates Korea's National Sports Promotion Act. This repository does not build
  betting-site integrations or automated betting.
- Collection is for **non-commercial research**, respects request intervals, and never touches paths
  disallowed by `robots.txt`.
- **Nothing in this repository guarantees a profit.** Every measured segment is negative.
