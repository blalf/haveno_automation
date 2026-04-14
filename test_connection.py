"""
Quick connection test — run this BEFORE launching the full UI.
Tells you if the daemon is reachable and shows your balance + payment accounts.

Usage:
    python3 test_connection.py
    python3 test_connection.py --host localhost --port 3201 --password yourpassword
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from haveno_client import HavenoClient
from config_manager import load_config, save_config


def main():
    parser = argparse.ArgumentParser(description="Test Haveno daemon connection")
    parser.add_argument("--host",     default=None)
    parser.add_argument("--port",     default=None, type=int)
    parser.add_argument("--password", default=None)
    parser.add_argument("--account-password", default=None, dest="account_password")
    args = parser.parse_args()

    cfg = load_config()
    host     = args.host     or cfg.get("host", "localhost")
    port     = args.port     or cfg.get("port", 3201)
    password = args.password or cfg.get("password", "apitest")
    acc_pw   = args.account_password or cfg.get("account_password", "")

    print(f"\n🔌 Connecting to {host}:{port} …")

    client = HavenoClient(host=host, port=port, password=password,
                          account_password=acc_pw)
    try:
        client.connect()

        version = client.get_version()
        print(f"✓ Connected!  Daemon version: {version}")

        # Ensure account is open (works for both daemon and desktop modes)
        account_status = client.ensure_account_open()
        print(f"✓ Account: {account_status}")

        balances = client.get_balances()
        avail = HavenoClient.atomic_to_xmr(balances["available_balance"])
        reserved = HavenoClient.atomic_to_xmr(balances["reserved_offer_balance"])
        print(f"\n💰 XMR Balances:")
        print(f"   Available:       {avail:.6f} XMR")
        print(f"   Reserved offers: {reserved:.6f} XMR")

        accounts = client.get_payment_accounts()
        print(f"\n🏦 Payment accounts ({len(accounts)}):")
        for a in accounts:
            print(f"   [{a['id'][:8]}…] {a['account_name']}  ({a['currency_code']})")

        offers = client.get_my_offers()
        print(f"\n📋 Active offers: {len(offers)}")
        for o in offers[:5]:
            xmr = HavenoClient.atomic_to_xmr(o["amount"])
            print(f"   {o['currency_code']:6s} {xmr:.4f} XMR  +{o['market_price_margin_pct']:.1f}%  [{o['id'][:12]}…]")
        if len(offers) > 5:
            print(f"   … and {len(offers) - 5} more")

        # Save working config
        cfg["host"]     = host
        cfg["port"]     = port
        cfg["password"] = password
        save_config(cfg)
        print(f"\n✅ Config saved to app_config.json")

    except Exception as e:
        print(f"\n✗ Connection failed: {e}")
        print("\nTroubleshooting:")
        print("  1. Make sure RetoSwap is running")
        print("  2. Check host/port — try --port 8080 if 3201 doesn't work")
        print("  3. Check your API password")
        sys.exit(1)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
