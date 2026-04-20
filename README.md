# Prosperity Round 2 — Trading Algorithm

**Author:** Dibyajyoti Parida  
**Final Score:** 279,587  
**Products:** `ASH_COATED_OSMIUM` · `INTARIAN_PEPPER_ROOT`

---

## Products

### ASH_COATED_OSMIUM (Osmium)
- Fair value is **~10,000** — strongly mean-reverting with a standard deviation of ~5 ticks
- Market spread: ~16 ticks
- Strategy: symmetric market-making with inventory skew

### INTARIAN_PEPPER_ROOT (Pepper)
- Fair value follows a **perfectly linear upward trend**: `fair = base_price + timestamp / 1000`
- `base_price` is a round multiple of 1,000 that depends on the day (e.g. 13,000 on day 1)
- Rises exactly 1 tick per 1,000 timestamps → ~100 ticks of appreciation per day
- Market spread: ~16 ticks
- Strategy: aggressive long accumulation

---

## Strategy

### Osmium — Symmetric Market-Making

Fair value is estimated from the order book mid-price. Quotes are posted symmetrically at `fair ± 3`, skewed toward the flat side when holding inventory. Orders significantly mispriced versus fair are taken immediately.

```
our_bid = fair - 3 - skew
our_ask = fair + 3 - skew
skew    = (position / limit) * 3
```

**Aggressive taking:** buy any ask at `fair - 2` or below; sell into any bid at `fair + 2` or above.

### Pepper — Directional Long Accumulation

Because Pepper rises every single timestamp, holding a long position is the core profit driver. Every unit held earns `0.001 × remaining_timestamps` in mark-to-market appreciation. The critical insight is that paying up to **8 ticks** to immediately lift an ask is highly profitable:

| Cost of lifting (ticks) | Gain from holding to end of day (ticks) | Net |
|---|---|---|
| ~7–8 | ~100 | ~+92 |

**Position is maxed at 50 units within ~700 timestamps** (the first few hundred milliseconds of the day), then held for the remainder.

```
Take:    buy all asks where ask ≤ fair + 8
Passive: bid at fair - 1 (to catch any dip)
         ask at fair + 10 (only unwind at a clear premium)
```

Selling short is never done — it is structurally unprofitable on a trending asset.

---

## Fair Value for Pepper

`base_price` is calibrated once on the first valid order book tick and cached across the entire day using `traderData`. It is snapped to the nearest 1,000 to eliminate order book noise:

```python
raw_base = mid - timestamp / 1000
base_price = round(raw_base / 1000) * 1000   # always a round thousand
fair = base_price + timestamp / 1000
```

This produces a fair value estimate with an error of less than 12 ticks versus the market mid — essentially perfect.

---

## Iteration History

| Version | PnL | Key change |
|---|---|---|
| v1 | **−9,460** | Initial symmetric market-making on both products |
| v2 | **+4,910** | Fixed Pepper: formula-based fair value, long-only direction |
| v3 | **+6,000+** | Lifted Pepper asks immediately (up to fair+8) to max position by t=700 |

### v1 → v2: Two critical bugs fixed

**Bug 1 (~−6,000 PnL):** When the Pepper order book was one-sided, the fallback fair value used `12000 + ts/1000` instead of `13000 + ts/1000` — off by exactly 1,000. Orders posted 1,000 ticks away from market fair value were immediately filled at catastrophic prices.

**Bug 2 (~−2,800 PnL):** Symmetric market-making on a trending asset. Every short position filled via passive asks lost money as Pepper continued rising. The fix was to go directional long-only and hold.

### v2 → v3: Position build speed

With `TAKE_EDGE = 3`, the aggressiveness threshold was `fair - 3`. The market's best ask sat at `fair + 7` to `fair + 8`, so **no aggressive takes ever happened** on Pepper. The position took ~30,000 timestamps to reach its limit of 50 via slow passive fills, leaving ~1,500 PnL on the table. Raising the threshold to `fair + 8` immediately filled the position and recovered the gap.

---

## Parameters

```python
# Osmium
OSM_MM_SPREAD  = 3    # half-spread for passive quotes
OSM_TAKE_EDGE  = 2    # ticks from fair to trigger aggressive take
OSM_ORDER_SIZE = 8    # passive quote size

# Pepper
PEPPER_LIFT_EDGE  = 8   # lift asks up to fair + 8
PEPPER_BID_SPREAD = 1   # passive bid: fair - 1
PEPPER_ASK_SPREAD = 10  # passive ask: fair + 10 (wide; hold longs)
PEPPER_ORDER_SIZE = 50  # fill the position limit in one shot
```

---
