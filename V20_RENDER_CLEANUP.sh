#!/bin/bash
# V20 PRODUCTION CLEANUP - Run this in Render Shell
# Access: Render Dashboard → permitgrab → Shell tab

echo "=========================================="
echo "V20 PRODUCTION DATABASE CLEANUP"
echo "=========================================="

# STEP 1: Check current state
echo ""
echo "STEP 1: Checking current state..."
python3 -c "
import db; db.init_db(); conn = db.get_connection()
total = conn.execute('SELECT COUNT(*) FROM permits').fetchone()[0]
garbage = conn.execute(\"SELECT COUNT(*) FROM permits WHERE city GLOB '[0-9]*'\").fetchone()[0]
calgary = conn.execute(\"SELECT COUNT(*) FROM permits WHERE city='Calgary'\").fetchone()[0]
dupes = conn.execute('''SELECT SUM(cnt-1) FROM (SELECT COUNT(*) as cnt FROM permits WHERE address IS NOT NULL AND address != '' AND address != 'Address not provided' GROUP BY address,city,state,filing_date HAVING COUNT(*)>1)''').fetchone()[0] or 0
zero = conn.execute(\"SELECT COUNT(*) FROM prod_cities WHERE status='active' AND (total_permits IS NULL OR total_permits = 0)\").fetchone()[0]
print(f'BEFORE CLEANUP:')
print(f'  Total permits:        {total:>10}')
print(f'  Garbage city names:   {garbage:>10}')
print(f'  Calgary permits:      {calgary:>10}')
print(f'  Duplicate excess:     {dupes:>10}')
print(f'  Zero-permit cities:   {zero:>10}')
"

# STEP 2: Delete garbage city names
echo ""
echo "STEP 2: Deleting garbage city names (numeric cities)..."
python3 -c "
import db; db.init_db(); conn = db.get_connection()
d = conn.execute(\"DELETE FROM permits WHERE city GLOB '[0-9]*'\").rowcount
conn.commit()
print(f'  Deleted {d} garbage city rows')
"

# STEP 3: Fix Orlando neighborhoods
echo ""
echo "STEP 3: Fixing Orlando neighborhoods..."
python3 -c "
import db; db.init_db(); conn = db.get_connection()
hoods = ['Lake Nona South','Lake Nona Central','Lake Nona Estates','Vista Park','Vista East','College Park','Meridian Park','Florida Center','Florida Center North','Southeastern Oaks','Central Business District','Johnson Village','33Rd St. Industrial','Airport North','Metro West','Doctor Phillips','Windermere','Meadow Woods','Pine Hills','Union Park','Azalea Park','Conway','Holden Heights','Tangelo Park','Sky Lake','Rio Grande Park','Signal Hill','South Apopka','Taft','Oak Ridge','Edgewood','Rosemont','Rosemont North','Audubon Park','Holden/Parramore','Colonialtown South','Colonial Town Center','Lake Eola Heights','Lake Fairview','Lake Davis/Greenwood','Lake Terrace','Lake Underhill','Lake Como','Lake Cherokee','Lake Formosa','Lake Sunset','Lake Copeland','Lake Mann Estates','Lake Weldona','Park Lake/Highland','Spring Lake','Clear Lake','Kirkman North','Kirkman South','Mercy Drive','Boggy Creek','Storey Park','Randal Park','Northlake Park At Lake Nona','Sunbridge/Icp','Dover Shores West','Dover Shores East','Dover Estates','Dover Manor','Rose Isle','Pineloch','Princeton/Silver Star','Milk District','Thornton Park','South Eola','Delaney Park','Lorna Doone','Pershing','Catalina','Bryn Mawr','Monterey','Rock Lake','Windhover','Carver Shores','North Quarter','Southern Oaks','South Semoran','Lancaster Park','Rowena Gardens','Lawsona/Fern Creek','Dixie Belle','Malibu Groves','Southport','Bel Air','Richmond Heights','Richmond Estates','Engelwood Park','Wadeview Park','Orwin Manor','Ventura','Bal Bay','Crescent Park','Timberleaf','Countryside','Mariners Village','Orlando Executive Airport','Orlando International Airport','Seaboard Industrial','Beltway Commerce Center','Palomar','East Park','West Colonial','North Orange','South Orange','Lavina']
t = 0
for h in hoods:
    u = conn.execute('UPDATE permits SET city=\"Orlando\" WHERE city=? AND state=\"FL\"', (h,)).rowcount
    t += u
conn.commit()
print(f'  Updated {t} rows to Orlando')
"

# STEP 4: Delete Calgary and invalid states
echo ""
echo "STEP 4: Deleting non-US data..."
python3 -c "
import db; db.init_db(); conn = db.get_connection()
d1 = conn.execute(\"DELETE FROM permits WHERE city='Calgary'\").rowcount
valid = ['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC','PR']
ph = ','.join(['?' for _ in valid])
d2 = conn.execute(f'DELETE FROM permits WHERE state NOT IN ({ph}) AND state IS NOT NULL AND state != \"\"', valid).rowcount
conn.commit()
print(f'  Deleted {d1} Calgary + {d2} invalid-state rows')
"

# STEP 5: Deduplicate permits
echo ""
echo "STEP 5: Deduplicating permits (this may take a minute)..."
python3 -c "
import db; db.init_db(); conn = db.get_connection()
before = conn.execute('SELECT COUNT(*) FROM permits').fetchone()[0]
conn.execute('''DELETE FROM permits WHERE rowid NOT IN (SELECT MIN(rowid) FROM permits GROUP BY address, city, state, filing_date) AND address IS NOT NULL AND address != '' AND address != 'Address not provided' ''')
conn.commit()
after = conn.execute('SELECT COUNT(*) FROM permits').fetchone()[0]
print(f'  Before: {before}, After: {after}, Removed: {before - after}')
"

# STEP 6: Fix zero-permit active cities
echo ""
echo "STEP 6: Fixing zero-permit active cities..."
python3 -c "
import db; db.init_db(); conn = db.get_connection()
u = conn.execute(\"UPDATE prod_cities SET status='pending' WHERE status='active' AND (total_permits IS NULL OR total_permits = 0)\").rowcount
conn.commit()
print(f'  Set {u} zero-permit cities to pending')
"

# STEP 7: Delete FL neighborhood entries from prod_cities
echo ""
echo "STEP 7: Removing FL neighborhood entries from prod_cities..."
python3 -c "
import db; db.init_db(); conn = db.get_connection()
hoods = ['College Park','North Orange','Orlando International Airport','Airport North','Vista East','Rosemont North','Florida Center North','Wadeview Park','Engelwood Park','Lake Como','Lake Nona South','Vista Park','Meridian Park','Florida Center','Southeastern Oaks','Central Business District','Johnson Village','33Rd St. Industrial','Metro West','Doctor Phillips','Windermere','Meadow Woods','Pine Hills','Union Park','Azalea Park','Conway','Holden Heights','Tangelo Park','Sky Lake','Rio Grande Park','Signal Hill','South Apopka','Taft','Oak Ridge','Edgewood','Rosemont','Lake Nona Central','Lake Nona Estates','Lavina']
t = 0
for h in hoods:
    d = conn.execute('DELETE FROM prod_cities WHERE city=? AND state=\"FL\"', (h,)).rowcount
    t += d
conn.commit()
print(f'  Deleted {t} FL neighborhood entries')
"

# STEP 8: Recalculate stats
echo ""
echo "STEP 8: Recalculating permit stats..."
python3 -c "
import db; db.init_db(); conn = db.get_connection()
cities = conn.execute('SELECT city, state, city_slug FROM prod_cities WHERE status=\"active\"').fetchall()
for c in cities:
    t = conn.execute('SELECT COUNT(*) FROM permits WHERE city=? AND state=?', (c[0], c[1])).fetchone()[0]
    l = conn.execute(\"SELECT COUNT(*) FROM permits WHERE city=? AND state=? AND filing_date >= date('now', '-30 days')\", (c[0], c[1])).fetchone()[0]
    conn.execute('UPDATE prod_cities SET total_permits=?, permits_last_30d=? WHERE city_slug=?', (t, l, c[2]))
conn.commit()
print(f'  Recalculated stats for {len(cities)} cities')
"

# STEP 9: Check us_cities seeding
echo ""
echo "STEP 9: Checking us_cities seeding..."
python3 -c "
import db; db.init_db()
c = db.get_connection().execute('SELECT COUNT(*) FROM us_cities').fetchone()[0]
print(f'  us_cities: {c} rows')
if c == 0:
    print('  WARNING: us_cities empty! Run these commands:')
    print('    python3 seed_us_cities.py')
    print('    python3 seed_us_counties.py')
    print('    python3 backfill_coverage.py')
else:
    print('  OK - already seeded')
"

# STEP 10: Final verification
echo ""
echo "=========================================="
echo "STEP 10: FINAL VERIFICATION"
echo "=========================================="
python3 -c "
import db; db.init_db(); conn = db.get_connection()
total = conn.execute('SELECT COUNT(*) FROM permits').fetchone()[0]
garbage = conn.execute(\"SELECT COUNT(*) FROM permits WHERE city GLOB '[0-9]*'\").fetchone()[0]
calgary = conn.execute(\"SELECT COUNT(*) FROM permits WHERE city='Calgary'\").fetchone()[0]
dupes = conn.execute('''SELECT SUM(cnt-1) FROM (SELECT COUNT(*) as cnt FROM permits WHERE address IS NOT NULL AND address != '' AND address != 'Address not provided' GROUP BY address,city,state,filing_date HAVING COUNT(*)>1)''').fetchone()[0] or 0
zero = conn.execute(\"SELECT COUNT(*) FROM prod_cities WHERE status='active' AND (total_permits IS NULL OR total_permits = 0)\").fetchone()[0]
print(f'AFTER CLEANUP:')
print(f'  Total permits:        {total:>10}')
print(f'  Garbage city names:   {garbage:>10}  (target: 0)')
print(f'  Calgary permits:      {calgary:>10}  (target: 0)')
print(f'  Duplicate excess:     {dupes:>10}  (target: 0)')
print(f'  Zero-permit cities:   {zero:>10}  (target: 0)')
if garbage == 0 and calgary == 0 and dupes == 0 and zero == 0:
    print('')
    print('ALL CHECKS PASSED!')
else:
    print('')
    print('SOME CHECKS FAILED - review above')
"

echo ""
echo "=========================================="
echo "CLEANUP COMPLETE"
echo "=========================================="
echo "Verify live site:"
echo "  - https://permitgrab.com/ (no duplicates, no garbage cities)"
echo "  - https://permitgrab.com/api/health (should return JSON)"
echo "  - https://permitgrab.com/permits/los-angeles (should have permits)"
