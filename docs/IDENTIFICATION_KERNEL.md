# Identification Kernel — Segment-Based Cycle Point Detection

**[← Back to Pattern Analysis](PATTERN_ANALYSIS.md)** | **[← Back to README](../README.md)**

---

This document describes the **identification kernel**: the algorithm that detects structural min/max points within Bitcoin halving cycles. These points are the input to all four projection methods (trendline, Fibonacci, diminishing returns, historical peak).

## Overview

The identification kernel (`_identify_cycle_points`) divides price history into **segments between consecutive halvings** and processes each segment with a 3-pass algorithm. This replaces the earlier fixed-window approach and handles edge cases like cross-halving dips (e.g., the COVID crash) and tokens with short price histories.

**Entry point**: `CyclePatternAnalyzer._identify_cycle_points(df)` in [`src/analysis/cycle_patterns.py`](../src/analysis/cycle_patterns.py)

## Point Types

Each cycle can have up to 4 characteristic points:

| Point | Role | Required? | Cycle Assignment |
|-------|------|-----------|-----------------|
| **max2** | Post-halving cycle peak | Structural (always detected) | Previous cycle |
| **min2** | Post-halving dip before rally | Optional (requires validation) | Previous cycle |
| **min1** | Pre-halving cycle trough | Structural (always detected) | Current cycle |
| **max1** | Pre-halving local high | Optional (requires validation) | Current cycle |

**Cycle assignment rule**: Within a segment [H[n-1], H[n]], max2 and min2 belong to cycle n-1, while min1 and max1 belong to cycle n.

## Segments

The price history is divided at halving boundaries. Given halvings H2, H3, H4, H5:

```
Segment 0: [H2, H3]  →  Cycle 2 (max2, min2) + Cycle 3 (min1, max1)
Segment 1: [H3, H4]  →  Cycle 3 (max2, min2) + Cycle 4 (min1, max1)
Segment 2: [H4, H5]  →  Cycle 4 (max2, min2) + Cycle 5 (min1, max1)
Post:       (H5, now] →  Cycle 5 (max2, min2, min1 — current/incomplete)
```

Each segment is represented by a `SegmentData` dataclass containing the date range, cycle numbers, price data slice, and mutable fields for detected max2/min2 candidates.

## 3-Pass Algorithm

### Pass 1: Find max2 (cycle peaks)

For each segment, finds the **maximum price** in [seg_start, seg_end - buffer]:

- `MAX2_PRE_HALVING_BUFFER_DAYS` (60 days) excludes the pre-halving rally zone from the max2 search
- This prevents a pre-halving pump (structurally max1 of the next cycle) from being picked as the cycle peak

### Pass 2: Find min2 candidates (post-halving dips)

For each segment, finds the **minimum price** in [seg_start, max2_date]:

- This is a candidate only — validation happens in Pass 3
- The search window extends from the halving to the cycle peak

### Pass 3: Validate and detect all points

Processes segments **sequentially** (order matters because each segment's decisions depend on the previous segment's state). For each segment:

#### Step 1: Extend min2 search (cross-halving dips)

When the previous segment had a max1, the min2 search extends backward to the previous max1 date. This catches dips that cross the halving boundary — e.g., the COVID crash (March 2020) occurs before the 3rd halving (May 2020) but is the true structural min2 for cycle 3.

#### Step 2: Validate min2

A min2 candidate must pass all of these checks:

1. **Alternation rule**: If the previous segment had no max1, this segment has no min2. Two consecutive min-type endings would mean no separating high.
2. **Fibonacci retracement**: The retracement ratio `log10(max2/min2) / log10(max2/prev_min1)` must be ≥ `MIN_RETRACEMENT_LEVEL` (23.6%). This ensures the dip is significant relative to the prior cycle's range.
3. **Launch date guard**: For the first segment (no prior context), a min2 within `LAUNCH_DATE_BUFFER_DAYS` (7 days) of the coin's first data point is suppressed — launch price is not a structural dip.

#### Step 3: Handle validated min2

If min2 is **valid**:
- Emit min2 as a point for the previous cycle

If min2 is **invalid**:
- When the previous segment had a max1 AND min2 fails, the max1 and max2 are adjacent peaks without a separating dip → merge them via `_merge_adjacent_maxes()`. The merged point is always **max2** (major type).
- When no min2 exists between the previous min1 and current max2, check if the price went lower in that gap. If so, replace min1 with the lower point (`_replace_min1_if_lower`)

#### Step 4: Find min1

Find the minimum price in (max2_date, effective_end]:
- Must be below max2 even without prior context (no-context else branch)
- When prior min1 exists, must also be below it for the "lower low" expectation

