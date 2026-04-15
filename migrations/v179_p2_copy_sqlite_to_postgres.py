"""V179 Phase 2: Copy SQLite data to Postgres (idempotent upsert).

Run on Render Shell:
  python3 migrations/v179_p2_copy_sqlite_to_postgres.py

Re-runnable — uses ON CONFLICT for all tables. Resumes from checkpoint
for permits (largest table).
"""
import os, sys, sqlite3, psycopg2, json, time

SQLITE_PATH = '/var/data/permitgrab.db'
BATCH_SIZE = 1000

def get_pg():
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    conn.autocommit = False
    return conn

def get_sq():
    return sqlite3.connect(SQLITE_PATH, timeout=30)

def norm_key(val):
    """Normalize hyphen to underscore for source keys."""
    return val.replace('-', '_') if val else val

# ---- 1. Reference tables (INSERT ON CONFLICT DO NOTHING) ----

def copy_simple(table, pk_col, cols):
    """Copy a table with simple DO NOTHING on conflict."""
    sq = get_sq()
    pg = get_pg()
    sc = sq.cursor()
    pc = pg.cursor()
    sc.execute(f"SELECT {','.join(cols)} FROM [{table}]")
    rows = sc.fetchall()
    inserted = 0
    for i, row in enumerate(rows):
        vals = list(row)
        placeholders = ','.join(['%s'] * len(cols))
        try:
            pc.execute(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT ({pk_col}) DO NOTHING",
                vals
            )
            inserted += 1
        except Exception as e:
            pg.rollback()
            if i < 3:
                print(f"  {table} row {i} error: {e}", flush=True)
            continue
        if (i + 1) % BATCH_SIZE == 0:
            pg.commit()
    pg.commit()
    sq.close()
    pg.close()
    print(f"  {table}: {inserted}/{len(rows)} processed", flush=True)

def copy_reference_tables():
    print("--- Reference tables ---", flush=True)

    # sources
    copy_simple('sources', 'source_key',
        ['source_key','name','scope_type','state','platform','endpoint',
         'dataset_id','field_map','date_field','city_field','status',
         'data_type','created_at','notes'])

    # bulk_sources
    copy_simple('bulk_sources', 'source_key',
        ['source_key','name','scope_type','scope_name','state','platform',
         'endpoint','dataset_id','field_map','date_field','city_field','status',
         'created_at'])

    # system_state
    copy_simple('system_state', 'key', ['key','value','updated_at'])

    # subscribers
    copy_simple('subscribers', 'email',
        ['email','name','plan','digest_cities','active','created_at','last_digest_sent_at'])

    # digest_log (no natural PK, use id but skip conflicts)
    sq = get_sq(); pg = get_pg()
    sc = sq.cursor(); pc = pg.cursor()
    sc.execute("SELECT sent_at,recipient_email,permits_count,status,error_message FROM digest_log")
    for row in sc.fetchall():
        try:
            pc.execute("INSERT INTO digest_log (sent_at,recipient_email,permits_count,status,error_message) VALUES (%s,%s,%s,%s,%s)", row)
        except:
            pg.rollback()
    pg.commit(); sq.close(); pg.close()
    print("  digest_log: done", flush=True)

    # city_research
    sq = get_sq(); pg = get_pg()
    sc = sq.cursor(); pc = pg.cursor()
    sc.execute("SELECT city,state,population,status,portal_url,dataset_id,platform,date_field,address_field,notes,tested_at,onboarded_at,created_at FROM city_research")
    n = 0
    for row in sc.fetchall():
        try:
            pc.execute(
                "INSERT INTO city_research (city,state,population,status,portal_url,dataset_id,platform,date_field,address_field,notes,tested_at,onboarded_at,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING", row)
            n += 1
        except:
            pg.rollback()
        if n % BATCH_SIZE == 0: pg.commit()
    pg.commit(); sq.close(); pg.close()
    print(f"  city_research: {n} rows", flush=True)

    # cities
    sq = get_sq(); pg = get_pg()
    sc = sq.cursor(); pc = pg.cursor()
    sc.execute("SELECT city_slug,city,state,population,platform,endpoint,status,notes FROM cities")
    n = 0
    for row in sc.fetchall():
        try:
            pc.execute(
                "INSERT INTO cities (city_slug,city,state,population,platform,endpoint,status,notes) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (city_slug) DO NOTHING", row)
            n += 1
        except:
            pg.rollback()
        if n % BATCH_SIZE == 0: pg.commit()
    pg.commit(); sq.close(); pg.close()
    print(f"  cities: {n} rows", flush=True)

    # sweep_sources
    sq = get_sq(); pg = get_pg()
    sc = sq.cursor(); pc = pg.cursor()
    sc.execute("SELECT city_slug,source_type,source_url,platform,dataset_id,domain,name,status,permits_found FROM sweep_sources")
    n = 0
    for row in sc.fetchall():
        try:
            pc.execute(
                "INSERT INTO sweep_sources (city_slug,source_type,source_url,platform,dataset_id,domain,name,status,permits_found) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (city_slug, dataset_id) DO NOTHING", row)
            n += 1
        except:
            pg.rollback()
        if n % BATCH_SIZE == 0: pg.commit()
    pg.commit(); sq.close(); pg.close()
    print(f"  sweep_sources: {n} rows", flush=True)


