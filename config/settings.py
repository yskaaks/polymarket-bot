"""
Polymarket Bot Configuration

Loads settings from environment variables and provides centralized config.
"""

import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


@dataclass
class Config:
    """Configuration settings for Polymarket bot."""
    
    # API Endpoints
    CLOB_API_URL: str = "https://clob.polymarket.com"
    GAMMA_API_URL: str = "https://gamma-api.polymarket.com"
    WEBSOCKET_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    # Chain settings
    CHAIN_ID: int = 137  # Polygon mainnet
    
    # Credentials (loaded from environment)
    private_key: Optional[str] = None
    funder_address: Optional[str] = None
    signature_type: int = 0  # 0=EOA, 1=Magic, 2=Proxy
    
    # Trading settings
    dry_run: bool = True  # Safety: default to dry run
    
    # Contract addresses (Polygon)
    USDC_ADDRESS: str = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    CTF_ADDRESS: str = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    EXCHANGE_ADDRESS: str = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    NEG_RISK_EXCHANGE: str = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
    NEG_RISK_ADAPTER: str = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
    
    def __post_init__(self):
        """Load credentials from environment after initialization."""
        self.private_key = os.getenv("PRIVATE_KEY")
        self.funder_address = os.getenv("FUNDER_ADDRESS")
        self.signature_type = int(os.getenv("SIGNATURE_TYPE", "0"))
        self.dry_run = os.getenv("DRY_RUN", "1") == "1"
    
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
