import os
import json
import time
import logging
import asyncio
from typing import Dict, Any, Optional, List

import requests
from web3 import Web3
from web3.exceptions import BlockNotFound
from web3.contract import Contract
from dotenv import load_dotenv

# --- Configuration Loading ---
# In a real-world application, use environment variables or a secure config manager.
load_dotenv()

# --- Basic Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class Config:
    """Houses all configuration parameters for the bridge listener."""
    # Source Chain (e.g., Ethereum)
    SOURCE_CHAIN_RPC_URL = os.getenv('SOURCE_CHAIN_RPC_URL', 'https://rpc.ankr.com/eth_sepolia')
    SOURCE_CHAIN_BRIDGE_CONTRACT_ADDRESS = os.getenv('SOURCE_CHAIN_BRIDGE_CONTRACT_ADDRESS', '0xSourceBridgeContractAddress') # Replace with actual address

    # Destination Chain (e.g., Polygon)
    DEST_CHAIN_RPC_URL = os.getenv('DEST_CHAIN_RPC_URL', 'https://rpc.ankr.com/polygon_mumbai')
    DEST_CHAIN_BRIDGE_CONTRACT_ADDRESS = os.getenv('DEST_CHAIN_BRIDGE_CONTRACT_ADDRESS', '0xDestinationBridgeContractAddress') # Replace with actual address
    # IMPORTANT: In a real scenario, this key should be managed by a secure vault.
    DEST_CHAIN_SIGNER_PRIVATE_KEY = os.getenv('DEST_CHAIN_SIGNER_PRIVATE_KEY', '0x' + 'a' * 64) # Placeholder private key

    # Listener Configuration
    SCAN_INTERVAL_SECONDS = 15.0 # How often to scan for new blocks
    BLOCK_PROCESSING_CHUNK_SIZE = 100 # Number of blocks to scan in one go
    CONFIRMATION_BLOCKS = 6 # Number of blocks to wait for event finality

    # State Management
    STATE_FILE_PATH = 'bridge_listener_state.json'

    # Oracle Configuration
    GAS_PRICE_API_URL = 'https://api.etherscan.io/api?module=gastracker&action=gasoracle&apikey=YourApiKeyToken'

# --- Contract ABIs (Simplified for simulation) ---
# In a real project, these would be loaded from JSON files.
SOURCE_BRIDGE_ABI = json.loads('''
[
    {
        "anonymous": false,
        "inputs": [
            {"indexed": true, "name": "from", "type": "address"},
            {"indexed": true, "name": "to", "type": "address"},
            {"indexed": false, "name": "amount", "type": "uint256"},
            {"indexed": false, "name": "nonce", "type": "uint256"},
            {"indexed": false, "name": "destinationChainId", "type": "uint256"}
        ],
        "name": "TokensLocked",
        "type": "event"
    }
]
''')

DEST_BRIDGE_ABI = json.loads('''
[
    {
        "constant": false,
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "sourceNonce", "type": "uint256"}
        ],
        "name": "mintTokens",
        "outputs": [],
        "payable": false,
        "stateMutability": "nonpayable",
        "type": "function"
    }
]
''')


class BlockchainConnector:
    """Manages the connection to a blockchain node via Web3."""

    def __init__(self, rpc_url: str):
        """
        Initializes the connector.
        Args:
            rpc_url (str): The HTTP/WSS RPC endpoint for the blockchain node.
        """
        self.rpc_url = rpc_url
        self.web3: Optional[Web3] = None
        self.connect()

    def connect(self) -> None:
        """Establishes a connection to the RPC endpoint."""
        logger.info(f"Connecting to blockchain node at {self.rpc_url}...")
        try:
            self.web3 = Web3(Web3.HTTPProvider(self.rpc_url))
            if not self.web3.is_connected():
                raise ConnectionError("Failed to connect to the node.")
            logger.info(f"Successfully connected. Chain ID: {self.web3.eth.chain_id}")
        except Exception as e:
            logger.error(f"Error connecting to {self.rpc_url}: {e}")
            self.web3 = None

    def get_contract(self, address: str, abi: List[Dict[str, Any]]) -> Optional[Contract]:
        """Gets a contract instance if the connection is active."""
        if not self.web3 or not self.web3.is_connected():
            logger.warning("Web3 not connected. Attempting to reconnect...")
            self.connect()
            if not self.web3 or not self.web3.is_connected():
                return None
        
        try:
            checksum_address = self.web3.to_checksum_address(address)
            return self.web3.eth.contract(address=checksum_address, abi=abi)
        except ValueError as e:
            logger.error(f"Invalid address or ABI provided: {e}")
            return None


