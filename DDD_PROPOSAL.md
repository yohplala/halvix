# DDD proposal — pending decisions

Two larger restructurings surfaced during the architectural review that I
deliberately did **not** apply because each touches more than 15 files
(mostly tests) and the cost/benefit isn't obvious without your call.

## 1. Split `src/analysis/cycle_patterns.py` (2329 LOC)

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

### Recommendation

Defer until a real second projection variant or a second pattern analyzer
lands. The seams are already documented inside the file via section
comments, so the file size alone isn't currently hurting comprehension.

## 2. Rename `processor_total2b.py` → `processor.py` and inline `processor_base.py`

Right now we have:

```
data/
├── processor.py            # thin facade, re-exports get_processor
├── processor_base.py       # BaseTotal2Processor (ABC, 621 LOC)
└── processor_total2b.py    # Total2bProcessor (concrete, 624 LOC)
```

`BaseTotal2Processor` has exactly one subclass and the ABC has no external
consumers (verified after the facade narrowing commit). The
"base + concrete" split exists to anticipate a future `Total2aProcessor`
that may never arrive — and "total2b" reads as an implementation detail,
not an intent, since the family currently has one member.

### Concrete proposal

Either:

a. **Collapse**: merge `processor_base.py` and `processor_total2b.py` into
   `processor.py`, drop the abstract method (it becomes the only method),
   and rename `Total2bProcessor` to `Total2Processor`. Single concrete class,
   single file (~1200 LOC, comparable to `cycle_patterns.py`).

b. **Keep but rename**: rename `processor_total2b.py` to
   `processor_freeze_scaling.py` (describes _how_, not _which letter
   suffix_) and keep the ABC for the day a second variant lands.

### Why I didn't do it

The rename alone touches `main.py`, `tests/conftest.py`,
`tests/test_processor.py`, `data/__init__.py`, and the three docs files
that reference `processor_total2b.py` by name (CLAUDE.md, README,
TOTAL2_CALCULATION.md). It's a class rename, so it also breaks any
external pickle/parquet that stored the class name — not the case here,
but worth flagging.

### Recommendation

Pick (a) if you trust there will never be a `Total2cProcessor`.
Pick (b) if you want to preserve the extension point without the
"version-letter-suffix" smell. I'd lean (a): YAGNI on the ABC.
