"""Dedicated Ink Sepolia test wallet; no transaction broadcasting."""
import argparse
import json
import os
import stat
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

ROOT = Path(__file__).resolve().parent
WALLET = ROOT / ".wallet"
KEY = WALLET / "ink-sepolia.key"
ADDRESS = WALLET / "address.txt"
RPCS = (
    "https://rpc-gel-sepolia.inkonchain.com",
    "https://rpc-qnd-sepolia.inkonchain.com",
)
CHAIN_ID = 763373


def account():
    if WALLET.is_symlink() or KEY.is_symlink():
        raise RuntimeError("Refusing symlink wallet storage")
    for path in (WALLET, KEY):
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError(f"Wallet storage must be owner-only: {path}")
    return Account.from_key(KEY.read_text().strip())


def init():
    if KEY.exists():
        print(account().address)
        return
    if WALLET.is_symlink():
        raise RuntimeError("Refusing symlink wallet storage")
    WALLET.mkdir(mode=0o700, exist_ok=True)
    os.chmod(WALLET, 0o700)
    a = Account.create()
    with os.fdopen(os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        f.write(a.key.hex() + "\n")
    ADDRESS.write_text(a.address + "\n")
    print(a.address)


def signing_check():
    a = account()
    message = encode_defunct(text="Ink Sepolia local wallet signing verification")
    signature = a.sign_message(message).signature
    if Account.recover_message(message, signature=signature) != a.address:
        raise RuntimeError("Signature recovery failed")
    signed = a.sign_transaction({
        "chainId": CHAIN_ID, "nonce": 0, "to": a.address, "value": 0,
        "gas": 21000, "gasPrice": 1000000,
    })
    if Account.recover_transaction(signed.raw_transaction) != a.address:
        raise RuntimeError("Transaction recovery failed")
    print(json.dumps({"address": a.address, "message_signing": "PASS",
                      "transaction_signing": "PASS", "broadcast": False}))


def status():
    # Balance reads need only the public address, never the private key.
    address = Web3.to_checksum_address(ADDRESS.read_text().strip())
    results = []
    for rpc in RPCS:
        provider = Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20})
        provider.global_ccip_read_enabled = False
        w3 = Web3(provider)
        chain = w3.eth.chain_id
        if chain != CHAIN_ID:
            raise RuntimeError(f"Refusing unexpected chain {chain}")
        block = w3.eth.block_number
        balance = w3.eth.get_balance(address, block)
        results.append({"rpc": rpc, "chain_id": chain, "block": block,
                        "balance_wei": balance,
                        "balance_eth": str(Web3.from_wei(balance, "ether"))})
    print(json.dumps({"address": address, "networks": results}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "address", "signing-check", "status"))
    args = parser.parse_args()
    if args.command == "init":
        init()
    elif args.command == "address":
        print(ADDRESS.read_text().strip())
    elif args.command == "signing-check":
        signing_check()
    else:
        status()
