"""
Polymarket Bot Configuration

Loads settings from environment variables and provides centralized config.
ALL trading parameters MUST be set in .env — no silent defaults.
"""

import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def _require_env(name: str) -> str:
    """Get a required environment variable or raise with a clear message."""
    val = os.getenv(name)
    if val is None or val.strip() == "":
        raise RuntimeError(
            f"Missing required env var: {name}. Set it in your .env file."
        )
    return val


def _require_float(name: str) -> float:
    val = _require_env(name)
    try:
        return float(val)
    except ValueError:
        raise RuntimeError(f"Env var {name}={val!r} is not a valid number.")


def _require_int(name: str) -> int:
    val = _require_env(name)
    try:
        return int(val)
    except ValueError:
        raise RuntimeError(f"Env var {name}={val!r} is not a valid integer.")


@dataclass
class Config:
    """Configuration settings for Polymarket bot."""

    # API Endpoints (these are Polymarket's public endpoints, not user config)
    CLOB_API_URL: str = "https://clob.polymarket.com"
    GAMMA_API_URL: str = "https://gamma-api.polymarket.com"
    WEBSOCKET_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    # Chain settings
    CHAIN_ID: int = 137  # Polygon mainnet

    # Contract addresses (Polygon) — these are Polymarket's contracts, same for everyone
    USDC_ADDRESS: str = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    CTF_ADDRESS: str = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    EXCHANGE_ADDRESS: str = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    NEG_RISK_EXCHANGE: str = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
    NEG_RISK_ADAPTER: str = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

    def __post_init__(self):
        """Load all settings from environment. Crashes if required vars are missing."""
        # Credentials
        self.private_key = os.getenv("PRIVATE_KEY")
        self.funder_address = os.getenv("FUNDER_ADDRESS")
        self.signature_type = int(os.getenv("SIGNATURE_TYPE") or "0")
        self.dry_run = _require_env("DRY_RUN") == "1"

        # Network
        self.polygon_rpc_url = _require_env("POLYGON_RPC_URL")

        # WebSocket RPC URL (derived from HTTP URL if not set)
        default_ws_url = self.polygon_rpc_url.replace("https://", "wss://").replace("http://", "ws://")
        self.polygon_ws_url = os.getenv("POLYGON_WS_URL") or default_ws_url

        # Trading parameters — ALL required, no silent defaults
        self.max_order_size = _require_float("MAX_ORDER_SIZE")
        self.min_edge = _require_float("MIN_EDGE")

        # Telegram notifications — optional (empty string = disabled)
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        # Market-making settings — ALL required, no silent defaults
        self.mm_max_markets = _require_int("MM_MAX_MARKETS")
        self.mm_max_position_per_market = _require_float("MM_MAX_POSITION_PER_MARKET")
        self.mm_max_total_exposure = _require_float("MM_MAX_TOTAL_EXPOSURE")
        self.mm_min_spread = _require_float("MM_MIN_SPREAD")
        self.mm_max_spread = _require_float("MM_MAX_SPREAD")
        self.mm_min_liquidity = _require_float("MM_MIN_LIQUIDITY")
        self.mm_min_volume_24h = _require_float("MM_MIN_VOLUME_24H")
        self.mm_quote_refresh = _require_float("MM_QUOTE_REFRESH")
        self.mm_capital = _require_float("MM_CAPITAL")
        self.mm_stop_loss_per_market = _require_float("MM_STOP_LOSS_PER_MARKET")
    
    @property
    def has_credentials(self) -> bool:
        """Check if trading credentials are configured."""
        return bool(self.private_key and self.funder_address)
    
    def validate(self) -> list[str]:
        """Validate configuration and return list of issues."""
        issues = []
        
        if not self.private_key:
            issues.append("PRIVATE_KEY not set in environment")
        elif not self.private_key.startswith("0x") and len(self.private_key) != 64:
            if not (len(self.private_key) == 66 and self.private_key.startswith("0x")):
                issues.append("PRIVATE_KEY appears to be in wrong format")
        
        if not self.funder_address:
            issues.append("FUNDER_ADDRESS not set in environment")
        elif not self.funder_address.startswith("0x") or len(self.funder_address) != 42:
            issues.append("FUNDER_ADDRESS appears to be invalid (should be 0x + 40 hex chars)")
        
        if self.signature_type not in [0, 1, 2]:
            issues.append(f"SIGNATURE_TYPE must be 0, 1, or 2 (got {self.signature_type})")
        
        return issues


# Global config instance
config = Config()


def get_config() -> Config:
    """Get the global configuration instance."""
    return config


def reload_config() -> Config:
    """Reload configuration from environment."""
    global config
    load_dotenv(override=True)
    config = Config()
    return config
