# Polymarket Arbitrage Bot

A modular Python framework for interacting with Polymarket's APIs, designed for building market making and arbitrage strategies.

## Quick Start

### 1. Install dependencies
```bash
cd polymarket-bot
pip install -r requirements.txt
```

### 2. Configure credentials
```bash
cp .env.example .env
# Edit .env with your private key and wallet address
```

### 3. Test connection
```bash
python scripts/test_connection.py
```

## Project Structure

```
├── config/settings.py      # Configuration management
├── src/
│   ├── client.py           # Main Polymarket client wrapper
│   ├── markets.py          # Market discovery (Gamma API)
│   ├── trading.py          # Order placement (CLOB API)
│   ├── orderbook.py        # Orderbook analysis
│   └── websocket_feed.py   # Real-time data streams
├── scripts/
│   ├── test_connection.py  # Verify API connectivity
│   └── view_markets.py     # Browse active markets
└── examples/
    └── market_making_demo.py
```

## API Overview

### Read-Only (No Auth Required)
- Get market prices
- View orderbooks
- Fetch market metadata

### Trading (Auth Required)
- Place limit/market orders
- Cancel orders
- View positions

## Token Allowances (EOA Wallets)

If using MetaMask or hardware wallet, you must set allowances before trading:
- USDC: `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
- Conditional Tokens: `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`

See `scripts/check_allowances.py` for verification.

## Safety

⚠️ **Never commit your `.env` file with real credentials!**

Set `DRY_RUN=1` in `.env` to test without placing real orders.
