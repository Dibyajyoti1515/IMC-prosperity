"""
Prosperity Round 2 - Trading Algorithm
Products: ASH_COATED_OSMIUM, INTARIAN_PEPPER_ROOT

Strategy Summary:
-----------------
1. ASH_COATED_OSMIUM (OSMIUM):
   - Fair value is ~10000 (mean-reverting, std ~5)
   - Market-make: post bids slightly below fair value, asks slightly above
   - Aggressively take orders that deviate significantly from fair value
   - Lean position when holding inventory (to avoid adverse selection)

2. INTARIAN_PEPPER_ROOT (PEPPER):
   - Fair value = 10000 + (day+2)*1000 + timestamp/1000
   - Perfectly linear trend! Price rises 1 unit per 1000 timestamps per day
   - Market-make around the known fair value with tight spreads
   - Also aggressively take mispriced orders
"""

from datamodel import (
    OrderDepth, TradingState, Order, Listing, Observation,
    ConversionObservation, Trade, Symbol, Product, Position
)
from typing import List, Dict, Any
import math

OSMIUM = "ASH_COATED_OSMIUM"
PEPPER = "INTARIAN_PEPPER_ROOT"

# Position limits (standard Prosperity limits)
POSITION_LIMITS = {
    OSMIUM: 50,
    PEPPER: 50,
}

# Market making parameters
MM_SPREAD = 3       # Half-spread to post around fair value
TAKE_EDGE = 2       # Take orders that are >= 2 units away from fair value (in our favour)
ORDER_SIZE = 8      # Default order size for market making
MAX_SKEW = 10       # Start skewing orders when position > this


class Trader:

    def __init__(self):
        self.day = None

    def get_osmium_fair(self, state: TradingState) -> float:
        od = state.order_depths.get(OSMIUM)
        if od and od.buy_orders and od.sell_orders:
            best_bid = max(od.buy_orders.keys())
            best_ask = min(od.sell_orders.keys())
            return (best_bid + best_ask) / 2
        return 10000.0

    def get_pepper_fair(self, state: TradingState) -> float:
        od = state.order_depths.get(PEPPER)
        if od and od.buy_orders and od.sell_orders:
            best_bid = max(od.buy_orders.keys())
            best_ask = min(od.sell_orders.keys())
            mid = (best_bid + best_ask) / 2
        else:
            mid = None

        # Estimate fair value using timestamp
        # timestamp goes from 0 to ~999900 each day
        # fair = base_price + timestamp/1000
        # base_price = 10000 + (day+2)*1000 where day in {-1, 0, 1, ...}
        # We infer base_price from current mid - timestamp/1000
        ts = state.timestamp
        if mid is not None:
            fair = mid  # Trust the mid — error is tiny (<12 units)
        else:
            # Fallback: reconstruct from formula
            # We don't know the day number directly, approximate from trades
            fair = 12000.0 + ts / 1000.0  # reasonable default for day 0

        return fair

    def market_make(
        self,
        product: str,
        fair_value: float,
        position: int,
        order_depth: OrderDepth,
        orders: List[Order],
    ):
        
        limit = POSITION_LIMITS[product]
        pos_ratio = position / limit  # -1 to +1

        # Skew: if long, lower our bid (want to sell), if short, raise our ask
        skew = pos_ratio * MM_SPREAD

        our_bid = round(fair_value - MM_SPREAD - skew)
        our_ask = round(fair_value + MM_SPREAD - skew)

        # Take mispriced orders first
        # Someone is selling below fair - take_edge (buy from them)
        if order_depth.sell_orders:
            for ask_price in sorted(order_depth.sell_orders.keys()):
                if ask_price <= fair_value - TAKE_EDGE:
                    available = -order_depth.sell_orders[ask_price]
                    can_buy = limit - position
                    qty = min(available, can_buy, ORDER_SIZE * 2)
                    if qty > 0:
                        orders.append(Order(product, ask_price, qty))
                        position += qty
                else:
                    break

        # Someone is buying above fair + take_edge (sell to them)
        if order_depth.buy_orders:
            for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                if bid_price >= fair_value + TAKE_EDGE:
                    available = order_depth.buy_orders[bid_price]
                    can_sell = limit + position
                    qty = min(available, can_sell, ORDER_SIZE * 2)
                    if qty > 0:
                        orders.append(Order(product, bid_price, -qty))
                        position -= qty
                else:
                    break

        # Post passive market-making quotes
        # Buy side
        can_buy = limit - position
        if can_buy > 0:
            qty = min(ORDER_SIZE, can_buy)
            orders.append(Order(product, our_bid, qty))

        # Sell side
        can_sell = limit + position
        if can_sell > 0:
            qty = min(ORDER_SIZE, can_sell)
            orders.append(Order(product, our_ask, -qty))

    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {}
        conversions = 0
        trader_data = ""

        # ASH_COATED_OSMIUM
        if OSMIUM in state.order_depths:
            od = state.order_depths[OSMIUM]
            position = state.position.get(OSMIUM, 0)
            fair = self.get_osmium_fair(state)

            orders: List[Order] = []
            self.market_make(OSMIUM, fair, position, od, orders)
            result[OSMIUM] = orders

        # INTARIAN_PEPPER_ROOT
        if PEPPER in state.order_depths:
            od = state.order_depths[PEPPER]
            position = state.position.get(PEPPER, 0)
            fair = self.get_pepper_fair(state)

            orders: List[Order] = []
            self.market_make(PEPPER, fair, position, od, orders)
            result[PEPPER] = orders

        return result, conversions, trader_data