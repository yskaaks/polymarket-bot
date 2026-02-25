# Polymarket Arbitrage Bot

A modular Python framework for Polymarket trading, built around a 6-layer architecture. Currently implements a UMA Oracle arbitrage strategy that monitors on-chain settlement events for trading opportunities.

## Quick Start

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edit with PRIVATE_KEY and FUNDER_ADDRESS
```

Set `DRY_RUN=1` (default) to simulate orders without placing them on-chain.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ Layer 0: Ingestion                                               │
│  polymarket_gamma.py   – Market discovery (Gamma REST API)       │
│  polymarket_clob.py    – CLOB client + auth (L0/L1/L2)          │
│  uma_client.py         – UMA Oracle Settle event listener        │
│  orderbook.py          – Spread, imbalance, slippage analysis    │
│  websocket_feed.py     – Real-time price/trade/book streams      │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│ Layer 1: Research                     (not yet implemented)      │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│ Layer 2: Signals                                                 │
│  uma_arb_signal.py     – Generate signals from UMA settlements   │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│ Layer 3: Portfolio & Risk                                        │
│  risk_manager.py       – Signal validation (confidence gate)     │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│ Layer 4: Execution                                               │
│  trading.py            – Limit/market orders, dry-run support    │
│  execution_agent.py    – Trade execution wrapper                 │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│ Layer 5: Monitoring                   (not yet implemented)      │
└──────────────────────────────────────────────────────────────────┘
```

Orchestrated by `src/strategies/uma_arb_strategy.py` which wires all layers together in a polling loop.

## Project Structure

```
├── config/
│   └── settings.py                        # Config dataclass, loads from .env
├── src/
│   ├── layer0_ingestion/
│   │   ├── polymarket_clob.py             # CLOB client wrapper + auth lifecycle
│   │   ├── polymarket_gamma.py            # Market metadata (Gamma REST API)
│   │   └── uma_client.py                  # UMA Optimistic Oracle V3 client
│   ├── layer1_research/                   # (empty — not yet implemented)
│   ├── layer2_signals/
│   │   └── uma_arb_signal.py              # UMA settlement → trade signal
│   ├── layer3_portfolio/
│   │   └── risk_manager.py                # Signal validation
│   ├── layer4_execution/
│   │   ├── trading.py                     # Order placement (limit/market/cancel)
│   │   └── execution_agent.py             # Trade execution wrapper
│   ├── layer5_monitoring/                 # (empty — not yet implemented)
│   ├── strategies/
│   │   └── uma_arb_strategy.py            # Main strategy orchestrator
│   ├── orderbook.py                       # Orderbook analysis + arb detection
│   ├── websocket_feed.py                  # Async WebSocket feed
│   └── utils.py                           # Logging, math, formatting helpers
├── scripts/
│   ├── debug_uma_signal.py                # Debug signal generation pipeline
│   ├── test_connection.py                 # API connectivity tests
│   ├── check_allowances.py               # EOA wallet allowance guide
│   ├── explore_market.py                  # Interactive market explorer
│   └── view_markets.py                    # Market listing utility
└── examples/
    ├── place_limit_order.py               # Single limit order demo
    └── market_making_demo.py              # Two-sided market making demo
```

## UMA Arbitrage Strategy

The core strategy monitors UMA Optimistic Oracle V3 on Polygon for `Settle` events, which signal that a market's outcome has been resolved on-chain. The flow:

1. **Poll UMA** — `UMAClient` fetches `Settle` events from recent blocks
2. **Parse settlement** — Extract `resolvedPrice` and `ancillaryData` (contains condition ID)
3. **Match to Polymarket** — Find the corresponding market via Gamma API
4. **Check edge** — Compare resolved price against current orderbook *(stubbed)*
5. **Risk check** — Validate signal confidence against threshold
6. **Execute** — Place order via CLOB API *(stubbed in dry-run)*

### Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| Market discovery (Gamma API) | Working | Pagination, retry, filtering |
| CLOB auth (L0/L1/L2) | Working | EOA, Magic, Proxy wallet support |
| UMA event fetching | Working | Settle events from OOV3 |
| Orderbook analysis | Working | Spread, slippage, YES/NO arb |
| Signal generation | Partial | `_check_profitability()` always returns True |
| Risk manager | Minimal | Confidence threshold only |
| Trade execution | Working | Limit/market orders, dry-run mode |
| Execution agent | Stub | Logs but doesn't call TradingClient |
| WebSocket feed | Partial | No reconnection logic |
| Layer 1 (Research) | Not started | |
| Layer 5 (Monitoring) | Not started | |

## Running

```bash
# Start the UMA arb bot (dry-run by default)
python -m src.strategies.uma_arb_strategy

# Debug signal generation
python scripts/debug_uma_signal.py

# Explore a market interactively
python scripts/explore_market.py

# Check EOA wallet allowances
python scripts/check_allowances.py
```

## Key Concepts

- **Token IDs**: Each outcome (YES/NO) has a distinct CLOB token ID, separate from the market's `condition_id`
- **Price range**: Limit orders must use prices between 0.01 and 0.99
- **Chain**: Polygon mainnet (chain ID 137), USDC settlement
- **Neg-risk markets**: Outcome prices must sum to 1.0, uses separate exchange contract

## Safety

Set `DRY_RUN=1` in `.env` (default) to test without placing real orders. Never commit `.env` with real credentials.
