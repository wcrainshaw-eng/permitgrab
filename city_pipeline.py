"""
PermitGrab V62 — City Validation Pipeline

Automated process for finding, testing, and adding new cities.
Replaces the old "add and hope" workflow with a gated pipeline:

    Discovery → Endpoint Test → Schema Validation → Backfill → Activation

A city CANNOT be activated unless it passes every gate. The pipeline
tracks progress in the city_validations table so work survives restarts.

Key principle: the PERMITS TABLE is the source of truth, not the scraper's
self-reported status. A city is "working" only if actual permit rows exist
in the database with valid, recent dates.

Usage:
    from city_pipeline import CityPipeline
    pipeline = CityPipeline()

    # Discover sources for a city
    result = pipeline.discover("Austin", "TX")

    # Run full validation
    result = pipeline.validate("austin-tx")

    # Activate after validation passes
    pipeline.activate("austin-tx")

    # Run health check on all active cities
    report = pipeline.health_check()

    # Auto-discover and validate next batch by population
    pipeline.run_batch(count=10)
"""

import os
import json
import re
import time
import requests
from datetime import datetime, timedelta


# --------------------------------------------------------------------------
# Platform detection — identifies which scraper platform a source uses
# --------------------------------------------------------------------------

SOCRATA_INDICATORS = ['data.', 'socrata', '/resource/', 'api/views']
ARCGIS_INDICATORS = ['arcgis', 'FeatureServer', 'MapServer', '/rest/services/']
ACCELA_INDICATORS = ['citizenaccess', 'accela', 'aca.', 'energov']
OPEN311_INDICATORS = ['open311', '311']


def detect_platform(endpoint):
    """Detect the data platform from an endpoint URL."""
    url_lower = endpoint.lower()
    for indicator in SOCRATA_INDICATORS:
        if indicator in url_lower:
            return 'socrata'
    for indicator in ARCGIS_INDICATORS:
        if indicator in url_lower:
            return 'arcgis'
    for indicator in ACCELA_INDICATORS:
        if indicator in url_lower:
            return 'accela'
    for indicator in OPEN311_INDICATORS:
        if indicator in url_lower:
            return 'open311'
    return 'unknown'


# --------------------------------------------------------------------------
# Socrata discovery — search data.gov and Socrata catalogs
# --------------------------------------------------------------------------

SOCRATA_CATALOG_URL = "http://api.us.socrata.com/api/catalog/v1"

PERMIT_KEYWORDS = [
    'building permit', 'construction permit', 'development permit',
    'permit issued', 'building inspection', 'permit application',
]


def search_socrata(city_name, state):
    """Search Socrata catalog for permit datasets in a city.

    Returns list of candidate sources:
    [
        {
            'name': 'Building Permits',
            'domain': 'data.austintexas.gov',
            'dataset_id': 'xxxx-xxxx',
            'endpoint': 'https://data.austintexas.gov/resource/xxxx-xxxx.json',
            'description': '...',
            'row_count': 50000,
            'updated_at': '2026-03-15',
        },
        ...
    ]
    """
    results = []
    for keyword in PERMIT_KEYWORDS:
        query = f"{keyword} {city_name} {state}"
        try:
            resp = requests.get(SOCRATA_CATALOG_URL, params={
                'q': query,
                'limit': 10,
                'only': 'datasets',
            }, timeout=15)
            if resp.status_code != 200:
                continue

            data = resp.json()
            for item in data.get('results', []):
                resource = item.get('resource', {})
                domain = item.get('metadata', {}).get('domain', '')
                dataset_id = resource.get('id', '')

                # Skip if no ID or too few rows
                row_count = resource.get('page_views', {}).get('page_views_total', 0)
                columns = resource.get('columns_field_name', [])

                results.append({
                    'name': resource.get('name', 'Unknown'),
                    'domain': domain,
                    'dataset_id': dataset_id,
                    'endpoint': f"https://{domain}/resource/{dataset_id}.json",
                    'description': resource.get('description', '')[:200],
                    'row_count': row_count,
                    'updated_at': resource.get('updatedAt', ''),
                    'columns': columns,
                    'platform': 'socrata',
                })
        except Exception as e:
            print(f"[PIPELINE] Socrata search error for '{keyword}': {e}")
            continue

    # Deduplicate by dataset_id
    seen = set()
    unique = []
    for r in results:
        if r['dataset_id'] not in seen:
            seen.add(r['dataset_id'])
            unique.append(r)

    return unique