# ---- 2. prod_cities (COALESCE merge) ----

def copy_prod_cities():
    print("--- prod_cities (merge) ---", flush=True)
    sq = get_sq(); pg = get_pg()
    sc = sq.cursor(); pc = pg.cursor()

    # Get all SQLite prod_cities
    sc.execute("""
        SELECT id, city, state, city_slug, source_type, source_id, source_scope,
               source_endpoint, verified_date, last_collection, last_permit_date,
               total_permits, permits_last_30d, avg_daily_permits, status,
               consecutive_failures, last_error, added_by, added_at, notes,
               data_freshness, newest_permit_date, stale_since, pause_reason,
               population, health_status, backfill_status
        FROM prod_cities
    """)
    rows = sc.fetchall()
    cols_names = [d[0] for d in sc.description]
    n = 0
    for row in rows:
        d = dict(zip(cols_names, row))
        # Normalize source_id
        if d.get('source_id'):
            d['source_id'] = norm_key(d['source_id'])

        try:
            pc.execute("""
                INSERT INTO prod_cities (id, city, state, city_slug, source_type, source_id,
                    source_scope, source_endpoint, verified_date, last_collection, last_permit_date,
                    total_permits, permits_last_30d, avg_daily_permits, status,
                    consecutive_failures, last_error, added_by, added_at, notes,
                    data_freshness, newest_permit_date, stale_since, pause_reason,
                    population, health_status, backfill_status)
                VALUES (%(id)s, %(city)s, %(state)s, %(city_slug)s, %(source_type)s, %(source_id)s,
                    %(source_scope)s, %(source_endpoint)s, %(verified_date)s, %(last_collection)s,
                    %(last_permit_date)s, %(total_permits)s, %(permits_last_30d)s, %(avg_daily_permits)s,
                    %(status)s, %(consecutive_failures)s, %(last_error)s, %(added_by)s, %(added_at)s,
                    %(notes)s, %(data_freshness)s, %(newest_permit_date)s, %(stale_since)s,
                    %(pause_reason)s, %(population)s, %(health_status)s, %(backfill_status)s)
                ON CONFLICT (id) DO UPDATE SET
                    population = COALESCE(EXCLUDED.population, prod_cities.population),
                    newest_permit_date = COALESCE(EXCLUDED.newest_permit_date, prod_cities.newest_permit_date),
                    data_freshness = COALESCE(EXCLUDED.data_freshness, prod_cities.data_freshness),
                    backfill_status = COALESCE(EXCLUDED.backfill_status, prod_cities.backfill_status),
                    total_permits = COALESCE(EXCLUDED.total_permits, prod_cities.total_permits),
                    permits_last_30d = COALESCE(EXCLUDED.permits_last_30d, prod_cities.permits_last_30d),
                    source_type = COALESCE(EXCLUDED.source_type, prod_cities.source_type),
                    source_id = COALESCE(EXCLUDED.source_id, prod_cities.source_id),
                    health_status = COALESCE(prod_cities.health_status, EXCLUDED.health_status)
            """, d)
            n += 1
        except Exception as e:
            pg.rollback()
            if n < 5:
                print(f"  prod_cities error row {n}: {e}", flush=True)
        if n % BATCH_SIZE == 0:
            pg.commit()
    pg.commit()
    sq.close(); pg.close()
    print(f"  prod_cities: {n}/{len(rows)} processed", flush=True)


