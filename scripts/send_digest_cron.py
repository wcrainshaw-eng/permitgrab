#!/usr/bin/env python3
"""V93: Cron job script to trigger daily digest via API endpoint.

This script is called by Render's cron service to send the daily digest.
It bypasses the daemon thread approach which fails when the app spins down.
"""
import os
import sys
import requests

def main():
    admin_key = os.environ.get('ADMIN_KEY')
    if not admin_key:
        print("ERROR: ADMIN_KEY environment variable not set")
        sys.exit(1)

    url = f"https://permitgrab.onrender.com/api/admin/send-digest?key={admin_key}"

    print(f"Triggering daily digest...")
    try:
        response = requests.post(url, timeout=120)
        print(f"Response status: {response.status_code}")
        print(f"Response: {response.text[:500]}")

        if response.status_code == 200:
            print("Digest sent successfully!")
            sys.exit(0)
        else:
            print(f"Digest failed with status {response.status_code}")
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