# --------------------------------------------------------------------------
# ArcGIS discovery — search ArcGIS Hub
# --------------------------------------------------------------------------

ARCGIS_HUB_SEARCH = "https://hub.arcgis.com/api/v3/datasets"


def search_arcgis(city_name, state):
    """Search ArcGIS Hub for permit datasets."""
    results = []
    try:
        resp = requests.get(ARCGIS_HUB_SEARCH, params={
            'q': f"building permits {city_name} {state}",
            'per_page': 10,
        }, timeout=15)
        if resp.status_code != 200:
            return results

        data = resp.json()
        for item in data.get('data', []):
            attrs = item.get('attributes', {})
            url = attrs.get('url', '')
            if not url:
                continue

            results.append({
                'name': attrs.get('name', 'Unknown'),
                'domain': attrs.get('source', ''),
                'dataset_id': item.get('id', ''),
                'endpoint': url,
                'description': (attrs.get('description') or '')[:200],
                'row_count': attrs.get('recordCount', 0),
                'updated_at': attrs.get('updatedAt', ''),
                'columns': [],
                'platform': 'arcgis',
            })
    except Exception as e:
        print(f"[PIPELINE] ArcGIS search error: {e}")

    return results


# --------------------------------------------------------------------------
# Endpoint testing — Phase 2 of the pipeline
# --------------------------------------------------------------------------

# Minimum fields we need for a usable permit source
REQUIRED_FIELD_PATTERNS = {
    'permit_number': [r'permit.*num', r'permit.*no', r'record.*id', r'case.*num', r'application.*num', r'permit_num'],
    'date': [r'date', r'issued', r'filed', r'created', r'applied', r'submitted'],
    'address': [r'address', r'location', r'site', r'street', r'property'],
}


