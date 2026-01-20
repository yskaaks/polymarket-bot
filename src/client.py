"""
Polymarket Client Wrapper

Main client that combines CLOB API access with authentication handling.
Uses the official py-clob-client under the hood.
"""

import sys
from typing import Optional
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

sys.path.insert(0, str(__file__).rsplit("src", 1)[0])
from config.settings import Config, get_config


class PolymarketClient:
    """
    Main wrapper for Polymarket API interactions.
    
    Handles authentication levels:
    - Level 0: Read-only (no auth)
    - Level 1: Private key signing (for orders)
    - Level 2: API key auth (for private endpoints)
    """
    
    def __init__(self, config: Optional[Config] = None):
        """
        Initialize the Polymarket client.
        
        Args:
            config: Configuration instance. Uses global config if not provided.
        """
        self.config = config or get_config()
        self._clob_client: Optional[ClobClient] = None
        self._api_creds: Optional[ApiCreds] = None
        self._is_authenticated = False
    
    @property
    def clob(self) -> ClobClient:
        """Get the underlying CLOB client, initializing if needed."""
        if self._clob_client is None:
            self._init_client()
        return self._clob_client
    
    def _init_client(self) -> None:
        """Initialize the CLOB client based on available credentials."""
        if self.config.has_credentials:
            # Full auth client
            self._clob_client = ClobClient(
                host=self.config.CLOB_API_URL,
                key=self.config.private_key,
                chain_id=self.config.CHAIN_ID,
                signature_type=self.config.signature_type,
                funder=self.config.funder_address
            )
        else:
            # Read-only client
            self._clob_client = ClobClient(
                host=self.config.CLOB_API_URL
            )
    
    def connect(self) -> bool:
        """
        Connect and authenticate with Polymarket.
        
        Returns:
            True if connection successful
        """
        try:
            # Test basic connectivity
            ok = self.clob.get_ok()
            if not ok:
                return False
            
            # If we have credentials, authenticate
            if self.config.has_credentials:
                self._api_creds = self.clob.create_or_derive_api_creds()
                self.clob.set_api_creds(self._api_creds)
                self._is_authenticated = True
            
            return True
        except Exception as e:
            print(f"Connection error: {e}")
            return False
    
    @property
    def is_authenticated(self) -> bool:
        """Check if client is authenticated for trading."""
        return self._is_authenticated
    
    def get_server_time(self) -> str:
        """Get current server time."""
        return self.clob.get_server_time()
    
    def get_balance(self) -> dict:
        """
        Get account balance information.
        
        Returns:
            Balance data including USDC
        """
        if not self.is_authenticated:
            raise RuntimeError("Must be authenticated to get balance")
        # Note: Balance is typically fetched from chain, not CLOB API
        # This is a placeholder - real implementation would query chain
        return {"note": "Query chain for actual USDC balance"}
    
    def get_positions(self) -> list:
        """
        Get current open positions.
        
        Returns:
            List of position objects
        """
        if not self.is_authenticated:
            raise RuntimeError("Must be authenticated to get positions")
        # The CLOB client doesn't have a direct positions endpoint
        # You'd typically track this from your trades
        return []
    
    def test_connection(self) -> dict:
        """
        Test API connectivity and return status info.
        
        Returns:
            Dict with connection status details
        """
        result = {
            "ok": False,
            "server_time": None,
            "authenticated": False,
            "error": None
        }
        
        try:
            result["ok"] = self.clob.get_ok()
            result["server_time"] = self.clob.get_server_time()
            result["authenticated"] = self.is_authenticated
        except Exception as e:
            result["error"] = str(e)
        
        return result


def create_client(config: Optional[Config] = None) -> PolymarketClient:
    """
    Factory function to create and connect a Polymarket client.
    
    Args:
        config: Optional configuration override
    
    Returns:
        Connected PolymarketClient instance
    """
    client = PolymarketClient(config)
    client.connect()
    return client


# Convenience function for quick read-only access
def get_readonly_client() -> PolymarketClient:
    """Get a read-only client (no auth required)."""
    empty_config = Config()
    empty_config.private_key = None
    empty_config.funder_address = None
    return PolymarketClient(empty_config)