#### Step 5: Find max1

Find the maximum price in [min1_date, seg_end], extended into the next segment's min2 zone:
- If the next segment has a min2 candidate, the max1 search extends to that min2 date
- Validated via Fibonacci retracement: `log10(max2/max1) / log10(max2/min1)` must be ≥ 23.6%

#### Step 6: Correct min1 with max1 boundary

The initial min1 search is bounded by the segment end (halving date). The true bottom may occur a few days past the halving. When max1 is found, rescan [min1, max1) for a potentially lower point.

#### Step 7: Merge mins (no max1 case)

When no max1 is detected in the current segment:
- min2 and min1 are not separated by a high, so they merge
- Keep the lower of min2/min1 as the new min1 (major type)
- Remove min2 from the points list

This ensures that when a segment has only two extrema (no optional points), they are always the two **major** ones: max2 and min1. This is critical for projection methods (especially diminishing returns) that rely on min1→max2 pairs.

### Post-halving detection (current cycle)

After processing all inter-halving segments, the algorithm handles data beyond the final halving:

1. **max2**: Maximum price since the last halving (provisional — cycle is ongoing)
2. **min2**: If the previous segment had max1, searches [prev_max1_date, max2_date] for a dip, validated by Fibonacci retracement
3. **min1**: Minimum price since max2 (provisional current-cycle trough)

## Merge Invariant: Major Extrema Survive

When an optional point (min2 or max1) is merged with its major counterpart, the result always keeps the **major type**:

| Merge | Trigger | Surviving Type | Rule |
|-------|---------|---------------|------|
| max1 + max2 | No min2 separates them | **max2** | Keep the higher price |
| min2 + min1 | No max1 separates them | **min1** | Keep the lower price |

This guarantees that a segment with only two extrema always has the two structural (major) points: **max2** and **min1**. Projection methods — especially diminishing returns — rely on min1→max2 pairs across cycles.

## State Flow Between Segments

Pass 3 maintains state across segments via instance attributes:

| State Variable | Purpose |
|----------------|---------|
| `prev_min1_price` | Last detected min1 price (for min2 Fib retracement validation) |
| `prev_min1_point` | Last detected min1 CyclePoint (for min1 replacement) |
| `prev_had_max1` | Whether the previous segment had a max1 (alternation rule) |
| `prev_max1_date` | Date of the previous max1 (min2 search extension) |

These are stored as `self._prev_*` attributes to pass state from `_pass3_validate_and_detect` to `_detect_post_halving_points`.

## Configuration Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX2_PRE_HALVING_BUFFER_DAYS` | 60 | Days before halving excluded from max2 search |
| `MIN_RETRACEMENT_LEVEL` | 0.236 | 23.6% Fibonacci threshold for optional points |
| `LAUNCH_DATE_BUFFER_DAYS` | 7 | Suppress min2 within this many days of first price data |

## Output

The kernel returns a flat `list[CyclePoint]`. Each point has:

```python
@dataclass
class CyclePoint:
    date: date
    price: float
    cycle_num: int
    point_type: PointType  # "min1" | "max1" | "min2" | "max2"
    days_from_halving: int
    projected: bool = False  # True for projected points (e.g., min1 at 23.6% level)
```

Downstream, `_build_points_index()` converts this list into a `dict[tuple[int, PointType], list[CyclePoint]]` for O(1) lookup by (cycle, point_type). This index is built once per coin and passed to all four projection methods.

## Method Decomposition

The kernel is split into focused helpers:

| Method | Role |
|--------|------|
| `_identify_cycle_points()` | Orchestrator |
| `_build_segments()` | Constructs `list[SegmentData]` from halvings |
| `_pass1_find_max2()` | Pass 1: finds max2 per segment |
| `_pass2_find_min2_candidates()` | Pass 2: finds min2 candidates |
| `_pass3_validate_and_detect()` | Pass 3: sequential validation loop |
| `_extend_min2_search()` | Extends min2 search to prev max1 |
| `_validate_min2()` | Checks alternation, Fib retracement, launch guard |
| `_merge_adjacent_maxes()` | Merges max1 + max2 when no min2 separates them |
| `_replace_min1_if_lower()` | Replaces min1 when bear continues past it |
| `_find_min1()` | Finds min1 with retracement check |
| `_find_max1()` | Finds max1 with extended search |
| `_find_max1_before_min2()` | Finds max1 for short-history tokens with no prior max1 |
| `_correct_min1_with_max1()` | Corrects min1 using max1 boundary |
| `_detect_post_halving_points()` | Handles current/incomplete cycle |

---

**[← Back to Pattern Analysis](PATTERN_ANALYSIS.md)** | **[← Back to README](../README.md)**