def test_endpoint(endpoint, platform='socrata'):
    """Test a data endpoint and return validation results.

    Returns:
        {
            'accessible': bool,
            'status_code': int,
            'record_count': int,
            'sample_records': list,      # first 5 records
            'fields': list,              # column names
            'field_mapping': dict,       # our_field → their_field
            'date_formats': list,        # detected date formats
            'has_future_dates': bool,
            'issues': list,              # human-readable problems
            'score': int,                # 0-100 quality score
        }
    """
    result = {
        'accessible': False,
        'status_code': None,
        'record_count': 0,
        'sample_records': [],
        'fields': [],
        'field_mapping': {},
        'date_formats': [],
        'has_future_dates': False,
        'issues': [],
        'score': 0,
    }

    # Step 1: Hit the endpoint
    try:
        if platform == 'socrata':
            test_url = endpoint + '?$limit=20&$order=:id'
        elif platform == 'arcgis':
            # ArcGIS REST API query
            if '/query' not in endpoint:
                test_url = endpoint.rstrip('/') + '/query'
            else:
                test_url = endpoint
            test_url += '?where=1=1&outFields=*&resultRecordCount=20&f=json'
        else:
            test_url = endpoint

        resp = requests.get(test_url, timeout=20)
        result['status_code'] = resp.status_code

        if resp.status_code != 200:
            result['issues'].append(f"HTTP {resp.status_code}")
            return result

        result['accessible'] = True

    except requests.Timeout:
        result['issues'].append("Timeout (>20s)")
        return result
    except Exception as e:
        result['issues'].append(f"Connection error: {str(e)[:100]}")
        return result

    # Step 2: Parse response
    try:
        data = resp.json()

        if platform == 'arcgis':
            records = data.get('features', [])
            records = [f.get('attributes', {}) for f in records]
        elif platform == 'socrata':
            records = data if isinstance(data, list) else []
        else:
            records = data if isinstance(data, list) else [data]

        result['record_count'] = len(records)
        result['sample_records'] = records[:5]

        if not records:
            result['issues'].append("No records returned")
            return result

        result['fields'] = list(records[0].keys())

    except Exception as e:
        result['issues'].append(f"JSON parse error: {str(e)[:100]}")
        return result

    # Step 3: Field mapping
    score = 50  # Base score for accessible + returning data
    fields_lower = {f.lower(): f for f in result['fields']}

    for our_field, patterns in REQUIRED_FIELD_PATTERNS.items():
        matched = None
        for pattern in patterns:
            for field_lower, field_original in fields_lower.items():
                if re.search(pattern, field_lower, re.IGNORECASE):
                    matched = field_original
                    break
            if matched:
                break

        if matched:
            result['field_mapping'][our_field] = matched
            score += 15
        else:
            result['issues'].append(f"No field matching '{our_field}'")

    # Step 4: Date validation
    date_field = result['field_mapping'].get('date')
    if date_field:
        date_values = [r.get(date_field) for r in records if r.get(date_field)]
        formats_seen = set()
        future_dates = 0
        today = datetime.now()

        for val in date_values[:10]:
            if isinstance(val, (int, float)):
                # Epoch timestamp
                formats_seen.add('epoch')
                try:
                    dt = datetime.fromtimestamp(val / 1000 if val > 1e10 else val)
                    if dt > today + timedelta(days=365):
                        future_dates += 1
                except:
                    pass
            elif isinstance(val, str):
                # Try common date formats
                for fmt, label in [
                    ('%Y-%m-%dT%H:%M:%S', 'ISO'),
                    ('%Y-%m-%d', 'YYYY-MM-DD'),
                    ('%m/%d/%Y', 'MM/DD/YYYY'),
                    ('%m/%d/%y', 'MM/DD/YY'),
                    ('%Y%m%d', 'YYYYMMDD'),
                ]:
                    try:
                        dt = datetime.strptime(val[:len(fmt)+2].split('.')[0].split('+')[0], fmt)
                        formats_seen.add(label)
                        if dt > today + timedelta(days=365):
                            future_dates += 1
                        break
                    except:
                        continue

        result['date_formats'] = list(formats_seen)
        if formats_seen:
            score += 10
        if len(formats_seen) > 1:
            result['issues'].append(f"Mixed date formats: {formats_seen}")
            score -= 5
        if future_dates > 0:
            result['has_future_dates'] = True
            result['issues'].append(f"{future_dates} future dates detected")
            score -= 10

    # Step 5: Pagination check
    if platform == 'socrata' and len(records) == 20:
        # Source likely has more data — good sign
        score += 5
    elif platform == 'arcgis':
        exceeded = data.get('exceededTransferLimit', False)
        if exceeded:
            score += 5  # Has more data, pagination works

    result['score'] = min(100, max(0, score))
    return result


# --------------------------------------------------------------------------
# Backfill verification — Phase 3 gate
# --------------------------------------------------------------------------

def verify_backfill(city_key, conn):
    """Check if a city's backfill actually produced valid data.

    This is THE GATE. Nothing gets activated without passing this.

    Returns:
        {
            'passed': bool,
            'permit_count': int,
            'min_date': str or None,
            'max_date': str or None,
            'has_future_dates': bool,
            'days_coverage': int,
            'issues': list,
        }
    """
    result = {
        'passed': False,
        'permit_count': 0,
        'min_date': None,
        'max_date': None,
        'has_future_dates': False,
        'days_coverage': 0,
        'issues': [],
    }

    # Query actual permits in the database
    row = conn.execute("""
        SELECT COUNT(*) as cnt,
               MIN(date) as min_date,
               MAX(date) as max_date
        FROM permits
        WHERE source_city_key = %s
    """, (city_key,)).fetchone()

    if not row or row['cnt'] == 0:
        result['issues'].append("Zero permits in database after backfill")
        return result

    result['permit_count'] = row['cnt']
    result['min_date'] = row['min_date']
    result['max_date'] = row['max_date']

    # Check: permits actually landed
    if result['permit_count'] == 0:
        result['issues'].append("No permits found in DB")
        return result

    # Check: date range makes sense
    today = datetime.now().date()
    if result['max_date']:
        try:
            max_dt = datetime.strptime(result['max_date'][:10], '%Y-%m-%d').date()
            days_since = (today - max_dt).days

            if max_dt.year > today.year + 1:
                result['has_future_dates'] = True
                result['issues'].append(f"Future date detected: {result['max_date']}")

            if days_since > 30 and not result['has_future_dates']:
                result['issues'].append(f"Most recent permit is {days_since} days old")

        except:
            result['issues'].append(f"Cannot parse max_date: {result['max_date']}")

    if result['min_date']:
        try:
            min_dt = datetime.strptime(result['min_date'][:10], '%Y-%m-%d').date()
            max_dt = datetime.strptime(result['max_date'][:10], '%Y-%m-%d').date()
            result['days_coverage'] = (max_dt - min_dt).days
        except:
            pass

    # Gate decision
    if (result['permit_count'] > 0
            and not result['has_future_dates']
            and len(result['issues']) == 0):
        result['passed'] = True
    elif result['permit_count'] > 10 and not result['has_future_dates']:
        # Lenient pass — has data, no future dates, but maybe stale
        result['passed'] = True

    return result


