import asyncio
import json
import logging
from typing import Optional, List, Dict, Any, Callable
from web3 import Web3
from web3.contract import Contract
import websockets

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

    def get_recent_settlements(self, from_block: int, to_block: int | str = 'latest') -> List[Dict[str, Any]]:
        """
        Fetch recent 'Settle' events from the Optimistic Oracle.
        These represent resolved oracle requests.
        
        Args:
            from_block: Starting block number
            to_block: Ending block number or 'latest'
            
        Returns:
            List of parsed event dictionaries
        """
        if to_block == 'latest':
            to_block = self.w3.eth.block_number

        settlements = []
        chunk_size = 2000  # Paid Alchemy supports up to 100k blocks per query
        start = from_block

        while start <= to_block:
            end = min(start + chunk_size - 1, to_block)
            try:
                entries = self.contract.events.Settle.get_logs(
                    from_block=start,
                    to_block=end
                )
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
            except Exception as e:
                logger.error(f"Error fetching settlements for blocks {start}..{end}: {e}")
            start = end + 1

        return settlements
            
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


# Settle event topic0: keccak256 of the Settle event signature
SETTLE_EVENT_TOPIC = Web3.keccak(
    text="Settle(address,bytes32,uint32,bytes,address,address,address,int256,int256)"
).hex()


class UMAWebSocketClient:
    """
    Real-time WebSocket listener for UMA Settle events.
    Uses Alchemy WSS endpoint with eth_subscribe for sub-second detection.
    """

    def __init__(self, ws_url: str, oov3_address: str = DEFAULT_OOV3_ADDRESS):
        self.ws_url = ws_url
        self.oov3_address = Web3.to_checksum_address(oov3_address)
        self._callbacks: list[Callable[[Dict[str, Any]], None]] = []
        self._running = False
        self._ws = None
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

    def on_settle(self, callback: Callable[[Dict[str, Any]], None]):
        """Register a callback for Settle events."""
        self._callbacks.append(callback)

    async def _subscribe(self, ws):
        """Send eth_subscribe for Settle logs."""
        subscribe_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_subscribe",
            "params": [
                "logs",
                {
                    "address": self.oov3_address,
                    "topics": [SETTLE_EVENT_TOPIC]
                }
            ]
        }
        await ws.send(json.dumps(subscribe_msg))
        response = await ws.recv()
        data = json.loads(response)
        if "result" in data:
            logger.info(f"Subscribed to Settle events, subscription ID: {data['result']}")
        else:
            logger.error(f"Subscription failed: {data}")
            raise RuntimeError(f"eth_subscribe failed: {data}")

    def _parse_log(self, log: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a raw log entry into a settlement dict."""
        contract = Web3().eth.contract(
            address=self.oov3_address, abi=OOV3_ABI
        )
        # Decode the non-indexed parameters from log data
        topics = log.get("topics", [])
        data_hex = log.get("data", "0x")

        # Indexed params: currency (topic1), identifier (topic2), expirationTimestamp (topic3)
        currency = topics[1] if len(topics) > 1 else "0x"
        identifier = topics[2] if len(topics) > 2 else "0x"
        expiration_raw = topics[3] if len(topics) > 3 else "0x0"
        expiration_ts = int(expiration_raw, 16)

        # Non-indexed params decoded from data
        # data layout: ancillaryData (dynamic), requester, proposer, disputer, resolvedPrice, settledPrice
        try:
            decoded = contract.events.Settle().process_log({
                "address": self.oov3_address,
                "topics": [bytes.fromhex(t[2:]) for t in topics],
                "data": bytes.fromhex(data_hex[2:]) if data_hex.startswith("0x") else bytes.fromhex(data_hex),
                "blockNumber": int(log.get("blockNumber", "0x0"), 16),
                "transactionHash": bytes.fromhex(log.get("transactionHash", "0x" + "0" * 64)[2:]),
                "transactionIndex": int(log.get("transactionIndex", "0x0"), 16),
                "blockHash": bytes.fromhex(log.get("blockHash", "0x" + "0" * 64)[2:]),
                "logIndex": int(log.get("logIndex", "0x0"), 16),
                "removed": log.get("removed", False),
            })
            args = decoded["args"]
            return {
                "transactionHash": log.get("transactionHash", ""),
                "blockNumber": int(log.get("blockNumber", "0x0"), 16),
                "identifier": args["identifier"].hex(),
                "expirationTimestamp": args["expirationTimestamp"],
                "ancillaryData": args["ancillaryData"].hex(),
                "resolvedPrice": args["resolvedPrice"],
                "settledPrice": args["settledPrice"],
            }
        except Exception as e:
            logger.error(f"Failed to decode Settle log: {e}")
            # Fallback: return raw data
            return {
                "transactionHash": log.get("transactionHash", ""),
                "blockNumber": int(log.get("blockNumber", "0x0"), 16),
                "identifier": identifier,
                "expirationTimestamp": expiration_ts,
                "ancillaryData": data_hex,
                "resolvedPrice": None,
                "settledPrice": None,
            }

    async def listen(self):
        """Connect and listen for Settle events with auto-reconnect."""
        self._running = True
        delay = self._reconnect_delay

        while self._running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    delay = self._reconnect_delay  # reset on successful connect
                    logger.info(f"UMA WebSocket connected to {self.ws_url}")
                    await self._subscribe(ws)

                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(message)
                            # eth_subscribe notifications have method "eth_subscription"
                            if data.get("method") == "eth_subscription":
                                log_entry = data["params"]["result"]
                                settlement = self._parse_log(log_entry)
                                logger.info(f"Real-time Settle event detected! Block: {settlement['blockNumber']}")
                                for cb in self._callbacks:
                                    try:
                                        cb(settlement)
                                    except Exception as e:
                                        logger.error(f"Settle callback error: {e}")
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON from WSS: {message[:100]}")

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"UMA WebSocket closed: {e}")
            except Exception as e:
                logger.error(f"UMA WebSocket error: {e}")

            if self._running:
                logger.info(f"Reconnecting in {delay:.1f}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_reconnect_delay)

    async def stop(self):
        """Stop the WebSocket listener."""
        self._running = False
        if self._ws:
            await self._ws.close()