class EventScanner:
    """Scans a source blockchain for specific contract events."""

    def __init__(self, connector: BlockchainConnector, contract_address: str, contract_abi: List[Dict[str, Any]], state_file: str):
        self.connector = connector
        self.contract_address = contract_address
        self.contract_abi = contract_abi
        self.state_file = state_file
        self.last_scanned_block = self._load_state()

    def _load_state(self) -> int:
        """Loads the last scanned block number from a state file."""
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                last_block = int(state.get('last_scanned_block', 0))
                logger.info(f"Loaded last scanned block from state: {last_block}")
                return last_block
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            logger.warning("State file not found or invalid. Starting scan from scratch.")
            return 0 # Or fetch contract creation block for optimization

    def _save_state(self, block_number: int) -> None:
        """Saves the last scanned block number to the state file."""
        try:
            with open(self.state_file, 'w') as f:
                json.dump({'last_scanned_block': block_number}, f)
                # logger.info(f"Saved state. Last scanned block: {block_number}")
        except IOError as e:
            logger.error(f"Failed to save state to {self.state_file}: {e}")

    async def scan_for_events(self) -> List[Dict[str, Any]]:
        """Scans for 'TokensLocked' events from the last scanned block to the latest."""
        w3 = self.connector.web3
        if not w3 or not w3.is_connected():
            logger.error("Cannot scan events, Web3 is not connected.")
            return []

        contract = self.connector.get_contract(self.contract_address, self.contract_abi)
        if not contract:
            logger.error(f"Could not instantiate contract at {self.contract_address}")
            return []

        try:
            latest_block = w3.eth.block_number
        except Exception as e:
            logger.error(f"Could not fetch the latest block number: {e}")
            return []

        if self.last_scanned_block == 0:
            # On first run, start from a recent block to avoid scanning the whole chain
            self.last_scanned_block = latest_block - 1000
            logger.info(f"First run. Starting scan from block {self.last_scanned_block}")

        # Ensure we don't scan unconfirmed blocks
        target_block = latest_block - Config.CONFIRMATION_BLOCKS
        if self.last_scanned_block >= target_block:
            # logger.info("No new confirmed blocks to scan.")
            return []

        all_events = []
        logger.info(f"Scanning for 'TokensLocked' events from block {self.last_scanned_block + 1} to {target_block}")

        for from_block in range(self.last_scanned_block + 1, target_block + 1, Config.BLOCK_PROCESSING_CHUNK_SIZE):
            to_block = min(from_block + Config.BLOCK_PROCESSING_CHUNK_SIZE - 1, target_block)
            
            try:
                event_filter = contract.events.TokensLocked.create_filter(
                    fromBlock=from_block,
                    toBlock=to_block
                )
                events = event_filter.get_all_entries()
                if events:
                    logger.info(f"Found {len(events)} events between blocks {from_block}-{to_block}")
                    for event in events:
                        all_events.append(event['args'])
            except Exception as e:
                logger.error(f"Error scanning blocks {from_block}-{to_block}: {e}")
                # If a chunk fails, we stop and retry in the next cycle
                # We don't update last_scanned_block to ensure we re-scan this chunk
                return [] # Return what we have so far, or empty to force a retry
        
        # If scan was successful, update and save state
        self.last_scanned_block = target_block
        self._save_state(self.last_scanned_block)
        return all_events


class TransactionProcessor:
    """Processes events and prepares transactions for the destination chain."""

    def __init__(self, connector: BlockchainConnector, contract_address: str, contract_abi: List[Dict[str, Any]], private_key: str):
        self.connector = connector
        self.contract_address = contract_address
        self.contract_abi = contract_abi
        self.private_key = private_key
        self.signer_address = self.connector.web3.eth.account.from_key(private_key).address
        logger.info(f"Transaction processor initialized for signer address: {self.signer_address}")

    async def process_and_submit(self, event_data: Dict[str, Any]) -> Optional[str]:
        """
        Validates an event and submits a corresponding transaction to the destination chain.
        
        Args:
            event_data (Dict[str, Any]): The arguments of the 'TokensLocked' event.
        
        Returns:
            Optional[str]: The transaction hash if submitted, otherwise None.
        """
        logger.info(f"Processing event with nonce {event_data.get('nonce')}")

        # 1. Validation (add more complex rules as needed)
        if not all(k in event_data for k in ['from', 'to', 'amount', 'nonce']):
            logger.error(f"Malformed event data received: {event_data}")
            return None

        # 2. Prepare transaction
        w3 = self.connector.web3
        contract = self.connector.get_contract(self.contract_address, self.contract_abi)
        if not w3 or not contract:
            logger.error("Cannot prepare transaction, destination chain is not connected.")
            return None

        try:
            # Build the function call payload
            tx_payload = contract.functions.mintTokens(
                event_data['from'],
                event_data['to'],
                event_data['amount'],
                event_data['nonce']
            )

            # Estimate gas and construct the transaction object
            tx = tx_payload.build_transaction({
                'from': self.signer_address,
                'nonce': w3.eth.get_transaction_count(self.signer_address),
                'gas': 200000, # In production, use tx_payload.estimateGas()
                'gasPrice': w3.to_wei('50', 'gwei') # In production, use a gas oracle
            })

            # 3. Sign the transaction
            signed_tx = w3.eth.account.sign_transaction(tx, self.private_key)

            # 4. Submit transaction (SIMULATION)
            # In a real system, you would uncomment the following line:
            # tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            # logger.info(f"Submitted transaction to mint tokens. Hash: {w3.to_hex(tx_hash)}")
            # return w3.to_hex(tx_hash)

            # --- SIMULATION LOGIC ---
            simulated_tx_hash = w3.keccak(signed_tx.rawTransaction).hex()
            logger.info(f"[SIMULATION] Would submit transaction to mint {event_data['amount']} tokens for {event_data['to']}.")
            logger.info(f"[SIMULATION] Transaction hash: {simulated_tx_hash}")
            return simulated_tx_hash

        except Exception as e:
            logger.error(f"Failed to process transaction for nonce {event_data.get('nonce')}: {e}")
            return None