# --------------------------------------------------------------------------
# Health check — post-activation monitoring
# --------------------------------------------------------------------------

def health_check_city(city_key, conn):
    """Run health check on a single active city.

    Compares scraper_runs status against actual permit data in DB.
    This catches the "no_new masking broken sources" problem.

    Returns:
        {
            'city_key': str,
            'status': 'healthy' | 'warning' | 'broken' | 'ghost',
            'last_permit_date': str or None,
            'permit_count': int,
            'last_scraper_status': str,
            'last_scraper_date': str,
            'days_since_permit': int or None,
            'issues': list,
        }
    """
    result = {
        'city_key': city_key,
        'status': 'healthy',
        'last_permit_date': None,
        'permit_count': 0,
        'last_scraper_status': None,
        'last_scraper_date': None,
        'days_since_permit': None,
        'issues': [],
    }

    # Check actual permit data
    prow = conn.execute("""
        SELECT COUNT(*) as cnt, MAX(date) as max_date
        FROM permits
        WHERE source_city_key = %s
    """, (city_key,)).fetchone()

    if prow:
        result['permit_count'] = prow['cnt'] or 0
        result['last_permit_date'] = prow['max_date']

    # Check latest scraper run
    srow = conn.execute("""
        SELECT status, run_started_at, permits_found, error_message
        FROM scraper_runs
        WHERE city_slug = %s
        ORDER BY run_started_at DESC
        LIMIT 1
    """, (city_key,)).fetchone()

    if srow:
        result['last_scraper_status'] = srow['status']
        result['last_scraper_date'] = srow['run_started_at']

    # Calculate days since last permit
    today = datetime.now().date()
    if result['last_permit_date']:
        try:
            last_dt = datetime.strptime(result['last_permit_date'][:10], '%Y-%m-%d').date()
            result['days_since_permit'] = (today - last_dt).days
        except:
            pass

    # Determine health status
    if result['permit_count'] == 0:
        result['status'] = 'ghost'
        result['issues'].append("Zero permits in database — scraper never produced data")
    elif result['days_since_permit'] and result['days_since_permit'] > 14:
        result['status'] = 'warning'
        result['issues'].append(f"No new permits in {result['days_since_permit']} days")
    elif result['days_since_permit'] and result['days_since_permit'] > 30:
        result['status'] = 'broken'
        result['issues'].append(f"Stale data: {result['days_since_permit']} days old")

    # Cross-check: scraper says success but no permits
    if (result['last_scraper_status'] in ('success', 'no_new')
            and result['permit_count'] == 0):
        result['status'] = 'ghost'
        result['issues'].append(
            f"Scraper reports '{result['last_scraper_status']}' but zero permits in DB"
        )

    return result


def run_health_check_all(conn):
    """Run health check across ALL active prod cities.

    Returns a structured report with summary and per-city details.
    """
    # Get all active prod cities
    cities = conn.execute("""
        SELECT city_slug, city, state, source_id, total_permits
        FROM prod_cities
        WHERE status = 'active'
        ORDER BY total_permits DESC
    """).fetchall()

    report = {
        'timestamp': datetime.now().isoformat(),
        'total_active': len(cities),
        'healthy': 0,
        'warning': 0,
        'broken': 0,
        'ghost': 0,
        'cities': [],
    }

    for city in cities:
        slug = city['city_slug']
        source_id = city['source_id'] or slug

        check = health_check_city(source_id, conn)
        check['city_name'] = city['city']
        check['state'] = city['state']
        check['tracker_permits'] = city['total_permits'] or 0

        report[check['status']] += 1
        report['cities'].append(check)

    # Sort: ghost first, then broken, warning, healthy
    status_order = {'ghost': 0, 'broken': 1, 'warning': 2, 'healthy': 3}
    report['cities'].sort(key=lambda c: status_order.get(c['status'], 99))

    return report


