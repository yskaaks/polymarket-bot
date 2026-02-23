import json
import logging
from typing import Optional, List, Dict, Any
from web3 import Web3
from web3.contract import Contract

logger = logging.getLogger(__name__)

# Typical OptimisticOracleV3 address on Polygon (used by Polymarket)
# Note: May need verification based on current Polymarket contract usage
DEFAULT_OOV3_ADDRESS = "0x5953c82c114cbab00fa446A3bbDB2D4B663f73B3"

# Minimal required ABI for listening to resolved price requests
OOV3_ABI = json.loads('''[
  {
    "anonymous": false,
    "inputs": [
      {
        "indexed": true,
        "internalType": "contract IERC20",
        "name": "currency",
        "type": "address"
      },
      {
        "indexed": true,
        "internalType": "bytes32",
        "name": "identifier",
        "type": "bytes32"
      },
      {
        "indexed": true,
        "internalType": "uint32",
        "name": "expirationTimestamp",
        "type": "uint32"
      },
      {
        "indexed": false,
        "internalType": "bytes",
        "name": "ancillaryData",
        "type": "bytes"
      },
      {
        "indexed": false,
        "internalType": "address",
        "name": "requester",
        "type": "address"
      },
      {
        "indexed": false,
        "internalType": "address",
        "name": "proposer",
        "type": "address"
      },
      {
        "indexed": false,
        "internalType": "address",
        "name": "disputer",
        "type": "address"
      },
      {
        "indexed": false,
        "internalType": "int256",
        "name": "resolvedPrice",
        "type": "int256"
      },
      {
        "indexed": false,
        "internalType": "int256",
        "name": "settledPrice",
        "type": "int256"
      }
    ],
    "name": "Settle",
    "type": "event"
  }
]''')

class UMAClient:
    """
    Client for interacting with UMA Optimistic Oracle contracts.
    Monitors for 'Settle' events which indicate a market has been resolved.
    """
    def __init__(self, rpc_url: str, oov3_address: str = DEFAULT_OOV3_ADDRESS):
        """
        Initialize the UMA client.
        
        Args:
            rpc_url: Web3 RPC URL (e.g., Alchemy/Infura Polygon endpoint)
            oov3_address: Address of the Optimistic Oracle V3 contract
        """
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            logger.warning(f"Could not connect to Web3 provider at {rpc_url}")
            
        self.oov3_address = Web3.to_checksum_address(oov3_address)
        self.contract: Contract = self.w3.eth.contract(address=self.oov3_address, abi=OOV3_ABI)

    def get_recent_settlements(self, from_block: int, to_block: str = 'latest') -> List[Dict[str, Any]]:
        """
        Fetch recent 'Settle' events from the Optimistic Oracle.
        These represent resolved oracle requests.
        
        Args:
            from_block: Starting block number
            to_block: Ending block number or 'latest'
            
        Returns:
            List of parsed event dictionaries
        """
        try:
            event_filter = self.contract.events.Settle.create_filter(
                fromBlock=from_block,
                toBlock=to_block
            )
            entries = event_filter.get_all_entries()
            
            settlements = []
            for entry in entries:
                args = entry['args']
                settlements.append({
                    'transactionHash': entry['transactionHash'].hex(),
                    'blockNumber': entry['blockNumber'],
                    'identifier': args['identifier'].hex(),
                    'expirationTimestamp': args['expirationTimestamp'],
                    'ancillaryData': args['ancillaryData'].hex(),
                    'resolvedPrice': args['resolvedPrice'],
                    'settledPrice': args['settledPrice']
                })
                
            return settlements
        except Exception as e:
            logger.error(f"Error fetching settlements: {e}")
            return []
            
    def parse_ancillary_data(self, ancillary_data_hex: str) -> str:
        """
        Attempt to decode ancillary data from hex to string.
        Ancillary data often contains the specific question or condition ID for Polymarket.
        """
        try:
            # Remove '0x' prefix if present
            if ancillary_data_hex.startswith('0x'):
                ancillary_data_hex = ancillary_data_hex[2:]
            
            # Convert hex to bytes, then decode string (ignoring non-ascii)
            data_bytes = bytes.fromhex(ancillary_data_hex)
            return data_bytes.decode('utf-8', errors='ignore')
        except Exception as e:
            logger.error(f"Failed to parse ancillary data: {e}")
            return ""
