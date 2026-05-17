# DDD proposal — status

Two larger restructurings surfaced during the architectural review.

**Status**:
- Item #1 (cycle_patterns split): **partial** — helpers + dataclasses
  extracted into `cycle_points.py` (commit `d43b36b`). Kernel and projection
  extractions left for a dedicated follow-up PR; see "Why I didn't do it"
  below.
- Item #2 (processor collapse): **done** — commit `ddbb661`. Single
  `Total2Processor` class in `processor.py`, on-disk metadata preserved.

## 1. Split `src/analysis/cycle_patterns.py` (2329 LOC → 2229 LOC after first slice)

The file has clean internal seams. Roughly half the lines belong to the
identification kernel, half to the projection methods, and a thin orchestration
layer at the top.

```
cycle_patterns.py (2329 LOC)
├── L1-262    helpers + dataclasses           ─►  cycle_points.py
│             (_to_date, fib_retracement_ratio,
│              CyclePoint, CoinPatternResult,
│              SegmentData, _SegmentIterState,
│              _make_point, _project_min1)
│
├── L264-1103 identification kernel           ─►  point_detection.py
│             (_identify_cycle_points + 3-pass
│              algorithm, _build_segments,
│              _build_points_index,
│              _count_min1_cycles)
│
├── L1105-1717 projection methods             ─►  projections.py
│             (trendline fitting, fib extension,
│              diminishing return, historical
│              peak, composite, retracement)
│
├── L1718-1825 classification + runner        ─►  stays in cycle_patterns.py
│             (_classify_pattern,                  (the orchestrator)
│              _run_projections)
│
└── L1826-2329 orchestration + IO             ─►  stays in cycle_patterns.py
              (_smooth_round_trips, analyze_btc,    (or move IO to a separate
               analyze_coin, analyze_all_coins,      results_writer.py)
               get_top_coins, save_results)
```

### Why I didn't do it

`tests/test_cycle_patterns.py` exercises ~40 private methods directly via
`analyzer._fit_log_trendlines(...)`, `analyzer._identify_cycle_points(...)`,
`CyclePatternAnalyzer._build_points_index(...)`, etc. A clean split would
require either:

a. Turning the private methods into module-level functions and updating every
   call site in the tests (~80 test methods touched).
b. Keeping the class facade intact with delegation methods that just forward
   to the new modules — which is mostly cosmetic and adds indirection.

Option (a) is the right architectural answer but it's a single big rewrite
of the test file; option (b) is reversible but earns less.

### What was done

First slice extracted in commit `d43b36b`:

- `PointType`, `Confidence` type aliases
- `CyclePoint`, `CoinPatternResult`, `SegmentData`, `_SegmentIterState` dataclasses
- `_to_date`, `fib_retracement_ratio`, `_make_point`, `_project_min1` helpers

All moved to `src/analysis/cycle_points.py` (208 LOC). `cycle_patterns.py`
re-imports the names so existing tests keep working.

### What remains

The identification kernel (`_pass1_find_max2` etc.) and the projection
methods (`_fit_log_trendlines` etc.) — roughly 1500 LOC. Audit of the
test surface confirms the agent's original cost estimate:

| method                          | test call sites |
|---------------------------------|-----------------|
| `_identify_cycle_points`        | 24              |
| `_calculate_weighted_composite` | 13              |
| `_fit_log_trendlines`           | 10              |
| `_calculate_retracement_ratio`  | 9               |
| `_calculate_fib_extension`      | 8               |
| `_calculate_diminishing_return` | 8               |
| `_classify_pattern`             | 6               |
| ...                             | (~10 more)      |

Total ~88 test sites. The most-called methods are instance methods that
genuinely use analyzer state (`self.all_halvings`, `self.price_cache`),
so a clean option-(a) refactor needs to either thread that state as
explicit parameters or accept a thin orchestrator class delegating to
module-level functions.

### Recommendation

Defer until a real second projection variant or a second pattern analyzer
lands. The first-slice extraction has documented the natural seams via
imports, and the file size (2229 LOC) is comparable to the consolidated
processor.py (1011 LOC) after item #2 landed — not unreasonable for a
single domain concept.

## 2. Rename `processor_total2b.py` → `processor.py` and inline `processor_base.py` — **DONE**

Resolved in commit `ddbb661` via option (a) "Collapse". `processor_base.py`
and `processor_total2b.py` are gone; a single `Total2Processor` class lives
in `processor.py`. The on-disk metadata still labels itself `total2b` (the
`index_type` field in `Total2Result` defaults to `"total2b"`, and the
`scaling_events` records still carry `prev_total2b`) so any existing JSON
consumer continues to work unchanged.