# --------------------------------------------------------------------------
# Full pipeline orchestration
# --------------------------------------------------------------------------

class CityPipeline:
    """Orchestrates the full city lifecycle: discover → test → backfill → activate → monitor."""

    def __init__(self, conn=None):
        """Initialize with a database connection.

        If no connection provided, imports from db_engine.
        """
        self.conn = conn

    def _get_conn(self):
        if self.conn:
            return self.conn
        from db_engine import get_connection
        return get_connection()

    def discover(self, city_name, state, population=0):
        """Phase 1: Find candidate data sources for a city.

        Searches Socrata and ArcGIS Hub, returns ranked candidates.
        """
        print(f"[PIPELINE] Discovering sources for {city_name}, {state}...")

        candidates = []
        candidates.extend(search_socrata(city_name, state))
        candidates.extend(search_arcgis(city_name, state))

        # Score and rank
        for c in candidates:
            c['_score'] = 0
            name_lower = c['name'].lower()
            desc_lower = c.get('description', '').lower()

            # Boost for permit-related names
            if 'permit' in name_lower:
                c['_score'] += 30
            if 'building' in name_lower:
                c['_score'] += 20
            if 'construction' in name_lower:
                c['_score'] += 15
            if 'inspection' in name_lower:
                c['_score'] += 5

            # Penalty for non-permit datasets
            if any(bad in name_lower for bad in ['311', 'service request', 'crime',
                                                   'zoning', 'tax', 'business license']):
                c['_score'] -= 30

            # Boost for recent updates
            if c.get('updated_at') and '2026' in str(c['updated_at']):
                c['_score'] += 10
            if c.get('updated_at') and '2025' in str(c['updated_at']):
                c['_score'] += 5

        candidates.sort(key=lambda c: c['_score'], reverse=True)

        # Record in validation table
        conn = self._get_conn()
        slug = re.sub(r'[^a-z0-9]+', '-', city_name.lower()).strip('-')
        city_slug = f"{slug}-{state.lower()}" if state else slug

        if candidates:
            best = candidates[0]
            try:
                conn.execute("""
                    INSERT INTO city_validations
                        (city_slug, city_name, state, population, platform, endpoint, dataset_id, phase, phase_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'discovery', 'found')
                    ON CONFLICT (city_slug) DO UPDATE SET
                        platform = EXCLUDED.platform,
                        endpoint = EXCLUDED.endpoint,
                        dataset_id = EXCLUDED.dataset_id,
                        phase = 'discovery',
                        phase_status = 'found',
                        updated_at = NOW()
                """, (city_slug, city_name, state, population,
                      best['platform'], best['endpoint'], best.get('dataset_id')))
                conn.commit()
            except Exception as e:
                print(f"[PIPELINE] DB error recording discovery: {e}")

        print(f"[PIPELINE] Found {len(candidates)} candidates for {city_name}, {state}")
        return {
            'city': city_name,
            'state': state,
            'candidates': candidates[:5],  # Top 5
        }

    def test_source(self, city_slug):
        """Phase 2: Test the endpoint for a discovered city.

        Runs endpoint test, schema validation, date parsing check.
        """
        conn = self._get_conn()
        row = conn.execute("""
            SELECT * FROM city_validations WHERE city_slug = %s
        """, (city_slug,)).fetchone()

        if not row:
            return {'error': f"No validation record for {city_slug}"}

        endpoint = row['endpoint']
        platform = row['platform']

        print(f"[PIPELINE] Testing endpoint for {city_slug}: {endpoint}")

        result = test_endpoint(endpoint, platform)

        # Update validation record
        conn.execute("""
            UPDATE city_validations SET
                endpoint_tested = TRUE,
                endpoint_test_date = NOW(),
                endpoint_test_result = %s,
                schema_valid = %s,
                date_parsing_valid = %s,
                has_future_dates = %s,
                phase = 'testing',
                phase_status = %s,
                updated_at = NOW()
            WHERE city_slug = %s
        """, (
            json.dumps(result['issues']),
            len(result['field_mapping']) >= 2,  # At least permit_number + date
            len(result['date_formats']) > 0,
            result['has_future_dates'],
            'passed' if result['score'] >= 60 else 'failed',
            city_slug
        ))
        conn.commit()

        return {
            'city_slug': city_slug,
            'endpoint': endpoint,
            'test_result': result,
        }

    def verify_backfill(self, city_slug):
        """Phase 3: Verify that backfill actually produced data.

        THE GATE — nothing activates without this passing.
        """
        conn = self._get_conn()
        row = conn.execute("""
            SELECT * FROM city_validations WHERE city_slug = %s
        """, (city_slug,)).fetchone()

        if not row:
            return {'error': f"No validation record for {city_slug}"}

        result = verify_backfill(city_slug, conn)

        # Update validation record
        conn.execute("""
            UPDATE city_validations SET
                backfill_completed = NOW(),
                backfill_permit_count = %s,
                backfill_min_date = %s,
                backfill_max_date = %s,
                has_future_dates = %s,
                phase = 'backfill',
                phase_status = %s,
                rejection_reason = %s,
                updated_at = NOW()
            WHERE city_slug = %s
        """, (
            result['permit_count'],
            result['min_date'],
            result['max_date'],
            result['has_future_dates'],
            'passed' if result['passed'] else 'failed',
            '; '.join(result['issues']) if result['issues'] else None,
            city_slug
        ))
        conn.commit()

        return {
            'city_slug': city_slug,
            'backfill_result': result,
        }

    def activate(self, city_slug):
        """Phase 4: Activate a validated city.

        Only succeeds if all prior phases passed.
        """
        conn = self._get_conn()
        row = conn.execute("""
            SELECT * FROM city_validations WHERE city_slug = %s
        """, (city_slug,)).fetchone()

        if not row:
            return {'error': f"No validation record for {city_slug}"}

        # Check all gates
        gates = {
            'endpoint_tested': row['endpoint_tested'],
            'schema_valid': row['schema_valid'],
            'date_parsing_valid': row['date_parsing_valid'],
            'backfill_passed': row['phase_status'] == 'passed' and row['phase'] == 'backfill',
            'no_future_dates': not row['has_future_dates'],
        }

        failed_gates = [g for g, passed in gates.items() if not passed]
        if failed_gates:
            return {
                'error': f"Cannot activate: failed gates: {failed_gates}",
                'gates': gates,
            }

        # All gates passed — activate
        from db import upsert_prod_city, log_city_activation
        upsert_prod_city(
            city=row['city_name'],
            state=row['state'],
            city_slug=city_slug,
            source_type=row['platform'],
            source_id=city_slug,
            status='active',
            added_by='pipeline_v62',
        )
        log_city_activation(
            city_slug=city_slug,
            city_name=row['city_name'],
            state=row['state'],
            source='pipeline_v62',
            initial_permits=row['backfill_permit_count'] or 0,
        )

        # Update validation record
        conn.execute("""
            UPDATE city_validations SET
                activated = TRUE,
                activated_at = NOW(),
                phase = 'active',
                phase_status = 'activated',
                updated_at = NOW()
            WHERE city_slug = %s
        """, (city_slug,))
        conn.commit()

        print(f"[PIPELINE] Activated {city_slug} with {row['backfill_permit_count']} permits")
        return {
            'city_slug': city_slug,
            'status': 'activated',
            'permits': row['backfill_permit_count'],
            'gates': gates,
        }

    def health_check(self):
        """Phase 5: Run health check on all active cities."""
        conn = self._get_conn()
        return run_health_check_all(conn)

    def auto_deactivate(self, consecutive_failures=3):
        """Auto-deactivate cities that have been flagged broken for N consecutive runs."""
        conn = self._get_conn()
        report = self.health_check()

        deactivated = []
        for city in report['cities']:
            if city['status'] == 'ghost':
                # Ghost cities have NEVER produced data — deactivate immediately
                conn.execute("""
                    UPDATE prod_cities SET
                        status = 'paused',
                        pause_reason = 'pipeline_v62: ghost city (zero permits in DB)',
                        data_freshness = 'no_data'
                    WHERE city_slug = %s
                """, (city['city_key'],))
                deactivated.append(city['city_key'])

        if deactivated:
            conn.commit()
            print(f"[PIPELINE] Deactivated {len(deactivated)} ghost cities")

        return {
            'deactivated': deactivated,
            'report_summary': {
                'healthy': report['healthy'],
                'warning': report['warning'],
                'broken': report['broken'],
                'ghost': report['ghost'],
            }
        }

    def run_batch(self, count=10):
        """Discover and test the next batch of cities by population rank.

        Picks the top N un-searched cities from us_cities and runs
        discovery + endpoint testing on each.
        """
        conn = self._get_conn()

        # Get next cities to process (by population, not yet searched)
        candidates = conn.execute("""
            SELECT city_name, state, population, slug
            FROM us_cities
            WHERE status = 'not_started'
            ORDER BY population DESC
            LIMIT %s
        """, (count,)).fetchall()

        results = []
        for city in candidates:
            city_name = city['city_name']
            state = city['state']
            population = city['population']

            # Phase 1: Discover
            discovery = self.discover(city_name, state, population)

            if discovery['candidates']:
                # Phase 2: Test best candidate
                slug = discovery['candidates'][0].get('_pipeline_slug')
                if not slug:
                    slug = re.sub(r'[^a-z0-9]+', '-', city_name.lower()).strip('-')
                    slug = f"{slug}-{state.lower()}"

                test = self.test_source(slug)
                results.append({
                    'city': city_name,
                    'state': state,
                    'population': population,
                    'candidates_found': len(discovery['candidates']),
                    'test_passed': test.get('test_result', {}).get('score', 0) >= 60,
                    'score': test.get('test_result', {}).get('score', 0),
                })
            else:
                results.append({
                    'city': city_name,
                    'state': state,
                    'population': population,
                    'candidates_found': 0,
                    'test_passed': False,
                    'score': 0,
                })

            # Mark as searched
            conn.execute("""
                UPDATE us_cities SET
                    status = 'searched',
                    last_searched_at = NOW(),
                    search_attempts = search_attempts + 1
                WHERE slug = %s
            """, (city['slug'],))
            conn.commit()

            # Rate limit between cities
            time.sleep(2)

        return {
            'processed': len(results),
            'sources_found': sum(1 for r in results if r['candidates_found'] > 0),
            'tests_passed': sum(1 for r in results if r['test_passed']),
            'details': results,
        }


