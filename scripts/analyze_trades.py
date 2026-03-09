#!/usr/bin/env python3
"""
Analyze all trades and open orders for the bot's wallet.
Extracts only OUR matched amounts from multi-participant trades.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import get_config
from src.layer0_ingestion.polymarket_clob import create_client
import httpx


@dataclass
class MyFill:
    """Our portion of a trade."""
    timestamp: datetime
    market: str
    asset_id: str
    side: str
    outcome: str
    size: float
    price: float
    cost: float
    role: str  # MAKER or TAKER


def ts_to_dt(ts_str: str) -> datetime:
    return datetime.fromtimestamp(int(ts_str), tz=timezone.utc)


def fetch_market_questions(market_ids: set[str]) -> dict[str, str]:
    """Fetch market questions from CLOB API."""
    questions = {}
    for mid in market_ids:
        try:
            r = httpx.get(f"https://clob.polymarket.com/markets/{mid}", timeout=5)
            if r.status_code == 200:
                questions[mid] = r.json().get("question", mid[:16] + "...")
            else:
                questions[mid] = mid[:16] + "..."
        except Exception:
            questions[mid] = mid[:16] + "..."
    return questions


def extract_my_fills(trades: list[dict], my_address: str) -> list[MyFill]:
    """
    Extract our actual matched amounts from trades.

    The CLOB API returns trades where we participated, but the top-level
    `size` is the TOTAL trade size across all participants. Our actual
    fill is either:
    - In `maker_orders` if we were a maker (matched_amount for our address)
    - The top-level size if we were the sole taker and maker_address is us
    """
    my_addr = my_address.lower()
    fills = []

    for t in trades:
        dt = ts_to_dt(t["match_time"])
        market = t["market"]
        asset_id = t["asset_id"]
        side = t["side"]
        price = float(t["price"])
        outcome = t.get("outcome", "?")
        trader_side = t.get("trader_side", "?")
        top_maker = (t.get("maker_address") or "").lower()

        # Check if we're in maker_orders
        my_matched = 0.0
        for mo in t.get("maker_orders", []):
            if (mo.get("maker_address") or "").lower() == my_addr:
                my_matched += float(mo.get("matched_amount", 0))

        if my_matched > 0:
            # We were a maker in this trade
            fills.append(MyFill(
                timestamp=dt, market=market, asset_id=asset_id,
                side=side, outcome=outcome, size=my_matched,
                price=price, cost=my_matched * price, role="MAKER",
            ))
        elif top_maker == my_addr:
            # We're the top-level maker (taker took from us directly)
            total_size = float(t["size"])
            fills.append(MyFill(
                timestamp=dt, market=market, asset_id=asset_id,
                side=side, outcome=outcome, size=total_size,
                price=price, cost=total_size * price, role="MAKER",
            ))
        elif trader_side == "TAKER":
            # We were the taker
            total_size = float(t["size"])
            fills.append(MyFill(
                timestamp=dt, market=market, asset_id=asset_id,
                side=side, outcome=outcome, size=total_size,
                price=price, cost=total_size * price, role="TAKER",
            ))

    fills.sort(key=lambda f: f.timestamp)
    return fills


def main():
    config = get_config()
    client = create_client()
    my_address = config.funder_address

    # ── Fetch all trades ──
    trades = client.clob.get_trades()
    trades = trades or []

    # ── Extract only our fills ──
    fills = extract_my_fills(trades, my_address)

    # ── Fetch open orders ──
    from py_clob_client.clob_types import OpenOrderParams
    open_orders = client.clob.get_orders(OpenOrderParams())
    open_orders = open_orders or []

    # ── Collect unique markets ──
    market_ids = set()
    for f in fills:
        market_ids.add(f.market)
    for o in open_orders:
        market_ids.add(o["market"])

    print("Fetching market details...")
    questions = fetch_market_questions(market_ids)

    # ── Print trade history ──
    print(f"\n{'='*80}")
    print(f"TRADE HISTORY ({len(fills)} fills from {len(trades)} market trades)")
    print(f"{'='*80}")

    total_spent = 0.0
    total_received = 0.0

    for f in fills:
        question = questions.get(f.market, f.market[:16] + "...")
        if f.side == "BUY":
            total_spent += f.cost
        else:
            total_received += f.cost

        print(
            f"  {f.timestamp.strftime('%m/%d %H:%M')} | {f.side:4s} | "
            f"{f.size:>8.2f} @ ${f.price:.4f} = ${f.cost:>7.2f} | "
            f"{f.outcome:3s} | {f.role:5s} | {question[:42]}"
        )

    # ── Positions (net per market+outcome) ──
    print(f"\n{'='*80}")
    print("CURRENT POSITIONS")
    print(f"{'='*80}")

    positions = defaultdict(lambda: {"qty": 0.0, "cost": 0.0, "outcome": ""})
    for f in fills:
        key = (f.market, f.asset_id)
        if f.side == "BUY":
            positions[key]["qty"] += f.size
            positions[key]["cost"] += f.cost
        else:
            positions[key]["qty"] -= f.size
            positions[key]["cost"] -= f.cost
        positions[key]["outcome"] = f.outcome

    has_positions = False
    for (market_id, asset_id), pos in sorted(positions.items(), key=lambda x: -abs(x[1]["cost"])):
        if abs(pos["qty"]) < 0.01:
            continue
        has_positions = True
        question = questions.get(market_id, market_id[:16] + "...")
        avg_price = pos["cost"] / pos["qty"] if pos["qty"] != 0 else 0
        direction = "LONG" if pos["qty"] > 0 else "SHORT"
        print(
            f"  {direction:5s} {abs(pos['qty']):>8.2f} {pos['outcome']:3s} "
            f"@ avg ${abs(avg_price):.4f} "
            f"(cost: ${abs(pos['cost']):.2f}) | {question[:42]}"
        )

    if not has_positions:
        print("  No open positions")

    # ── Open orders ──
    print(f"\n{'='*80}")
    print(f"OPEN ORDERS ({len(open_orders)})")
    print(f"{'='*80}")

    for o in open_orders:
        question = questions.get(o["market"], o["market"][:16] + "...")
        size = float(o.get("original_size", 0))
        matched = float(o.get("size_matched", 0))
        price = float(o.get("price", 0))
        side = o.get("side", "?")
        outcome = o.get("outcome", "?")
        notional = size * price

        print(
            f"  {side:4s} {size:>8.1f} {outcome:3s} @ ${price:.4f} "
            f"(${notional:>6.2f}) | matched: {matched:.1f} | {question[:40]}"
        )

    if not open_orders:
        print("  No open orders")

    # ── Summary ──
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"  Total fills:      {len(fills)}")
    print(f"  Total spent:      ${total_spent:>8.2f}")
    print(f"  Total received:   ${total_received:>8.2f}")
    print(f"  Net P&L:          ${total_received - total_spent:>8.2f}")
    print(f"  Open orders:      {len(open_orders)}")

    if fills:
        buy_fills = [f for f in fills if f.side == "BUY"]
        sell_fills = [f for f in fills if f.side == "SELL"]
        taker_fills = [f for f in fills if f.role == "TAKER"]
        maker_fills = [f for f in fills if f.role == "MAKER"]
        print(f"\n  Buys:  {len(buy_fills):>4d}   |  Sells: {len(sell_fills):>4d}")
        print(f"  Taker: {len(taker_fills):>4d}   |  Maker: {len(maker_fills):>4d}")
        print(f"\n  First fill: {fills[0].timestamp.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"  Last fill:  {fills[-1].timestamp.strftime('%Y-%m-%d %H:%M UTC')}")

    # Markets traded
    market_fills = defaultdict(list)
    for f in fills:
        market_fills[f.market].append(f)

    print(f"\n  Unique markets: {len(market_fills)}")
    for mid, mfills in sorted(market_fills.items(), key=lambda x: -sum(f.cost for f in x[1])):
        q = questions.get(mid, mid[:16] + "...")
        buys = sum(1 for f in mfills if f.side == "BUY")
        sells = sum(1 for f in mfills if f.side == "SELL")
        vol = sum(f.cost for f in mfills)
        print(f"    {len(mfills):>3d} fills (B:{buys}/S:{sells}) ${vol:>7.2f} vol | {q[:48]}")


if __name__ == "__main__":
    main()
