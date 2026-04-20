"""
Prosperity Round 2 - Trading Algorithm v3
Products: ASH_COATED_OSMIUM, INTARIAN_PEPPER_ROOT

=== CHANGES FROM v2 (4,910 PnL) ===

PEPPER — position built too slowly (took ~30k timestamps to max at 50 units)
  Root cause: TAKE_EDGE=3 only bought asks at fair-3, but market asks sit at
  fair+7 to fair+8. We NEVER lifted aggressively. All fills came from passive
  bids queued inside the spread — slow.

  The math: 1 unit of pepper held from t=0 earns 100 ticks by end of day
  (price rises exactly 1 per 1000 timestamps * 100,000 timestamps = 100).
  Paying 7-8 ticks to lift an ask costs 7-8 and earns ~100 net. Hugely worth it.

  Fix:
    • Lift ANY ask at fair+8 or below (was: only fair-3 or below)
    • Post passive bid at fair-1 (was fair-2)
    • Order size = 50 (grab everything available, max out in one shot)
    • Simulated result: position maxes at t=700 vs t=30000 → +~1100 extra PnL

OSMIUM — unchanged (486 PnL, well-optimized)

Expected v3 PnL: ~6,000+ (4,910 + ~1,100 net pepper improvement)
"""

from datamodel import TradingState, Order
from typing import List, Dict
import json

OSMIUM = "ASH_COATED_OSMIUM"
PEPPER = "INTARIAN_PEPPER_ROOT"

POSITION_LIMITS = {OSMIUM: 50, PEPPER: 50}

# Osmium (mean-reverting ~10000)
OSM_MM_SPREAD  = 3
OSM_TAKE_EDGE  = 2
OSM_ORDER_SIZE = 8

# Pepper (trends up exactly 0.001 per timestamp unit)
# Lifting an ask at fair+8 costs 8 ticks but earns ~100 by end of day → net +92
PEPPER_LIFT_EDGE  = 8   # Buy asks up to fair + this many ticks above fair
PEPPER_BID_SPREAD = 1   # Passive bid: fair - 1 (tight, inside the ~16-tick spread)
PEPPER_ASK_SPREAD = 10  # Passive ask: fair + 10 (wide; keep longs, don't dump)
PEPPER_ORDER_SIZE = 50  # Max out position in one shot


class Trader:

    def get_osmium_fair(self, state: TradingState) -> float:
        od = state.order_depths.get(OSMIUM)
        if od and od.buy_orders and od.sell_orders:
            return (max(od.buy_orders) + min(od.sell_orders)) / 2
        return 10000.0

    def get_pepper_fair(self, state: TradingState, tdata: dict) -> float:
        """
        fair = base_price + timestamp / 1000
        base_price is calibrated once from the first valid order book reading
        and snapped to the nearest 1000 (always a round thousand per day).
        """
        ts = state.timestamp
        od = state.order_depths.get(PEPPER)

        if 'pepper_base' in tdata:
            return tdata['pepper_base'] + ts / 1000.0

        mid = None
        if od:
            if od.buy_orders and od.sell_orders:
                mid = (max(od.buy_orders) + min(od.sell_orders)) / 2
            elif od.buy_orders:
                mid = max(od.buy_orders)
            elif od.sell_orders:
                mid = min(od.sell_orders)

        if mid is not None:
            tdata['pepper_base'] = round((mid - ts / 1000.0) / 1000) * 1000
            return tdata['pepper_base'] + ts / 1000.0

        return 13000.0 + ts / 1000.0  # safe fallback

    def trade_osmium(self, state: TradingState, fair: float, orders: List[Order]):
        """Symmetric market-making with inventory skew for mean-reverting Osmium."""
        od    = state.order_depths[OSMIUM]
        pos   = state.position.get(OSMIUM, 0)
        limit = POSITION_LIMITS[OSMIUM]

        skew    = (pos / limit) * OSM_MM_SPREAD
        our_bid = round(fair - OSM_MM_SPREAD - skew)
        our_ask = round(fair + OSM_MM_SPREAD - skew)

        # Aggressive takes
        for ask in sorted(od.sell_orders):
            if ask > fair - OSM_TAKE_EDGE:
                break
            qty = min(-od.sell_orders[ask], limit - pos, OSM_ORDER_SIZE * 2)
            if qty > 0:
                orders.append(Order(OSMIUM, ask, qty))
                pos += qty

        for bid in sorted(od.buy_orders, reverse=True):
            if bid < fair + OSM_TAKE_EDGE:
                break
            qty = min(od.buy_orders[bid], limit + pos, OSM_ORDER_SIZE * 2)
            if qty > 0:
                orders.append(Order(OSMIUM, bid, -qty))
                pos -= qty

        # Passive quotes
        if (can_buy := limit - pos) > 0:
            orders.append(Order(OSMIUM, our_bid, min(OSM_ORDER_SIZE, can_buy)))
        if (can_sell := limit + pos) > 0:
            orders.append(Order(OSMIUM, our_ask, -min(OSM_ORDER_SIZE, can_sell)))

    def trade_pepper(self, state: TradingState, fair: float, orders: List[Order]):
        """
        Aggressive long accumulation for trending Pepper.

        Pepper rises ~1 tick per 1000 timestamps. Being long is ALWAYS profitable.

        We immediately lift asks up to fair+8, which gets us to the 50-unit
        position limit within the first few hundred timestamps.  The premium paid
        (~7-8 ticks) is dwarfed by the ~100-tick appreciation over the day.

        We NEVER sell below fair value. We only reduce position if someone
        bids 10+ ticks above fair (windfall), or passively at fair+10.
        """
        od    = state.order_depths.get(PEPPER)
        if od is None:
            return
        pos   = state.position.get(PEPPER, 0)
        limit = POSITION_LIMITS[PEPPER]

        # --- Aggressively lift asks up to fair + LIFT_EDGE ---
        if od.sell_orders and pos < limit:
            for ask in sorted(od.sell_orders):
                if ask > fair + PEPPER_LIFT_EDGE:
                    break
                qty = min(-od.sell_orders[ask], limit - pos, PEPPER_ORDER_SIZE)
                if qty > 0:
                    orders.append(Order(PEPPER, ask, qty))
                    pos += qty

        # --- Sell only into extreme overbought bids ---
        if od.buy_orders:
            for bid in sorted(od.buy_orders, reverse=True):
                if bid < fair + 10:
                    break
                qty = min(od.buy_orders[bid], limit + pos, PEPPER_ORDER_SIZE)
                if qty > 0:
                    orders.append(Order(PEPPER, bid, -qty))
                    pos -= qty

        # --- Passive bid: tight inside spread ---
        our_bid = round(fair - PEPPER_BID_SPREAD)
        if (can_buy := limit - pos) > 0:
            orders.append(Order(PEPPER, our_bid, min(PEPPER_ORDER_SIZE, can_buy)))

        # --- Passive ask: wide spread to hold longs ---
        our_ask = round(fair + PEPPER_ASK_SPREAD)
        if pos > 0 and (can_sell := limit + pos) > 0:
            orders.append(Order(PEPPER, our_ask, -min(PEPPER_ORDER_SIZE, can_sell)))

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        try:
            tdata = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            tdata = {}

        if OSMIUM in state.order_depths:
            orders: List[Order] = []
            self.trade_osmium(state, self.get_osmium_fair(state), orders)
            result[OSMIUM] = orders

        if PEPPER in state.order_depths:
            orders: List[Order] = []
            fair = self.get_pepper_fair(state, tdata)
            self.trade_pepper(state, fair, orders)
            result[PEPPER] = orders

        return result, conversions, json.dumps(tdata)