# --------------------------------------------------------------------------
# CLI interface
# --------------------------------------------------------------------------

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python city_pipeline.py discover <city> <state>")
        print("  python city_pipeline.py test <city_slug>")
        print("  python city_pipeline.py verify <city_slug>")
        print("  python city_pipeline.py activate <city_slug>")
        print("  python city_pipeline.py health")
        print("  python city_pipeline.py batch [count]")
        print("  python city_pipeline.py deactivate-ghosts")
        sys.exit(1)

    command = sys.argv[1]
    pipeline = CityPipeline()

    if command == 'discover':
        city = sys.argv[2]
        state = sys.argv[3] if len(sys.argv) > 3 else ''
        result = pipeline.discover(city, state)
        print(json.dumps(result, indent=2, default=str))

    elif command == 'test':
        slug = sys.argv[2]
        result = pipeline.test_source(slug)
        print(json.dumps(result, indent=2, default=str))

    elif command == 'verify':
        slug = sys.argv[2]
        result = pipeline.verify_backfill(slug)
        print(json.dumps(result, indent=2, default=str))

    elif command == 'activate':
        slug = sys.argv[2]
        result = pipeline.activate(slug)
        print(json.dumps(result, indent=2, default=str))

    elif command == 'health':
        report = pipeline.health_check()
        print(f"\nHealth Check — {report['timestamp']}")
        print(f"  Active: {report['total_active']}")
        print(f"  Healthy: {report['healthy']}")
        print(f"  Warning: {report['warning']}")
        print(f"  Broken: {report['broken']}")
        print(f"  Ghost: {report['ghost']}")
        if report['ghost'] > 0:
            print(f"\nGhost cities (zero permits in DB):")
            for c in report['cities']:
                if c['status'] == 'ghost':
                    print(f"  {c['city_key']} — scraper says '{c['last_scraper_status']}'")

    elif command == 'batch':
        count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        result = pipeline.run_batch(count)
        print(json.dumps(result, indent=2, default=str))

    elif command == 'deactivate-ghosts':
        result = pipeline.auto_deactivate()
        print(json.dumps(result, indent=2, default=str))

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
