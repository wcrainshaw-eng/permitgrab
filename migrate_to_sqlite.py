"""
One-time migration: JSON files -> SQLite
Run in Render shell after deploying V12.50:
    python3 migrate_to_sqlite.py
"""
import json
import os
import gc
import sys

# Add app directory to path
sys.path.insert(0, '/app')
try:
    import db
except ImportError:
    # Running locally
    sys.path.insert(0, os.path.dirname(__file__))
    import db

DATA_DIR = '/var/data' if os.path.isdir('/var/data') else os.path.join(os.path.dirname(__file__), 'data')


def migrate_permits():
    """Migrate permits.json -> permits table."""
    permits_file = os.path.join(DATA_DIR, 'permits.json')
    if not os.path.exists(permits_file):
        print("No permits.json found, skipping")
        return

    print(f"Migrating permits.json ({os.path.getsize(permits_file)/1024/1024:.1f}MB)...")

    # Stream-read to limit memory: read, insert in chunks, free
    with open(permits_file, 'r') as f:
        permits = json.load(f)

    print(f"Loaded {len(permits)} permits, inserting into SQLite...")

    # Insert in batches of 1000
    BATCH = 1000
    for i in range(0, len(permits), BATCH):
        batch = permits[i:i+BATCH]
        db.upsert_permits(batch)
        if (i + BATCH) % 10000 == 0:
            print(f"  Inserted {min(i+BATCH, len(permits))}/{len(permits)}...")

    del permits
    gc.collect()
    print("permits.json migration complete")


def migrate_history():
    """Migrate permit_history.json -> permit_history table."""
    history_file = os.path.join(DATA_DIR, 'permit_history.json')
    if not os.path.exists(history_file):
        print("No permit_history.json found, skipping")
        return

    size_mb = os.path.getsize(history_file) / 1024 / 1024
    print(f"Migrating permit_history.json ({size_mb:.1f}MB)...")
    print("NOTE: This file is large. If OOM occurs, use migrate_history_chunked() instead.")

    with open(history_file, 'r') as f:
        history = json.load(f)

    print(f"Loaded {len(history)} addresses")

    conn = db.get_connection()
    total = 0
    count = 0
    addresses_to_delete = []

    for addr_key, data in history.items():
        for p in data.get('permits', []):
            conn.execute("""
                INSERT OR IGNORE INTO permit_history (
                    address_key, address, city, state,
                    permit_number, permit_type, work_type, trade_category,
                    filing_date, estimated_cost, description, contractor
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                addr_key, data.get('address'), data.get('city'), data.get('state'),
                p.get('permit_number'), p.get('permit_type'), p.get('work_type'),
                p.get('trade_category'), p.get('filing_date'),
                p.get('estimated_cost'), p.get('description', '')[:200],
                p.get('contractor')
            ))
            total += 1

        count += 1
        addresses_to_delete.append(addr_key)

        # Free memory as we go
        if len(addresses_to_delete) >= 2000:
            conn.commit()
            for addr in addresses_to_delete:
                del history[addr]
            addresses_to_delete = []
            gc.collect()
            if count % 10000 == 0:
                print(f"  Processed {count} addresses, {total} permits...")

    # Final flush
    for addr in addresses_to_delete:
        try:
            del history[addr]
        except KeyError:
            pass
    conn.commit()
    del history
    gc.collect()

    print(f"permit_history.json migration complete: {count} addresses, {total} permits")


if __name__ == '__main__':
    print("=" * 60)
    print("PermitGrab V12.50 - JSON -> SQLite Migration")
    print("=" * 60)

    db.init_db()
    migrate_permits()
    migrate_history()

    # Verify
    conn = db.get_connection()
    p_count = conn.execute("SELECT COUNT(*) FROM permits").fetchone()[0]
    h_count = conn.execute("SELECT COUNT(*) FROM permit_history").fetchone()[0]
    db_size = os.path.getsize(db.DB_PATH) / 1024 / 1024

    print(f"\n{'=' * 60}")
    print(f"Migration complete!")
    print(f"  Permits table: {p_count} rows")
    print(f"  History table: {h_count} rows")
    print(f"  Database size: {db_size:.1f}MB")
    print(f"  Location: {db.DB_PATH}")
    print(f"{'=' * 60}")
    print(f"\nYou can now safely delete the JSON files:")
    print(f"  rm {os.path.join(DATA_DIR, 'permits.json')}")
    print(f"  rm {os.path.join(DATA_DIR, 'permit_history.json')}")