class CrossChainBridgeListener:
    """Orchestrates the entire cross-chain listening and processing workflow."""

    def __init__(self, config: Config):
        self.config = config
        self.is_running = False

        # Initialize components
        logger.info("Initializing bridge listener components...")
        self.source_connector = BlockchainConnector(config.SOURCE_CHAIN_RPC_URL)
        self.dest_connector = BlockchainConnector(config.DEST_CHAIN_RPC_URL)

        self.event_scanner = EventScanner(
            connector=self.source_connector,
            contract_address=config.SOURCE_CHAIN_BRIDGE_CONTRACT_ADDRESS,
            contract_abi=SOURCE_BRIDGE_ABI,
            state_file=config.STATE_FILE_PATH
        )

        self.tx_processor = TransactionProcessor(
            connector=self.dest_connector,
            contract_address=config.DEST_CHAIN_BRIDGE_CONTRACT_ADDRESS,
            contract_abi=DEST_BRIDGE_ABI,
            private_key=config.DEST_CHAIN_SIGNER_PRIVATE_KEY
        )
        logger.info("Bridge listener initialized successfully.")

    async def _run_cycle(self):
        """Executes a single scan-and-process cycle."""
        logger.info("Starting new scan cycle...")
        
        # 1. Scan for events on the source chain
        try:
            events = await self.event_scanner.scan_for_events()
        except Exception as e:
            logger.error(f"An unexpected error occurred during event scanning: {e}")
            events = []

        if not events:
            logger.info("No new events found in this cycle.")
            return

        logger.info(f"Found {len(events)} new events to process.")

        # 2. Process each event and submit a transaction to the destination chain
        for event in events:
            try:
                await self.tx_processor.process_and_submit(event)
                # Add a small delay to avoid nonce issues if processing is very fast
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"An unexpected error occurred during transaction processing for event {event}: {e}")

    async def start(self):
        """Starts the main event listening loop."""
        self.is_running = True
        logger.info("Cross-Chain Bridge Listener has started.")
        while self.is_running:
            await self._run_cycle()
            logger.info(f"Cycle finished. Waiting {self.config.SCAN_INTERVAL_SECONDS} seconds for the next one.")
            await asyncio.sleep(self.config.SCAN_INTERVAL_SECONDS)

    def stop(self):
        """Stops the event listening loop gracefully."""
        logger.info("Stopping Cross-Chain Bridge Listener...")
        self.is_running = False


if __name__ == '__main__':
    logger.info("--- Promt-Soft Cross-Chain Bridge Listener Simulation ---")
    
    # Check for placeholder configuration values
    if Config.SOURCE_CHAIN_BRIDGE_CONTRACT_ADDRESS == '0xSourceBridgeContractAddress' or \
       Config.DEST_CHAIN_BRIDGE_CONTRACT_ADDRESS == '0xDestinationBridgeContractAddress':
        logger.warning("Using placeholder contract addresses. The script will run but find no events.")
    
    if Config.DEST_CHAIN_SIGNER_PRIVATE_KEY == '0x' + 'a' * 64:
         logger.warning("Using a placeholder private key for the signer.")

    # Create and run the listener
    listener = CrossChainBridgeListener(Config)
    try:
        asyncio.run(listener.start())
    except KeyboardInterrupt:
        logger.info("Shutdown signal received.")
        listener.stop()
    except Exception as e:
        logger.critical(f"A critical error forced the listener to stop: {e}")
        listener.stop()
    finally:
        logger.info("Listener has been shut down.")
