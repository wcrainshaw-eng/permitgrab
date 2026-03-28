#!/bin/bash
# V21 PRODUCTION CLEANUP - Run this in Render Shell
# Access: Render Dashboard → permitgrab → Shell tab
# Execute each step in order, copy-paste one at a time

echo "=========================================="
echo "V21 PRODUCTION DATABASE CLEANUP"
echo "=========================================="

# STEP 1: Fix JSON addresses
echo ""
echo "STEP 1: Fixing JSON addresses..."
python3 -c "
import sqlite3, json
conn = sqlite3.connect('/var/data/permitgrab.db')
rows = conn.execute(\"SELECT rowid, address FROM permits WHERE address LIKE '%human_address%' OR address LIKE '%coordinates%'\").fetchall()
fixed = 0
for rowid, addr in rows:
    try:
        parsed = json.loads(addr.replace(chr(39), chr(34)))
        human = parsed.get('human_address', '')
        if human:
            conn.execute('UPDATE permits SET address=? WHERE rowid=?', (human, rowid))
            fixed += 1
    except: pass
conn.commit()
print('Fixed', fixed, 'of', len(rows), 'JSON addresses')
"

# STEP 2: Fix Houston OK → TX (verify first)
echo ""
echo "STEP 2: Checking Houston OK..."
python3 -c "
import sqlite3
conn = sqlite3.connect('/var/data/permitgrab.db')
# Check what these look like first
rows = conn.execute(\"SELECT address, permit_type, filing_date, source_city_key FROM permits WHERE city='Houston' AND state='OK' LIMIT 5\").fetchall()
for r in rows: print(r)
print('Houston OK count:', conn.execute(\"SELECT COUNT(*) FROM permits WHERE city='Houston' AND state='OK'\").fetchone()[0])
print('Houston TX count:', conn.execute(\"SELECT COUNT(*) FROM permits WHERE city='Houston' AND state='TX'\").fetchone()[0])
"
# IF they look like Texas permits, run:
# python3 -c "import sqlite3; conn = sqlite3.connect('/var/data/permitgrab.db'); print(conn.execute(\"UPDATE permits SET state='TX' WHERE city='Houston' AND state='OK'\").rowcount); conn.commit()"

# STEP 3: Delete permit-type-as-city-name records
echo ""
echo "STEP 3: Checking fake city names..."
python3 -c "
import sqlite3
conn = sqlite3.connect('/var/data/permitgrab.db')
rows = conn.execute(\"SELECT city, COUNT(*) FROM permits WHERE city IN ('Gas','Plumbing','Electrical','Mechanical','Building','Fire','Roofing','Sewer','Demolition','Grading','Pool','Sign','Fence') GROUP BY city\").fetchall()
print('Fake cities found:')
for r in rows: print(r)
total = sum(r[1] for r in rows)
print('Total:', total)
"
# Then delete them:
# python3 -c "import sqlite3; conn = sqlite3.connect('/var/data/permitgrab.db'); d = conn.execute(\"DELETE FROM permits WHERE city IN ('Gas','Plumbing','Electrical','Mechanical','Building','Fire','Roofing','Sewer','Demolition','Grading','Pool','Sign','Fence')\").rowcount; conn.commit(); print('Deleted:', d)"

# STEP 4: Delete remaining Alberta permits
echo ""
echo "STEP 4: Deleting Alberta permits..."
python3 -c "
import sqlite3
conn = sqlite3.connect('/var/data/permitgrab.db')
d = conn.execute(\"DELETE FROM permits WHERE state='AB'\").rowcount
conn.commit()
print('Deleted AB permits:', d)
"

# STEP 5: Backfill empty states where city is unambiguous
echo ""
echo "STEP 5: Backfilling empty states..."
python3 -c "
import sqlite3
conn = sqlite3.connect('/var/data/permitgrab.db')
# Map common cities to their states
fixes = {
    'Orlando': 'FL', 'Houston': 'TX', 'Chicago': 'IL', 'Austin': 'TX',
    'Phoenix': 'AZ', 'Denver': 'CO', 'Portland': 'OR', 'Boston': 'MA',
    'Los Angeles': 'CA', 'San Jose': 'CA', 'Milwaukee': 'WI',
    'Baltimore': 'MD', 'Fort Worth': 'TX', 'Dallas': 'TX',
    'San Antonio': 'TX', 'Nashville': 'TN', 'Mesa': 'AZ',
    'New York City': 'NY', 'Seattle': 'WA', 'San Francisco': 'CA',
    'Little Rock': 'AR', 'New Orleans': 'LA', 'Utica': 'NY'
}
total = 0
for city, state in fixes.items():
    n = conn.execute(\"UPDATE permits SET state=? WHERE city=? AND (state='' OR state IS NULL)\", (state, city)).rowcount
    if n > 0:
        print(f'  {city} -> {state}: {n} fixed')
        total += n
conn.commit()
print('Total empty states fixed:', total)
"

# STEP 6: Backfill empty filing dates from alternative date fields
echo ""
echo "STEP 6: Backfilling filing dates..."
python3 -c "
import sqlite3
conn = sqlite3.connect('/var/data/permitgrab.db')
n = conn.execute(\"UPDATE permits SET filing_date = COALESCE(issued_date, date) WHERE (filing_date IS NULL OR filing_date = '') AND (issued_date IS NOT NULL AND issued_date != '' OR date IS NOT NULL AND date != '')\").rowcount
conn.commit()
print('Filing dates backfilled:', n)
"

# STEP 7: Populate prod_cities from permit data
echo ""
echo "STEP 7: Populating prod_cities..."
python3 -c "
import sqlite3
conn = sqlite3.connect('/var/data/permitgrab.db')
conn.execute('''INSERT OR IGNORE INTO prod_cities (city, state, total_permits, status)
  SELECT city, state, COUNT(*), 'active'
  FROM permits
  WHERE city != '' AND state != '' AND city NOT IN ('Gas','Plumbing','Electrical','Mechanical')
  GROUP BY city, state
  HAVING COUNT(*) >= 10''')
conn.commit()
print('Populated prod_cities:', conn.execute('SELECT COUNT(*) FROM prod_cities').fetchone()[0])
"

# STEP 8: Deduplicate us_cities
echo ""
echo "STEP 8: Deduplicating us_cities..."
python3 -c "
import sqlite3
conn = sqlite3.connect('/var/data/permitgrab.db')
before = conn.execute('SELECT COUNT(*) FROM us_cities').fetchone()[0]
conn.execute('DELETE FROM us_cities WHERE id NOT IN (SELECT MIN(id) FROM us_cities GROUP BY city_name, state)')
after = conn.execute('SELECT COUNT(*) FROM us_cities').fetchone()[0]
conn.commit()
print('us_cities before:', before, 'after:', after, 'removed:', before - after)
"

# STEP 9: Final verification
echo ""
echo "=========================================="
echo "STEP 9: FINAL VERIFICATION"
echo "=========================================="
python3 -c "
import sqlite3
conn = sqlite3.connect('/var/data/permitgrab.db')
print('=== FINAL STATE ===')
print('Total permits:', conn.execute('SELECT COUNT(*) FROM permits').fetchone()[0])
print('Garbage cities:', conn.execute(\"SELECT COUNT(*) FROM permits WHERE city GLOB '[0-9]*'\").fetchone()[0])
print('Calgary:', conn.execute(\"SELECT COUNT(*) FROM permits WHERE city='Calgary'\").fetchone()[0])
print('Alberta:', conn.execute(\"SELECT COUNT(*) FROM permits WHERE state='AB'\").fetchone()[0])
print('Empty state:', conn.execute(\"SELECT COUNT(*) FROM permits WHERE state='' OR state IS NULL\").fetchone()[0])
print('No date:', conn.execute(\"SELECT COUNT(*) FROM permits WHERE filing_date IS NULL OR filing_date=''\").fetchone()[0])
print('JSON addresses:', conn.execute(\"SELECT COUNT(*) FROM permits WHERE address LIKE '%coordinates%' OR address LIKE '%human_address%'\").fetchone()[0])
print('Fake city names:', conn.execute(\"SELECT COUNT(*) FROM permits WHERE city IN ('Gas','Plumbing','Electrical','Mechanical','Building','Fire')\").fetchone()[0])
print('Houston OK:', conn.execute(\"SELECT COUNT(*) FROM permits WHERE city='Houston' AND state='OK'\").fetchone()[0])
print('prod_cities:', conn.execute('SELECT COUNT(*) FROM prod_cities').fetchone()[0])
print('us_cities:', conn.execute('SELECT COUNT(*) FROM us_cities').fetchone()[0])
print('=== DONE ===')
"

echo ""
echo "=========================================="
echo "V21 CLEANUP COMPLETE"
echo "=========================================="