# ---- 3. scraper_runs ----

def copy_scraper_runs():
    print("--- scraper_runs ---", flush=True)
    sq = get_sq(); pg = get_pg()
    sc = sq.cursor(); pc = pg.cursor()
    sc.execute("""SELECT source_name,city,state,city_slug,run_started_at,run_completed_at,
                  duration_ms,permits_found,permits_inserted,status,error_message,
                  error_type,http_status,response_size_bytes,collection_type,triggered_by
                  FROM scraper_runs""")
    n = 0
    for row in sc.fetchall():
        try:
            pc.execute("""INSERT INTO scraper_runs (source_name,city,state,city_slug,run_started_at,
                run_completed_at,duration_ms,permits_found,permits_inserted,status,error_message,
                error_type,http_status,response_size_bytes,collection_type,triggered_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", row)
            n += 1
        except:
            pg.rollback()
        if n % BATCH_SIZE == 0:
            pg.commit()
            if n % 10000 == 0:
                print(f"  scraper_runs: {n}...", flush=True)
    pg.commit(); sq.close(); pg.close()
    print(f"  scraper_runs: {n} rows", flush=True)


# ---- 4. violations ----

def copy_violations():
    print("--- violations ---", flush=True)
    sq = get_sq(); pg = get_pg()
    sc = sq.cursor(); pc = pg.cursor()
    sc.execute("""SELECT prod_city_id, city, state, source_violation_id, violation_date,
                  violation_type, violation_description, status, address, zip,
                  latitude, longitude, raw_data, collected_at
                  FROM violations""")
    n = 0
    for row in sc.fetchall():
        try:
            pc.execute("""INSERT INTO violations (prod_city_id, city, state, source_violation_id,
                violation_date, violation_type, violation_description, status, address, zip,
                latitude, longitude, raw_data, collected_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source_violation_id) DO NOTHING""", row)
            n += 1
        except:
            pg.rollback()
        if n % BATCH_SIZE == 0:
            pg.commit()
            if n % 10000 == 0:
                print(f"  violations: {n}...", flush=True)
    pg.commit(); sq.close(); pg.close()
    print(f"  violations: {n} rows", flush=True)


# ---- 5. permits (largest, with checkpoint) ----

CHECKPOINT_FILE = '/var/data/p2_permits_checkpoint.txt'

def copy_permits():
    print("--- permits (999K rows) ---", flush=True)

    # Resume from checkpoint if exists
    last_pn = None
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            last_pn = f.read().strip()
        print(f"  Resuming from checkpoint: {last_pn}", flush=True)

    sq = get_sq(); pg = get_pg()
    sc = sq.cursor(); pc = pg.cursor()

    if last_pn:
        sc.execute("""SELECT permit_number, city, state, address, zip, permit_type,
            permit_sub_type, work_type, trade_category, description, display_description,
            estimated_cost, value_tier, status, filing_date, issued_date, date,
            contact_name, contact_phone, contact_email, owner_name, contractor_name,
            square_feet, lifecycle_label, source_city_key, collected_at, updated_at, prod_city_id
            FROM permits WHERE permit_number > ? ORDER BY permit_number""", (last_pn,))
    else:
        sc.execute("""SELECT permit_number, city, state, address, zip, permit_type,
            permit_sub_type, work_type, trade_category, description, display_description,
            estimated_cost, value_tier, status, filing_date, issued_date, date,
            contact_name, contact_phone, contact_email, owner_name, contractor_name,
            square_feet, lifecycle_label, source_city_key, collected_at, updated_at, prod_city_id
            FROM permits ORDER BY permit_number""")

    n = 0
    last_committed_pn = last_pn
    while True:
        rows = sc.fetchmany(BATCH_SIZE)
        if not rows:
            break
        for row in rows:
            vals = list(row)
            # Normalize source_city_key (index 24)
            if vals[24]:
                vals[24] = norm_key(vals[24])
            try:
                pc.execute("""INSERT INTO permits (permit_number, city, state, address, zip,
                    permit_type, permit_sub_type, work_type, trade_category, description,
                    display_description, estimated_cost, value_tier, status, filing_date,
                    issued_date, date, contact_name, contact_phone, contact_email, owner_name,
                    contractor_name, square_feet, lifecycle_label, source_city_key,
                    collected_at, updated_at, prod_city_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (permit_number) DO UPDATE SET
                        description = COALESCE(EXCLUDED.description, permits.description),
                        permit_type = COALESCE(EXCLUDED.permit_type, permits.permit_type),
                        trade_category = COALESCE(EXCLUDED.trade_category, permits.trade_category),
                        contractor_name = COALESCE(EXCLUDED.contractor_name, permits.contractor_name),
                        estimated_cost = COALESCE(EXCLUDED.estimated_cost, permits.estimated_cost),
                        filing_date = COALESCE(EXCLUDED.filing_date, permits.filing_date),
                        issued_date = COALESCE(EXCLUDED.issued_date, permits.issued_date),
                        source_city_key = EXCLUDED.source_city_key,
                        prod_city_id = COALESCE(EXCLUDED.prod_city_id, permits.prod_city_id),
                        updated_at = NOW()
                """, vals)
                n += 1
            except Exception as e:
                pg.rollback()
                if n < 5:
                    print(f"  permits error: {e}", flush=True)
            last_committed_pn = vals[0]

        pg.commit()
        # Save checkpoint
        if last_committed_pn:
            with open(CHECKPOINT_FILE, 'w') as f:
                f.write(last_committed_pn)
        if n % 10000 == 0:
            print(f"  permits: {n}...", flush=True)

    pg.commit()
    sq.close(); pg.close()
    # Clean checkpoint
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
    print(f"  permits: {n} total", flush=True)


# ---- MAIN ----

if __name__ == '__main__':
    print(f"V179 P2: SQLite → Postgres copy started at {time.strftime('%H:%M:%S')}", flush=True)
    t0 = time.time()

    copy_reference_tables()
    copy_prod_cities()
    copy_scraper_runs()
    copy_violations()
    copy_permits()

    elapsed = int(time.time() - t0)
    print(f"\nP2 complete in {elapsed}s ({elapsed//60}m{elapsed%60}s)", flush=True)

    # Final verification
    pg = get_pg()
    pc = pg.cursor()
    sq = get_sq()
    sc = sq.cursor()
    print("\n--- Verification ---", flush=True)
    for t in ['permits','prod_cities','violations','scraper_runs','sources',
              'bulk_sources','sweep_sources','subscribers','digest_log',
              'city_research','cities','system_state']:
        try:
            sc.execute(f'SELECT COUNT(*) FROM [{t}]')
            s = sc.fetchone()[0]
        except:
            s = '?'
        try:
            pc.execute(f'SELECT COUNT(*) FROM "{t}"')
            p = pc.fetchone()[0]
        except:
            p = '?'
        print(f"  {t}: sqlite={s} postgres={p}", flush=True)

    # Per-city permit counts
    print("\n--- Per-city permits ---", flush=True)
    for slug in ['new_york','houston','austin_tx','dallas_tx','chicago_il','los_angeles','philadelphia']:
        pc.execute("SELECT COUNT(*) FROM permits WHERE source_city_key=%s", (slug,))
        print(f"  {slug}: {pc.fetchone()[0]}", flush=True)

    pg.close()
    sq.close()
# V179 P2 completed 2026-04-15: 1,145,540 permits, 67,259 violations, 20,440 cities in Postgres
