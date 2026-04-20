"""
Prosperity Round 2 - Trading Algorithm v2 (FIXED)
Products: ASH_COATED_OSMIUM, INTARIAN_PEPPER_ROOT

=== BUGS FIXED FROM v1 ===

BUG 1 (CRITICAL ~-6000 PnL): Pepper fallback fair value was OFF BY 1000
  - When order book was one-sided, fallback used "12000 + ts/1000"
  - Real fair is "13000 + ts/1000" on day 1
  - Fix: snap inferred base_price to nearest 1000; store it in traderData

BUG 2 (CRITICAL ~-2800 PnL): Pepper is TRENDING — symmetric MM loses money
  - Old strategy posted bids AND asks symmetrically around fair value
  - Since pepper rises 0.001/tick, short positions compound losses as price rises
  - Fix: directional strategy — only accumulate longs, sell at a premium

BUG 3 (minor): One-sided order book → wrong fallback → catastrophic fills
  - Fix: infer base_price from whichever side is available, round to 1000
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict
import json

OSMIUM = "ASH_COATED_OSMIUM"
PEPPER = "INTARIAN_PEPPER_ROOT"

POSITION_LIMITS = {OSMIUM: 50, PEPPER: 50}

# Osmium (mean-reverting ~10000)
OSM_MM_SPREAD  = 3    # Half-spread for passive quotes
OSM_TAKE_EDGE  = 2    # Take orders this many ticks from fair
OSM_ORDER_SIZE = 8

# Pepper (trending up ~0.001 per timestamp unit)
PEPPER_TAKE_EDGE  = 3   # Buy asks this cheap vs fair
PEPPER_BID_SPREAD = 2   # Post bids 2 below fair
PEPPER_ASK_SPREAD = 10  # Post asks 10 above fair (only reduce when at premium)
PEPPER_ORDER_SIZE = 10


class Trader:

    # ------------------------------------------------------------------ #
    #  Fair value helpers                                                  #
    # ------------------------------------------------------------------ #

    def get_osmium_fair(self, state: TradingState) -> float:
        od = state.order_depths.get(OSMIUM)
        if od and od.buy_orders and od.sell_orders:
            return (max(od.buy_orders) + min(od.sell_orders)) / 2
        return 10000.0

    def get_pepper_fair(self, state: TradingState, tdata: dict) -> float:
        """
        fair = base_price + timestamp / 1000

        base_price is inferred once and cached.  It should be a multiple
        of 1000 (e.g. 11000, 12000, 13000) depending on the day.
        """
        ts = state.timestamp
        od = state.order_depths.get(PEPPER)

        if 'pepper_base' in tdata:
            return tdata['pepper_base'] + ts / 1000.0

        # Try to calibrate from the order book
        mid = None
        if od:
            if od.buy_orders and od.sell_orders:
                mid = (max(od.buy_orders) + min(od.sell_orders)) / 2
            elif od.buy_orders:
                mid = max(od.buy_orders)
            elif od.sell_orders:
                mid = min(od.sell_orders)

        if mid is not None:
            raw_base = mid - ts / 1000.0
            # Snap to nearest 1000 — base is always a whole thousand
            tdata['pepper_base'] = round(raw_base / 1000) * 1000
            return tdata['pepper_base'] + ts / 1000.0

        # Last-resort fallback (very unlikely after first tick)
        return 13000.0 + ts / 1000.0

    # ------------------------------------------------------------------ #
    #  Trading logic                                                       #
    # ------------------------------------------------------------------ #

    def trade_osmium(self, state: TradingState, fair: float, orders: List[Order]):
        """Symmetric market-making + aggressive taking for mean-reverting Osmium."""
        od    = state.order_depths[OSMIUM]
        pos   = state.position.get(OSMIUM, 0)
        limit = POSITION_LIMITS[OSMIUM]

        # Inventory-skewed quotes
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
            orders.append(Order(OSMIUM, our_bid,  min(OSM_ORDER_SIZE, can_buy)))
        if (can_sell := limit + pos) > 0:
            orders.append(Order(OSMIUM, our_ask, -min(OSM_ORDER_SIZE, can_sell)))

    def trade_pepper(self, state: TradingState, fair: float, orders: List[Order]):
        """
        Directional long strategy for trending Pepper.

        Pepper price rises ~1 per 1000 timestamps per day.  Being SHORT is
        systematically unprofitable.  Strategy:
          • Aggressively BUY cheap asks (below fair - edge)
          • Post passive bids just below fair to accumulate longs
          • Sell ONLY at a significant premium (far above fair) to reduce position
            when near the limit; never sell below fair value
        """
        od    = state.order_depths.get(PEPPER)
        if od is None:
            return
        pos   = state.position.get(PEPPER, 0)
        limit = POSITION_LIMITS[PEPPER]

        # Aggressive buy: snap up cheap asks
        for ask in sorted(od.sell_orders):
            if ask > fair - PEPPER_TAKE_EDGE:
                break
            qty = min(-od.sell_orders[ask], limit - pos, PEPPER_ORDER_SIZE * 2)
            if qty > 0:
                orders.append(Order(PEPPER, ask, qty))
                pos += qty

        # Aggressive sell: only dump into absurdly overbought bids
        for bid in sorted(od.buy_orders, reverse=True):
            if bid < fair + 8:
                break
            qty = min(od.buy_orders[bid], limit + pos, PEPPER_ORDER_SIZE)
            if qty > 0:
                orders.append(Order(PEPPER, bid, -qty))
                pos -= qty

        # Passive bid: accumulate longs
        our_bid = round(fair - PEPPER_BID_SPREAD)
        if (can_buy := limit - pos) > 0:
            orders.append(Order(PEPPER, our_bid, min(PEPPER_ORDER_SIZE, can_buy)))

        # Passive ask: ONLY post if we are long; keep spread wide so longs hold
        our_ask = round(fair + PEPPER_ASK_SPREAD)
        if pos > 0 and (can_sell := limit + pos) > 0:
            orders.append(Order(PEPPER, our_ask, -min(PEPPER_ORDER_SIZE, can_sell)))

    # ------------------------------------------------------------------ #
    #  Main entry point                                                    #
    # ------------------------------------------------------------------ #

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        try:
            tdata = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            tdata = {}

        if OSMIUM in state.order_depths:
            fair = self.get_osmium_fair(state)
            osm_orders: List[Order] = []
            self.trade_osmium(state, fair, osm_orders)
            result[OSMIUM] = osm_orders

        if PEPPER in state.order_depths:
            fair = self.get_pepper_fair(state, tdata)
            pep_orders: List[Order] = []
            self.trade_pepper(state, fair, pep_orders)
            result[PEPPER] = pep_orders

        return result, conversions, json.dumps(tdata)
