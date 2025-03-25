# Promt-Soft Cross-Chain Bridge Event Listener

This repository contains a Python-based simulation of a critical component for a cross-chain bridge: an event listener. This script is designed to monitor a smart contract on a source blockchain (e.g., Ethereum), detect specific events (`TokensLocked`), and initiate corresponding actions on a destination blockchain (e.g., Polygon).

This is an architectural prototype intended to demonstrate a robust, scalable, and resilient design for off-chain bridge infrastructure.

## Concept

Cross-chain bridges enable interoperability between different blockchains, allowing users to transfer assets or data from one network to another. A common pattern is the "lock-and-mint" mechanism:

1.  **Lock:** A user locks their tokens in a bridge contract on the source chain.
2.  **Event Emission:** The bridge contract emits an event (e.g., `TokensLocked`) containing details of the transaction (sender, recipient, amount, etc.).
3.  **Listen & Verify:** Off-chain services, called listeners or oracles, constantly monitor the source chain for these events.
4.  **Mint:** Upon detecting a confirmed event, the listener relays this information to a bridge contract on the destination chain, which then mints a corresponding amount of a wrapped/pegged token for the recipient.

This script simulates the crucial step 3 and the initiation of step 4.

## Code Architecture

The script is designed with a clear separation of concerns, using several classes to handle distinct responsibilities. This makes the system easier to maintain, test, and extend.

```
+---------------------------------+
|   CrossChainBridgeListener      | (Orchestrator)
+---------------------------------+
           |         |
           |         |         +-----------------------+
           |         +-------->| TransactionProcessor  | (Handles Destination Chain)
           |                   +-----------------------+
           |                             |
           |                             |         +-----------------------+
           v                             +-------->|   BlockchainConnector | (Web3 for Dest Chain)
+-----------------------+                          +-----------------------+
|      EventScanner     | (Handles Source Chain)
+-----------------------+
           |
           |         +-----------------------+
           +-------->|   BlockchainConnector | (Web3 for Source Chain)
                     +-----------------------+
```

### Core Components

*   `Config`: A centralized class to hold all configuration parameters, such as RPC URLs, contract addresses, and private keys. It is designed to load values from environment variables for security and flexibility.

*   `BlockchainConnector`: A reusable utility class that manages the `web3.py` connection to a blockchain node. It handles connection establishment and provides a simple interface for getting contract objects.

*   `EventScanner`: This is the heart of the listening mechanism. Its sole responsibility is to scan the source chain for new events since the last check. It maintains its state (the last block number it scanned) in a local file (`bridge_listener_state.json`) to ensure it can resume from where it left off after a restart.

*   `TransactionProcessor`: Once the `EventScanner` finds an event, it passes the data to the `TransactionProcessor`. This class is responsible for validating the event, constructing a new transaction for the destination chain (e.g., a `mintTokens` call), signing it with a private key, and submitting it. **In this simulation, the final submission step is logged instead of being executed.**

*   `CrossChainBridgeListener`: The main orchestrator class. It initializes all other components and runs the main asynchronous loop. The loop periodically triggers the `EventScanner` and, if new events are found, passes them to the `TransactionProcessor`.

## How it Works

The operational flow of the listener is as follows:

1.  **Initialization**: When the script starts, the `CrossChainBridgeListener` is instantiated. It creates instances of `BlockchainConnector` for both the source and destination chains, an `EventScanner` for the source chain, and a `TransactionProcessor` for the destination chain.

2.  **State Loading**: The `EventScanner` attempts to load its last known state from `bridge_listener_state.json`. If the file doesn't exist, it prepares to start scanning from a recent block.

3.  **Main Loop**: The listener enters an infinite `asyncio` loop.

4.  **Scanning**: In each cycle, the `EventScanner`:
    a. Fetches the latest block number from the source chain's RPC node.
    b. Calculates a target block to scan up to, leaving a buffer of `CONFIRMATION_BLOCKS` for event finality.
    c. Queries the bridge contract for `TokensLocked` events in chunks between its `last_scanned_block` and the `target_block`.
    d. If events are found, they are returned to the main loop.
    e. The `last_scanned_block` is updated, and the new state is saved to the JSON file.

5.  **Processing**: For each event found:
    a. The `TransactionProcessor` receives the event data.
    b. It validates the data to ensure it's well-formed.
    c. It builds a `mintTokens` transaction for the destination bridge contract.
    d. It signs the transaction using the provided private key.
    e. **(Simulation)** It calculates the would-be transaction hash and logs the action to the console instead of sending it to the network.

6.  **Wait**: After a cycle is complete, the script sleeps for `SCAN_INTERVAL_SECONDS` before starting the next cycle.

## Usage Example

Follow these steps to set up and run the simulation.

### 1. Prerequisites

*   Python 3.8+
*   An RPC endpoint URL for a source chain (e.g., Ethereum Sepolia from Infura, Alchemy, or Ankr).
*   An RPC endpoint URL for a destination chain (e.g., Polygon Mumbai).

### 2. Installation

Clone the repository and install the required Python packages:

```bash
git clone https://github.com/your-username/promt-soft.git
cd promt-soft
pip install -r requirements.txt
```

### 3. Configuration

Create a file named `.env` in the root directory of the project and populate it with your specific configuration. This method keeps your sensitive data out of the script.

```env
# .env file

# Source chain (e.g., Ethereum Sepolia testnet)
SOURCE_CHAIN_RPC_URL="https://rpc.ankr.com/eth_sepolia"
SOURCE_CHAIN_BRIDGE_CONTRACT_ADDRESS="0x...YourSourceBridgeContractAddress..."

# Destination chain (e.g., Polygon Mumbai testnet)
DEST_CHAIN_RPC_URL="https://rpc.ankr.com/polygon_mumbai"
DEST_CHAIN_BRIDGE_CONTRACT_ADDRESS="0x...YourDestinationBridgeContractAddress..."

# Private key for the account that will submit minting transactions on the destination chain
# IMPORTANT: Use a key from a temporary/burner wallet for testing.
DEST_CHAIN_SIGNER_PRIVATE_KEY="0x...YourSignerPrivateKey..."
```

*Note: If you don't have actual bridge contract addresses, you can run the script with the default placeholder addresses. It will run correctly but won't find any events.*

### 4. Running the Script

Execute the script from your terminal:

```bash
python script.py
```

You will see log output in your terminal indicating the listener's status, such as connecting to nodes, scanning for blocks, and processing any found events.

```
2023-10-27 14:30:00 - __main__ - INFO - --- Promt-Soft Cross-Chain Bridge Listener Simulation ---
2023-10-27 14:30:00 - __main__ - INFO - Initializing bridge listener components...
2023-10-27 14:30:01 - __main__ - INFO - Connecting to blockchain node at https://rpc.ankr.com/eth_sepolia...
2023-10-27 14:30:02 - __main__ - INFO - Successfully connected. Chain ID: 11155111
... (more initialization logs) ...
2023-10-27 14:30:05 - __main__ - INFO - Cross-Chain Bridge Listener has started.
2023-10-27 14:30:05 - __main__ - INFO - Starting new scan cycle...
2023-10-27 14:30:05 - EventScanner - INFO - Loaded last scanned block from state: 4501234
2023-10-27 14:30:07 - EventScanner - INFO - Scanning for 'TokensLocked' events from block 4501235 to 4501300
...
```

To stop the listener gracefully, press `CTRL+C`.