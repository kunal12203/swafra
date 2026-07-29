"""Operator CLI for API keys.

    python -m cloud.keys mint --tenant acme --workspace default
    python -m cloud.keys revoke sk_...
"""
from __future__ import annotations

import argparse

from cloud.auth import ApiKeyRegistry
from cloud.config import get_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="cloud.keys", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    mint = sub.add_parser("mint", help="mint a new API key (plaintext shown once)")
    mint.add_argument("--tenant", required=True)
    mint.add_argument("--workspace", default="default")
    mint.add_argument("--user", default=None)
    mint.add_argument("--scopes", default="")

    revoke = sub.add_parser("revoke", help="revoke an existing key")
    revoke.add_argument("key")

    args = parser.parse_args()
    registry = ApiKeyRegistry(get_config().data_dir / "api-keys.db")

    if args.cmd == "mint":
        key = registry.mint(args.tenant, args.workspace, args.user, args.scopes)
        print(key)
    elif args.cmd == "revoke":
        print("revoked" if registry.revoke(args.key) else "not found / already revoked")


if __name__ == "__main__":
    main()
