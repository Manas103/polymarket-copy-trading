"""
Minimal test: place a tiny ($0.10) trade on Polymarket to verify connectivity.

Usage:
    python test_trade.py              # dry-run (shows what would happen)
    python test_trade.py --execute    # actually place the order
"""

from __future__ import annotations

import sys
import json

from dotenv import load_dotenv

load_dotenv()

from config import AppConfig
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, MarketOrderArgs, OrderType, PartialCreateOrderOptions


TRADE_AMOUNT_USD = 5.0  # $5 minimum on Polymarket


def main():
    execute = "--execute" in sys.argv
    config = AppConfig()

    if not config.clob.private_key:
        print("ERROR: PRIVATE_KEY not set in .env")
        sys.exit(1)

    # Connect to CLOB
    print("Connecting to CLOB API...")
    creds = ApiCreds(
        api_key=config.clob.api_key,
        api_secret=config.clob.api_secret,
        api_passphrase=config.clob.api_passphrase,
    )
    client = ClobClient(
        config.clob.api_url,
        key=config.clob.private_key,
        chain_id=config.clob.chain_id,
    )
    client.set_api_creds(creds)

    # Verify connectivity
    ok = client.get_ok()
    print(f"API status: {ok}")

    # Fetch active markets via sampling endpoint (returns popular/active markets)
    print("\nFetching active markets...")
    resp = client.get_sampling_markets()
    markets = resp.get("data", []) if isinstance(resp, dict) else resp

    # Find a market with ask price between 0.30 and 0.70 (good spread for test)
    selected = None
    token_id = None
    selected_outcome = None
    for market in markets:
        tokens = market.get("tokens", [])
        for tok in tokens:
            p = tok.get("price")
            if p is not None and 0.30 <= float(p) <= 0.70:
                min_size = float(market.get("minimum_order_size", 1))
                if min_size <= TRADE_AMOUNT_USD:
                    selected = market
                    token_id = tok["token_id"]
                    selected_outcome = tok.get("outcome", "?")
                    break
        if selected:
            break

    if not selected or not token_id:
        print("Could not find a suitable market. Try increasing TRADE_AMOUNT_USD.")
        sys.exit(1)

    question = selected.get("question", "Unknown")
    condition_id = selected.get("condition_id", "")
    neg_risk = selected.get("neg_risk", False)
    min_order = selected.get("minimum_order_size", "?")
    tick_size = selected.get("minimum_tick_size", "?")
    print(f"\nSelected market: {question}")
    print(f"  Outcome:        {selected_outcome}")
    print(f"  condition_id:   {condition_id}")
    print(f"  neg_risk:       {neg_risk}")
    print(f"  min_order_size: {min_order}")
    print(f"  tick_size:      {tick_size}")
    print(f"  token_id:       {token_id[:40]}...")

    # Get orderbook
    print("\nFetching orderbook...")
    try:
        ob = client.get_order_book(token_id)
        asks = ob.get("asks", []) if isinstance(ob, dict) else getattr(ob, "asks", [])
        bids = ob.get("bids", []) if isinstance(ob, dict) else getattr(ob, "bids", [])

        if asks:
            best_ask_entry = asks[0] if isinstance(asks[0], dict) else {"price": asks[0].price, "size": asks[0].size}
            best_ask = float(best_ask_entry.get("price", best_ask_entry.get("p", 0)))
            ask_size = float(best_ask_entry.get("size", best_ask_entry.get("s", 0)))
            print(f"  Best ask: {best_ask} (size: {ask_size})")
        else:
            print("  No asks available!")
            sys.exit(1)

        if bids:
            best_bid_entry = bids[-1] if isinstance(bids[-1], dict) else {"price": bids[-1].price, "size": bids[-1].size}
            best_bid = float(best_bid_entry.get("price", best_bid_entry.get("p", 0)))
            print(f"  Best bid: {best_bid}")
        else:
            best_bid = 0.0
            print("  No bids")

        if best_bid:
            print(f"  Spread:   {best_ask - best_bid:.4f}")
    except Exception as e:
        print(f"Could not fetch orderbook: {e}")
        sys.exit(1)

    # Calculate worst price (with 2% slippage)
    slippage = config.trading.max_slippage_pct / 100
    worst_price = min(best_ask * (1 + slippage), 0.99)

    # Estimate tokens received
    est_tokens = TRADE_AMOUNT_USD / best_ask

    print(f"\n--- Trade Details ---")
    print(f"  Side:         BUY")
    print(f"  Amount:       ${TRADE_AMOUNT_USD:.2f}")
    print(f"  Best ask:     {best_ask}")
    print(f"  Worst price:  {worst_price:.4f}")
    print(f"  Est. tokens:  {est_tokens:.2f}")
    print(f"  Order type:   FAK (Fill-And-Kill)")
    print(f"  Market:       {question[:70]}")
    print(f"  Outcome:      {selected_outcome}")

    if not execute:
        print(f"\n  DRY RUN - no order placed.")
        print(f"  Run with --execute to place the order.")
        return

    # Build and submit
    print(f"\nSubmitting order...")
    try:
        args = MarketOrderArgs(
            token_id=token_id,
            amount=TRADE_AMOUNT_USD,
            side="BUY",
            price=worst_price,
            order_type=OrderType.FAK,
        )
        is_neg_risk = selected.get("neg_risk", False)
        tick = str(selected.get("minimum_tick_size", "0.01"))
        options = PartialCreateOrderOptions(neg_risk=is_neg_risk, tick_size=tick)
        signed = client.create_market_order(args, options)
        result = client.post_order(signed, OrderType.FAK)

        print(f"\n--- Result ---")
        print(json.dumps(result, indent=2, default=str))

        status = result.get("status", "unknown") if isinstance(result, dict) else str(result)
        if status == "matched":
            print(f"\nSUCCESS: Order filled!")
            print(f"  Order ID:    {result.get('orderID', 'n/a')}")
            print(f"  Matched:     {result.get('matchedAmount', 'n/a')}")
            print(f"  Avg price:   {result.get('avgPrice', 'n/a')}")
        elif status == "delayed":
            print(f"\nOrder accepted (delayed matching)")
            print(f"  Order ID: {result.get('orderID', 'n/a')}")
        else:
            print(f"\nOrder status: {status}")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
