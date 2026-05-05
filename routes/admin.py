"""V471 PR2 (CODE_V471 Part 1B): admin blueprint extracted from server.py.

Routes: 96 URLs across 95 handlers.

Helpers/globals from server.py are accessed via `from server import *`,
which imports everything server.py defined before this blueprint was loaded
(blueprints are registered at the bottom of server.py, after all globals
are set). Underscored helpers are listed below explicitly because `import *`
skips names starting with `_`.

V471 PR2-prep moved daemon spawn + DB init out of module-level code, so
the worker can re-import server.py cleanly during fork or self-recycle
without racing this `from server import *` against the import lock.
"""
from flask import Blueprint, request, jsonify, render_template, session, redirect, abort, Response, g, url_for, send_from_directory
from datetime import datetime, timedelta
import os, json, time, re, threading, random, string, hashlib, hmac
from werkzeug.security import generate_password_hash, check_password_hash

# Pull in server.py's helpers, models, and globals.
from server import *
import server as _s

admin_bp = Blueprint('admin', __name__)


from server import _LICENSE_IMPORT_IN_FLIGHT, _LICENSE_IMPORT_LOCK, _collect_city_sync, _collector_started, _collectors_manually_started, _get_property_owners, _log_digest, _startup_done

@admin_bp.route('/api/admin/collect-v122', methods=['POST'])
def admin_collect_v122():
    """V122: Run collection from the cities table with per-city insert."""
    valid, error = check_admin_key()
    if not valid:
        return error
    data = request.json or {}
    days_back = data.get('days_back', 7)
    include_scrapers = data.get('include_scrapers', True)

    def run():
        try:
            from collector import collect_v122
            collect_v122(days_back=days_back, include_scrapers=include_scrapers)
        except Exception as e:
            print(f"V122 collection error: {e}", flush=True)
            import traceback
            traceback.print_exc()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'days_back': days_back, 'include_scrapers': include_scrapers}), 200


@admin_bp.route('/api/admin/force-collection', methods=['POST'])
def admin_force_collection():
    """V64: Force collection — runs ALL platforms, supports filtering.

    JSON body:
      days_back: int (default 7, max 90)
      platform: str (optional — filter to one platform: socrata, arcgis, ckan, carto, accela)
      city_slug: str (optional — run a single city only)
      include_scrapers: bool (default false — run Accela/Playwright scrapers too)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    data = request.json or {}
    days_back = min(int(data.get('days_back', 7)), 90)
    platform_filter = data.get('platform')
    city_slug = data.get('city_slug')
    include_scrapers = data.get('include_scrapers', True)  # V74: Default to True so Accela/CKAN get collected

    if city_slug:
        # Synchronous single-city mode (fast enough)
        try:
            from collector import collect_single_city
            result = collect_single_city(city_slug, days_back=days_back)
            return jsonify({
                'mode': 'single_city',
                'city_slug': city_slug,
                'days_back': days_back,
                'result': result
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        # Background thread for full/filtered collection.
        # V229 A3: renamed from run_collection for clarity (3 inner functions
        # with the same name across this module — shadowing-safe because each
        # is scoped to its enclosing handler, but the name reuse made grep
        # noisy and obscured intent).
        def _run_refresh_collection():
            try:
                from collector import collect_refresh
                print(f"[Admin] Starting REFRESH collection (platform={platform_filter}, scrapers={include_scrapers})...")
                collect_refresh(
                    days_back=days_back,
                    platform_filter=platform_filter,
                    include_scrapers=include_scrapers
                )
                print("[Admin] Refresh collection complete.")
            except Exception as e:
                print(f"[Admin] Collection error: {e}")
                import traceback
                traceback.print_exc()

        thread = threading.Thread(target=_run_refresh_collection, daemon=True)
        thread.start()

        return jsonify({
            'message': 'REFRESH collection started',
            'mode': 'background',
            'days_back': days_back,
            'platform_filter': platform_filter,
            'include_scrapers': include_scrapers,
            'note': 'V64: Supports all platforms, check logs for progress'
        })


@admin_bp.route('/api/admin/full-collection', methods=['POST'])
def admin_full_collection():
    """V12.50: Trigger FULL collection (rebuild SQLite)."""
    valid, error = check_admin_key()
    if not valid:
        return error

    # V229 A3: renamed from run_collection (see _run_refresh_collection above)
    def _run_full_collection():
        try:
            from collector import collect_full
            print("[Admin] Starting FULL collection (rebuild mode)...")
            collect_full(days_back=365)
            print("[Admin] Full collection complete.")
        except Exception as e:
            print(f"[Admin] Full collection error: {e}")

    thread = threading.Thread(target=_run_full_collection, daemon=True)
    thread.start()

    return jsonify({
        'message': 'FULL collection started (rebuild mode)',
        'note': 'V12.50: Rebuilds SQLite database. Takes 30-60 minutes.'
    })


@admin_bp.route('/api/admin/force-collect', methods=['GET', 'POST'])
def admin_force_collect():
    """V226 T2: Structured single-city collection with before/after diff.

    POST body: {"city_slug": "chicago", "type": "permits"|"violations"|"both"}
    GET: /api/admin/force-collect?city=chicago&type=both
    Returns inserted/total/newest deltas + elapsed + errors.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    if request.method == 'POST':
        data = request.get_json() or {}
        city_slug = data.get('city_slug') or data.get('city')
        collect_type = data.get('type', 'both')
        days_back = data.get('days_back')
    else:
        city_slug = request.args.get('city') or request.args.get('city_slug')
        collect_type = request.args.get('type', 'both')
        days_back = request.args.get('days_back')

    if not city_slug:
        return jsonify({'error': 'city_slug required'}), 400
    if collect_type not in ('permits', 'violations', 'both'):
        return jsonify({'error': 'type must be permits/violations/both'}), 400
    # V233b: accept optional days_back override. Default None lets
    # _collect_city_sync auto-select 180 for pending-backfill cities
    # and 7 for everything else.
    if days_back is not None:
        try:
            days_back = min(max(int(days_back), 1), 730)
        except (TypeError, ValueError):
            return jsonify({'error': 'days_back must be an integer'}), 400

    # V234 P2: async mode for long-running backfills. Render kills the
    # HTTP connection at 30s; a 180-day force-collect can take several
    # minutes. If the caller explicitly asks (?async=true) or passes a
    # days_back >= 30 (implying backfill), spawn a background thread
    # and return immediately. Short incremental calls stay synchronous
    # so Cowork's tooling gets the before/after diff in one response.
    if request.method == 'POST':
        _async_flag = data.get('async')
    else:
        _async_flag = request.args.get('async')
    _want_async = (
        str(_async_flag).lower() in ('1', 'true', 'yes')
        or (days_back is not None and days_back >= 30)
    )
    if _want_async:
        import uuid as _uuid
        job_id = _uuid.uuid4().hex[:12]
        def _bg_collect():
            try:
                result = _collect_city_sync(city_slug, collect_type, days_back=days_back)
                print(f"[V234] force-collect job {job_id} done: {result}", flush=True)
            except Exception as _e:
                import traceback as _tb
                print(f"[V234] force-collect job {job_id} error: {_e}", flush=True)
                _tb.print_exc()
        threading.Thread(target=_bg_collect, daemon=True,
                         name=f'force_collect_{city_slug}').start()
        return jsonify({
            'status': 'started',
            'async': True,
            'job_id': job_id,
            'city_slug': city_slug,
            'type': collect_type,
            'days_back': days_back or 'auto',
            'message': 'running in background — check collection_log / scraper_runs for status',
        }), 202

    try:
        result = _collect_city_sync(city_slug, collect_type, days_back=days_back)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'city_slug': city_slug}), 500


@admin_bp.route('/api/admin/city-health', methods=['GET'])
def admin_city_health():
    """V226 T3: One-glance health status for every active top-cities entry.

    GREEN  = newest permit < 7d AND enrichment >= 40% (or no profiles)
    YELLOW = newest permit 7-21d OR enrichment 20-40%
    RED    = newest permit > 21d OR 0 permits OR enrichment < 20% (when profiles > 50)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    conn = permitdb.get_connection()
    top_slugs = [
        'new-york-city','los-angeles','chicago','houston','phoenix',
        'philadelphia','dallas','austin','san-antonio','san-diego',
        'nashville','seattle','san-francisco','fort-worth','columbus',
        'denver','memphis','new-orleans','milwaukee','san-jose',
    ]
    placeholders = ','.join('?' * len(top_slugs))
    rows = conn.execute(f"""
        SELECT pc.city_slug, pc.state, pc.status,
               COALESCE(p.cnt, 0) as permits,
               p.newest as newest_permit,
               COALESCE(v.cnt, 0) as violations,
               v.newest as newest_violation,
               COALESCE(cp.cnt, 0) as profiles,
               COALESCE(cp.enriched, 0) as enriched,
               pc.last_collection
        FROM prod_cities pc
        LEFT JOIN (
            SELECT source_city_key, COUNT(*) cnt,
                   MAX(COALESCE(NULLIF(filing_date,''),NULLIF(issued_date,''),NULLIF(date,''))) newest
            FROM permits GROUP BY source_city_key
        ) p ON p.source_city_key = pc.city_slug
        LEFT JOIN (
            SELECT prod_city_id, COUNT(*) cnt, MAX(violation_date) newest
            FROM violations GROUP BY prod_city_id
        ) v ON v.prod_city_id = pc.id
        LEFT JOIN (
            SELECT source_city_key, COUNT(*) cnt,
                   SUM(CASE WHEN (phone IS NOT NULL AND phone != '') OR (website IS NOT NULL AND website != '')
                            THEN 1 ELSE 0 END) enriched
            FROM contractor_profiles GROUP BY source_city_key
        ) cp ON cp.source_city_key = pc.city_slug
        WHERE pc.city_slug IN ({placeholders})
        ORDER BY permits DESC
    """, top_slugs).fetchall()

    from datetime import datetime as _dt, timedelta as _td
    today = _dt.utcnow().date()
    summary = {'green': 0, 'yellow': 0, 'red': 0}
    cities = []
    for r in rows:
        row = {k: r[k] for k in r.keys()}
        newest = (row.get('newest_permit') or '')[:10]
        age = None
        if newest and len(newest) == 10:
            try:
                age = (today - _dt.strptime(newest, '%Y-%m-%d').date()).days
            except Exception:
                age = None
        row['permit_age_days'] = age
        pct = 0
        if row['profiles']:
            pct = round(100.0 * row['enriched'] / row['profiles'], 1)
        row['enrichment_pct'] = pct

        # Health scoring
        if row['status'] != 'active':
            health = 'PAUSED'
        elif age is None or row['permits'] == 0 or age > 21:
            health = 'RED'
        elif row['profiles'] > 50 and pct < 20:
            health = 'RED'
        elif age > 7 or (row['profiles'] > 50 and pct < 40):
            health = 'YELLOW'
        else:
            health = 'GREEN'
        row['health'] = health
        if health in summary:
            summary[health.lower()] = summary.get(health.lower(), 0) + 1
        cities.append(row)

    from datetime import datetime as _dt2
    return jsonify({
        'generated_at': _dt2.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'total_cities_checked': len(cities),
        'cities': cities,
        'summary': summary,
    })


@admin_bp.route('/api/admin/enrich', methods=['POST'])
def admin_enrich():
    """V226 T5: Force-enrich N profiles for a given city using
    FreeEnrichmentEngine (DuckDuckGo + domain guess + YellowPages fallback).

    Body: {"city_slug": "chicago", "batch_size": 30}
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    data = request.get_json() or {}
    city_slug = data.get('city_slug') or data.get('city')
    batch_size = min(int(data.get('batch_size', 20)), 100)
    if not city_slug:
        return jsonify({'error': 'city_slug required'}), 400

    import time as _t
    from datetime import datetime as _dt
    from web_enrichment import FreeEnrichmentEngine, normalize_name

    t0 = _t.time()
    conn = permitdb.get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT cp.id, cp.contractor_name_raw, cp.contractor_name_normalized,
               cp.city, cp.state
        FROM contractor_profiles cp
        WHERE cp.source_city_key = ?
          AND (cp.enrichment_status IS NULL OR cp.enrichment_status = 'pending')
          AND cp.contractor_name_raw IS NOT NULL AND cp.contractor_name_raw != ''
          AND LENGTH(cp.contractor_name_raw) >= 5
          AND cp.contractor_name_raw NOT LIKE 'NOT GIVEN%'
          AND cp.contractor_name_raw NOT LIKE 'HOMEOWNER%'
          AND cp.contractor_name_raw NOT LIKE 'OWNER %'
          AND cp.contractor_name_raw NOT GLOB '[0-9]*'
          AND (cp.phone IS NULL OR cp.phone = '')
          AND (cp.website IS NULL OR cp.website = '')
        ORDER BY cp.total_permits DESC
        LIMIT ?
    """, (city_slug, batch_size)).fetchall()

    engine = FreeEnrichmentEngine()
    attempted = 0
    enriched = 0
    failed = 0
    for r in rows:
        attempted += 1
        raw = r['contractor_name_raw']
        norm = r['contractor_name_normalized'] or normalize_name(raw)
        try:
            phone, website = engine.enrich_one(raw, r['city'] or '', r['state'] or '')
        except Exception:
            phone, website = None, None
        now = _dt.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        try:
            if phone or website:
                enriched += 1
                conn.execute("""INSERT OR REPLACE INTO contractor_contacts
                        (contractor_name_normalized, display_name, phone, website,
                         source, confidence, looked_up_at)
                        VALUES (?, ?, ?, ?, 'web_enrichment', 'low', ?)""",
                        (norm, raw, phone, website, now))
                conn.execute("""UPDATE contractor_profiles
                        SET phone = COALESCE(NULLIF(phone, ''), ?),
                            website = COALESCE(NULLIF(website, ''), ?),
                            enrichment_status = 'enriched',
                            enriched_at = ?, updated_at = ?
                        WHERE id = ?""", (phone, website, now, now, r['id']))
                conn.execute("""INSERT INTO enrichment_log
                        (contractor_profile_id, source, status, cost, created_at)
                        VALUES (?, 'web_enrichment', 'enriched', 0.0, ?)""",
                        (r['id'], now))
            else:
                failed += 1
                conn.execute("""UPDATE contractor_profiles
                        SET enrichment_status = 'not_found',
                            enriched_at = ?, updated_at = ?
                        WHERE id = ?""", (now, now, r['id']))
                conn.execute("""INSERT INTO enrichment_log
                        (contractor_profile_id, source, status, cost, created_at)
                        VALUES (?, 'web_enrichment', 'not_found', 0.0, ?)""",
                        (r['id'], now))
            conn.commit()
        except Exception as e:
            failed += 1
            print(f"[V226 enrich] write err {raw}: {e}", flush=True)

    return jsonify({
        'city_slug': city_slug,
        'attempted': attempted,
        'enriched': enriched,
        'failed': failed,
        'elapsed_seconds': round(_t.time() - t0, 2),
    })


@admin_bp.route('/api/admin/enrich-cities', methods=['POST'])
def admin_enrich_cities():
    """V231 Phase 4: run admin_enrich in series for a list of city_slugs.

    Body: {"city_slugs": ["fort-worth", "san-francisco", "seattle", "houston"],
           "batch_size": 50}

    Phase 4 of the master launch plan wants enrichment fast-tracked on
    specific cities without waiting for the 30-min daemon cycle to crawl
    them in FIFO order. This is a convenience wrapper that calls the
    same single-city enrichment logic once per slug and returns a
    per-city summary.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    data = request.get_json() or {}
    slugs = data.get('city_slugs') or []
    batch_size = min(int(data.get('batch_size', 30)), 100)
    # V234 P0: default to async. Enrichment for 3-4 cities at batch_size=50
    # regularly exceeds Render's 30s HTTP timeout (each DDG lookup is
    # 1-3s, so a 200-profile batch can easily hit 10+ minutes). Spawn a
    # background thread and return a job_id; Cowork can poll
    # enrichment_log to see results as they stream in.
    async_param = data.get('async')
    run_async = True if async_param is None else (
        str(async_param).lower() in ('1', 'true', 'yes')
    )
    if not slugs:
        return jsonify({'error': 'city_slugs required'}), 400
    if not isinstance(slugs, list):
        return jsonify({'error': 'city_slugs must be a list'}), 400

    import time as _t
    from datetime import datetime as _dt
    from web_enrichment import FreeEnrichmentEngine, normalize_name

    def _do_enrich(slug_list, _batch_size, _job_id=None):
        """Inline worker — shared by the sync + async paths."""
        _t0 = _t.time()
        _engine = FreeEnrichmentEngine()
        _conn = permitdb.get_connection()
        _conn.row_factory = sqlite3.Row
        _results = []
        for city_slug in slug_list:
            city_t0 = _t.time()
            rows = _conn.execute("""
                SELECT cp.id, cp.contractor_name_raw, cp.contractor_name_normalized,
                       cp.city, cp.state
                FROM contractor_profiles cp
                WHERE cp.source_city_key = ?
                  AND (cp.enrichment_status IS NULL OR cp.enrichment_status = 'pending')
                  AND cp.contractor_name_raw IS NOT NULL AND cp.contractor_name_raw != ''
                  AND LENGTH(cp.contractor_name_raw) >= 5
                  AND cp.contractor_name_raw NOT LIKE 'NOT GIVEN%'
                  AND cp.contractor_name_raw NOT LIKE 'HOMEOWNER%'
                  AND cp.contractor_name_raw NOT LIKE 'OWNER %'
                  AND cp.contractor_name_raw NOT GLOB '[0-9]*'
                  AND (cp.phone IS NULL OR cp.phone = '')
                  AND (cp.website IS NULL OR cp.website = '')
                ORDER BY cp.total_permits DESC
                LIMIT ?
            """, (city_slug, _batch_size)).fetchall()
            attempted = enriched = failed = 0
            for r in rows:
                attempted += 1
                raw = r['contractor_name_raw']
                norm = r['contractor_name_normalized'] or normalize_name(raw)
                try:
                    phone, website = _engine.enrich_one(raw, r['city'] or '', r['state'] or '')
                except Exception:
                    phone, website = None, None
                now = _dt.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                try:
                    if phone or website:
                        enriched += 1
                        _conn.execute("""INSERT OR REPLACE INTO contractor_contacts
                                (contractor_name_normalized, display_name, phone, website,
                                 source, confidence, looked_up_at)
                                VALUES (?, ?, ?, ?, 'web_enrichment', 'low', ?)""",
                                (norm, raw, phone, website, now))
                        _conn.execute("""UPDATE contractor_profiles
                                SET phone=?, website=?, enrichment_status='enriched',
                                    enriched_at=?, updated_at=?
                                WHERE id=?""",
                                (phone, website, now, now, r['id']))
                        _conn.execute("""INSERT INTO enrichment_log
                                (contractor_profile_id, source, status, cost, created_at)
                                VALUES (?, 'web_enrichment', 'enriched', 0.0, ?)""",
                                (r['id'], now))
                    else:
                        _conn.execute("""UPDATE contractor_profiles
                                SET enrichment_status='not_found',
                                    enriched_at=?, updated_at=?
                                WHERE id=?""", (now, now, r['id']))
                        _conn.execute("""INSERT INTO enrichment_log
                                (contractor_profile_id, source, status, cost, created_at)
                                VALUES (?, 'web_enrichment', 'not_found', 0.0, ?)""",
                                (r['id'], now))
                    _conn.commit()
                except Exception as e:
                    failed += 1
                    print(f"[V234 enrich-cities] {city_slug} {raw}: {e}", flush=True)
            _summary = {
                'city_slug': city_slug,
                'attempted': attempted,
                'enriched': enriched,
                'failed': failed,
                'elapsed_seconds': round(_t.time() - city_t0, 2),
            }
            _results.append(_summary)
            if _job_id:
                print(f"[V234] enrich-cities job {_job_id}: {_summary}", flush=True)
        return {
            'cities': _results,
            'total_elapsed_seconds': round(_t.time() - _t0, 2),
        }

    if run_async:
        import uuid as _uuid
        job_id = _uuid.uuid4().hex[:12]
        def _bg():
            try:
                final = _do_enrich(slugs, batch_size, _job_id=job_id)
                print(f"[V234] enrich-cities job {job_id} COMPLETE: {final}", flush=True)
            except Exception as _e:
                import traceback as _tb
                print(f"[V234] enrich-cities job {job_id} ERROR: {_e}", flush=True)
                _tb.print_exc()
        threading.Thread(target=_bg, daemon=True,
                         name=f'enrich_cities_{job_id}').start()
        return jsonify({
            'status': 'started',
            'async': True,
            'job_id': job_id,
            'city_slugs': slugs,
            'batch_size': batch_size,
            'message': 'running in background — poll enrichment_log / contractor_profiles for progress',
        }), 202

    return jsonify(_do_enrich(slugs, batch_size))


@admin_bp.route('/api/admin/license-import', methods=['POST'])
def admin_license_import():
    """V237 PR#1: download a state's contractor-license open-data CSV and
    enrich contractor_profiles.

    Body: {"state": "OR", "async": true|false (default true)}
    Response (async): 202 {"status":"started", "job_id":..., "state":...}
    Response (sync):  200 {"state":..., "source_rows":..., "by_license":{...}, ...}
    Response (busy):  409 {"error":"import in progress", "state":..., "job_id":...}

    V241 P4: a threading.Lock gates every invocation (sync and async).
    Concurrent imports OOM'd Render's worker — the serialization keeps
    at most one state's CSV in memory at a time.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    data = request.get_json() or {}
    state = (data.get('state') or '').upper().strip()
    if not state:
        return jsonify({'error': 'state required'}), 400

    async_param = data.get('async')
    run_async = True if async_param is None else (
        str(async_param).lower() in ('1', 'true', 'yes')
    )

    from license_enrichment import import_state, STATE_CONFIGS
    if state not in STATE_CONFIGS:
        return jsonify({
            'error': f'unknown state {state!r}',
            'available': sorted(STATE_CONFIGS.keys()),
        }), 400

    # V241 P4: non-blocking try-acquire so a busy request rejects
    # immediately rather than hanging the caller for the entire
    # download.
    if not _LICENSE_IMPORT_LOCK.acquire(blocking=False):
        return jsonify({
            'error': 'license-import already in progress',
            'in_flight': dict(_LICENSE_IMPORT_IN_FLIGHT),
            'requested_state': state,
            'hint': 'wait for the current job to finish; check logs for '
                    'COMPLETE: {state: ...} before retrying',
        }), 409

    if run_async:
        import uuid as _uuid
        from datetime import datetime as _dt
        job_id = _uuid.uuid4().hex[:12]
        _LICENSE_IMPORT_IN_FLIGHT['state'] = state
        _LICENSE_IMPORT_IN_FLIGHT['job_id'] = job_id
        _LICENSE_IMPORT_IN_FLIGHT['started_at'] = _dt.utcnow().isoformat()

        def _bg():
            try:
                result = import_state(state)
                print(f"[V237] license-import job {job_id} COMPLETE: {result}",
                      flush=True)
            except Exception as e:
                import traceback as _tb
                print(f"[V237] license-import job {job_id} ERROR: {e}",
                      flush=True)
                _tb.print_exc()
            finally:
                _LICENSE_IMPORT_IN_FLIGHT['state'] = None
                _LICENSE_IMPORT_IN_FLIGHT['job_id'] = None
                _LICENSE_IMPORT_IN_FLIGHT['started_at'] = None
                _LICENSE_IMPORT_LOCK.release()

        threading.Thread(target=_bg, daemon=True,
                         name=f'license_import_{state}').start()
        return jsonify({
            'status': 'started',
            'async': True,
            'job_id': job_id,
            'state': state,
            'message': 'running in background — poll enrichment_log or '
                       'contractor_profiles for progress',
        }), 202

    # Sync path — lock already held; release after the import completes.
    try:
        result = import_state(state)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'state': state}), 500
    finally:
        _LICENSE_IMPORT_LOCK.release()


@admin_bp.route('/api/admin/dashboard', methods=['GET'])
def admin_dashboard():
    """V226 T9: Combined one-stop status page — aggregates everything.

    Returns overall totals, ad-ready buckets, recent collection_log,
    recent enrichment_log, city-health matrix.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    conn = permitdb.get_connection()
    # V411 (loop /CODE_V286 grind): ALSO break out absolute phone count
    # at the system-level totals. The existing enrichment_pct mixes
    # phone OR website — same misalignment V373 caught on the per-city
    # ad-ready threshold. Phones are the revenue signal; tracking the
    # raw phone count + phones_pct (phones / profiles) gives Wes the
    # number that matters for the $149/mo lead product.
    totals = conn.execute("""
        SELECT
          (SELECT COUNT(*) FROM permits) as permits,
          (SELECT COUNT(*) FROM violations) as violations,
          (SELECT COUNT(*) FROM contractor_profiles) as profiles,
          (SELECT COUNT(*) FROM contractor_profiles
             WHERE (phone IS NOT NULL AND phone != '')
                OR (website IS NOT NULL AND website != '')) as enriched,
          (SELECT COUNT(*) FROM contractor_profiles
             WHERE phone IS NOT NULL AND phone != '') as phones,
          (SELECT COUNT(*) FROM prod_cities WHERE status = 'active') as active_cities,
          (SELECT COUNT(*) FROM prod_cities WHERE status = 'paused') as paused_cities
    """).fetchone()
    totals_dict = {k: totals[k] for k in totals.keys()}
    if totals_dict.get('profiles', 0):
        totals_dict['enrichment_pct'] = round(
            100.0 * (totals_dict.get('enriched') or 0) / totals_dict['profiles'], 1
        )
        totals_dict['phones_pct'] = round(
            100.0 * (totals_dict.get('phones') or 0) / totals_dict['profiles'], 1
        )
    else:
        totals_dict['enrichment_pct'] = 0
        totals_dict['phones_pct'] = 0

    # recent collections (last 15)
    recent_collections = []
    try:
        for r in conn.execute("""
            SELECT city_slug, status, records_inserted, api_rows_returned,
                   duplicate_rows_skipped, created_at
            FROM collection_log
            ORDER BY created_at DESC LIMIT 15
        """).fetchall():
            recent_collections.append({k: r[k] for k in r.keys()})
    except Exception:
        pass

    # recent enrichments (last 15)
    recent_enrichments = []
    try:
        for r in conn.execute("""
            SELECT contractor_profile_id, source, status, created_at
            FROM enrichment_log
            ORDER BY created_at DESC LIMIT 15
        """).fetchall():
            recent_enrichments.append({k: r[k] for k in r.keys()})
    except Exception:
        pass

    # V369 (loop /CODE_V286 grind): ad-ready computation used a hardcoded
    # top-20 slug list (e.g. "chicago", "phoenix", "san-jose") that didn't
    # match the prod_cities canonical slugs ("chicago-il", "phoenix-az",
    # "san-jose-ca") AND missed actual ad-ready cities like Miami-Dade,
    # Henderson, Anaheim, Cleveland, Buffalo, and Orlando-FL — leaving the
    # admin dashboard reporting a count well below ground truth. Compute
    # against every active city with >= 100 permits instead so the bucket
    # tracks the real shape of the product.
    # V373 (loop /CODE_V286 grind): also break out absolute phone count.
    # CLAUDE.md defines ad-ready as profiles>100 AND phones>50 AND
    # violations>0 — the previous threshold (`pct > 40` on enrichment_rate
    # of phone-OR-website) hid cities with hundreds of phones but a long
    # zero-phone tail bringing pct under 40, and let through cities where
    # the only "enrichment" was a website link with zero phones to call.
    # Phones are the revenue signal; threshold matches CLAUDE.md.
    health_rows = conn.execute("""
        SELECT pc.city_slug, pc.state, pc.status,
               COALESCE(p.cnt, 0) as permits, p.newest as newest_permit,
               COALESCE(v.cnt, 0) as violations,
               COALESCE(cp.cnt, 0) as profiles,
               COALESCE(cp.enriched, 0) as enriched,
               COALESCE(cp.phones, 0) as phones
        FROM prod_cities pc
        LEFT JOIN (
            SELECT source_city_key, COUNT(*) cnt,
                   MAX(COALESCE(NULLIF(filing_date,''),NULLIF(issued_date,''),NULLIF(date,''))) newest
            FROM permits GROUP BY source_city_key
        ) p ON p.source_city_key = pc.city_slug
        LEFT JOIN (
            SELECT prod_city_id, COUNT(*) cnt FROM violations GROUP BY prod_city_id
        ) v ON v.prod_city_id = pc.id
        LEFT JOIN (
            SELECT source_city_key, COUNT(*) cnt,
                   SUM(CASE WHEN (phone IS NOT NULL AND phone != '')
                             OR (website IS NOT NULL AND website != '')
                            THEN 1 ELSE 0 END) enriched,
                   SUM(CASE WHEN phone IS NOT NULL AND phone != ''
                            THEN 1 ELSE 0 END) phones
            FROM contractor_profiles GROUP BY source_city_key
        ) cp ON cp.source_city_key = pc.city_slug
        WHERE pc.status = 'active' AND COALESCE(p.cnt, 0) >= 100
    """).fetchall()

    from datetime import datetime as _dt
    today = _dt.utcnow().date()
    yes, close, no_list = [], [], []
    # V374 (loop /CODE_V286 grind): collect per-city gap diagnostics so the
    # dashboard can answer "why is city X in 'close' instead of 'yes'?"
    # without a follow-up query. Each row is (slug, gaps[]) where gaps
    # are short labels like 'phones', 'violations', 'stale', 'profiles'.
    gap_diagnostics = {}
    for r in health_rows:
        slug = r['city_slug']
        if r['status'] != 'active':
            no_list.append(slug)
            continue
        newest = (r['newest_permit'] or '')[:10]
        age = None
        if newest and len(newest) == 10:
            try:
                age = (today - _dt.strptime(newest, '%Y-%m-%d').date()).days
            except Exception:
                age = None
        gaps = []
        if r['profiles'] <= 100:
            gaps.append('profiles')
        if r['phones'] <= 50:
            gaps.append('phones')
        if r['violations'] <= 0:
            gaps.append('violations')
        if age is None or age >= 7:
            gaps.append('stale')
        # V373: ad-ready definition matches CLAUDE.md North Star.
        if r['permits'] > 100 and not gaps:
            yes.append(slug)
        elif r['permits'] > 100 and age is not None and age < 14:
            close.append(slug)
            gap_diagnostics[slug] = {
                'profiles': r['profiles'], 'phones': r['phones'],
                'violations': r['violations'],
                'newest_permit': newest, 'age_days': age, 'gaps': gaps,
            }
        else:
            no_list.append(slug)
            gap_diagnostics[slug] = {
                'profiles': r['profiles'], 'phones': r['phones'],
                'violations': r['violations'],
                'newest_permit': newest, 'age_days': age, 'gaps': gaps,
            }

    return jsonify({
        'generated_at': _dt.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'overall': totals_dict,
        'ad_ready': {
            'yes': yes,
            'close': close,
            'no': no_list,
            # V374: per-city gap detail for close + no buckets so the
            # admin can see at a glance which lever to pull next.
            'gaps': gap_diagnostics,
        },
        'recent_collections': recent_collections,
        'recent_enrichments': recent_enrichments,
    })


@admin_bp.route('/admin', methods=['GET'])
@admin_bp.route('/admin/dashboard', methods=['GET'])
def admin_dashboard_html():
    """V227 T6: Interactive HTML command center — one page, force-collect
    and force-enrich buttons per city, inline JS that calls the existing
    JSON admin endpoints. Auth via ?key=... query param (same value as
    the X-Admin-Key header; this is admin-only so it isn't sensitive in a
    logged URL relative to the key itself)."""
    key = request.args.get('key') or request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY')
    if not expected:
        return Response(
            '<h1>503</h1><p>ADMIN_KEY env var not configured on this instance.</p>',
            status=503, mimetype='text/html')
    if key != expected:
        return Response(
            '<h1>401</h1><p>Append ?key=... or send X-Admin-Key header.</p>',
            status=401, mimetype='text/html')

    conn = permitdb.get_connection()
    top_slugs = [
        'new-york-city','los-angeles','chicago','houston','phoenix',
        'philadelphia','dallas','austin','san-antonio','san-diego',
        'nashville','seattle','san-francisco','fort-worth','columbus',
        'denver','memphis','new-orleans','milwaukee','san-jose',
    ]
    placeholders = ','.join('?' * len(top_slugs))
    rows = conn.execute(f"""
        SELECT pc.city_slug, pc.state, pc.status, pc.source_type,
               COALESCE(pc.expected_freshness_days, 14) expected_days,
               COALESCE(pc.needs_attention, 0) needs_attention,
               COALESCE(p.cnt, 0) permits,
               p.newest as newest_permit,
               COALESCE(v.cnt, 0) violations,
               COALESCE(cp.cnt, 0) profiles,
               COALESCE(cp.enriched, 0) enriched
        FROM prod_cities pc
        LEFT JOIN (
            SELECT source_city_key, COUNT(*) cnt,
                   MAX(COALESCE(NULLIF(filing_date,''),NULLIF(issued_date,''),NULLIF(date,''))) newest
            FROM permits GROUP BY source_city_key
        ) p ON p.source_city_key = pc.city_slug
        LEFT JOIN (
            SELECT prod_city_id, COUNT(*) cnt FROM violations GROUP BY prod_city_id
        ) v ON v.prod_city_id = pc.id
        LEFT JOIN (
            SELECT source_city_key, COUNT(*) cnt,
                   SUM(CASE WHEN (phone IS NOT NULL AND phone != '')
                             OR (website IS NOT NULL AND website != '')
                            THEN 1 ELSE 0 END) enriched
            FROM contractor_profiles GROUP BY source_city_key
        ) cp ON cp.source_city_key = pc.city_slug
        WHERE pc.city_slug IN ({placeholders})
        ORDER BY permits DESC
    """, top_slugs).fetchall()

    from datetime import datetime as _dt
    today = _dt.utcnow().date()
    table_rows = []
    for r in rows:
        row = {k: r[k] for k in r.keys()}
        newest = (row.get('newest_permit') or '')[:10]
        age = None
        if newest and len(newest) == 10:
            try:
                age = (today - _dt.strptime(newest, '%Y-%m-%d').date()).days
            except Exception:
                pass
        pct = round(100.0 * (row['enriched'] or 0) / row['profiles'], 1) if row['profiles'] else 0
        expected = row.get('expected_days') or 14
        if row['status'] != 'active':
            status = 'paused'
        elif age is None or row['permits'] == 0:
            status = 'no_data'
        elif age > expected * 3:
            status = 'critical'
        elif age > expected:
            status = 'stale'
        else:
            status = 'healthy'
        table_rows.append({
            'slug': row['city_slug'], 'state': row['state'],
            'status': status, 'days_stale': age,
            'expected': expected, 'permits': row['permits'],
            'violations': row['violations'], 'profiles': row['profiles'],
            'enriched': row['enriched'], 'enrichment_pct': pct,
            'source_type': row.get('source_type'),
            'needs_attention': bool(row.get('needs_attention')),
        })

    # Summary counters
    summary = {s: 0 for s in ('healthy','stale','critical','no_data','paused')}
    for r in table_rows:
        summary[r['status']] = summary.get(r['status'], 0) + 1

    # Render inline (keep it self-contained, no new template file needed)
    from markupsafe import escape
    html_rows = []
    for r in table_rows:
        slug = escape(r['slug'])
        status = r['status']
        attn_badge = ' <span class="attn">!</span>' if r['needs_attention'] else ''
        html_rows.append(
            f"<tr><td>{slug}</td>"
            f"<td class='{status}'>{status.upper()}{attn_badge}</td>"
            f"<td>{r['days_stale'] if r['days_stale'] is not None else '?'}d</td>"
            f"<td>{r['expected']}d</td>"
            f"<td>{escape(r['source_type'] or '?')}</td>"
            f"<td>{r['permits']:,}</td>"
            f"<td>{r['violations']:,}</td>"
            f"<td>{r['profiles']:,}</td>"
            f"<td>{r['enrichment_pct']}%</td>"
            f"<td><button class='btn-collect' onclick=\"forceCollect('{slug}')\">Collect</button>"
            f"<button class='btn-enrich' onclick=\"enrich('{slug}')\">Enrich</button></td></tr>"
        )

    return render_template('admin/command_center.html',
        now_utc=_dt.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
        summary=summary,
        rows_html=''.join(html_rows),
    )


@admin_bp.route('/api/admin/add-source', methods=['POST'])
def admin_add_source():
    """V12.50: Add a single source and upsert to SQLite."""
    valid, error = check_admin_key()
    if not valid:
        return error

    source_key = request.args.get('source')
    source_type = request.args.get('type', 'bulk')  # 'bulk' or 'city'

    if not source_key:
        return jsonify({'error': 'Missing source parameter. Usage: ?source=nj_statewide&type=bulk'}), 400

    # V229 A3: renamed from run_collection
    def _run_single_source_collection():
        try:
            from collector import collect_single_source
            print(f"[Admin] Adding single source: {source_key} ({source_type})...")
            collect_single_source(source_key, source_type)
            print(f"[Admin] Source {source_key} added successfully.")
        except Exception as e:
            print(f"[Admin] Add source error: {e}")

    thread = threading.Thread(target=_run_single_source_collection, daemon=True)
    thread.start()

    return jsonify({
        'message': f'Adding source: {source_key} ({source_type})',
        'note': 'V12.50: Data written directly to SQLite'
    })


@admin_bp.route('/api/admin/collection-status')
def admin_collection_status():
    """V12.29: Get last collection run status for debugging."""
    valid, error = check_admin_key()
    if not valid:
        return error

    stats_file = os.path.join(DATA_DIR, "collection_stats.json")
    if not os.path.exists(stats_file):
        return jsonify({'error': 'No collection stats found', 'path': stats_file}), 404

    try:
        with open(stats_file) as f:
            stats = json.load(f)

        # Calculate summary
        city_stats = stats.get('city_stats', {})
        total_cities = len(city_stats)
        cities_with_permits = sum(1 for s in city_stats.values() if s.get('normalized', 0) > 0)
        cities_empty = sum(1 for s in city_stats.values() if s.get('status') == 'success_empty')
        cities_errored = sum(1 for s in city_stats.values() if 'error' in str(s.get('status', '')))
        cities_timeout = sum(1 for s in city_stats.values() if 'timeout' in str(s.get('status', '').lower()))

        # Get list of failed cities
        failed_cities = [
            {'city': k, 'name': v.get('city_name', k), 'status': v.get('status', 'unknown')}
            for k, v in city_stats.items()
            if 'error' in str(v.get('status', '')) or 'timeout' in str(v.get('status', '').lower())
        ]

        return jsonify({
            'collected_at': stats.get('collected_at'),
            'total_permits': stats.get('total_permits', 0),
            'summary': {
                'total_cities_attempted': total_cities,
                'cities_with_permits': cities_with_permits,
                'cities_empty': cities_empty,
                'cities_errored': cities_errored,
                'cities_timeout': cities_timeout,
            },
            'failed_cities': failed_cities[:50],  # Limit to 50 to avoid huge response
            'trade_breakdown': stats.get('trade_breakdown', {}),
        })
    except Exception as e:
        return jsonify({'error': f'Failed to read stats: {str(e)}'}), 500


@admin_bp.route('/api/admin/start-collectors', methods=['POST'])
def admin_start_collectors():
    """V473b corrective: V471 PR4's "use worker.py" message was wrong —
    the permitgrab-worker Render service was never created (only declared
    in render.yaml). The web process is the only collector. Calling this
    endpoint spawns the daemon threads inside this process, which is the
    historical design per CLAUDE.md ("Daemon does NOT auto-start on
    deploy. Must call: POST /api/admin/start-collectors").

    V443 (P0 zombie-daemon fix): the previous flag-only check could
    return "already_running" forever after the daemon thread died.
    Probe for a live `scheduled_collection` thread first; if absent,
    reset the flags so the spawn fires.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    # V493: V481's WORKER_MODE noop guard removed. The permitgrab-worker
    # Background Worker service in render.yaml was never created on
    # Render — see CLAUDE.md ARCHITECTURE GROUND TRUTH. With the guard
    # in place this endpoint returned 'noop' and start_collectors itself
    # also returned early in server.py, so collection + email digest
    # were dead since 2026-04-29 (5 missed digests). Removing both guards
    # restores single-process daemons (the only working state).

    try:
        import threading
        live_daemon = any(
            t.is_alive() and t.name == 'scheduled_collection'
            for t in threading.enumerate()
        )
        force = request.args.get('force', '').lower() in ('1', 'true', 'yes')

        if live_daemon and not force:
            return jsonify({
                'status': 'already_running',
                'message': 'Collectors already started',
                'daemon_thread_alive': True,
            }), 200

        if not live_daemon or force:
            # Reset the one-way flag so start_collectors() actually spawns.
            # V475: also reset on force=1, otherwise force is a no-op when
            # the scheduled_collection thread is already alive — the
            # endpoint wouldn't reach this block, and start_collectors()
            # returns immediately because _collector_started=True. That
            # blocks any new threads we add to start_collectors() from
            # being spawned without a Render restart (hit during the
            # V475 email_scheduler restoration).
            _s._collector_started = False
            _s._collectors_manually_started = False
            print(
                f"[{datetime.now()}] V475: live_daemon={live_daemon} force={force}; "
                f"resetting flags and respawning",
                flush=True,
            )

        def _run_collectors():
            print(f"[{datetime.now()}] V473b: Manual start_collectors triggered via API")
            start_collectors()

        t = threading.Thread(target=_run_collectors, daemon=True)
        t.start()
        _s._collectors_manually_started = True

        return jsonify({
            'status': 'started',
            'message': 'Background collectors started in separate thread',
            'reset_dead_daemon': not live_daemon,
        }), 200

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_bp.route('/api/admin/debug/threads', methods=['GET'])
def admin_debug_threads():
    """V444 (P0 daemon-stuck diagnostic): dump every Python thread's
    name, alive state, and current stack frame. Read-only.

    Pairs with the V443 zombie-respawn fix — V443 spawned the thread
    correctly but it appears to be alive-yet-idle. This endpoint lets
    us see what each thread is actually executing without needing
    ptrace/py-spy access to the gunicorn worker.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    import threading as _th
    import traceback as _tb
    import sys as _sys

    frames = _sys._current_frames()
    threads = []
    for t in _th.enumerate():
        f = frames.get(t.ident)
        if f is None:
            stack = []
        else:
            stack = _tb.format_stack(f)[-12:]  # last 12 frames
        threads.append({
            'name': t.name,
            'ident': t.ident,
            'alive': t.is_alive(),
            'daemon': t.daemon,
            'stack': stack,
        })
    # Also surface flag state so we can confirm V443 reset
    try:
        from license_enrichment import IMPORT_IN_PROGRESS as _imp_flag
        import_running = _imp_flag.is_set()
    except Exception:
        import_running = None
    return jsonify({
        'thread_count': len(threads),
        'threads': threads,
        '_collector_started': _s._collector_started,
        '_collectors_manually_started': _s._collectors_manually_started,
        '_startup_done': _s._startup_done,
        'import_in_progress': import_running,
    })


@admin_bp.route('/api/admin/refresh-profiles', methods=['POST'])
def admin_refresh_profiles():
    """V182: Rebuild contractor_profiles aggregates + emblem flags.

    Query params:
      city: specific prod_cities.city_slug to refresh (optional; default = all active)

    Also triggers update_city_emblems() after refresh so has_enrichment
    flag stays in sync on prod_cities.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from contractor_profiles import refresh_contractor_profiles, update_city_emblems
        city_slug = request.args.get('city')
        result = refresh_contractor_profiles(city_slug=city_slug)
        emblems = update_city_emblems()
        return jsonify({'status': 'ok', 'emblems': emblems, **result}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_bp.route('/api/admin/refresh-emblems', methods=['POST'])
def admin_refresh_emblems():
    """V182: Recompute has_enrichment/has_violations flags on prod_cities.

    Call after violation collection. Fast (<1s for 2K cities).
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from contractor_profiles import update_city_emblems
        return jsonify({'status': 'ok', **update_city_emblems()}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_bp.route('/api/admin/backfill-property-owners', methods=['POST'])
def admin_backfill_property_owners():
    """V356 (CODE_V351 Part 3 Step 2): seed property_owners from the
    owner_name field already present on permits for cities whose feeds
    expose it (NYC, Miami-Dade today; more as field_maps add owner_name).

    V359 HOTFIX: aligned to V279 canonical schema (column names `address`
    and `source`, not `property_address`/`data_source` which V356 had wrong).

    Body or query param: city (optional). When unset, runs across all
    cities that have any permit with a non-empty owner_name. Dedup via
    the V278 UNIQUE INDEX on (address, owner_name, source); INSERT OR
    IGNORE skips collisions silently.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        body = request.get_json(silent=True) or {}
        city_slug = body.get('city') or request.args.get('city')
        conn = permitdb.get_connection()
        if city_slug:
            sql = """
                INSERT OR IGNORE INTO property_owners (address, owner_name, source)
                SELECT DISTINCT address, owner_name, 'permit_record'
                FROM permits
                WHERE source_city_key = ?
                  AND owner_name IS NOT NULL AND owner_name != ''
                  AND address IS NOT NULL AND address != ''
            """
            cursor = conn.execute(sql, (city_slug,))
        else:
            sql = """
                INSERT OR IGNORE INTO property_owners (address, owner_name, source)
                SELECT DISTINCT address, owner_name, 'permit_record'
                FROM permits
                WHERE owner_name IS NOT NULL AND owner_name != ''
                  AND address IS NOT NULL AND address != ''
            """
            cursor = conn.execute(sql)
        rows = cursor.rowcount
        conn.commit()
        return jsonify({
            'status': 'ok',
            'rows_inserted': rows,
            'scope': city_slug or 'all_cities',
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_bp.route('/api/admin/enrich-contractors', methods=['POST'])
def admin_enrich_contractors():
    """V182: Enrich contractor_profiles for one city via Google Places.

    Gated on GOOGLE_PLACES_API_KEY env var — returns 'skipped_no_api_key' if
    unset (no PR-merge-time failure mode). Call per-city with a cost cap.

    Query params:
      city: required — prod_cities.city_slug
      max_cost: optional $ cap (default $25)
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    city_slug = request.args.get('city')
    if not city_slug:
        return jsonify({'error': 'Missing city parameter'}), 400
    try:
        max_cost = float(request.args.get('max_cost', '25'))
    except ValueError:
        return jsonify({'error': 'max_cost must be a number'}), 400
    try:
        from contractor_profiles import enrich_city_profiles
        result = enrich_city_profiles(city_slug=city_slug, max_cost=max_cost)
        return jsonify({'status': 'ok', **result}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_bp.route('/api/admin/reclassify-general', methods=['POST'])
def admin_reclassify_general():
    """V182 T3: Re-run classify_trade on permits currently tagged 'General Construction'.

    Only touches rows where the current trade_category is 'General Construction'.
    Expands TRADE_CATEGORIES hits (insulation, drywall, tile, etc.) to more
    specific trades. Batched updates, 1000 permits per transaction.

    Never downgrades already-specific trades; only upgrades General Construction.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from collector import classify_trade
        conn = permitdb.get_connection()
        total_scanned = 0
        total_updated = 0
        batch_size = 1000
        updated_by_trade = {}

        while True:
            rows = conn.execute("""
                SELECT permit_number, description, work_type, permit_type
                FROM permits
                WHERE trade_category = 'General Construction'
                  AND (description IS NOT NULL AND description != '')
                LIMIT ?
                OFFSET ?
            """, (batch_size, total_scanned)).fetchall()
            if not rows:
                break
            for r in rows:
                text = ' '.join(filter(None, [r['description'], r['work_type'], r['permit_type']]))
                new_trade = classify_trade(text)
                if new_trade and new_trade != 'General Construction':
                    conn.execute(
                        "UPDATE permits SET trade_category = ? WHERE permit_number = ?",
                        (new_trade, r['permit_number']),
                    )
                    total_updated += 1
                    updated_by_trade[new_trade] = updated_by_trade.get(new_trade, 0) + 1
            conn.commit()
            total_scanned += len(rows)
            # If this batch was smaller than batch_size, we're done.
            if len(rows) < batch_size:
                break
        conn.close()
        return jsonify({
            'status': 'ok',
            'scanned': total_scanned,
            'updated': total_updated,
            'breakdown': updated_by_trade,
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_bp.route('/api/admin/recount-permits', methods=['POST'])
def admin_recount_permits():
    """V100: Recount total_permits from actual permits table."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from collector import update_total_permits_from_actual
        updated = update_total_permits_from_actual()
        return jsonify({'status': 'ok', 'updated': updated}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_bp.route('/api/admin/test-search', methods=['POST'])
def admin_test_search():
    """V111b: Test the search pipeline for a single city without saving."""
    valid, error = check_admin_key()
    if not valid:
        return error

    data = request.json or {}
    city = data.get('city', 'Oakland')
    state = data.get('state', 'CA')

    results = {'city': city, 'state': state, 'url_pattern_hits': [],
               'state_portal_hits': [], 'errors': []}

    # Phase 1: URL patterns
    try:
        from city_onboarding import _try_url_patterns
        url_hits = _try_url_patterns(city, state)
        results['url_pattern_hits'] = [{'url': h['url'], 'platform': h.get('platform', ''),
                                         'title': h.get('title', ''),
                                         'dataset_id': h.get('dataset_id', '')} for h in url_hits]
    except Exception as e:
        results['errors'].append(f"URL pattern error: {str(e)}")

    # Phase 2: State portal search (V113 — replaces DDG)
    try:
        from city_onboarding import _search_state_portal
        state_hits = _search_state_portal(state, city)
        results['state_portal_hits'] = [{'url': h['url'], 'domain': h.get('domain', ''),
                                          'dataset_id': h.get('dataset_id', ''),
                                          'city_column': h.get('city_column', ''),
                                          'title': h.get('title', '')} for h in state_hits]
    except Exception as e:
        results['errors'].append(f"State portal error: {str(e)}")

    return jsonify(results), 200


@admin_bp.route('/api/admin/pause-never-worked', methods=['POST'])
def admin_pause_never_worked():
    """V118: Pause cities with source assignments that never produced data."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        paused = conn.execute("""
            UPDATE prod_cities SET status = 'paused',
                pause_reason = 'V118: source never produced data'
            WHERE status = 'active' AND health_status = 'never_worked'
            AND total_permits = 0 AND last_successful_collection IS NULL
            AND source_type IS NOT NULL
        """).rowcount
        conn.commit()

        active = conn.execute("SELECT count(*) FROM prod_cities WHERE status = 'active'").fetchone()[0]
        print(f"V118: Paused {paused} never-worked cities, {active} remaining active", flush=True)
        return jsonify({'status': 'complete', 'paused': paused, 'remaining_active': active}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_bp.route('/api/admin/create-permits-index', methods=['POST'])
def admin_create_permits_index():
    """V118: Create index for fast city/state/collected_at lookups."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_permits_city_state_collected
            ON permits(city, state, collected_at)
        """)
        conn.commit()
        print("V118: Created permits city/state/collected_at index", flush=True)
        return jsonify({'status': 'complete'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_bp.route('/api/admin/add-verified-cities', methods=['POST'])
def admin_add_verified_cities():
    """V121: Add cities with manually verified and tested endpoints."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()

        # V121: Verified and tested endpoints (manually confirmed data is correct)
        cities = [
            # V119 originals (DC confirmed working with 2K+ permits)
            {
                'source_key': 'dc-arcgis', 'slug': 'washington',
                'name': 'Washington', 'state': 'DC', 'platform': 'arcgis',
                'endpoint': 'https://maps2.dcgis.dc.gov/dcgis/rest/services/FEEDS/DCRA/FeatureServer/4',
                'dataset_id': '',
            },
            # V121: Milwaukee — CKAN, 16K+ records, addresses confirmed Milwaukee WI
            {
                'source_key': 'milwaukee-ckan', 'slug': 'milwaukee',
                'name': 'Milwaukee', 'state': 'WI', 'platform': 'ckan',
                'endpoint': 'https://data.milwaukee.gov/api/3/action/datastore_search',
                'dataset_id': '828e9630-d7cb-42e4-960e-964eae916397',
            },
            # V121: Virginia Beach — ArcGIS FeatureServer, PermitNumber+StreetAddress confirmed
            {
                'source_key': 'virginia-beach-arcgis', 'slug': 'virginia-beach',
                'name': 'Virginia Beach', 'state': 'VA', 'platform': 'arcgis',
                'endpoint': 'https://services2.arcgis.com/CyVvlIiUfRBmMQuu/arcgis/rest/services/Building_Permits_Applications_view/FeatureServer/0',
                'dataset_id': '',
            },
            # V121: Gilbert AZ — ArcGIS MapServer, AddressCity=GILBERT confirmed
            {
                'source_key': 'gilbert-arcgis', 'slug': 'gilbert',
                'name': 'Gilbert', 'state': 'AZ', 'platform': 'arcgis',
                'endpoint': 'https://maps.gilbertaz.gov/arcgis/rest/services/OD/Growth_Development_Tables_1/MapServer/3',
                'dataset_id': '',
            },
            # V119: Albuquerque — ArcGIS FeatureServer, verified endpoint
            {
                'source_key': 'albuquerque-nm-arcgis', 'slug': 'albuquerque',
                'name': 'Albuquerque', 'state': 'NM', 'platform': 'arcgis',
                'endpoint': 'https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0',
                'dataset_id': '',
            },
            # V119: St. Paul — Socrata
            {
                'source_key': 'saint-paul-mn-socrata', 'slug': 'saint-paul',
                'name': 'St. Paul', 'state': 'MN', 'platform': 'socrata',
                'endpoint': 'https://information.stpaul.gov/resource/j8ip-eytd.json',
                'dataset_id': 'j8ip-eytd',
            },
        ]

        added = 0
        for c in cities:
            try:
                # Upsert city_sources
                conn.execute("""
                    INSERT OR REPLACE INTO city_sources
                    (source_key, name, state, platform, endpoint, dataset_id, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'active')
                """, (c['source_key'], c['name'], c['state'], c['platform'],
                      c['endpoint'], c['dataset_id']))

                # Find and update prod_cities — try slug first, then city name + state
                updated = conn.execute("""
                    UPDATE prod_cities SET source_type = ?, source_id = ?,
                    source_endpoint = ?, status = 'active', health_status = 'collecting',
                    consecutive_failures = 0
                    WHERE city_slug = ? OR (LOWER(city) = LOWER(?) AND state = ?)
                """, (c['platform'], c['source_key'], c['endpoint'],
                      c['slug'], c['name'], c['state'])).rowcount

                if updated:
                    added += 1
                    print(f"V119: Added {c['name']}, {c['state']} ({c['platform']})", flush=True)
                else:
                    print(f"V119: No prod_cities match for {c['slug']}", flush=True)
            except Exception as e:
                print(f"V119: Error adding {c['slug']}: {e}", flush=True)

        conn.commit()
        return jsonify({'status': 'complete', 'added': added, 'total': len(cities)}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_bp.route('/api/admin/test-city-collection', methods=['POST'])
def admin_test_city_collection():
    """V119: Test collection for a single city."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        data = request.json or {}
        city_slug = data.get('city_slug')
        days_back = data.get('days_back', 180)
        if not city_slug:
            return jsonify({'error': 'city_slug required'}), 400

        from collector import collect_single_city
        result = collect_single_city(city_slug, days_back=days_back)
        return jsonify({'city_slug': city_slug, 'result': result}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/city-research', methods=['GET'])
def admin_city_research_get():
    """V148: List city research entries."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        status_filter = request.args.get('status')
        limit = int(request.args.get('limit', 50))
        if status_filter:
            rows = conn.execute("SELECT * FROM city_research WHERE status=? ORDER BY population DESC LIMIT ?", (status_filter, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM city_research ORDER BY population DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return jsonify({'count': len(rows), 'rows': [dict(r) for r in rows]}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/city-research', methods=['POST'])
def admin_city_research_post():
    """V148: Upsert a city research entry."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        data = request.get_json() or {}
        city = data.get('city')
        state = data.get('state')
        if not city or not state:
            return jsonify({'error': 'city and state are required'}), 400
        conn = permitdb.get_connection()
        existing = conn.execute("SELECT id FROM city_research WHERE city=? AND state=?", (city, state)).fetchone()
        if existing:
            sets = []
            vals = []
            for col in ['population','status','portal_url','dataset_id','platform','date_field','address_field','notes','tested_at','onboarded_at']:
                if col in data:
                    sets.append(f"{col}=?")
                    vals.append(data[col])
            if sets:
                vals.extend([city, state])
                conn.execute(f"UPDATE city_research SET {','.join(sets)} WHERE city=? AND state=?", vals)
        else:
            conn.execute("""INSERT INTO city_research (city, state, population, status, portal_url, dataset_id, platform, date_field, address_field, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (city, state, data.get('population',0), data.get('status','untested'), data.get('portal_url'),
                 data.get('dataset_id'), data.get('platform'), data.get('date_field'), data.get('address_field'), data.get('notes')))
        conn.commit()
        row = conn.execute("SELECT * FROM city_research WHERE city=? AND state=?", (city, state)).fetchone()
        conn.close()
        return jsonify(dict(row)), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/city-research', methods=['DELETE'])
def admin_city_research_delete():
    """V148: Delete a city research entry."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        city = request.args.get('city')
        state = request.args.get('state')
        if not city or not state:
            return jsonify({'error': 'city and state query params required'}), 400
        conn = permitdb.get_connection()
        r = conn.execute("DELETE FROM city_research WHERE city=? AND state=?", (city, state)).rowcount
        conn.commit()
        conn.close()
        return jsonify({'deleted': r}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/config-audit')
def admin_config_audit():
    """REARCH: Audit config sources — shows gap between dicts and city_sources table."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from city_configs import CITY_REGISTRY, BULK_SOURCES
        conn = permitdb.get_connection()

        reg_active = sum(1 for v in CITY_REGISTRY.values() if v.get('active', False))
        bulk_active = sum(1 for v in BULK_SOURCES.values() if v.get('active', True))
        cs_city = conn.execute("SELECT count(*) FROM city_sources WHERE mode = 'city' AND status = 'active'").fetchone()[0]
        cs_bulk = conn.execute("SELECT count(*) FROM city_sources WHERE mode = 'bulk' AND status = 'active'").fetchone()[0]

        # Find CITY_REGISTRY keys not in city_sources
        cs_keys = {r[0] for r in conn.execute("SELECT source_key FROM city_sources").fetchall()}
        reg_missing = [k for k in CITY_REGISTRY if CITY_REGISTRY[k].get('active', False) and k not in cs_keys]
        bulk_missing = [k for k in BULK_SOURCES if BULK_SOURCES[k].get('active', True) and k not in cs_keys]

        return jsonify({
            'city_registry_active': reg_active,
            'bulk_sources_active': bulk_active,
            'city_sources_city': cs_city,
            'city_sources_bulk': cs_bulk,
            'registry_not_in_db': len(reg_missing),
            'bulk_not_in_db': len(bulk_missing),
            'sample_missing_registry': reg_missing[:20],
            'sample_missing_bulk': bulk_missing[:10],
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/cleanup-contamination', methods=['POST'])
def admin_cleanup_contamination():
    """REARCH-FIX: Remove permits from wrong-city onboard runs and reset cities."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        bad_slugs = ['las-vegas', 'saint-paul', 'san-jose', 'oklahoma-city', 'district-of-columbia-dc']
        total_deleted = 0

        for slug in bad_slugs:
            # Delete permits inserted by onboard (after April 9)
            d = conn.execute("""
                DELETE FROM permits WHERE source_city_key = ?
                AND collected_at > '2026-04-09 00:00'
            """, (slug,)).rowcount
            total_deleted += d

        # Reset prod_cities
        conn.execute("""
            UPDATE prod_cities SET status = 'pending', source_type = NULL, source_id = NULL,
            total_permits = 0, last_successful_collection = NULL, health_status = 'unknown'
            WHERE city_slug IN ('las-vegas', 'saint-paul', 'san-jose', 'oklahoma-city', 'district-of-columbia-dc')
        """)

        # Delete bad city_sources
        conn.execute("""
            DELETE FROM city_sources WHERE source_key IN (
                'las-vegas-socrata', 'saint-paul-socrata', 'san-jose-socrata',
                'oklahoma-city-socrata', 'district-of-columbia-dc-socrata',
                'las-vegas-arcgis', 'saint-paul-arcgis', 'san-jose-arcgis',
                'oklahoma-city-arcgis', 'district-of-columbia-dc-arcgis'
            )
        """)
        conn.commit()
        print(f"REARCH-FIX: Cleanup: {total_deleted} contaminated permits deleted, 5 cities reset", flush=True)
        return jsonify({'status': 'complete', 'permits_deleted': total_deleted, 'cities_reset': bad_slugs}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/onboard-next', methods=['POST'])
def admin_onboard_next():
    """V121: Process next N cities by population. Google-test-add."""
    valid, error = check_admin_key()
    if not valid:
        return error
    data = request.json or {}
    count = min(data.get('count', 1), 10)

    conn = permitdb.get_connection()
    candidates = conn.execute("""
        SELECT city_slug, city, state, population FROM prod_cities
        WHERE population >= 12907
        AND city_slug NOT IN (SELECT DISTINCT source_city_key FROM permits WHERE collected_at > datetime('now', '-7 days') AND source_city_key IS NOT NULL)
        AND (health_status IS NULL OR health_status != 'no_source_found')
        ORDER BY population DESC LIMIT ?
    """, (count,)).fetchall()

    try:
        from onboard import onboard_single_city
    except ImportError as e:
        return jsonify({'error': f'onboard module not available: {e}'}), 500
    results = []
    for row in candidates:
        result = onboard_single_city(row[0], row[1], row[2], row[3])
        results.append(result)
        time.sleep(2)

    successes = sum(1 for r in results if r.get('outcome') == 'success')
    return jsonify({'processed': len(results), 'successes': successes, 'results': results}), 200


@admin_bp.route('/api/admin/onboard-city', methods=['POST'])
def admin_onboard_city():
    """V121: Google it, test it, add it. Uses onboard.py module.

    Body: {"city_slug": "las-vegas"} or {"city_slugs": [...]} or {"top_pending": 20}
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from onboard import onboard_single_city
    except ImportError as e:
        return jsonify({'error': f'onboard module not available: {e}'}), 500
    data = request.json or {}
    conn = permitdb.get_connection()

    # Build city list
    city_slugs = data.get('city_slugs', [])
    if data.get('city_slug'):
        city_slugs = [data['city_slug']]
    if data.get('top_pending'):
        limit = min(int(data['top_pending']), 50)
        rows = conn.execute("""
            SELECT city_slug FROM prod_cities
            WHERE (status IN ('pending', 'paused') OR (status = 'active' AND total_permits = 0))
            AND population >= 12907 AND health_status != 'no_source'
            ORDER BY population DESC LIMIT ?
        """, (limit,)).fetchall()
        city_slugs = [r[0] for r in rows]

    if not city_slugs:
        return jsonify({'error': 'No cities to process'}), 400

    results = []
    for slug in city_slugs:
        city = conn.execute(
            "SELECT city, state, population, total_permits FROM prod_cities WHERE city_slug = ?",
            (slug,)
        ).fetchone()
        if not city:
            results.append({'city_slug': slug, 'outcome': 'not_found'})
            continue
        city_name, state, pop, tp = city[0], city[1], city[2], city[3]

        if tp and tp > 100:
            results.append({'city_slug': slug, 'city': city_name, 'outcome': 'already_has_data', 'permits': tp})
            continue

        # V121: Use the new onboard module
        result = onboard_single_city(slug, city_name, state, pop)
        results.append(result)
        time.sleep(2)

    successes = sum(1 for r in results if r.get('outcome') == 'success')
    total_inserted = sum(r.get('permits_inserted', 0) for r in results)
    return jsonify({
        'summary': {'successes': successes, 'total_permits': total_inserted, 'total': len(city_slugs)},
        'results': results
    }), 200


@admin_bp.route('/api/admin/sweep-status')
def admin_sweep_status():
    """V114: Check catalog sweep results."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        stats = conn.execute("""
            SELECT status, COUNT(*) as cnt, SUM(permits_found) as permits
            FROM sweep_sources GROUP BY status ORDER BY cnt DESC
        """).fetchall()
        recent = conn.execute("""
            SELECT city_slug, platform, name, permits_found, status, discovered_at
            FROM sweep_sources WHERE status = 'confirmed'
            ORDER BY discovered_at DESC LIMIT 30
        """).fetchall()
        return jsonify({
            'stats': [{'status': r[0], 'count': r[1], 'permits': r[2]} for r in stats],
            'confirmed': [{'slug': r[0], 'platform': r[1], 'name': r[2],
                           'permits': r[3], 'discovered': r[5]} for r in recent]
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_bp.route('/api/admin/city-health-legacy')
def admin_city_health_legacy():
    """V100: Legacy city-health summary — health_status buckets + stale list
    + never-worked platforms. Kept under a new path because V226 repurposed
    the /api/admin/city-health route for the 20-top-cities per-row rollup
    used by the /admin HTML dashboard. Same two endpoints collided at
    deploy time (AssertionError: endpoint admin_city_health already
    registered) which is what V228 is fixing.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()

        summary = conn.execute("""
            SELECT health_status, COUNT(*) as cnt,
                   SUM(total_permits) as permits,
                   AVG(days_since_new_data) as avg_days_stale
            FROM prod_cities
            WHERE status = 'active'
            GROUP BY health_status
            ORDER BY cnt DESC
        """).fetchall()

        stale = conn.execute("""
            SELECT city, state, city_slug, total_permits, latest_permit_date,
                   days_since_new_data, last_failure_reason, source_type
            FROM prod_cities
            WHERE status = 'active' AND health_status = 'stale'
            ORDER BY total_permits DESC
            LIMIT 50
        """).fetchall()

        never_worked = conn.execute("""
            SELECT source_type, COUNT(*) as cnt
            FROM prod_cities
            WHERE status = 'active' AND health_status = 'never_worked'
            GROUP BY source_type
            ORDER BY cnt DESC
        """).fetchall()

        # SQLite rows support index access; convert to dicts
        summary_list = []
        for r in summary:
            summary_list.append({
                'health_status': r[0], 'cnt': r[1],
                'permits': r[2], 'avg_days_stale': round(r[3], 1) if r[3] else None
            })

        stale_list = []
        for r in stale:
            stale_list.append({
                'city': r[0], 'state': r[1], 'city_slug': r[2],
                'total_permits': r[3], 'latest_permit_date': r[4],
                'days_since_new_data': r[5], 'last_failure_reason': r[6],
                'source_type': r[7]
            })

        nw_list = []
        for r in never_worked:
            nw_list.append({'source_type': r[0], 'cnt': r[1]})

        return jsonify({
            'summary': summary_list,
            'stale_cities': stale_list,
            'never_worked_by_platform': nw_list
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_bp.route('/api/admin/validation-results')
def admin_validation_results():
    """V12.31: Get endpoint validation results for applying fixes."""
    valid, error = check_admin_key()
    if not valid:
        return error

    validation_file = os.path.join(DATA_DIR, "endpoint_validation.json")
    if not os.path.exists(validation_file):
        return jsonify({'error': 'No validation results found. Run validate_endpoints.py first.'}), 404

    try:
        with open(validation_file) as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'Failed to read validation results: {str(e)}'}), 500


@admin_bp.route('/api/admin/suggested-fixes')
def admin_suggested_fixes():
    """V12.31: Get suggested fixes for broken endpoints."""
    valid, error = check_admin_key()
    if not valid:
        return error

    fixes_file = os.path.join(DATA_DIR, "suggested_fixes.json")
    if not os.path.exists(fixes_file):
        return jsonify({'error': 'No suggested fixes found. Run validate_endpoints.py --fix first.'}), 404

    try:
        with open(fixes_file) as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'Failed to read suggested fixes: {str(e)}'}), 500


@admin_bp.route('/api/admin/coverage')
def admin_coverage():
    """V12.33/V31: Get coverage statistics - which cities/states have data.
    V31: Distinguishes active cities (with live data sources) from historical
    cities that only appear in permit data from bulk sources.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    # V31: Active cities = prod_cities with status='active' (these are being pulled)
    active_city_count = 0
    active_cities_list = []
    try:
        if permitdb.prod_cities_table_exists():
            active_cities_list = permitdb.get_prod_cities(status='active')
            active_city_count = len(active_cities_list)
    except Exception:
        pass

    # Also get counts by status for a full breakdown
    status_breakdown = {}
    try:
        conn = permitdb.get_connection()
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM prod_cities GROUP BY status"
        ).fetchall()
        status_breakdown = {r['status']: r['cnt'] for r in rows}
    except Exception:
        pass

    # V34: Analyze coverage from SQLite DB (not permits.json which is deprecated)
    try:
        conn = permitdb.get_connection()

        # Get total permits
        total_row = conn.execute("SELECT COUNT(*) as cnt FROM permits").fetchone()
        total_permits = total_row['cnt'] if total_row else 0

        # Analyze by city and state
        city_rows = conn.execute("""
            SELECT city, state, COUNT(*) as cnt
            FROM permits GROUP BY city, state ORDER BY cnt DESC
        """).fetchall()

        city_counts = {}
        state_counts = {}
        for r in city_rows:
            city_key = f"{r['city']}, {r['state']}"
            city_counts[city_key] = r['cnt']
            state_counts[r['state'] or 'Unknown'] = state_counts.get(r['state'] or 'Unknown', 0) + r['cnt']

        top_cities = list(city_counts.items())[:50]
        states_covered = sorted(state_counts.items(), key=lambda x: -x[1])

        all_states = ['AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
                      'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
                      'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
                      'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
                      'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY']
        states_missing = [s for s in all_states if s not in state_counts]

        # V34: Get verified active city count (cities with actual data)
        verified_count = permitdb.get_prod_city_count()

        return jsonify({
            'active_cities': active_city_count,
            'verified_active_with_data': verified_count,
            'prod_cities_by_status': status_breakdown,
            'distinct_cities_in_permits': len(city_counts),
            'total_permits': total_permits,
            'total_states_with_data': len(state_counts),
            'states_covered': states_covered,
            'states_missing': states_missing,
            'top_50_cities': top_cities,
        })

    except Exception as e:
        return jsonify({'error': f'Failed to analyze coverage: {str(e)}'}), 500


@admin_bp.route('/api/admin/audit')
def admin_audit_cities():
    """V34: Comprehensive audit of all active cities vs actual permit data.
    Returns detailed report showing which cities have data, which don't,
    and recommendations for cleanup.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        results = permitdb.audit_prod_cities()
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': f'Audit failed: {str(e)}'}), 500


@admin_bp.route('/api/admin/reactivate-paused', methods=['POST'])
def admin_reactivate_paused():
    """V35: Lightweight endpoint to reactivate paused cities that have permit data.
    Only does one fast UPDATE — no heavy cleanup."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()
        # First sync counts for paused cities only
        paused = conn.execute(
            "SELECT id, city, state FROM prod_cities WHERE status = 'paused'"
        ).fetchall()
        updated_counts = 0
        for row in paused:
            actual = conn.execute(
                "SELECT COUNT(*) as cnt FROM permits WHERE LOWER(city) = LOWER(?) AND state = ?",
                (row['city'], row['state'])
            ).fetchone()['cnt']
            if actual > 0:
                conn.execute(
                    "UPDATE prod_cities SET total_permits = ?, status = 'active' WHERE id = ?",
                    (actual, row['id'])
                )
                updated_counts += 1
        conn.commit()

        # Get the updated list
        reactivated = conn.execute(
            "SELECT city, state, total_permits FROM prod_cities WHERE status = 'active' ORDER BY total_permits DESC"
        ).fetchall()

        return jsonify({
            'reactivated_count': updated_counts,
            'total_active': len(reactivated),
            'message': f'Reactivated {updated_counts} paused cities with data'
        })
    except Exception as e:
        return jsonify({'error': f'Reactivation failed: {str(e)}'}), 500


@admin_bp.route('/api/admin/scraper-history')
def admin_scraper_history():
    """V35: Per-city collection history from scraper_runs table.
    Shows last collection result for every city to identify broken vs working endpoints.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()
        # Get the most recent run for each city
        runs = conn.execute("""
            SELECT city_slug, source_name, city, state,
                   permits_found, permits_inserted, status, error_message,
                   duration_ms, run_started_at,
                   ROW_NUMBER() OVER (PARTITION BY city_slug ORDER BY run_started_at DESC) as rn
            FROM scraper_runs
        """).fetchall()

        # Filter to most recent per city
        latest = {}
        for r in runs:
            if r['city_slug'] not in latest or r['rn'] == 1:
                if r['rn'] == 1:
                    latest[r['city_slug']] = dict(r)

        # Categorize
        working = []  # returned permits
        empty = []    # success but 0 permits
        errored = []  # error status
        for slug, r in latest.items():
            entry = {
                'slug': slug,
                'name': r.get('source_name') or r.get('city') or slug,
                'state': r.get('state', ''),
                'permits_found': r.get('permits_found', 0),
                'status': r.get('status', ''),
                'error': r.get('error_message', ''),
                'last_run': r.get('run_started_at', ''),
                'duration_ms': r.get('duration_ms', 0),
            }
            if r.get('status') == 'error' or (r.get('error_message') and r.get('error_message') != ''):
                errored.append(entry)
            elif r.get('permits_found', 0) > 0:
                working.append(entry)
            else:
                empty.append(entry)

        return jsonify({
            'total_cities': len(latest),
            'working': len(working),
            'empty': len(empty),
            'errored': len(errored),
            'working_cities': sorted(working, key=lambda x: -x['permits_found']),
            'empty_cities': sorted(empty, key=lambda x: x['name']),
            'errored_cities': sorted(errored, key=lambda x: x['name']),
        })
    except Exception as e:
        return jsonify({'error': f'Scraper history failed: {str(e)}'}), 500


@admin_bp.route('/api/admin/test-and-backfill', methods=['POST'])
def admin_test_and_backfill():
    """V35: Test an endpoint, backfill 6 months of data, and activate the source.

    POST body: {"city_key": "phoenix"} — uses existing CITY_REGISTRY config
    OR: {"city_key": "new_city", "config": {...}} — provide full config

    Steps:
    1. Test: fetch 5 records to verify the endpoint works
    2. Backfill: fetch 180 days of historical data
    3. Normalize and insert into DB
    4. Activate: set city_source status='active', create/update prod_city
    5. Report results

    This ensures we KNOW the collector will succeed before activating.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        # V233b: accept `city_slug` as an alias. Cowork tooling sends
        # `city_slug` consistently across admin endpoints; this was the
        # one holdout that still required `city_key`.
        city_key = data.get('city_key') or data.get('city_slug')
        if not city_key:
            return jsonify({'error': 'city_key (or city_slug) is required'}), 400

        days_back = data.get('days_back', 180)

        # Import collection functions
        from collector import fetch_permits, normalize_permit
        from city_source_db import get_city_config

        # Get config (from request body or existing registry)
        config = data.get('config')
        if not config:
            config = get_city_config(city_key)
        if not config:
            return jsonify({'error': f'No config found for {city_key}. Provide config in request body.'}), 404

        # Force active for testing
        config['active'] = True

        # Step 1: TEST — fetch a small sample to verify endpoint works
        from collector import fetch_socrata, fetch_arcgis, fetch_ckan, fetch_carto
        platform = config.get('platform', 'socrata')
        test_config = dict(config)
        test_config['limit'] = 5  # Just 5 records for testing

        test_config['limit'] = 10  # Small sample for freshness check
        try:
            # V128: Test with 90-day window (was 30 — too strict for monthly-updating portals)
            if platform == 'socrata':
                test_raw = fetch_socrata(test_config, 90)
            elif platform == 'arcgis':
                test_raw = fetch_arcgis(test_config, 90)
            elif platform == 'ckan':
                test_raw = fetch_ckan(test_config, 90)
            elif platform == 'carto':
                test_raw = fetch_carto(test_config, 90)
            elif platform == 'accela':
                from accela_portal_collector import fetch_accela as _portal_fetch
                test_raw = _portal_fetch(test_config, 90)
            elif platform == 'accela_arcgis_hybrid':
                from accela_portal_collector import fetch_accela_arcgis_hybrid
                test_raw = fetch_accela_arcgis_hybrid(test_config, 90)
            else:
                return jsonify({'error': f'Unsupported platform: {platform}'}), 400
        except Exception as e:
            return jsonify({
                'status': 'FAILED',
                'step': 'test',
                'error': str(e),
                'message': f'Endpoint test failed for {city_key}. Do NOT activate.'
            }), 400

        if not test_raw:
            return jsonify({
                'status': 'FAILED',
                'step': 'test',
                'error': 'No permits in last 30 days',
                'message': f'{city_key} has no data in the last 90 days. Stale source — do NOT activate.'
            }), 400

        # Step 2: BACKFILL — fetch full historical data
        config['limit'] = config.get('limit', 2000)  # Restore normal limit
        try:
            if platform == 'socrata':
                raw = fetch_socrata(config, days_back)
            elif platform == 'arcgis':
                raw = fetch_arcgis(config, days_back)
            elif platform == 'ckan':
                raw = fetch_ckan(config, days_back)
            elif platform == 'carto':
                raw = fetch_carto(config, days_back)
            elif platform == 'accela':
                from accela_portal_collector import fetch_accela as _portal_fetch
                raw = _portal_fetch(config, days_back)
        except Exception as e:
            return jsonify({
                'status': 'FAILED',
                'step': 'backfill_fetch',
                'error': str(e),
                'test_passed': True,
                'test_records': len(test_raw),
            }), 500

        # V126: Save config to city_sources BEFORE normalizing so normalize_permit can find it
        try:
            from city_source_db import upsert_city_source
            import json as _json
            upsert_city_source({
                'source_key': city_key,
                'name': config.get('name', city_key),
                'state': config.get('state', ''),
                'platform': platform,
                'endpoint': config.get('endpoint', ''),
                'dataset_id': config.get('dataset_id', ''),
                'date_field': config.get('date_field', ''),
                'field_map': config.get('field_map', {}),
                'mode': 'city',
                'status': 'active',
            })
        except Exception as e:
            print(f"[V126] Warning: could not save config to city_sources: {e}", flush=True)

        # Step 3: NORMALIZE — convert raw records to our schema
        normalized = []
        for record in raw:
            try:
                permit = normalize_permit(record, city_key)
                if permit and permit.get('permit_number'):
                    normalized.append(permit)
            except Exception:
                continue

        if not normalized:
            return jsonify({
                'status': 'WARNING',
                'step': 'normalize',
                'raw_fetched': len(raw),
                'normalized': 0,
                'message': f'Got {len(raw)} raw records but 0 normalized. Check field_map config.'
            }), 400

        # Step 4: INSERT into DB
        # Always use hyphen-format of city_key as source_city_key — matches score query: REPLACE(source_key, '_', '-')
        source_slug = city_key.replace('_', '-')
        inserted = permitdb.upsert_permits(normalized, source_city_key=source_slug)

        # Step 5: ACTIVATE — update city_sources and prod_cities
        conn = permitdb.get_connection()

        # Activate in city_sources
        existing = conn.execute(
            "SELECT source_key FROM city_sources WHERE source_key = ?", (city_key,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE city_sources SET status = 'active' WHERE source_key = ?",
                (city_key,)
            )
        # No else — if it's not in city_sources, the CITY_REGISTRY dict entry is used

        # Create/update prod_city
        city_name = config.get('name', city_key.replace('_', ' ').title())
        state = config.get('state', '')
        from db import normalize_city_slug
        city_slug = normalize_city_slug(city_name)
        # Look up by city+state OR by slug (handles cases where slug exists from prior attempt)
        existing_prod = conn.execute(
            "SELECT id FROM prod_cities WHERE (city = ? AND state = ?) OR city_slug = ?",
            (city_name, state, city_slug)
        ).fetchone()
        if existing_prod:
            conn.execute("""
                UPDATE prod_cities SET status = 'active', total_permits = ?, source_id = ?,
                    city = ?, state = ?
                WHERE id = ?
            """, (len(normalized), city_key, city_name, state, existing_prod['id']))
        else:
            conn.execute("""
                INSERT INTO prod_cities (city, state, city_slug, source_id, status, total_permits)
                VALUES (?, ?, ?, ?, 'active', ?)
            """, (city_name, state, city_slug, city_key, len(normalized)))

        conn.commit()

        # V145: Update sources table metadata after successful test-and-backfill
        try:
            source_slug = city_key.replace('_', '-')
            # Try multiple key formats
            for sk in [source_slug, city_key, source_slug.split('-')[0]]:
                permitdb.get_connection().execute("""
                    UPDATE sources SET
                        total_permits = ?, newest_permit_date = (SELECT MAX(date) FROM permits WHERE source_city_key = ?),
                        last_attempt_at = datetime('now'), last_attempt_status = 'success',
                        last_success_at = datetime('now'), last_permits_found = ?, last_permits_inserted = ?,
                        consecutive_failures = 0, updated_at = datetime('now')
                    WHERE source_key = ?
                """, (len(normalized), source_slug, len(raw), inserted[0] if isinstance(inserted, (list, tuple)) else inserted, sk))
            permitdb.get_connection().commit()
        except Exception as src_e:
            print(f"[V145] sources update in test-and-backfill: {str(src_e)[:50]}")

        return jsonify({
            'status': 'SUCCESS',
            'city_key': city_key,
            'city_name': city_name,
            'state': state,
            'platform': platform,
            'test_records': len(test_raw),
            'raw_fetched': len(raw),
            'normalized': len(normalized),
            'inserted': inserted,
            'days_back': days_back,
            'message': f'✓ {city_name} is live. {len(normalized)} permits backfilled. Collector will pick it up next run.'
        })

    except Exception as e:
        return jsonify({'error': f'Test and backfill failed: {str(e)}'}), 500


@admin_bp.route('/api/admin/discover-and-activate', methods=['POST'])
def admin_discover_and_activate():
    """V35: Auto-discover fresh endpoints for stale cities, test, backfill, and activate.

    POST body (all optional):
      {"cities": ["milwaukee", "sacramento"]}  — specific cities to process
      If omitted, processes ALL stale cities from the discovery module.

    For each city:
    1. Search Socrata Discovery API, ArcGIS Hub, CKAN catalogs
    2. Test each discovered endpoint for 30-day freshness
    3. Build field mapping from sample data
    4. Backfill 180 days of historical data
    5. Normalize, insert, and activate

    Returns detailed results for each city.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from discover_fresh_endpoints import discover_all, STALE_CITIES
        from collector import normalize_permit, fetch_socrata, fetch_arcgis, fetch_ckan, fetch_carto
        from city_source_db import get_city_config
        from db import normalize_city_slug

        data = request.get_json() or {}
        target_cities = data.get('cities')  # None = all stale cities
        days_back = data.get('days_back', 180)
        dry_run = data.get('dry_run', False)

        # Step 1: Discover fresh endpoints
        discovery_results = discover_all(target_cities)

        # Step 2: For each FOUND city, run test-and-backfill
        activation_results = {}
        for city_key, disc in discovery_results.items():
            if disc["status"] not in ("FOUND", "EXISTING_WORKS"):
                activation_results[city_key] = {
                    "status": disc["status"],
                    "message": f"No fresh endpoint found: {disc['status']}",
                }
                continue

            if dry_run:
                activation_results[city_key] = {
                    "status": "DRY_RUN",
                    "config": disc["config"],
                    "freshness": disc["freshness"],
                    "message": f"Would activate with {disc['config']['platform']} endpoint",
                }
                continue

            config = disc["config"]
            platform = config.get("platform", "socrata")

            try:
                # Backfill: fetch 180 days
                config["active"] = True
                if platform == "socrata":
                    raw = fetch_socrata(config, days_back)
                elif platform == "arcgis":
                    raw = fetch_arcgis(config, days_back)
                elif platform == "ckan":
                    raw = fetch_ckan(config, days_back)
                elif platform == "carto":
                    raw = fetch_carto(config, days_back)
                else:
                    activation_results[city_key] = {"status": "ERROR", "error": f"Unknown platform: {platform}"}
                    continue

                if not raw:
                    activation_results[city_key] = {"status": "ERROR", "error": "Backfill returned 0 records"}
                    continue

                # Normalize — we need a config in the registry or city_sources for normalize_permit to work.
                # Use the discovered config's field_map directly.
                normalized = []
                fmap = config.get("field_map", {})
                city_name = config.get("name", city_key)
                state = config.get("state", "")

                for record in raw:
                    try:
                        # Manual normalization using discovered field_map
                        import re as _re
                        def _get(field_name):
                            raw_key = fmap.get(field_name, "")
                            if not raw_key:
                                return ""
                            return str(record.get(raw_key, "")).strip()

                        permit_number = _get("permit_number")
                        if not permit_number:
                            continue

                        # Parse date
                        date_str = _get("filing_date") or _get("date") or _get("issued_date")
                        parsed_date = ""
                        if date_str:
                            if str(date_str).isdigit() and len(str(date_str)) >= 10:
                                try:
                                    parsed_date = datetime.fromtimestamp(int(date_str) / 1000).strftime("%Y-%m-%d")
                                except (ValueError, OSError):
                                    pass
                            if not parsed_date:
                                for fmt in ["%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"]:
                                    try:
                                        parsed_date = datetime.strptime(str(date_str)[:26], fmt).strftime("%Y-%m-%d")
                                        break
                                    except ValueError:
                                        continue
                            if not parsed_date and '/' in str(date_str):
                                try:
                                    parts = str(date_str).split()[0].split('/')
                                    if len(parts) == 3:
                                        m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                                        parsed_date = f"{y:04d}-{m:02d}-{d:02d}"
                                except (ValueError, IndexError):
                                    pass
                            if not parsed_date:
                                parsed_date = str(date_str)[:10]

                        # Parse cost
                        cost_str = _get("estimated_cost")
                        try:
                            cost = float(_re.sub(r'[^\d.]', '', cost_str)) if cost_str else 0
                        except (ValueError, TypeError):
                            cost = 0
                        if cost > 50_000_000:
                            cost = 50_000_000

                        address = _get("address") or "Address not provided"
                        description = _get("description") or _get("work_type") or ""

                        normalized.append({
                            "permit_number": permit_number,
                            "permit_type": _get("permit_type") or "Building Permit",
                            "work_type": _get("work_type") or "",
                            "address": address,
                            "city": city_name,
                            "state": state,
                            "zip": _get("zip") or "",
                            "filing_date": parsed_date,
                            "status": _get("status") or "",
                            "estimated_cost": cost,
                            "description": description,
                            "owner_name": _get("owner_name") or "",
                            "contact_name": _get("contact_name") or "",
                        })
                    except Exception:
                        continue

                if not normalized:
                    activation_results[city_key] = {
                        "status": "ERROR",
                        "error": f"Got {len(raw)} raw records but 0 normalized. Field map may be wrong.",
                        "config": config,
                    }
                    continue

                # Insert
                inserted = permitdb.upsert_permits(normalized, source_city_key=city_key)

                # Activate in city_sources
                conn = permitdb.get_connection()
                existing = conn.execute(
                    "SELECT source_key FROM city_sources WHERE source_key = ?", (city_key,)
                ).fetchone()

                if existing:
                    # Update existing source with new endpoint info
                    conn.execute("""
                        UPDATE city_sources SET
                            status = 'active',
                            endpoint = ?,
                            platform = ?,
                            date_field = ?,
                            field_map = ?
                        WHERE source_key = ?
                    """, (
                        config["endpoint"],
                        platform,
                        config.get("date_field", ""),
                        json.dumps(config.get("field_map", {})),
                        city_key,
                    ))
                else:
                    # Insert new city_source
                    conn.execute("""
                        INSERT INTO city_sources (source_key, name, state, platform, endpoint,
                            date_field, field_map, status, mode)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 'city')
                    """, (
                        city_key,
                        city_name,
                        state,
                        platform,
                        config["endpoint"],
                        config.get("date_field", ""),
                        json.dumps(config.get("field_map", {})),
                    ))

                # Create/update prod_city (lookup by slug too to avoid UNIQUE constraint)
                city_slug = normalize_city_slug(city_name)
                existing_prod = conn.execute(
                    "SELECT id FROM prod_cities WHERE (city = ? AND state = ?) OR city_slug = ?",
                    (city_name, state, city_slug)
                ).fetchone()
                if existing_prod:
                    conn.execute("""
                        UPDATE prod_cities SET status = 'active', total_permits = ?, source_id = ?,
                            city = ?, state = ?
                        WHERE id = ?
                    """, (len(normalized), city_key, city_name, state, existing_prod['id']))
                else:
                    conn.execute("""
                        INSERT INTO prod_cities (city, state, city_slug, source_id, status, total_permits)
                        VALUES (?, ?, ?, ?, 'active', ?)
                    """, (city_name, state, city_slug, city_key, len(normalized)))

                conn.commit()

                activation_results[city_key] = {
                    "status": "ACTIVATED",
                    "raw_fetched": len(raw),
                    "normalized": len(normalized),
                    "inserted": inserted,
                    "platform": platform,
                    "endpoint": config["endpoint"],
                    "date_field": config.get("date_field"),
                    "newest_date": disc["freshness"].get("newest_date"),
                    "message": f"✓ {city_name} is live. {len(normalized)} permits backfilled.",
                }

            except Exception as e:
                activation_results[city_key] = {
                    "status": "ERROR",
                    "error": str(e),
                    "config": config,
                }

        # Summary
        activated = [k for k, v in activation_results.items() if v.get("status") == "ACTIVATED"]
        failed = [k for k, v in activation_results.items() if v.get("status") == "ERROR"]
        not_found = [k for k, v in activation_results.items() if v.get("status") in ("NOT_FOUND", "STALE")]

        return jsonify({
            "summary": {
                "activated": len(activated),
                "failed": len(failed),
                "not_found": len(not_found),
                "dry_run": dry_run,
            },
            "activated_cities": activated,
            "failed_cities": failed,
            "not_found_cities": not_found,
            "details": activation_results,
            "discovery": {k: {
                "status": v["status"],
                "endpoints_found": len(v.get("all_discovered", [])),
                "endpoints_tested": len(v.get("all_tested", [])),
            } for k, v in discovery_results.items()},
        })

    except Exception as e:
        import traceback
        return jsonify({'error': f'Discovery failed: {str(e)}', 'traceback': traceback.format_exc()}), 500


@admin_bp.route('/api/admin/pause-empty', methods=['POST'])
def admin_pause_empty_cities():
    """V34: Pause all active prod_cities that have 0 actual permits in DB.
    This cleans up cities that are marked active but have never successfully collected data.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        paused = permitdb.pause_cities_without_data()
        return jsonify({
            'paused_count': len(paused),
            'paused_cities': paused,
            'message': f'Paused {len(paused)} cities with no permit data'
        })
    except Exception as e:
        return jsonify({'error': f'Pause operation failed: {str(e)}'}), 500


@admin_bp.route('/api/admin/cleanup-data', methods=['POST'])
def admin_cleanup_data():
    """V35: Run comprehensive data cleanup — fix wrong states, remove garbage records.
    This is safe to run multiple times (idempotent).
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()
        before = conn.execute("SELECT COUNT(*) as cnt FROM permits").fetchone()['cnt']

        # Step 1: Clean prod_cities names first (so state lookups work)
        prod_all = conn.execute("SELECT id, city, state FROM prod_cities").fetchall()
        name_fixes = 0
        for row in prod_all:
            cleaned = permitdb.clean_city_name_for_prod(row['city'], row['state'])
            if cleaned != row['city']:
                # Check if cleaned name already exists (avoid UNIQUE constraint violation)
                existing = conn.execute(
                    "SELECT id FROM prod_cities WHERE city = ? AND state = ?",
                    (cleaned, row['state'])
                ).fetchone()
                if existing:
                    conn.execute("DELETE FROM prod_cities WHERE id = ?", (row['id'],))
                else:
                    conn.execute("UPDATE prod_cities SET city = ? WHERE id = ?", (cleaned, row['id']))
                name_fixes += 1
        conn.commit()

        # Step 2: Fix wrong states using cleaned prod_cities as truth
        prod_rows = conn.execute(
            "SELECT city, state FROM prod_cities WHERE state IS NOT NULL AND state != ''"
        ).fetchall()
        state_fixes = 0
        for row in prod_rows:
            result = conn.execute(
                "UPDATE permits SET state = ? WHERE (city = ? OR LOWER(city) = LOWER(?)) AND state != ?",
                (row['state'], row['city'], row['city'], row['state'])
            )
            if result.rowcount > 0:
                state_fixes += result.rowcount
        conn.commit()

        # Step 3: Run V34/V35 data cleanup (garbage deletion, casing fixes)
        cleanup_fixed = permitdb._run_v34_data_cleanup(conn)

        # Step 4: Sync permit counts
        permitdb._sync_prod_city_counts(conn)

        after = conn.execute("SELECT COUNT(*) as cnt FROM permits").fetchone()['cnt']

        return jsonify({
            'prod_city_names_fixed': name_fixes,
            'state_assignments_fixed': state_fixes,
            'cleanup_records_affected': cleanup_fixed,
            'permits_before': before,
            'permits_after': after,
            'permits_removed': before - after,
        })
    except Exception as e:
        return jsonify({'error': f'Cleanup failed: {str(e)}'}), 500


@admin_bp.route('/api/admin/migrate-violations', methods=['POST'])
def admin_migrate_violations():
    """V162: Drop and recreate violations table with new schema."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        conn.execute("DROP TABLE IF EXISTS violations")
        conn.commit()
        from violation_collector import _ensure_table
        _ensure_table()
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='violations'").fetchone()
        return jsonify({'success': True, 'schema': schema[0] if schema else 'not found'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/backfill-trade-tags', methods=['POST'])
def admin_backfill_trade_tags():
    """V170 B1: Backfill trade_tag on existing permits. Runs in background thread."""
    valid, error = check_admin_key()
    if not valid:
        return error

    def _backfill():
        from collector import classify_trade
        conn = permitdb.get_connection()
        total = conn.execute("SELECT COUNT(*) FROM permits WHERE trade_category IS NULL OR trade_category = ''").fetchone()[0]
        print(f"[BACKFILL] Starting trade_tag backfill: {total} permits to process")
        offset = 0
        batch_size = 10000
        updated = 0
        while True:
            rows = conn.execute(
                "SELECT id, description, permit_type FROM permits "
                "WHERE (trade_category IS NULL OR trade_category = '') "
                "ORDER BY id LIMIT ? OFFSET ?", (batch_size, offset)
            ).fetchall()
            if not rows:
                break
            for r in rows:
                tag = classify_trade(r['description'] or r['permit_type'], r['permit_type'])
                conn.execute("UPDATE permits SET trade_category = ? WHERE id = ?", (tag, r['id']))
                updated += 1
            conn.commit()
            offset += batch_size
            print(f"[BACKFILL] {updated}/{total} ({100*updated//max(total,1)}%)", flush=True)
        print(f"[BACKFILL] Complete: {updated} permits tagged")

    t = threading.Thread(target=_backfill, daemon=True, name='trade_backfill')
    t.start()
    return jsonify({'status': 'started', 'message': 'Trade tag backfill running in background'})


@admin_bp.route('/api/admin/collection-log', methods=['GET'])
def admin_collection_log():
    """V214: Show the N most-recent entries from collection_log — gives operators
    (and future debuggers) a view into silent-failure patterns like the V213
    Carto-parse bug. Requires the usual admin auth."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        limit = int(request.args.get('limit', 50))
    except ValueError:
        limit = 50
    limit = min(max(limit, 1), 500)
    conn = permitdb.get_connection()
    try:
        rows = conn.execute("""
            SELECT city_slug, collection_type, status,
                   records_fetched, records_inserted, error_message,
                   duration_seconds, created_at,
                   api_url, query_params,
                   api_rows_returned, duplicate_rows_skipped
            FROM collection_log
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
    except Exception as e:
        return jsonify({'error': f'table query failed: {e}'}), 500
    out = []
    for r in rows:
        if hasattr(r, 'keys'):
            out.append({k: r[k] for k in r.keys()})
        else:
            out.append({
                'city_slug': r[0], 'collection_type': r[1], 'status': r[2],
                'records_fetched': r[3], 'records_inserted': r[4],
                'error_message': r[5],
                'duration_seconds': round(r[6], 2) if r[6] is not None else None,
                'created_at': r[7],
                'api_url': r[8], 'query_params': r[9],
                'api_rows_returned': r[10], 'duplicate_rows_skipped': r[11],
            })
    # V215: three-state status surface for quick triage
    totals = {'success': 0, 'caught_up': 0, 'no_api_data': 0,
              'error': 0, 'empty': 0}
    for row in out:
        s = row.get('status', 'unknown')
        totals[s] = totals.get(s, 0) + 1
    return jsonify({'rows': out, 'totals': totals, 'limit': limit})


@admin_bp.route('/api/admin/recalc-freshness', methods=['POST'])
def admin_recalc_freshness():
    """V170: One-shot freshness recalc using correct Postgres schema.
    Join: permits.source_city_key = prod_cities.source_id
    Dates: TEXT columns, need substring cast for comparison."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        import psycopg2
        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute("SET statement_timeout = '600000'")
        # Step 1: backfill NULL newest_permit_date
        cur.execute("""
            UPDATE prod_cities
            SET newest_permit_date = sub.max_date
            FROM (
                SELECT source_city_key,
                       MAX(COALESCE(NULLIF(filing_date, ''), NULLIF(issued_date, ''), NULLIF(date, ''),
                           to_char(collected_at, 'YYYY-MM-DD'))) AS max_date
                FROM permits
                WHERE source_city_key IS NOT NULL
                GROUP BY source_city_key
            ) sub
            WHERE prod_cities.source_id = sub.source_city_key
              AND prod_cities.source_type IS NOT NULL
              AND prod_cities.newest_permit_date IS NULL
              AND sub.max_date IS NOT NULL
              AND sub.max_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
        """)
        n_dates = cur.rowcount
        # Step 2: bucket data_freshness
        cur.execute("""
            UPDATE prod_cities SET data_freshness = CASE
                WHEN substring(newest_permit_date, 1, 10)::date >= CURRENT_DATE - INTERVAL '7 days' THEN 'fresh'
                WHEN substring(newest_permit_date, 1, 10)::date >= CURRENT_DATE - INTERVAL '30 days' THEN 'aging'
                WHEN substring(newest_permit_date, 1, 10)::date >= CURRENT_DATE - INTERVAL '90 days' THEN 'stale'
                ELSE 'no_data'
            END
            WHERE source_type IS NOT NULL
              AND newest_permit_date IS NOT NULL
              AND newest_permit_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
        """)
        n_freshness = cur.rowcount
        conn.commit()
        # Get result
        cur.execute("SELECT data_freshness, COUNT(*) as cnt FROM prod_cities WHERE source_type IS NOT NULL GROUP BY 1 ORDER BY cnt DESC")
        rows = cur.fetchall()
        conn.close()
        return jsonify({
            'status': 'complete',
            'dates_updated': n_dates,
            'freshness_updated': n_freshness,
            'distribution': {r[0]: r[1] for r in rows},
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/backfill-normalized-addresses', methods=['POST'])
def admin_backfill_addresses():
    """V170 B5: Backfill normalized_address on permits + violations."""
    valid, error = check_admin_key()
    if not valid:
        return error

    def _backfill():
        from address_utils import normalize_address
        conn = permitdb.get_connection()
        # Add columns if missing
        for table in ('permits', 'violations'):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN normalized_address TEXT")
            except Exception:
                pass  # Column already exists
            try:
                conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_norm_addr ON {table}(normalized_address, city, state)")
            except Exception:
                pass
        conn.commit()

        for table, addr_col in [('permits', 'address'), ('violations', 'address')]:
            total = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE normalized_address IS NULL AND {addr_col} IS NOT NULL").fetchone()[0]
            print(f"[BACKFILL] {table}: {total} rows to normalize", flush=True)
            offset = 0
            updated = 0
            while True:
                rows = conn.execute(
                    f"SELECT id, {addr_col} FROM {table} WHERE normalized_address IS NULL AND {addr_col} IS NOT NULL ORDER BY id LIMIT 10000 OFFSET ?",
                    (offset,)
                ).fetchall()
                if not rows:
                    break
                for r in rows:
                    norm = normalize_address(r[addr_col] if isinstance(r, dict) else r[1])
                    rid = r['id'] if isinstance(r, dict) else r[0]
                    conn.execute(f"UPDATE {table} SET normalized_address = ? WHERE id = ?", (norm, rid))
                    updated += 1
                conn.commit()
                offset += 10000
                print(f"[BACKFILL] {table}: {updated}/{total} ({100*updated//max(total,1)}%)", flush=True)
            print(f"[BACKFILL] {table}: complete ({updated} normalized)", flush=True)

    t = threading.Thread(target=_backfill, daemon=True, name='addr_backfill')
    t.start()
    return jsonify({'status': 'started', 'message': 'Address normalization backfill running in background'})


@admin_bp.route('/api/admin/collect-violations', methods=['POST'])
def admin_collect_violations():
    """V162: Trigger violation collection from all configured Socrata sources."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from violation_collector import collect_violations
        results = collect_violations()
        # Get totals
        conn = permitdb.get_connection()
        totals = conn.execute(
            "SELECT city, state, COUNT(*) as cnt, MAX(violation_date) as newest "
            "FROM violations GROUP BY city, state ORDER BY cnt DESC"
        ).fetchall()
        # V215: results[slug] is a diagnostic dict — flatten 'inserted'
        # to preserve the shape older consumers of this endpoint expect,
        # but also pass the full diagnostics through.
        _flat = {}
        if isinstance(results, dict):
            for _slug, _agg in results.items():
                if isinstance(_agg, dict):
                    _flat[_slug] = _agg.get('inserted', 0) or 0
                else:
                    _flat[_slug] = int(_agg or 0)
        return jsonify({
            'collection_results': _flat,
            'diagnostics': results,
            'totals': [dict(r) for r in totals],
        })
    except Exception as e:
        return jsonify({'error': f'Violation collection failed: {str(e)}'}), 500


@admin_bp.route('/api/admin/digest/status', methods=['GET'])
def admin_digest_status():
    """V158: Get digest daemon status and recent history."""
    valid, error = check_admin_key()
    if not valid:
        return error

    # Check if email_scheduler thread is alive
    daemon_alive = False
    for t in threading.enumerate():
        if t.name == 'email_scheduler' and t.is_alive():
            daemon_alive = True
            break

    # Get recent digest logs
    conn = permitdb.get_connection()
    recent_logs = []
    try:
        rows = conn.execute(
            "SELECT * FROM digest_log ORDER BY sent_at DESC LIMIT 10"
        ).fetchall()
        recent_logs = [dict(r) for r in rows]
    except Exception:
        pass

    # Get subscriber count
    sub_count = 0
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM subscribers WHERE active=1").fetchone()
        sub_count = row['cnt'] if row else 0
    except Exception:
        # Fall back to JSON file
        try:
            from email_alerts import load_subscribers
            sub_count = len(load_subscribers())
        except Exception:
            pass

    return jsonify({
        'daemon_alive': daemon_alive,
        'digest_status': DIGEST_STATUS,
        'subscriber_count': sub_count,
        'recent_logs': recent_logs,
        'smtp_configured': bool(os.environ.get('SMTP_PASS', '')),
    })


@admin_bp.route('/api/admin/digest/trigger', methods=['POST'])
def admin_digest_trigger():
    """V158: Manually trigger a digest send."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from email_alerts import send_daily_digest, send_test_digest
        data = request.get_json() or {}

        # Option 1: Send to specific email
        test_email = data.get('email')
        if test_email:
            result = send_test_digest(test_email, city=data.get('city'))
            _log_digest(test_email, result, 'manual_trigger')
            return jsonify({'status': 'sent', 'email': test_email, 'result': str(result)})

        # Option 2: Send full digest to all subscribers
        sent, failed = send_daily_digest()
        _log_digest('all_subscribers', f'sent={sent},failed={failed}', 'manual_trigger')
        return jsonify({'status': 'sent', 'sent': sent, 'failed': failed})
    except Exception as e:
        _log_digest('error', str(e), 'manual_trigger_error')
        return jsonify({'error': f'Digest trigger failed: {str(e)}'}), 500


@admin_bp.route('/api/admin/debug-property-owners', methods=['POST'])
def admin_debug_property_owners():
    """V285 diagnostic: call _get_property_owners() in the same runtime
    the city_landing renderer uses, so we can see why V284's section
    is empty on /permits/new-york-city despite the raw SQL returning
    5 rows. Returns the exact dict list the template would receive.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    body = request.get_json(silent=True) or {}
    city = body.get('city') or 'New York City'
    state = body.get('state') or 'NY'
    rows = _get_property_owners(city, state, limit=10)
    return jsonify({'city': city, 'state': state, 'count': len(rows), 'rows': rows})


@admin_bp.route('/api/admin/extract-property-owners', methods=['POST'])
def admin_extract_property_owners():
    """V278 (task doc V276 Phase 1): Extract owner_name from already-
    collected permits into the property_owners table. Runs for the
    cities that have owner_name populated in their permit rows (NYC
    via field_map owner_name=owner_name from DOB NOW dq6g-a4sc).

    Miami-Dade's field_map maps ownername → owner_name but the live
    permits table has 0 populated rows — the collector or upstream
    feed isn't carrying the value through. That's a separate bug;
    this route extracts what's actually in permits today and skips
    cities with no populated owner_name.

    Chicago's task-doc plan assumed a contact_type column with
    'OWNER' values; the permits table has only a single contact_name
    field with no type discriminator, so Chicago is a no-op here
    until the collector is extended.

    Uses INSERT OR IGNORE against the unique (address, owner_name,
    source) index added in V278 so re-runs are idempotent.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    body = request.get_json(silent=True) or {}
    slugs = body.get('source_city_keys') or ['new-york-city', 'miami-dade-county']
    lookback_days = int(body.get('lookback_days') or 365)
    conn = permitdb.get_connection()
    results = {}
    for slug in slugs:
        row = conn.execute(
            "SELECT COUNT(*) FROM permits WHERE source_city_key = ? "
            "AND owner_name IS NOT NULL AND LENGTH(TRIM(owner_name)) > 2 "
            "AND address IS NOT NULL AND LENGTH(TRIM(address)) > 3 "
            "AND date > date('now', ?)",
            (slug, f'-{lookback_days} days')
        ).fetchone()
        candidate_count = row[0] if not isinstance(row, dict) else row['COUNT(*)']
        source_tag = f'permit_record:{slug}'
        before = conn.execute(
            "SELECT COUNT(*) FROM property_owners WHERE source = ?",
            (source_tag,)
        ).fetchone()
        before_n = before[0] if not isinstance(before, dict) else before['COUNT(*)']
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO property_owners
                    (address, city, state, zip, owner_name, source, last_updated)
                SELECT DISTINCT
                    TRIM(p.address), p.city, p.state, p.zip,
                    TRIM(p.owner_name), ?, datetime('now')
                FROM permits p
                WHERE p.source_city_key = ?
                  AND p.owner_name IS NOT NULL
                  AND LENGTH(TRIM(p.owner_name)) > 2
                  AND p.address IS NOT NULL
                  AND LENGTH(TRIM(p.address)) > 3
                  AND p.date > date('now', ?)
                """,
                (source_tag, slug, f'-{lookback_days} days')
            )
            conn.commit()
            after = conn.execute(
                "SELECT COUNT(*) FROM property_owners WHERE source = ?",
                (source_tag,)
            ).fetchone()
            after_n = after[0] if not isinstance(after, dict) else after['COUNT(*)']
            results[slug] = {
                'candidate_permit_rows': candidate_count,
                'before': before_n,
                'after': after_n,
                'added': after_n - before_n,
            }
        except Exception as e:
            results[slug] = {
                'candidate_permit_rows': candidate_count,
                'error': str(e)[:300],
            }
    return jsonify({'status': 'ok', 'lookback_days': lookback_days, 'results': results})


@admin_bp.route('/api/admin/fix-property-owner-cities', methods=['POST'])
def admin_fix_property_owner_cities():
    """V429 (CODE_V428 Phase 4): retag misattributed property_owners
    rows. The Maricopa County feed pulls Phoenix-metro suburb names
    (TOLLESON, AVONDALE, GLENDALE) and the Bexar County feed pulls
    San Antonio-metro suburb names (ELMENDORF) into the property's
    own city field — but the city we want for matching against
    permits.source_city_key is the parent metro slug.

    This one-shot migration normalizes by `source` tag:
      assessor:maricopa* → city='Phoenix' (covers TOLLESON, AVONDALE, etc.)
      assessor:bexar*    → city='San Antonio' (covers ELMENDORF)
    Plus fills the null-city rows whose source tag tells us the metro.

    Idempotent — running multiple times just re-stamps the same value.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        # V437: optional `target` filter so a single rule fires per call.
        # Avoids gunicorn 120s timeout when all 12 rules contend for the
        # write lock under heavy concurrent assessor imports.
        target_filter = (request.args.get('target') or '').strip()
        if not target_filter and request.is_json:
            try:
                body = request.get_json(silent=True) or {}
                target_filter = (body.get('target') or '').strip()
            except Exception:
                target_filter = ''

        conn = permitdb.get_connection()
        # Track before/after counts per metro for the response payload.
        def _count(city):
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM property_owners WHERE city = ?",
                (city,)
            ).fetchone()
            return row[0] if not isinstance(row, dict) else row['c']

        rules = [
            # (target_city, where_clause)
            ('Phoenix', "source LIKE 'assessor:maricopa%'"),
            ('San Antonio', "source LIKE 'assessor:bexar%'"),
            ('Chicago', "source LIKE 'assessor:cook%'"),
            ('Cleveland', "source LIKE 'assessor:cuyahoga%'"),
            # V430: Miami-Dade county feed covers Hialeah too. Tagging as
            # "Miami-Dade" matches the prod_cities slug "miami-dade-county"
            # via the address-match join; Hialeah rows also surface in
            # Hialeah city pages because the address-normalizer scopes by
            # city slug from the permits side.
            ('Miami-Dade', "source LIKE 'assessor:miami_dade%'"),
            ('Nashville', "source LIKE 'assessor:davidson_nashville%'"),
            ('Philadelphia', "source LIKE 'assessor:philadelphia_opa%'"),
            ('Minneapolis', "source LIKE 'assessor:hennepin_minneapolis%'"),
            ('Austin', "source LIKE 'assessor:travis_austin%'"),
            ('Cincinnati', "source LIKE 'assessor:hamilton_cincinnati%'"),
            ('Portland', "source LIKE 'assessor:multnomah_portland%'"),
            # erie_buffalo intentionally NOT retagged — source MUNI_NAME
            # is populated per-row (Buffalo, Cheektowaga, Lackawanna,
            # Tonawanda, Akron, etc.) and should flow through verbatim
            # so non-Buffalo Erie County rows aren't misattributed.
            # Same pattern as Clark County (LV+Henderson).
            ('New York', "source LIKE 'assessor:nyc_pluto%'"),
        ]

        if target_filter:
            rules = [(t, w) for (t, w) in rules if t.lower() == target_filter.lower()]
            if not rules:
                return jsonify({
                    'status': 'error',
                    'error': f'no rule for target={target_filter!r}'
                }), 400

        # V436 (CODE_V434 follow-on): set busy_timeout high so the
        # UPDATE waits for concurrent assessor-import write locks
        # instead of failing immediately with "database is locked".
        try:
            conn.execute("PRAGMA busy_timeout = 60000")
        except Exception:
            pass

        import time as _time
        report = {}
        for target, where in rules:
            before = _count(target)
            updated = None
            last_err = None
            # V437: 3 retries with shorter backoff (2,4,8s = 14s ladder)
            # so a single-target call stays under gunicorn 120s. Combine
            # with `?target=Phoenix`-style chunking to retag all rules
            # without timing out under concurrent import write-lock load.
            for attempt in range(3):
                try:
                    cur = conn.execute(
                        f"UPDATE property_owners SET city = ? WHERE {where}",
                        (target,)
                    )
                    conn.commit()
                    updated = cur.rowcount
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    msg = str(e).lower()
                    if 'lock' in msg or 'busy' in msg:
                        _time.sleep(2 * (2 ** attempt))  # 2,4,8s
                        continue
                    break
            if last_err is not None:
                report[target] = {'error': str(last_err)[:200], 'before': before}
            else:
                report[target] = {
                    'before': before,
                    'after': _count(target),
                    'rows_updated': updated,
                }

        return jsonify({'status': 'ok', 'rules_applied': len(rules), 'report': report})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'error': str(e)[:500]}), 500


@admin_bp.route('/api/admin/collect-assessor-data', methods=['POST'])
def admin_collect_assessor_data():
    """V279 (task doc V276 Phase 2): Collect property owner + address
    rows from a county assessor open data portal into property_owners.

    Body:
      {
        "source": "maricopa",          # key in assessor_collector.ASSESSOR_SOURCES
        "max_records": 5000,           # optional cap for smoke-test calls
        "page_size": 1000,             # optional override (default 1000)
        "start_offset": 0              # optional resume
      }

    This endpoint is synchronous on a Render worker (30-60s request
    limit). For full backfills (Maricopa = 1.76M parcels, Phoenix
    alone ~600K), call repeatedly with start_offset chained off the
    previous response's last_offset. Alternately, wrap in a future
    background task — out of V279 scope.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    body = request.get_json(silent=True) or {}
    source = body.get('source') or 'maricopa'
    max_records = body.get('max_records')
    page_size = body.get('page_size')
    start_offset = int(body.get('start_offset') or 0)
    try:
        from assessor_collector import collect as assessor_collect
    except ImportError as e:
        return jsonify({'status': 'error', 'error': f'assessor_collector import failed: {e}'}), 500
    try:
        result = assessor_collect(
            source,
            max_records=max_records,
            page_size=page_size,
            start_offset=start_offset,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({'status': 'error', 'error': str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'error': str(e)[:500]}), 500


@admin_bp.route('/api/admin/refresh-city-stats', methods=['POST'])
def admin_refresh_city_stats():
    """V492: force-refresh the city_stats system_state cache.

    Body:
      {"city": "buffalo-ny"}     # refresh ONE city
      {}                          # refresh ALL active cities
      {"limit": 20}               # refresh top-20 only (warm path)

    Returns the count refreshed. Use after big imports (V489/V490/V491
    drainage) so the city pages immediately reflect the new owner /
    permit / violation totals — otherwise the cache is up to 4 hr stale
    until worker.secondary_loop refreshes it.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    payload = request.get_json(silent=True) or {}
    try:
        from routes.city_stats_cache import (
            refresh_city_stats_cache,
            refresh_city_stats_cache_all,
        )
        if payload.get('city'):
            data = refresh_city_stats_cache(payload['city'])
            return jsonify({'status': 'ok', 'city': payload['city'], 'data': data})
        n_ok, n_total = refresh_city_stats_cache_all(limit=payload.get('limit'))
        return jsonify({'status': 'ok', 'refreshed': n_ok, 'total': n_total})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)[:300]}), 500


@admin_bp.route('/api/admin/patch-source-endpoint', methods=['POST'])
def admin_patch_source_endpoint():
    """V496: targeted patch for prod_cities rows whose source_endpoint
    drifted to NULL but the source_id still maps to a live registry
    entry. Body: {"cities": [{"slug": "cook-county",
    "source_endpoint": "https://...", "source_id": "cook_county",
    "source_type": "socrata"}]}. Returns per-row update counts.

    LESSON 2026-05-04: do NOT follow a bulk patch with parallel
    force-collect against many of the patched cities at once. Each
    force-collect holds a SQLite write transaction; >2 concurrent
    plus the daemon will WAL-deadlock the gunicorn workers and
    require a deploy to recover. Use /api/admin/force-collection
    (background full-cycle) instead, or sequential force-collect."""
    valid, error = check_admin_key()
    if not valid:
        return error
    payload = request.get_json(silent=True) or {}
    cities = payload.get('cities') or []
    if not cities:
        return jsonify({'error': 'cities[] required'}), 400
    conn = permitdb.get_connection()
    out = []
    for c in cities:
        slug = c.get('slug')
        if not slug:
            out.append({'slug': None, 'updated': 0, 'error': 'missing slug'})
            continue
        n = conn.execute("""
            UPDATE prod_cities SET source_endpoint = ?,
                source_id = COALESCE(?, source_id),
                source_type = COALESCE(?, source_type),
                status = COALESCE(?, status),
                pause_reason = COALESCE(?, pause_reason),
                consecutive_failures = 0,
                last_failure_reason = NULL
            WHERE city_slug = ?
        """, (c.get('source_endpoint'), c.get('source_id'),
              c.get('source_type'), c.get('status'),
              c.get('pause_reason'), slug)).rowcount
        out.append({'slug': slug, 'updated': n})
    conn.commit()
    return jsonify({'status': 'ok', 'patched': out})


@admin_bp.route('/api/admin/email-test', methods=['POST'])
def admin_email_test():
    """V495 Phase 3 follow-up: smoke-test the send_sales_email() path
    after Cloudflare Email Routing goes live for permitgrab.com.
    Body: {"to": "wcrainshaw@gmail.com", "kind": "sales" | "alerts"}.
    Returns provider response."""
    valid, error = check_admin_key()
    if not valid:
        return error
    data = request.get_json(silent=True) or {}
    to_email = data.get('to') or 'wcrainshaw@gmail.com'
    kind = (data.get('kind') or 'sales').lower()
    subject = f"PermitGrab email-test ({kind}) — {datetime.now().isoformat()}"
    body = (
        f"<p>This is a {kind}-channel test email.</p>"
        f"<p>If you received this in wcrainshaw@gmail.com, "
        f"the {kind} send path is working.</p>"
        f"<p>Reply to this email — the reply should also land in your "
        f"gmail inbox via Cloudflare Email Routing.</p>"
    )
    if kind == 'sales':
        from email_alerts import send_sales_email
        result = send_sales_email(to_email, subject, body)
    else:
        from email_alerts import send_email
        result = send_email(to_email, subject, body)
    return jsonify({'status': 'sent', 'to': to_email,
                    'kind': kind, 'result': str(result)})


@admin_bp.route('/api/admin/manual-subscriber', methods=['POST'])
def admin_manual_subscriber():
    """V494 emergency recovery: manually create or update a subscribers
    row for a paid customer who slipped through the no-city-capture
    signup gap (Higgins May 2, Meyer May 1, Gomes earlier).

    Body:
      {"email":  "...",          # required
       "name":   "...",          # optional, default ''
       "cities": ["miami-dade-county", "phoenix-az"],  # required, non-empty
       "plan":   "pro"}          # optional, default 'pro'

    Idempotent — uses INSERT OR REPLACE on email PK. Re-running with
    the same email updates the existing row.

    This endpoint stays in place even after V494 structural fix ships
    (concurrent recovery + the alert-Wes-on-orphan-checkout path in
    the webhook still benefits from a clean way to backfill).
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    name = (data.get('name') or '').strip()
    cities = data.get('cities') or []
    plan = (data.get('plan') or 'pro').strip().lower()
    if not email:
        return jsonify({'error': 'email required'}), 400
    if not cities or not isinstance(cities, list):
        return jsonify({'error': 'cities (non-empty list) required'}), 400
    import json as _j
    try:
        conn = permitdb.get_connection()
        # Defensive UPSERT — subscribers.email may not have a UNIQUE
        # constraint depending on schema-migration history. Use a
        # SELECT-then-UPDATE-OR-INSERT pattern instead of ON CONFLICT.
        existing = conn.execute(
            "SELECT id FROM subscribers WHERE LOWER(email) = ?",
            (email,)
        ).fetchone()
        if existing:
            sub_id = existing[0]
            conn.execute(
                "UPDATE subscribers SET name = ?, plan = ?, "
                "  digest_cities = ?, active = 1 "
                "WHERE id = ?",
                (name, plan, _j.dumps(cities), sub_id)
            )
            action = 'updated'
        else:
            conn.execute(
                "INSERT INTO subscribers "
                "(email, name, plan, digest_cities, active, created_at) "
                "VALUES (?, ?, ?, ?, 1, datetime('now'))",
                (email, name, plan, _j.dumps(cities))
            )
            action = 'created'
        conn.commit()
        return jsonify({'status': action, 'email': email,
                        'cities': cities, 'plan': plan})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)[:300]}), 500


@admin_bp.route('/api/admin/staleness-report', methods=['GET'])
def admin_staleness_report():
    """V493 IRONCLAD: which active cities haven't been collected lately.
    Slow query (68K-row scraper_runs JOIN), so NOT in the health probe.
    Hit on demand to investigate stale cities.

    Returns bucketed counts and the worst-offender list.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    conn = permitdb.get_connection()
    try:
        bucket_rows = conn.execute("""
            SELECT
              CASE
                WHEN sr.last_run > datetime('now','-6 hours') THEN '0_last_6h'
                WHEN sr.last_run > datetime('now','-24 hours') THEN '1_last_24h'
                WHEN sr.last_run > datetime('now','-3 days') THEN '2_last_3d'
                WHEN sr.last_run > datetime('now','-7 days') THEN '3_last_7d'
                WHEN sr.last_run > datetime('now','-30 days') THEN '4_last_30d'
                WHEN sr.last_run IS NOT NULL THEN '5_older_than_30d'
                ELSE '6_never'
              END AS bucket,
              COUNT(*) AS n_cities
            FROM prod_cities pc
            LEFT JOIN (
              SELECT source_name, MAX(run_started_at) AS last_run
              FROM scraper_runs GROUP BY source_name
            ) sr ON sr.source_name = pc.source_id
            WHERE pc.status='active' AND pc.source_id IS NOT NULL
            GROUP BY bucket ORDER BY bucket
        """).fetchall()
        buckets = {r[0]: r[1] for r in bucket_rows}

        worst_rows = conn.execute("""
            SELECT pc.city_slug, pc.source_id, sr.last_run,
                   pc.consecutive_failures, pc.last_error
            FROM prod_cities pc
            LEFT JOIN (
              SELECT source_name, MAX(run_started_at) AS last_run
              FROM scraper_runs GROUP BY source_name
            ) sr ON sr.source_name = pc.source_id
            WHERE pc.status='active' AND pc.source_id IS NOT NULL
            ORDER BY (sr.last_run IS NULL) DESC, sr.last_run ASC
            LIMIT 30
        """).fetchall()
        worst = [
            {
                'city_slug': r[0],
                'source_id': r[1],
                'last_run': r[2],
                'consecutive_failures': r[3],
                'last_error': (r[4] or '')[:120],
            }
            for r in worst_rows
        ]
        return jsonify({
            'status': 'ok',
            'buckets': buckets,
            'worst_30': worst,
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)[:300]}), 500


@admin_bp.route('/api/admin/prune-noise', methods=['POST'])
def admin_prune_noise():
    """V493 IRONCLAD pt.2: pause prod_cities entries that are pure noise.

    Pre-prune state (sample 2026-05-04):
      - 1,758 cities marked status='active'
      - But only 23 are 'fresh' (collected in last 30 days)
      - 1,020 have NO source_id at all (can't be collected — phantoms)
      - 8 cities have 10-100 consecutive_failures and are STILL active
        (auto-pause at 3+ exists in db.py:4078 but doesn't always fire —
        some collection paths bypass it)

    What this does (idempotent — safe to run repeatedly):
      1. status='active' AND source_id IS NULL  →  status='paused'
         (note: 'V493: phantom row - no source configured')
      2. status='active' AND consecutive_failures >= 10  →  status='paused'
         (note: 'V493: chronic failure - {N} consecutive')
      3. status='active' AND newest_permit_date < date('now','-180 days')
         AND total_permits = 0  →  status='paused'
         (note: 'V493: never produced data, configured >180d ago')

    Returns counts of what got paused.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    conn = permitdb.get_connection()
    results = {}
    try:
        # 1. No source_id = unreachable phantom
        cur = conn.execute("""
            UPDATE prod_cities SET
                status = 'paused',
                last_error = COALESCE(last_error, 'V493: phantom row - no source configured')
            WHERE status = 'active' AND (source_id IS NULL OR source_id = '')
        """)
        results['phantom_no_source'] = cur.rowcount

        # 2. Chronic failure
        cur = conn.execute("""
            UPDATE prod_cities SET
                status = 'paused',
                last_error = 'V493: chronic failure - ' || consecutive_failures || ' consecutive'
            WHERE status = 'active' AND consecutive_failures >= 10
        """)
        results['chronic_failures'] = cur.rowcount

        # 3. Active row that has never produced any permits AND has had
        # no successful collection — these are configured but never
        # actually worked. Original V493 spec used a created_at filter
        # but prod_cities has no such column; using last_successful_collection
        # IS NULL as the proxy (combined with zero permits + null permit
        # date this is unambiguously "never collected anything").
        cur = conn.execute("""
            UPDATE prod_cities SET
                status = 'paused',
                last_error = COALESCE(last_error, 'V493: never produced data')
            WHERE status = 'active'
              AND (total_permits IS NULL OR total_permits = 0)
              AND newest_permit_date IS NULL
              AND last_successful_collection IS NULL
        """)
        results['never_produced'] = cur.rowcount

        conn.commit()

        # Post-prune sanity check
        post = conn.execute("""
            SELECT COUNT(*) AS n FROM prod_cities WHERE status='active'
        """).fetchone()
        results['active_after'] = post[0] if post else None

        post_clean = conn.execute("""
            SELECT COUNT(*) AS n FROM prod_cities
            WHERE status='active'
              AND source_id IS NOT NULL AND source_id <> ''
              AND consecutive_failures < 10
        """).fetchone()
        results['active_operational'] = post_clean[0] if post_clean else None

        return jsonify({'status': 'ok', 'pruned': results})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)[:300]}), 500


@admin_bp.route('/api/admin/wal-checkpoint', methods=['POST'])
def admin_wal_checkpoint():
    """V488 IRONCLAD: force a PRAGMA wal_checkpoint(TRUNCATE).

    The /api/admin/query endpoint blocks the literal substring
    'TRUNCATE' (forbidden_keywords list), so we can't run this from
    there — and worker.py's heartbeat already does TRUNCATE every
    ~5 min but if the worker process holds a long-running write
    transaction, its own checkpoint returns busy=1 and can't shrink
    the WAL. This endpoint runs from the WEB process which has shorter
    transactions, so it actually does shrink in practice.

    Returns the (busy, frames_in_wal, frames_truncated) triple.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        # PRAGMA returns: (busy_int, log_size, checkpointed)
        return jsonify({
            'status': 'ok',
            'busy': result[0] if result else None,
            'frames_in_wal_before': result[1] if result else None,
            'frames_checkpointed': result[2] if result else None,
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)[:300]}), 500


@admin_bp.route('/api/admin/refresh-stats', methods=['POST'])
def admin_refresh_stats():
    """V479: force a stats-cache refresh.

    Heavy aggregate queries (1.28M-row GROUP BYs) run synchronously in
    THIS request so the caller pays the cost — never run them in normal
    page handlers, that's what crashed V478. The daemon's regular cycle
    also calls refresh_stats_cache() at the end; this endpoint is a
    way to force a refresh after a deploy without waiting for the next
    cycle.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from stats_cache import refresh_stats_cache, get_cached_stats
        conn = permitdb.get_connection()
        refresh_stats_cache(conn)
        s = get_cached_stats()
        return jsonify({
            'status': 'ok',
            'cities': len(s.get('city_stats') or {}),
            'permits': s.get('global', {}).get('total_permits', 0),
            'updated_at': s.get('updated_at'),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'error': str(e)[:500]}), 500


@admin_bp.route('/api/admin/query', methods=['POST'])
def admin_query():
    """V34: Run a read-only SQL query for diagnostics.
    Body: {"sql": "SELECT ...", "limit": 100}
    Only SELECT statements allowed.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        # V232: accept `sql` (canonical) and `query` (older name Cowork's
        # tooling still sends). The `query` param used to return a
        # confusing "Only SELECT queries allowed" — actually the code
        # was reading sql='' which failed the startswith check. Now
        # either name works.
        sql = (data.get('sql') or data.get('query') or '').strip()
        limit = min(data.get('limit', 100), 1000)

        # V66: Safety check — only allow SELECT queries
        # Use word boundaries to avoid false positives on column names like 'last_update'
        import re
        sql_upper = sql.upper()
        if not sql_upper.startswith('SELECT'):
            return jsonify({'error': 'Only SELECT queries allowed'}), 400

        # Check for forbidden keywords as standalone words (not within column names)
        forbidden_keywords = ['INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'ATTACH', 'TRUNCATE']
        for forbidden in forbidden_keywords:
            # \b = word boundary — won't match 'last_update' for 'UPDATE'
            if re.search(rf'\b{forbidden}\b', sql_upper):
                return jsonify({'error': f'Forbidden keyword: {forbidden}'}), 400

        conn = permitdb.get_connection()
        try:
            rows = conn.execute(sql).fetchmany(limit)
            result = [dict(r) for r in rows]
            return jsonify({'rows': result, 'count': len(result)})
        finally:
            conn.close()  # V66: Fix connection leak
    except Exception as e:
        return jsonify({'error': str(e)}), 500


_PERF_MIGRATE_STATE = {'running': False, 'steps': [], 'started_at': None,
                       'finished_at': None}

@admin_bp.route('/api/admin/indexnow-push', methods=['POST'])
def admin_indexnow_push():
    """V506 FIX 10: push URLs to IndexNow protocol (Bing/Yandex/Seznam).

    Body: {"urls": ["https://permitgrab.com/permits/...", ...]}
    Or: {"slugs": ["chicago-il", ...]} → expanded to /permits/<slug>.

    Returns the IndexNow API status_code (200/202 = accepted).
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    import os, requests as _ix_requests
    key = os.environ.get('INDEXNOW_KEY')
    if not key:
        return jsonify({'error': 'INDEXNOW_KEY env var not set'}), 503
    payload = request.get_json(silent=True) or {}
    urls = payload.get('urls') or []
    slugs = payload.get('slugs') or []
    site = 'https://permitgrab.com'
    for s in slugs:
        urls.append(f'{site}/permits/{s}')
    if not urls:
        return jsonify({'error': 'urls or slugs required'}), 400
    body = {
        'host': 'permitgrab.com',
        'key': key,
        'keyLocation': f'{site}/{key}.txt',
        'urlList': urls[:10000],
    }
    try:
        r = _ix_requests.post(
            'https://api.indexnow.org/indexnow', json=body,
            headers={'Content-Type': 'application/json'},
            timeout=15,
        )
        return jsonify({'status_code': r.status_code,
                        'pushed': len(urls), 'response_text': r.text[:200]})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@admin_bp.route('/api/admin/accela-detail-test', methods=['POST'])
def admin_accela_detail_test():
    """V508: smoke-test the Playwright-based Accela CapDetail scraper.

    Body: {"agency": "SBC", "permit": "26GEN-00750"}
    Returns whatever fields the scraper extracted, or {_error:...}.

    Useful for validating Chromium is installed + the URL pattern works
    BEFORE wiring the daemon to call this for every permit."""
    valid, error = check_admin_key()
    if not valid:
        return error
    body = request.get_json(silent=True) or {}
    agency = body.get('agency', 'SBC')
    permit = body.get('permit', '26GEN-00750')
    try:
        from accela_playwright_collector import fetch_accela_detail_playwright
        info = fetch_accela_detail_playwright(agency, permit, timeout_s=40)
        return jsonify({'agency': agency, 'permit': permit, 'info': info})
    except Exception as e:
        return jsonify({'error': f'{type(e).__name__}: {str(e)[:200]}'}), 500


@admin_bp.route('/api/admin/accela-detail-batch', methods=['POST'])
def admin_accela_detail_batch():
    """V508: backfill contractor info on stored permits via Playwright.

    Body: {"slug": "san-bernardino-county", "agency": "SBC",
           "limit": 25, "only_missing_contractor": true}
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    body = request.get_json(silent=True) or {}
    slug = body.get('slug', 'san-bernardino-county')
    agency = body.get('agency', 'SBC')
    limit = min(int(body.get('limit', 25)), 100)
    only_missing = body.get('only_missing_contractor', True)

    conn = permitdb.get_connection()
    try:
        sql = (
            "SELECT permit_number FROM permits WHERE source_city_key = ? "
            "AND permit_number IS NOT NULL AND permit_number != ''"
        )
        if only_missing:
            sql += " AND (contractor_name IS NULL OR contractor_name = '')"
        sql += " ORDER BY date DESC LIMIT ?"
        rows = conn.execute(sql, (slug, limit)).fetchall()
        permit_numbers = [r[0] if not isinstance(r, dict) else r['permit_number']
                          for r in rows]
    finally:
        conn.close()

    if not permit_numbers:
        return jsonify({'status': 'no_permits', 'slug': slug})

    try:
        from accela_playwright_collector import fetch_accela_details_batch
        results = fetch_accela_details_batch(
            agency, permit_numbers, max_permits=limit,
        )
    except Exception as e:
        return jsonify({'error': f'{type(e).__name__}: {str(e)[:200]}'}), 500

    # Persist contractor info on permits where we got a name
    updated = 0
    if results:
        conn = permitdb.get_connection()
        try:
            for pn, info in results.items():
                cn = (info or {}).get('contractor_name')
                if not cn:
                    continue
                rc = conn.execute(
                    "UPDATE permits SET contractor_name = ? "
                    "WHERE source_city_key = ? AND permit_number = ?",
                    (cn, slug, pn),
                ).rowcount
                updated += (rc or 0)
            conn.commit()
        finally:
            conn.close()

    return jsonify({
        'status': 'done',
        'slug': slug,
        'agency': agency,
        'permits_processed': len(permit_numbers),
        'permits_updated': updated,
        'sample_results': dict(list(results.items())[:5]),
    })


@admin_bp.route('/api/admin/perf-migrate', methods=['POST'])
def admin_perf_migrate():
    """V503: idempotent perf migrations — indexes + ANALYZE.

    Async — returns immediately. Poll /api/admin/perf-migrate-status
    for progress. Each step uses IF NOT EXISTS so re-running is a no-op.

    Async because CREATE INDEX on a 2.86M-row table can take >5 min
    under WAL contention with the active collection daemon. Doing it
    synchronously hangs the gunicorn worker → 502 → migration may or
    may not have committed → confused state.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    if _PERF_MIGRATE_STATE['running']:
        return jsonify({'status': 'already_running',
                        'started_at': _PERF_MIGRATE_STATE['started_at'],
                        'steps_done': len(_PERF_MIGRATE_STATE['steps'])})

    import time, threading
    steps = [
        ("idx_property_owners_state_city",
         "CREATE INDEX IF NOT EXISTS idx_property_owners_state_city "
         "ON property_owners (state, city)"),
        ("idx_violations_source_city_key",
         "CREATE INDEX IF NOT EXISTS idx_violations_source_city_key "
         "ON violations (source_city_key)"),
        ("ANALYZE",
         "ANALYZE"),
    ]
    def _run():
        from datetime import datetime as _dt
        _PERF_MIGRATE_STATE['running'] = True
        _PERF_MIGRATE_STATE['started_at'] = _dt.utcnow().isoformat()
        _PERF_MIGRATE_STATE['steps'] = []
        _PERF_MIGRATE_STATE['finished_at'] = None
        conn = permitdb.get_connection()
        # Give SQLite up to 5 min to acquire write lock against busy daemon
        try:
            conn.execute("PRAGMA busy_timeout = 300000")
        except Exception:
            pass
        try:
            for name, sql in steps:
                t0 = time.time()
                try:
                    conn.execute(sql)
                    conn.commit()
                    _PERF_MIGRATE_STATE['steps'].append({
                        'step': name, 'status': 'ok',
                        'elapsed_s': round(time.time() - t0, 2)})
                except Exception as e:
                    _PERF_MIGRATE_STATE['steps'].append({
                        'step': name, 'status': 'error',
                        'error': str(e)[:200],
                        'elapsed_s': round(time.time() - t0, 2)})
        finally:
            conn.close()
            _PERF_MIGRATE_STATE['running'] = False
            _PERF_MIGRATE_STATE['finished_at'] = _dt.utcnow().isoformat()

    threading.Thread(target=_run, daemon=True, name='perf_migrate').start()
    return jsonify({'status': 'started', 'poll': '/api/admin/perf-migrate-status'})


@admin_bp.route('/api/admin/perf-migrate-status', methods=['GET'])
def admin_perf_migrate_status():
    """V503: status of the most recent perf-migrate run."""
    valid, error = check_admin_key()
    if not valid:
        return error
    return jsonify(dict(_PERF_MIGRATE_STATE))


@admin_bp.route('/api/admin/execute', methods=['POST'])
def admin_execute():
    """V159: Run a write SQL statement (INSERT/UPDATE/DELETE).
    Body: {"sql": "UPDATE ..."}
    Requires X-Admin-Key. DROP/ALTER/ATTACH still forbidden.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        data = request.get_json() or {}
        sql = data.get('sql', '').strip()
        if not sql:
            return jsonify({'error': 'sql is required'}), 400

        import re
        sql_upper = sql.upper()
        forbidden = ['DROP', 'ALTER', 'ATTACH', 'TRUNCATE', 'CREATE']
        for kw in forbidden:
            if re.search(rf'\b{kw}\b', sql_upper):
                return jsonify({'error': f'Forbidden keyword: {kw}'}), 400

        conn = permitdb.get_connection()
        try:
            result = conn.execute(sql)
            conn.commit()
            rows_affected = result.rowcount
            print(f"[V159] Admin execute: {sql[:100]}... -> {rows_affected} rows")
            return jsonify({'success': True, 'rows_affected': rows_affected})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/data-freshness', methods=['GET'])
def admin_data_freshness():
    """V12.58: Return data freshness stats for all cities. Useful for monitoring stale sources."""
    valid, error = check_admin_key()
    if not valid:
        return error

    conn = permitdb.get_connection()
    cursor = conn.execute("""
        SELECT city, state, COUNT(*) as total_permits, MAX(filing_date) as newest_date
        FROM permits
        WHERE filing_date IS NOT NULL AND filing_date != ''
        GROUP BY city, state
        ORDER BY newest_date ASC
    """)

    results = []
    now = datetime.now()
    for row in cursor:
        newest = row['newest_date']
        days_stale = None
        if newest:
            try:
                newest_dt = datetime.strptime(newest[:10], '%Y-%m-%d')
                days_stale = (now - newest_dt).days
            except (ValueError, TypeError):
                pass
        results.append({
            'city': row['city'],
            'state': row['state'],
            'total_permits': row['total_permits'],
            'newest_filing_date': newest,
            'days_stale': days_stale
        })

    # Also get cities with NULL dates
    null_dates = conn.execute("""
        SELECT city, state, COUNT(*) as count
        FROM permits
        WHERE filing_date IS NULL OR filing_date = ''
        GROUP BY city, state
        ORDER BY count DESC
    """).fetchall()

    return jsonify({
        'cities': results,
        'cities_with_null_dates': [dict(r) for r in null_dates],
        'total_cities': len(results),
        'stale_count': len([r for r in results if r['days_stale'] and r['days_stale'] > 30])
    })


@admin_bp.route('/api/admin/stale-cities', methods=['GET'])
def admin_stale_cities():
    """V18: Get stale cities review queue and freshness summary."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        # Get freshness summary
        summary = permitdb.get_freshness_summary()

        # Get review queue
        review_queue = permitdb.get_review_queue()

        # Get currently stale cities (active but stale)
        stale = permitdb.get_stale_cities()

        return jsonify({
            'summary': summary,
            'review_queue': review_queue,
            'currently_stale': stale,
            'thresholds': {
                'stale_days': permitdb.FRESHNESS_STALE_DAYS,
                'very_stale_days': permitdb.FRESHNESS_VERY_STALE_DAYS,
            }
        })
    except Exception as e:
        return jsonify({'error': f'Failed to get stale cities: {str(e)}'}), 500


@admin_bp.route('/api/admin/send-welcome', methods=['POST'])
def admin_send_welcome():
    """V12.53: Send welcome email to a specific user."""
    valid, error = check_admin_key()
    if not valid:
        return error

    # V12.59: Read from both query string and JSON body
    email = request.args.get('email') or (request.json or {}).get('email', '')
    email_type = request.args.get('type') or (request.json or {}).get('type', 'free')

    if not email:
        return jsonify({'error': 'Email parameter required'}), 400

    user = find_user_by_email(email)
    if not user:
        return jsonify({'error': f'User {email} not found'}), 404

    try:
        from email_alerts import send_welcome_free, send_welcome_pro_trial
        if email_type == 'pro':
            send_welcome_pro_trial(user)
        else:
            send_welcome_free(user)
        return jsonify({'status': 'sent', 'to': email, 'type': email_type})
    except Exception as e:
        return jsonify({'error': f'Welcome email failed: {str(e)}'}), 500


@admin_bp.route('/api/admin/run-trial-check', methods=['POST'])
def admin_run_trial_check():
    """V12.53: Manually run trial lifecycle check."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from email_alerts import check_trial_lifecycle
        results = check_trial_lifecycle()
        return jsonify({'status': 'done', 'results': results})
    except Exception as e:
        return jsonify({'error': f'Trial check failed: {str(e)}'}), 500


@admin_bp.route('/api/admin/run-onboarding-check', methods=['POST'])
def admin_run_onboarding_check():
    """V12.53: Manually run onboarding nudge check."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from email_alerts import check_onboarding_nudges
        sent = check_onboarding_nudges()
        return jsonify({'status': 'done', 'sent': sent})
    except Exception as e:
        return jsonify({'error': f'Onboarding check failed: {str(e)}'}), 500


@admin_bp.route('/api/admin/email-stats')
def admin_email_stats():
    """V12.53: Get email system statistics."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        users = User.query.all()

        stats = {
            'total_users': len(users),
            'digest_active': sum(1 for u in users if u.digest_active),
            'email_verified': sum(1 for u in users if u.email_verified),
            'welcome_sent': sum(1 for u in users if u.welcome_email_sent),
            'pro_trial_users': sum(1 for u in users if u.plan == 'pro_trial'),
            'pro_users': sum(1 for u in users if u.plan == 'pro'),
            'free_users': sum(1 for u in users if u.plan == 'free'),
            'trial_midpoint_sent': sum(1 for u in users if u.trial_midpoint_sent),
            'trial_ending_sent': sum(1 for u in users if u.trial_ending_sent),
            'trial_expired_sent': sum(1 for u in users if u.trial_expired_sent),
        }
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': f'Stats failed: {str(e)}'}), 500


@admin_bp.route('/api/admin/email-status')
def admin_email_status():
    """V73: Comprehensive email system health check.
    V78: Added digest daemon thread status tracking."""
    diagnostics = {
        'timestamp': datetime.now().isoformat(),
        'checks': {}
    }

    # V78: Include daemon thread status
    diagnostics['digest_daemon'] = DIGEST_STATUS.copy()

    # Check 1: SMTP environment variables
    try:
        smtp_pass = os.environ.get('SMTP_PASS', '')
        smtp_host = os.environ.get('SMTP_HOST', 'smtp.sendgrid.net')
        smtp_port = os.environ.get('SMTP_PORT', '587')
        diagnostics['checks']['smtp'] = {
            'pass_configured': bool(smtp_pass),
            'pass_length': len(smtp_pass) if smtp_pass else 0,
            'host': smtp_host,
            'port': smtp_port,
            'status': 'OK' if smtp_pass else 'MISSING SMTP_PASS'
        }
    except Exception as e:
        diagnostics['checks']['smtp'] = {'status': 'ERROR', 'error': str(e)}

    # Check 2: email_alerts.py import
    try:
        from email_alerts import SMTP_PASS, SUBSCRIBERS_FILE, load_subscribers, send_email
        diagnostics['checks']['email_alerts_import'] = {
            'status': 'OK',
            'smtp_pass_in_module': bool(SMTP_PASS)
        }
    except ImportError as e:
        diagnostics['checks']['email_alerts_import'] = {'status': 'FAILED', 'error': str(e)}
        return jsonify(diagnostics), 500

    # Check 3: Subscribers file
    try:
        diagnostics['checks']['subscribers_file'] = {
            'path': str(SUBSCRIBERS_FILE),
            'exists': SUBSCRIBERS_FILE.exists(),
            'status': 'OK' if SUBSCRIBERS_FILE.exists() else 'MISSING'
        }
        if SUBSCRIBERS_FILE.exists():
            with open(SUBSCRIBERS_FILE) as f:
                raw_subs = json.load(f)
            diagnostics['checks']['subscribers_file']['total'] = len(raw_subs)
    except Exception as e:
        diagnostics['checks']['subscribers_file'] = {'status': 'ERROR', 'error': str(e)}

    # Check 4: Load subscribers function
    try:
        subs = load_subscribers()
        diagnostics['checks']['load_subscribers'] = {
            'status': 'OK',
            'active_count': len(subs),
            'sample_emails': [s.get('email', '?')[:3] + '***' for s in subs[:5]]
        }
    except Exception as e:
        diagnostics['checks']['load_subscribers'] = {'status': 'ERROR', 'error': str(e)}

    # Overall status
    all_ok = all(
        c.get('status') == 'OK'
        for c in diagnostics['checks'].values()
    )
    diagnostics['overall_status'] = 'HEALTHY' if all_ok else 'ISSUES_FOUND'

    return jsonify(diagnostics)


@admin_bp.route('/api/admin/us-cities')
def admin_us_cities():
    """V12.54: List cities with filters."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    status = request.args.get('status')
    state = request.args.get('state')
    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        query = "SELECT * FROM us_cities WHERE 1=1"
        params = []
        if status:
            query += " AND status=?"
            params.append(status)
        if state:
            query += " AND state=?"
            params.append(state)
        query += " ORDER BY priority ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/api/admin/us-counties')
def admin_us_counties():
    """V12.54: List counties with filters."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    status = request.args.get('status')
    limit = int(request.args.get('limit', 50))
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        query = "SELECT * FROM us_counties WHERE 1=1"
        params = []
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY priority ASC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/api/admin/city-sources')
def admin_city_sources():
    """V12.54: List all data sources."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        rows = conn.execute("SELECT * FROM city_sources ORDER BY last_collected_at DESC LIMIT 200").fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/api/admin/bulk-sources')
def admin_bulk_sources():
    """V87: List bulk sources (county/state level)."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        rows = conn.execute("""
            SELECT * FROM bulk_sources ORDER BY total_permits_collected DESC LIMIT 100
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/api/admin/architecture-stats')
def admin_architecture_stats():
    """V87: Get clean architecture statistics."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import db as permitdb
        conn = permitdb.get_connection()

        # Core counts
        total_cities = conn.execute("SELECT COUNT(*) FROM prod_cities").fetchone()[0]
        cities_with_data = conn.execute("SELECT COUNT(*) FROM prod_cities WHERE total_permits > 0").fetchone()[0]
        total_permits = conn.execute("SELECT COUNT(*) FROM permits").fetchone()[0]
        linked_permits = conn.execute("SELECT COUNT(*) FROM permits WHERE prod_city_id IS NOT NULL").fetchone()[0]

        # Source counts
        city_sources = conn.execute("SELECT COUNT(*) FROM city_sources WHERE status = 'active'").fetchone()[0]
        city_sources_linked = conn.execute("SELECT COUNT(*) FROM city_sources WHERE status = 'active' AND prod_city_id IS NOT NULL").fetchone()[0]

        # Bulk sources (may not exist yet)
        try:
            bulk_sources = conn.execute("SELECT COUNT(*) FROM bulk_sources WHERE status = 'active'").fetchone()[0]
        except Exception:
            bulk_sources = 0

        return jsonify({
            'prod_cities': {
                'total': total_cities,
                'with_data': cities_with_data,
                'without_data': total_cities - cities_with_data
            },
            'permits': {
                'total': total_permits,
                'linked': linked_permits,
                'unlinked': total_permits - linked_permits,
                'link_rate': f"{100 * linked_permits // total_permits}%" if total_permits > 0 else "0%"
            },
            'sources': {
                'city_sources': city_sources,
                'city_sources_linked': city_sources_linked,
                'bulk_sources': bulk_sources
            },
            'architecture': 'V87 - Clean FK-based relationships'
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/api/admin/discovery-log')
def admin_discovery_log():
    """V12.54: Recent discovery runs."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        rows = conn.execute("SELECT * FROM discovery_runs ORDER BY id DESC LIMIT 20").fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/api/admin/new-cities')
def admin_new_cities():
    """V17: Get recently activated cities for SEO tracking."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401

    days = int(request.args.get('days', 7))

    try:
        # Get recent activations
        activations = permitdb.get_recent_activations(days=days)

        # Get totals
        conn = permitdb.get_connection()
        total_active = conn.execute(
            "SELECT COUNT(*) as cnt FROM prod_cities WHERE status = 'active'"
        ).fetchone()['cnt']

        # Enrich with permit counts and page URLs
        enriched = []
        for a in activations:
            enriched.append({
                'city': a.get('city_name'),
                'state': a.get('state'),
                'slug': a.get('city_slug'),
                'activated_at': a.get('activated_at'),
                'permits': a.get('initial_permits', 0),
                'seo_status': a.get('seo_status', 'needs_content'),
                'source': a.get('source'),
                'page_url': f"https://permitgrab.com/permits/{a.get('city_slug')}"
            })

        return jsonify({
            'new_cities': enriched,
            'total_active': total_active,
            'activated_this_week': len([a for a in activations
                if a.get('activated_at', '') >= (datetime.now() - timedelta(days=7)).isoformat()])
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/api/admin/tracker')
def admin_tracker():
    """V64: Master city tracker — 20K rows, one per US city with coverage and freshness.

    Query params:
      state=TX — filter by state
      status=active — filter by coverage status (active/no_source)
      stale=true — only show stale/no_data cities
      limit=500 — limit rows (default 500, max 5000)
      offset=0 — pagination offset
      sort=population — sort field (population, last_permit_date, city)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    state = request.args.get('state')
    status = request.args.get('status')
    stale_only = request.args.get('stale') == 'true'
    limit = min(int(request.args.get('limit', 500)), 5000)
    offset = int(request.args.get('offset', 0))
    sort = request.args.get('sort', 'population')

    conn = permitdb.get_connection()
    try:
        # Build the tracker query
        # Join us_cities with prod_cities and scraper_runs for comprehensive view
        query = """
            SELECT
                uc.city_name,
                uc.state,
                uc.population,
                uc.slug as city_slug,
                uc.county,
                uc.covered_by_source,
                uc.status as discovery_status,
                -- Coverage info from prod_cities
                pc.status as coverage_status,
                pc.source_id,
                pc.source_type as platform,
                pc.source_scope,
                -- Freshness from prod_cities
                pc.newest_permit_date as last_permit_date,
                pc.last_collection as last_pull_date,
                pc.total_permits,
                pc.data_freshness,
                pc.consecutive_failures,
                pc.last_error
            FROM us_cities uc
            LEFT JOIN prod_cities pc ON (
                pc.city_slug = uc.slug
                OR pc.city_slug = REPLACE(uc.slug, '-', '_')
                OR pc.source_id = REPLACE(uc.slug, '-', '_')
            )
        """

        # Add WHERE clauses
        conditions = []
        params = []
        if state:
            conditions.append("uc.state = ?")
            params.append(state)
        if status == 'active':
            conditions.append("pc.status = 'active'")
        elif status == 'no_source':
            conditions.append("pc.city_slug IS NULL")
        if stale_only:
            conditions.append("(pc.data_freshness IN ('stale', 'very_stale', 'no_data') OR pc.city_slug IS NULL)")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        # Sort
        sort_map = {
            'population': 'uc.population DESC',
            'last_permit_date': 'pc.newest_permit_date DESC',
            'city': 'uc.city_name ASC',
        }
        query += f" ORDER BY {sort_map.get(sort, 'uc.population DESC')}"
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()

        # Get total count for pagination
        count_query = "SELECT COUNT(*) FROM us_cities uc"
        if conditions:
            count_query += " LEFT JOIN prod_cities pc ON pc.city_slug = uc.slug OR pc.city_slug = REPLACE(uc.slug, '-', '_')"
            count_query += " WHERE " + " AND ".join(conditions)
        total = conn.execute(count_query, params[:-2] if len(params) > 2 else []).fetchone()[0]

        # Summary stats
        summary = {
            'total_us_cities': conn.execute("SELECT COUNT(*) FROM us_cities").fetchone()[0],
            'active_in_prod': conn.execute("SELECT COUNT(*) FROM prod_cities WHERE status='active'").fetchone()[0],
            'with_permits': conn.execute("SELECT COUNT(DISTINCT city) FROM permits WHERE city IS NOT NULL").fetchone()[0],
            'stale_count': conn.execute("SELECT COUNT(*) FROM prod_cities WHERE data_freshness IN ('stale', 'very_stale')").fetchone()[0],
        }

        return jsonify({
            'summary': summary,
            'tracker': [dict(row) for row in rows],
            'pagination': {'limit': limit, 'offset': offset, 'total': total}
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/freshness')
def admin_freshness():
    """V64: Run freshness classification and return results.

    Shows which cities are fresh, stale, broken, or have no data.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    from collector import classify_city_freshness
    try:
        result = classify_city_freshness()
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/refresh-freshness', methods=['POST'])
def admin_refresh_freshness():
    """V71: Recalculate prod_cities freshness from actual permits table.

    Fixes the issue where 431 cities show 'no_data' despite having real permits.
    The root cause is that newest_permit_date was never populated for these cities.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    from datetime import datetime, timedelta

    try:
        today = datetime.now().strftime('%Y-%m-%d')
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

        conn = permitdb.get_connection()

        # Get all active prod_cities
        cities = conn.execute(
            "SELECT city_slug, source_id, city FROM prod_cities WHERE status='active'"
        ).fetchall()

        updated = 0
        freshness_counts = {'fresh': 0, 'aging': 0, 'stale': 0, 'no_data': 0}

        for row in cities:
            city_slug = row['city_slug'] if isinstance(row, dict) else row[0]
            source_id = row['source_id'] if isinstance(row, dict) else row[1]
            city_name = row['city'] if isinstance(row, dict) else row[2]

            newest = None
            recent = 0

            # Try source_city_key match first (primary join strategy)
            if source_id:
                result = conn.execute(
                    "SELECT MAX(date) as newest, COUNT(CASE WHEN date >= ? THEN 1 END) as recent "
                    "FROM permits WHERE source_city_key = ?",
                    (thirty_days_ago, source_id)
                ).fetchone()
                if result:
                    newest = result['newest'] if isinstance(result, dict) else result[0]
                    recent = (result['recent'] if isinstance(result, dict) else result[1]) or 0

            # Fallback: try city name match
            if not newest and city_name:
                result = conn.execute(
                    "SELECT MAX(date) as newest, COUNT(CASE WHEN date >= ? THEN 1 END) as recent "
                    "FROM permits WHERE city = ?",
                    (thirty_days_ago, city_name)
                ).fetchone()
                if result:
                    newest = result['newest'] if isinstance(result, dict) else result[0]
                    recent = (result['recent'] if isinstance(result, dict) else result[1]) or 0

            # Calculate freshness
            if newest:
                try:
                    days_old = (datetime.now() - datetime.strptime(newest, '%Y-%m-%d')).days
                    if days_old <= 14:
                        freshness = 'fresh'
                    elif days_old <= 30:
                        freshness = 'aging'
                    elif days_old <= 90:
                        freshness = 'stale'
                    else:
                        freshness = 'no_data'
                except Exception:
                    freshness = 'no_data'
            else:
                freshness = 'no_data'

            # Update prod_cities
            conn.execute(
                "UPDATE prod_cities SET newest_permit_date=?, permits_last_30d=?, data_freshness=? "
                "WHERE city_slug=?",
                (newest, recent, freshness, city_slug)
            )

            freshness_counts[freshness] = freshness_counts.get(freshness, 0) + 1
            updated += 1

        conn.commit()

        return jsonify({
            'status': 'success',
            'updated': updated,
            'freshness': freshness_counts
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/activate-city-sources', methods=['POST'])
def admin_activate_city_sources():
    """V71: Activate all inactive city_sources that have matching active prod_cities entries.

    Fixes the issue where 329 city_sources are 'inactive' despite having active prod_cities.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()

        # First: activate city_sources where there's a matching active prod_city
        conn.execute("""
            UPDATE city_sources SET status='active'
            WHERE status='inactive'
            AND source_key IN (SELECT source_id FROM prod_cities WHERE status='active')
        """)
        # Can't get rowcount reliably from all db backends, so we'll count after

        # Count how many are now active vs inactive
        active_result = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='active'").fetchone()
        inactive_result = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='inactive'").fetchone()

        active_count = active_result['cnt'] if isinstance(active_result, dict) else active_result[0]
        inactive_count = inactive_result['cnt'] if isinstance(inactive_result, dict) else inactive_result[0]

        conn.commit()

        return jsonify({
            'status': 'success',
            'city_sources_active': active_count,
            'city_sources_inactive': inactive_count,
            'message': 'Activated city_sources matching active prod_cities'
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/reactivate-from-configs', methods=['POST'])
def admin_reactivate_from_configs():
    """V84: Reactivate city_sources based on active configs in city_configs.py.

    This fixes the issue where sources were mass-deactivated by V35 but have
    valid active configs. It reactivates sources where:
    1. The source_key matches a key in CITY_REGISTRY or BULK_SOURCES
    2. The config has active=True (or no active field, defaulting to True)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from city_configs import CITY_REGISTRY, BULK_SOURCES

        # Build set of active config keys
        active_config_keys = set()
        for key, cfg in CITY_REGISTRY.items():
            if cfg.get('active', True):  # Default True if not specified
                active_config_keys.add(key)
        for key, cfg in BULK_SOURCES.items():
            if cfg.get('active', True):
                active_config_keys.add(key)

        conn = permitdb.get_connection()

        # Get current inactive sources
        inactive_sources = conn.execute("""
            SELECT source_key FROM city_sources WHERE status = 'inactive'
        """).fetchall()
        inactive_keys = {r['source_key'] for r in inactive_sources}

        # Find which ones should be reactivated
        to_reactivate = inactive_keys & active_config_keys

        if to_reactivate:
            # Reactivate them
            placeholders = ','.join(['?' for _ in to_reactivate])
            conn.execute(f"""
                UPDATE city_sources
                SET status = 'active',
                    last_failure_reason = 'v84_reactivated_from_config',
                    consecutive_failures = 0
                WHERE source_key IN ({placeholders})
            """, list(to_reactivate))
            conn.commit()

        # Get final counts
        active_count = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='active'").fetchone()
        inactive_count = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='inactive'").fetchone()

        return jsonify({
            'status': 'success',
            'reactivated': len(to_reactivate),
            'active_configs_count': len(active_config_keys),
            'city_sources_active': active_count['cnt'] if isinstance(active_count, dict) else active_count[0],
            'city_sources_inactive': inactive_count['cnt'] if isinstance(inactive_count, dict) else inactive_count[0],
            'message': f'Reactivated {len(to_reactivate)} sources from active configs'
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/reset-failures', methods=['POST'])
def admin_reset_failures():
    """V72: Reset consecutive_failures for a city or all cities.

    POST body: {"city_slug": "kansas-city"} to reset one city
    POST body: {} or {"city_slug": "all"} to reset all cities
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        city_slug = data.get('city_slug', 'all')

        conn = permitdb.get_connection()

        if city_slug == 'all':
            conn.execute("UPDATE prod_cities SET consecutive_failures=0, consecutive_no_new=0")
            result = conn.execute("SELECT COUNT(*) as cnt FROM prod_cities").fetchone()
            count = result['cnt'] if isinstance(result, dict) else result[0]
            conn.commit()
            return jsonify({
                'status': 'success',
                'message': f'Reset consecutive_failures for all {count} cities'
            })
        else:
            conn.execute(
                "UPDATE prod_cities SET consecutive_failures=0, consecutive_no_new=0 WHERE city_slug=?",
                (city_slug,)
            )
            conn.commit()
            return jsonify({
                'status': 'success',
                'message': f'Reset consecutive_failures for {city_slug}'
            })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/fix-broken-configs', methods=['POST'])
def admin_fix_broken_configs():
    """V75: Apply known fixes to broken city_sources configs.

    This endpoint runs SQL UPDATE statements to fix:
    - High-failure cities (reset failures, reactivate)
    - Platform mismatches (e.g., Rochester socrata -> accela)
    - Slug/key mismatches

    POST body: {} (no parameters needed)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        fixes = fix_known_broken_configs()
        return jsonify({
            'status': 'success',
            'fixes_applied': fixes,
            'count': len(fixes)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/audit-platforms', methods=['POST'])
def admin_audit_platforms():
    """V76: Audit city_sources for platform/endpoint mismatches.

    POST body:
    {
        "auto_fix": true  // optional, default false - automatically fix mismatches
    }
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        auto_fix = data.get('auto_fix', False)

        report = audit_platform_mismatches(auto_fix=auto_fix)
        return jsonify({
            'status': 'success',
            **report
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/update-source', methods=['POST'])
def admin_update_source():
    """V75: Update a city_sources row via SQL.

    This endpoint allows runtime updates to city_sources without redeploying.
    Useful for fixing broken configs, updating endpoints, resetting failures, etc.

    POST body:
    {
        "source_key": "kansas_city",
        "updates": {
            "endpoint": "https://data.kcmo.org/resource/NEW_ID.json",
            "dataset_id": "NEW_ID",
            "status": "active",
            "consecutive_failures": 0,
            "last_failure_reason": null
        }
    }

    Allowed fields to update:
    - endpoint, dataset_id, platform, date_field, field_map
    - status, consecutive_failures, last_failure_reason
    - covers_cities, limit_per_page
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        source_key = data.get('source_key')
        updates = data.get('updates', {})

        if not source_key:
            return jsonify({'error': 'source_key is required'}), 400

        if not updates:
            return jsonify({'error': 'updates object is required'}), 400

        # Whitelist of allowed fields to update
        allowed_fields = {
            'endpoint', 'dataset_id', 'platform', 'date_field', 'field_map',
            'status', 'consecutive_failures', 'last_failure_reason',
            'covers_cities', 'limit_per_page', 'name', 'state'
        }

        # Filter to only allowed fields
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields}
        if not filtered_updates:
            return jsonify({'error': f'No valid fields to update. Allowed: {allowed_fields}'}), 400

        conn = permitdb.get_connection()

        # Check if source exists
        existing = conn.execute(
            "SELECT source_key FROM city_sources WHERE source_key = ?",
            (source_key,)
        ).fetchone()

        if not existing:
            return jsonify({'error': f'Source {source_key} not found in city_sources'}), 404

        # Build UPDATE query
        set_clauses = []
        values = []
        for field, value in filtered_updates.items():
            set_clauses.append(f"{field} = ?")
            # Handle None/null for last_failure_reason
            values.append(value if value is not None else None)

        values.append(source_key)

        query = f"UPDATE city_sources SET {', '.join(set_clauses)} WHERE source_key = ?"
        conn.execute(query, values)
        conn.commit()

        return jsonify({
            'status': 'success',
            'source_key': source_key,
            'fields_updated': list(filtered_updates.keys())
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/cleanup-prod-cities', methods=['POST'])
def admin_cleanup_prod_cities():
    """V75: Clean up inflated prod_cities entries.

    This removes or deactivates prod_cities entries that:
    1. Have never been collected (last_collection IS NULL)
    2. Have 0 total_permits
    3. Don't have a matching active city_sources entry

    POST body:
    {
        "mode": "deactivate"  // or "delete" (default: deactivate)
    }
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        mode = data.get('mode', 'deactivate')

        conn = permitdb.get_connection()

        if mode == 'delete':
            # Delete entries that have never collected and have 0 permits
            result = conn.execute("""
                DELETE FROM prod_cities
                WHERE last_collection IS NULL
                AND total_permits = 0
                AND source_id NOT IN (SELECT source_key FROM city_sources WHERE status='active')
            """)
            action = 'deleted'
        else:
            # V76: Use 'paused' instead of 'inactive' — CHECK constraint only allows
            # 'active', 'paused', 'failed', 'pending'
            conn.execute("""
                UPDATE prod_cities SET status = 'paused'
                WHERE last_collection IS NULL
                AND total_permits = 0
                AND status = 'active'
                AND source_id NOT IN (SELECT source_key FROM city_sources WHERE status='active')
            """)
            action = 'paused'

        # Get counts
        active_count = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE status='active'"
        ).fetchone()[0]
        paused_count = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE status='paused'"
        ).fetchone()[0]
        no_data_count = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE status='active' AND total_permits=0"
        ).fetchone()[0]

        conn.commit()

        return jsonify({
            'status': 'success',
            'action': action,
            'prod_cities_active': active_count,
            'prod_cities_paused': paused_count,
            'no_data_count': no_data_count
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/retag-permits', methods=['POST'])
def admin_retag_permits():
    """V511: Move every permit (and contractor_profile) row from one
    source_city_key to another. Used when a misconfigured source ingested
    data under the wrong slug (e.g. SBCO→SBC fix found 1,967 Santa Barbara
    permits stored as San Bernardino).

    Body: {"from_slug": "san-bernardino-county",
           "to_slug": "santa-barbara-county-ca",
           "dry_run": true}
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    body = request.get_json(silent=True) or {}
    from_slug = body.get('from_slug')
    to_slug = body.get('to_slug')
    dry_run = bool(body.get('dry_run', True))
    if not from_slug or not to_slug:
        return jsonify({'error': 'from_slug and to_slug required'}), 400

    conn = permitdb.get_connection()
    try:
        permits_n = conn.execute(
            "SELECT COUNT(*) FROM permits WHERE source_city_key = ?",
            (from_slug,)
        ).fetchone()[0]
        try:
            profiles_n = conn.execute(
                "SELECT COUNT(*) FROM contractor_profiles WHERE source_city_key = ?",
                (from_slug,)
            ).fetchone()[0]
        except Exception:
            profiles_n = 0

        if dry_run:
            return jsonify({
                'dry_run': True,
                'from_slug': from_slug,
                'to_slug': to_slug,
                'permits_to_retag': permits_n,
                'profiles_to_retag': profiles_n,
            })

        conn.execute(
            "UPDATE permits SET source_city_key = ? WHERE source_city_key = ?",
            (to_slug, from_slug)
        )
        try:
            conn.execute(
                "UPDATE contractor_profiles SET source_city_key = ? WHERE source_city_key = ?",
                (to_slug, from_slug)
            )
        except Exception:
            pass
        conn.commit()
        return jsonify({
            'status': 'success',
            'from_slug': from_slug,
            'to_slug': to_slug,
            'permits_retagged': permits_n,
            'profiles_retagged': profiles_n,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@admin_bp.route('/api/admin/v480/deactivate-stale-cities', methods=['POST'])
def admin_v480_deactivate_stale_cities():
    """V480 P1-2: pause cities marked active that haven't published a permit
    in 2026 (or have no permits at all). The daemon was burning cycles on
    dead endpoints (brownsville-wi, lawnside, hi-nella, etc.) which inflate
    error logs and waste collection budget without adding any leads.

    Pauses (not deletes) so the row stays around for forensics. Uses
    'paused' rather than 'inactive' because prod_cities.status has a CHECK
    constraint that only allows ('active', 'paused', 'failed', 'pending').

    Optional POST body: {"cutoff": "2025-01-01"} — newest permit older
    than this counts as stale. Defaults to 2025-01-01 per V480 spec.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        body = request.get_json(silent=True) or {}
        cutoff = body.get('cutoff', '2025-01-01')
        conn = permitdb.get_connection()

        # 1) Cities whose newest permit is before cutoff OR who have no permits.
        stale_rows = conn.execute("""
            SELECT pc.city_slug, MAX(p.date) AS newest
            FROM prod_cities pc
            LEFT JOIN permits p ON p.source_city_key = pc.city_slug
            WHERE pc.status = 'active'
            GROUP BY pc.city_slug
            HAVING MAX(p.date) < ? OR MAX(p.date) IS NULL
        """, (cutoff,)).fetchall()
        stale_slugs = [r['city_slug'] for r in stale_rows]

        paused_count = 0
        if stale_slugs:
            placeholders = ','.join('?' * len(stale_slugs))
            res = conn.execute(
                f"UPDATE prod_cities SET status = 'paused' "
                f"WHERE city_slug IN ({placeholders}) AND status = 'active'",
                stale_slugs,
            )
            paused_count = res.rowcount or 0
            conn.commit()

        active_count = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE status='active'"
        ).fetchone()[0]
        return jsonify({
            'status': 'success',
            'cutoff': cutoff,
            'paused': paused_count,
            'paused_slugs': stale_slugs,
            'prod_cities_active_after': active_count,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/activate-paused-cities', methods=['POST'])
def admin_activate_paused_cities():
    """V77: Bulk-activate paused cities that have valid CITY_REGISTRY configs.

    This activates the 529+ cities that were synced from CITY_REGISTRY but
    inserted as status='paused' and never collected.

    For each paused city:
    1. Check if source_id has valid config in city_sources (active) OR in CITY_REGISTRY (active=True)
    2. If yes: activate in prod_cities AND activate matching city_sources entry
    3. Return count of activated cities

    POST body: {} (no parameters needed)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from city_configs import CITY_REGISTRY

        conn = permitdb.get_connection()
        activated = []
        skipped = []
        city_sources_activated = []

        # Get all paused cities
        paused_cities = conn.execute("""
            SELECT city_slug, source_id, city, state FROM prod_cities WHERE status = 'paused'
        """).fetchall()

        for row in paused_cities:
            city_slug = row[0]
            source_id = row[1]
            city_name = row[2]
            state = row[3]

            # Check if source_id has valid config
            has_valid_config = False

            # Check 1: city_sources table
            cs_row = conn.execute(
                "SELECT source_key, status FROM city_sources WHERE source_key = ?",
                (source_id,)
            ).fetchone()

            if cs_row:
                has_valid_config = True
                # Also activate city_sources if it's inactive
                if cs_row[1] != 'active':
                    conn.execute(
                        "UPDATE city_sources SET status = 'active' WHERE source_key = ?",
                        (source_id,)
                    )
                    city_sources_activated.append(source_id)

            # Check 2: CITY_REGISTRY dict (if not found in city_sources)
            if not has_valid_config and source_id in CITY_REGISTRY:
                if CITY_REGISTRY[source_id].get('active', False):
                    has_valid_config = True

            # Check 3: Try hyphen-to-underscore conversion
            if not has_valid_config:
                underscore_id = source_id.replace('-', '_')
                if underscore_id in CITY_REGISTRY:
                    if CITY_REGISTRY[underscore_id].get('active', False):
                        has_valid_config = True

            if has_valid_config:
                # Activate in prod_cities
                conn.execute("""
                    UPDATE prod_cities SET status = 'active', notes = 'V77: Bulk activated from paused'
                    WHERE city_slug = ?
                """, (city_slug,))
                activated.append({'city_slug': city_slug, 'source_id': source_id})
            else:
                skipped.append({'city_slug': city_slug, 'source_id': source_id, 'reason': 'no valid config'})

        conn.commit()

        # Get final counts
        active_count = conn.execute("SELECT COUNT(*) FROM prod_cities WHERE status='active'").fetchone()[0]
        paused_count = conn.execute("SELECT COUNT(*) FROM prod_cities WHERE status='paused'").fetchone()[0]

        return jsonify({
            'status': 'success',
            'activated_count': len(activated),
            'skipped_count': len(skipped),
            'city_sources_activated': len(city_sources_activated),
            'prod_cities_active': active_count,
            'prod_cities_paused': paused_count,
            'activated': activated[:50],  # Limit response size
            'skipped': skipped[:50]
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/trigger-search', methods=['POST'])
def admin_trigger_search():
    """V12.54: Manually trigger search for a city or county."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    slug = data.get('slug')
    fips = data.get('fips')
    try:
        if fips:
            from city_source_db import update_county_status
            update_county_status(fips, 'not_started')
            return jsonify({"status": "ok", "message": f"County {fips} reset to not_started"})
        elif slug:
            from city_source_db import update_city_status
            update_city_status(slug, 'not_started')
            return jsonify({"status": "ok", "message": f"City {slug} reset to not_started"})
        return jsonify({"error": "provide slug or fips"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/api/admin/traffic', methods=['GET'])
def admin_traffic():
    """V12.59b: Query persistent page view data from PostgreSQL."""
    admin_key = request.headers.get('X-Admin-Key', '')
    if admin_key != os.environ.get('ADMIN_KEY', ''):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        import psycopg2
        conn = psycopg2.connect(os.environ.get('DATABASE_URL', ''))
        cur = conn.cursor()

        hours = int(request.args.get('hours', 24))

        # Total page views
        cur.execute("SELECT COUNT(*) FROM page_views WHERE created_at > NOW() - INTERVAL '%s hours'", (hours,))
        total_views = cur.fetchone()[0]

        # Unique IPs (proxy for unique visitors)
        cur.execute("SELECT COUNT(DISTINCT ip_address) FROM page_views WHERE created_at > NOW() - INTERVAL '%s hours'", (hours,))
        unique_ips = cur.fetchone()[0]

        # Views by path
        cur.execute("""
            SELECT path, COUNT(*) as hits
            FROM page_views WHERE created_at > NOW() - INTERVAL '%s hours'
            GROUP BY path ORDER BY hits DESC LIMIT 20
        """, (hours,))
        paths = [{'path': r[0], 'hits': r[1]} for r in cur.fetchall()]

        # Views by user agent type
        cur.execute("""
            SELECT
                CASE
                    WHEN user_agent ILIKE '%%googlebot%%' THEN 'Googlebot'
                    WHEN user_agent ILIKE '%%bingbot%%' THEN 'Bingbot'
                    WHEN user_agent ILIKE '%%curl%%' THEN 'curl'
                    WHEN user_agent ILIKE '%%python%%' THEN 'Python'
                    WHEN user_agent ILIKE '%%chrome%%' THEN 'Chrome'
                    WHEN user_agent ILIKE '%%firefox%%' THEN 'Firefox'
                    WHEN user_agent ILIKE '%%safari%%' AND user_agent NOT ILIKE '%%chrome%%' THEN 'Safari'
                    WHEN user_agent ILIKE '%%bot%%' OR user_agent ILIKE '%%spider%%' OR user_agent ILIKE '%%crawl%%' THEN 'Other Bot'
                    ELSE 'Other'
                END as agent_type,
                COUNT(*) as hits
            FROM page_views WHERE created_at > NOW() - INTERVAL '%s hours'
            GROUP BY agent_type ORDER BY hits DESC
        """, (hours,))
        agents = [{'agent': r[0], 'hits': r[1]} for r in cur.fetchall()]

        # Recent views (last 10)
        cur.execute("""
            SELECT path, user_agent, ip_address, created_at::text
            FROM page_views ORDER BY created_at DESC LIMIT 10
        """)
        recent = [{'path': r[0], 'user_agent': r[1][:80] if r[1] else '', 'ip': r[2], 'time': r[3]} for r in cur.fetchall()]

        # Hourly breakdown (last 24h)
        cur.execute("""
            SELECT date_trunc('hour', created_at)::text as hour, COUNT(*) as hits
            FROM page_views WHERE created_at > NOW() - INTERVAL '24 hours'
            GROUP BY hour ORDER BY hour
        """)
        hourly = [{'hour': r[0], 'hits': r[1]} for r in cur.fetchall()]

        cur.close()
        conn.close()

        return jsonify({
            'period_hours': hours,
            'total_views': total_views,
            'unique_visitors': unique_ips,
            'paths': paths,
            'user_agents': agents,
            'recent': recent,
            'hourly': hourly
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/health')
def admin_daemon_health():
    """V146: Daemon health check — no auth required (for Render health checks).

    V256: self-heal — if the daemon has stopped AND no collection has
    happened in the last 15 minutes, kick off start_collectors() in a
    background thread before returning the status. Eliminates the
    "every deploy needs a manual POST /api/admin/start-collectors" toil
    that's burned hours this week. Safe because:
      - start_collectors is thread-safe (_s._collector_started flag)
      - health probe is called every minute by Render's TCP check, so
        self-heal fires on the first probe after a failed deploy
      - bound check (15 min) avoids hot-loop during the 120s startup
    """
    try:
        conn = permitdb.get_connection()
        # V488 follow-up: last_collection_at + collections_last_24h were
        # reading scraper_runs.run_started_at, but log_scraper_run() stopped
        # firing on 2026-05-01 12:41 (post-V485 worker rewire took a path
        # that no longer logs to scraper_runs OR collection_log). Permits
        # ARE flowing — `permits.collected_at` is still being stamped every
        # cycle — so the underlying signal is alive; only the logging
        # tables are silent. Read from the source-of-truth column instead
        # so the dashboard stops lying. The scraper_runs read remains for
        # `errors_24h` and `top_errors` (still only-source), which will
        # report 0 until the logger is restored.
        last_coll = conn.execute("SELECT MAX(collected_at) FROM permits").fetchone()
        last_coll_at = last_coll[0] if last_coll else None
        # Count distinct city collections in last 24h (proxy for cycle count)
        colls_24h = conn.execute(
            "SELECT COUNT(DISTINCT source_city_key) FROM permits "
            "WHERE collected_at > datetime('now', '-24 hours') "
            "AND source_city_key IS NOT NULL"
        ).fetchone()[0]
        errors_24h = conn.execute("SELECT COUNT(*) FROM scraper_runs WHERE run_started_at > datetime('now', '-24 hours') AND status = 'error'").fetchone()[0]
        # Fresh cities
        fresh = conn.execute("SELECT COUNT(DISTINCT city) FROM permits WHERE date >= date('now', '-7 days') AND date <= date('now')").fetchone()[0]
        # V396 (loop /CODE_V286 grind): include top-5 cities with the most
        # collection errors in the last 24h. The directive's P0 flagged
        # "errors_last_24h: 38, which is high" — but the prior payload
        # reported only the count. Wes had no way to know which cities
        # were burning that error budget without a follow-up SQL. Adding
        # the top-N breakdown so triage is one curl away.
        top_errors = []
        try:
            err_rows = conn.execute("""
                SELECT city_slug, COUNT(*) as n,
                       MAX(error_message) as last_err
                FROM scraper_runs
                WHERE run_started_at > datetime('now', '-24 hours')
                  AND status = 'error'
                  AND city_slug IS NOT NULL
                GROUP BY city_slug
                ORDER BY n DESC
                LIMIT 5
            """).fetchall()
            for r in err_rows:
                top_errors.append({
                    'city_slug': r[0],
                    'errors': r[1],
                    'last_error': (r[2] or '')[:120],
                })
        except Exception:
            # scraper_runs schema variant or transient db lock — don't
            # fail the health probe over diagnostics.
            top_errors = []
        # V256 self-heal trigger: minutes since last collection (if we can parse it)
        stale_minutes = None
        if last_coll_at:
            try:
                from datetime import datetime as _dt
                delta = _dt.utcnow() - _dt.strptime(last_coll_at[:19], '%Y-%m-%d %H:%M:%S')
                stale_minutes = int(delta.total_seconds() / 60)
            except Exception:
                pass
        conn.close()

        # V365b: In WORKER_MODE the daemon runs in a separate process.
        # daemon_running (in-process flag) will be False, but that's expected.
        # Health is determined by recent collection_log activity instead.
        if WORKER_MODE:
            daemon_running = True  # assume worker is running if we got collections
            is_healthy = colls_24h > 0
        else:
            daemon_running = _s._collector_started
            is_healthy = daemon_running and colls_24h > 0
        self_healed = False

        # V493 IRONCLAD self-heal: fires INSTANTLY when daemon threads
        # are dead — no 15-min stale_minutes wait. Render hits this
        # endpoint every ~60s, so minute-resolution recovery on:
        #   - silent thread death (memory bail, exception, etc)
        #   - missed start_collectors call after deploy
        #   - any future regression that re-introduces WORKER_MODE
        #     no-op gates (V471 PR4 / V481 / future)
        # The OLD V256 path waited stale_minutes > 15 — but stale_minutes
        # is computed from scraper_runs.run_started_at, which goes silent
        # when the logger is broken even if collection runs. So V256
        # could "self-heal" forever without ever spawning threads.
        # V493 instead checks threading.enumerate() directly: if there
        # is no live thread named 'scheduled_collection' — heal NOW.
        try:
            import threading as _th
            _live_names = {t.name for t in _th.enumerate() if t.is_alive()}
            _expected = {'scheduled_collection', 'enrichment_daemon', 'email_scheduler'}
            _missing = _expected - _live_names
            if _missing and not WORKER_MODE:
                def _selfheal_v493():
                    try:
                        print(
                            f"[V493 IRONCLAD] Self-heal: missing threads {_missing} "
                            f"(live={sorted(_live_names)}) — calling start_collectors()",
                            flush=True,
                        )
                        # Reset _collector_started so start_collectors
                        # actually re-runs (it's idempotent only after a
                        # successful first spawn).
                        try:
                            _s._collector_started = False
                        except Exception:
                            pass
                        start_collectors()
                    except Exception as e:
                        print(f"[V493 IRONCLAD] Self-heal failed: {e}", flush=True)
                _th.Thread(target=_selfheal_v493, daemon=True, name='v493_selfheal').start()
                self_healed = True
        except Exception as _shv493_e:
            # Never let self-heal break the health probe — Render uses
            # this for routing.
            print(f"[V493 IRONCLAD] self-heal guard error (non-fatal): {_shv493_e}", flush=True)

        # V491 IRONCLAD digest fallback: cron-tick the daily digest from
        # the WEB process via /api/admin/health. Render hits this endpoint
        # every minute, so this gives us minute-resolution scheduling
        # without depending on the worker thread that has been sticky-
        # broken since 2026-04-29 (system_state shows 0 email_* keys —
        # worker.email_scheduler hasn't actually run since the V475 push).
        #
        # Logic: if (a) we're past 7am ET and (b) today's date != the
        # 'web_last_digest_date' system_state key, fire send_daily_digest
        # in a background thread. Claim the date in system_state BEFORE
        # spawning the thread so concurrent gunicorn workers don't
        # double-send (atomic INSERT OR REPLACE).
        try:
            import pytz as _pytz_h
            _et = _pytz_h.timezone('America/New_York')
            from datetime import datetime as _dt_h
            _now_et = _dt_h.now(_et)
            _today_et = _now_et.date().isoformat()
            if _now_et.hour >= 7:
                _state = permitdb.get_system_state('web_last_digest_date')
                _last_sent = (_state or {}).get('value') if _state else None
                if _last_sent != _today_et:
                    # Claim the date BEFORE firing — prevents race
                    permitdb.set_system_state('web_last_digest_date', _today_et)
                    def _fire_digest():
                        try:
                            from email_alerts import send_daily_digest
                            sent, failed = send_daily_digest()
                            print(f"[V491-DIGEST] web-cron fired daily digest: sent={sent} failed={failed}", flush=True)
                        except Exception as _de:
                            # On failure, roll back the claim so a later probe retries
                            try:
                                permitdb.set_system_state('web_last_digest_date', _last_sent or '')
                            except Exception:
                                pass
                            print(f"[V491-DIGEST] web-cron digest failed: {_de}", flush=True)
                    import threading as _th2
                    _th2.Thread(target=_fire_digest, daemon=True, name='v491_digest_cron').start()
        except Exception as _dh_e:
            # Never let the digest cron break the health probe — Render
            # uses /api/admin/health for liveness routing.
            print(f"[V491-DIGEST] cron-tick guard error (non-fatal): {_dh_e}", flush=True)

        status_code = 200 if is_healthy else 503
        # V398 (CODE_V364 Part 4.5): memory monitoring on the health probe.
        # The directive's P0 root-cause hypothesis was OOM kill: "the
        # daemon thread + Flask web server + import jobs all share one
        # process. During collection cycles or enrichment imports, memory
        # spikes above 512MB → Render kills the process → 502." Reporting
        # actual memory usage every minute (Render's probe cadence) gives
        # us the trend data to confirm or rule that out without SSH.
        memory_mb = None
        memory_percent = None
        try:
            import psutil as _psutil
            _proc = _psutil.Process(os.getpid())
            memory_mb = round(_proc.memory_info().rss / 1024 / 1024, 1)
            memory_percent = round(memory_mb / 2048 * 100, 1)
        except Exception:
            # psutil missing or permission denied — don't fail the probe.
            pass

        # V456 (CODE_V455 Phase 2 + Phase 4): expose WAL size so we catch
        # WAL-bloat regressions (V445 was 3.4GB) before they freeze the
        # daemon, and surface auth/stripe metrics for the V455 directive.
        wal_size_mb = None
        try:
            import os as _os_h
            _wal_path = '/var/data/permitgrab.db-wal'
            if _os_h.path.exists(_wal_path):
                wal_size_mb = round(_os_h.path.getsize(_wal_path) / 1024 / 1024, 1)
        except Exception:
            pass

        auth_metrics = None
        stripe_metrics = None
        try:
            users_total = User.query.count()
            users_pro = User.query.filter(User.plan.in_(('pro', 'professional', 'enterprise'))).count()
            now_dt = datetime.utcnow()
            users_trial_active = User.query.filter(
                User.trial_end_date.isnot(None),
                User.trial_end_date > now_dt,
            ).count()
            users_trial_expired = User.query.filter(
                User.trial_end_date.isnot(None),
                User.trial_end_date <= now_dt,
            ).count()
            last_signup_at = db.session.query(db.func.max(User.created_at)).scalar()
            last_login_at = db.session.query(db.func.max(User.last_login_at)).scalar()
            auth_metrics = {
                'users_total': users_total,
                'users_pro': users_pro,
                'users_trial_active': users_trial_active,
                'users_trial_expired': users_trial_expired,
                'last_signup_at': last_signup_at.isoformat() if last_signup_at else None,
                'last_login_at': last_login_at.isoformat() if last_login_at else None,
            }
        except Exception as _auth_e:
            auth_metrics = {'error': str(_auth_e)[:120]}

        try:
            _sw_conn = permitdb.get_connection()
            sw_total = _sw_conn.execute("SELECT COUNT(*) FROM stripe_webhook_events").fetchone()[0]
            # V465 (CODE_V465 Phase 4): the actual column is processed_at —
            # the original code probed for received_at and silently fell back
            # to None on every health check. Fixed to use the real column name.
            _ts_cols = {c[1] for c in _sw_conn.execute("PRAGMA table_info(stripe_webhook_events)").fetchall()}
            _ts_col = 'processed_at' if 'processed_at' in _ts_cols else (
                'received_at' if 'received_at' in _ts_cols else None
            )
            sw_24h = None
            sw_last = None
            if _ts_col:
                try:
                    sw_24h = _sw_conn.execute(
                        f"SELECT COUNT(*) FROM stripe_webhook_events WHERE {_ts_col} > datetime('now', '-1 day')"
                    ).fetchone()[0]
                    _last_row = _sw_conn.execute(
                        f"SELECT MAX({_ts_col}) FROM stripe_webhook_events"
                    ).fetchone()
                    sw_last = _last_row[0] if _last_row else None
                except Exception:
                    pass
            stripe_metrics = {
                'webhook_events_total': sw_total,
                'webhook_events_24h': sw_24h,
                'last_webhook_at': sw_last,
                'webhook_healthy': bool(sw_total) or (auth_metrics and auth_metrics.get('users_pro', 0) == 0),
            }
        except Exception as _sw_e:
            stripe_metrics = {'error': str(_sw_e)[:120]}

        # V493 IRONCLAD diagnostic surface: per-cycle heartbeat + thread
        # inventory + never-pulled-cities count. Each piece is wrapped in
        # try/except so a single failure doesn't break the health probe.
        scheduled_cycle_completed_at = None
        cycle_stale_minutes = None
        try:
            _hb = permitdb.get_system_state('scheduled_cycle_completed_at')
            if _hb and _hb.get('value'):
                scheduled_cycle_completed_at = _hb['value']
                from datetime import datetime as _dt_h2
                try:
                    _delta = _dt_h2.utcnow() - _dt_h2.fromisoformat(scheduled_cycle_completed_at)
                    cycle_stale_minutes = int(_delta.total_seconds() / 60)
                except Exception:
                    pass
        except Exception:
            pass

        live_thread_names = []
        try:
            import threading as _th_h
            live_thread_names = sorted({
                t.name for t in _th_h.enumerate() if t.is_alive()
            })
        except Exception:
            pass

        # V493 IRONCLAD: cheap counts only on the health probe — Render
        # hits this every ~60s so an expensive 68K-row scraper_runs JOIN
        # is a non-starter. The expensive JOIN moved to a separate
        # /api/admin/staleness-report endpoint that you hit on demand.
        active_cities = None
        active_with_source = None
        active_chronic_failures = None
        try:
            _ac = conn.execute(
                "SELECT COUNT(*) FROM prod_cities WHERE status='active'"
            ).fetchone()
            active_cities = _ac[0] if _ac else None
            _aws = conn.execute(
                "SELECT COUNT(*) FROM prod_cities "
                "WHERE status='active' AND source_id IS NOT NULL "
                "AND consecutive_failures < 10"
            ).fetchone()
            active_with_source = _aws[0] if _aws else None
            _af = conn.execute(
                "SELECT COUNT(*) FROM prod_cities "
                "WHERE status='active' AND consecutive_failures >= 10"
            ).fetchone()
            active_chronic_failures = _af[0] if _af else None
        except Exception:
            pass

        payload = {
            'status': 'healthy' if is_healthy else 'unhealthy',
            'daemon_running': daemon_running,
            'last_collection_at': last_coll_at,
            'collections_last_24h': colls_24h,
            'errors_last_24h': errors_24h,
            'fresh_city_count': fresh,
            'top_error_cities': top_errors,
            'memory_mb': memory_mb,
            'memory_percent': memory_percent,
            'memory_limit_mb': 2048,
            'wal_size_mb': wal_size_mb,
            'auth': auth_metrics,
            'stripe': stripe_metrics,
            # V493 IRONCLAD diagnostic surface
            'scheduled_cycle_completed_at': scheduled_cycle_completed_at,
            'cycle_stale_minutes': cycle_stale_minutes,
            'live_thread_names': live_thread_names,
            'active_cities': active_cities,
            'active_with_source': active_with_source,
            'active_chronic_failures': active_chronic_failures,
        }
        if self_healed:
            payload['self_heal_triggered'] = True
        return jsonify(payload), status_code
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)[:100]}), 503


@admin_bp.route('/api/admin/send-weekly-digests', methods=['POST'])
def admin_send_weekly_digests():
    """Admin cron endpoint — call weekly (Mon 9am ET) to blast the
    V251 F16 weekly market report to every Pro subscriber per their
    digest_cities. Supports ?dry_run=1 for preview.
    """
    key = request.args.get('key') or request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY')
    if not expected or key != expected:
        return jsonify({'error': 'Admin key required'}), 401
    dry_run = request.args.get('dry_run') == '1'
    return jsonify(send_weekly_digests(dry_run=dry_run))


@admin_bp.route('/api/admin/fire-webhooks', methods=['POST'])
def admin_fire_webhooks():
    """Admin cron hook — run the webhook scan-and-fire pass.

    Call every 15m from an external scheduler; returns the count of
    webhooks fired so the cron job can log it. Window defaults to 60m
    so a cron hiccup doesn't lose a whole batch.
    """
    key = request.args.get('key') or request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY')
    if not expected or key != expected:
        return jsonify({'error': 'Admin key required'}), 401
    try:
        window = int(request.args.get('minutes') or 60)
    except (TypeError, ValueError):
        window = 60
    result = scan_and_fire_webhooks(window_minutes=window)
    return jsonify(result)


@admin_bp.route('/api/admin/competitor-alerts/preview')
def admin_competitor_alert_preview():
    """Admin endpoint — returns what would be sent on the next competitor-
    alert run so we can sanity-check before wiring it into the digest loop.
    """
    key = request.args.get('key') or request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY')
    if not expected or key != expected:
        return jsonify({'error': 'Admin key required'}), 401
    try:
        window = int(request.args.get('days', 1))
    except (TypeError, ValueError):
        window = 1
    batches = compute_competitor_alert_batches(window_days=window)
    return jsonify({'batch_count': len(batches), 'batches': batches})


@admin_bp.route('/admin/legacy')
def admin_page():
    """GET /admin/legacy - V12-era admin dashboard, password-gated. V229 A1:
    was @admin_bp.route('/admin') but silently shadowed V227's X-Admin-Key-gated
    HTML command center at /admin. Flask registered both, last-decorator-
    wins silently, so V227's dashboard was unreachable. Moved here; the
    before_request handler below still matches legacy path."""
    # Check for admin password in query param or session
    password = request.args.get('password', '')

    if password and ADMIN_PASSWORD and password == ADMIN_PASSWORD:
        session['admin_authenticated'] = True

    if not session.get('admin_authenticated'):
        if not ADMIN_PASSWORD:
            return render_template('admin/not_configured.html'), 500

        return render_template('admin/login.html')

    # V12.51: Load data from SQLite for admin dashboard
    permit_stats = permitdb.get_permit_stats()
    # V12.53: Count digest subscribers from User model instead of subscribers.json
    digest_subscribers = User.query.filter(User.digest_active == True).all()
    stats = load_stats()

    # V11 Fix 2.1: Get real user stats from database
    all_users = User.query.all()
    pro_users = User.query.filter(User.plan.in_(['pro', 'professional', 'enterprise'])).all()
    alert_users = User.query.filter_by(daily_alerts=True).all()

    # Stats from SQLite
    city_count = permit_stats['city_count']

    # V12: Load collection diagnostic
    diag_path = os.path.join(DATA_DIR, 'collection_diagnostic.json')
    diagnostic = {}
    if os.path.exists(diag_path):
        try:
            with open(diag_path) as f:
                diagnostic = json.load(f, strict=False)
        except Exception:
            pass

    return render_template('admin/legacy.html',
        total_permits=permit_stats['total_permits'],
        city_count=city_count,
        total_users=len(all_users),
        pro_users=len(pro_users),
        last_updated=stats.get('collected_at', ''),
        subscribers=digest_subscribers,
        total_subscribers=len(digest_subscribers),
        diagnostic=diagnostic,
        success_msg=request.args.get('success', ''),
        error_msg=request.args.get('error', ''),
    )


@admin_bp.route('/admin/trigger-collection', methods=['POST'])
def admin_trigger_collection():
    """V12.2: Manually trigger data collection (admin only)."""
    if not session.get('admin_authenticated'):
        return jsonify({"error": "Unauthorized"}), 403

    import threading
    from collector import collect_all

    # Run in background thread so it doesn't block
    # V12.38: Expanded from 60 to 180 days
    thread = threading.Thread(target=collect_all, kwargs={"days_back": 180}, daemon=True)
    thread.start()

    return jsonify({
        "status": "Collection triggered",
        "message": "Running in background. Check /api/stats in a few minutes."
    })


@admin_bp.route('/admin/collector-health')
def admin_collector_health():
    """V15: Collector health dashboard - shows status of all prod cities."""
    if not session.get('admin_authenticated'):
        return redirect('/admin?error=Please+log+in')

    # Get health data
    try:
        health_data = permitdb.get_city_health_status()
        summary = permitdb.get_daily_collection_summary()
        recent_runs = permitdb.get_recent_scraper_runs(limit=20)
    except Exception as e:
        health_data = []
        summary = None
        recent_runs = []
        print(f"[V15] Error loading collector health: {e}")

    # Count by status
    green_count = sum(1 for c in health_data if c.get('health_color') == 'GREEN')
    yellow_count = sum(1 for c in health_data if c.get('health_color') == 'YELLOW')
    red_count = sum(1 for c in health_data if c.get('health_color') == 'RED')

    return render_template('admin/collector_health.html',
        green_count=green_count,
        yellow_count=yellow_count,
        red_count=red_count,
        health_data=health_data,
        summary=summary,
        recent_runs=recent_runs,
    )


@admin_bp.route('/admin/upgrade-user', methods=['POST'])
def admin_upgrade_user():
    """POST /admin/upgrade-user - Upgrade a user's subscription plan."""
    # Check admin authentication
    if not session.get('admin_authenticated'):
        return jsonify({'error': 'Unauthorized'}), 401

    email = request.form.get('email', '').strip().lower()
    plan = request.form.get('plan', 'free').strip().lower()

    if not email:
        return redirect('/admin?error=Email+is+required')

    if plan not in ('free', 'pro', 'enterprise'):
        return redirect('/admin?error=Invalid+plan')

    # V12.53: Direct User model update (removed subscribers.json dependency)
    user_obj = find_user_by_email(email)
    if user_obj:
        user_obj.plan = plan
        db.session.commit()
        return redirect(f'/admin?success=Upgraded+{email}+to+{plan}')
    else:
        return redirect(f'/admin?error=User+{email}+not+found')


@admin_bp.route('/admin/analytics')
def admin_analytics_page():
    """Admin analytics dashboard."""
    # Check admin authentication
    user = get_current_user()
    if not user or user.get('email', '').lower() not in ADMIN_EMAILS:
        if not session.get('admin_authenticated'):
            return "Unauthorized - Admin access required", 403

    # Gather all analytics data
    data = {
        'visitors_today': analytics.get_visitors_today(),
        'signups_week': analytics.get_signups_this_week(),
        'active_users_7d': analytics.get_active_users_7d(),
        'trial_starts_30d': analytics.get_trial_starts_30d(),
        'daily_traffic': analytics.get_daily_traffic(30),
        'top_pages': analytics.get_top_pages(7, 20),
        'funnel': analytics.get_conversion_funnel(30),
        'event_counts': analytics.get_event_counts(7),
        'city_engagement': analytics.get_city_engagement(30),
        'traffic_sources': analytics.get_traffic_sources(30),
        'health_status': analytics.get_latest_health_status(),
        'health_failures': analytics.get_health_failures_recent(20),
        'city_health': analytics.get_city_health_summary(),
        'route_health': analytics.get_route_health_summary(),
        'service_health': analytics.get_service_health_status(),
        'email_perf_7d': analytics.get_email_performance(7),
        'email_perf_30d': analytics.get_email_performance(30),
    }

    return render_template('admin_analytics.html', data=data)


@admin_bp.route('/api/admin/run-daily-alerts', methods=['POST'])
def admin_run_daily_alerts():
    """V170 C3: Manually trigger daily alert emails."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        send_daily_alerts()
        return jsonify({'triggered': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# V521 (audit followup) — fleet-wide Accela contractor backfill via Playwright
# ----------------------------------------------------------------------------
# Status of the long-running backfill thread (only one at a time)
_V521_STATE = {
    'running': False,
    'started_at': None,
    'finished_at': None,
    'cities_attempted': 0,
    'cities_skipped': 0,
    'cities_with_inserts': 0,
    'total_permits_updated': 0,
    'current_city': None,
    'last_error': None,
    'log': [],
}


@admin_bp.route('/api/admin/v521/accela-fleet-status', methods=['GET'])
def admin_v521_accela_fleet_status():
    """V521: status of the Accela fleet-wide Playwright contractor backfill."""
    valid, error = check_admin_key()
    if not valid:
        return error
    return jsonify(_V521_STATE)


@admin_bp.route('/api/admin/v521/accela-fleet-backfill', methods=['POST'])
def admin_v521_accela_fleet_backfill():
    """V521: iterate every active CITY_REGISTRY entry with platform=accela
    and call the V508 Playwright detail-batch backfill on permits with
    missing contractor_name. Runs in a background thread; poll
    /accela-fleet-status for progress.

    Body (all optional):
      {"limit_per_city": 25,        # max Playwright fetches per city
       "max_cities": 200,            # cap on cities processed this run
       "only_missing_contractor": true}

    Per-city ETA ≈ 25 permits × ~3s Playwright = ~75s. 66 Accela cities
    ≈ 80 min total. The HTTP call returns immediately."""
    valid, error = check_admin_key()
    if not valid:
        return error
    if _V521_STATE['running']:
        return jsonify({
            'status': 'already_running',
            'started_at': _V521_STATE['started_at'],
            'current_city': _V521_STATE['current_city'],
            'cities_attempted': _V521_STATE['cities_attempted'],
        }), 409

    body = request.get_json(silent=True) or {}
    limit_per_city = min(int(body.get('limit_per_city', 25)), 100)
    max_cities = int(body.get('max_cities', 200))
    only_missing = bool(body.get('only_missing_contractor', True))

    import threading
    from datetime import datetime as _dt

    def _run():
        _V521_STATE.update({
            'running': True,
            'started_at': _dt.utcnow().isoformat(),
            'finished_at': None,
            'cities_attempted': 0,
            'cities_skipped': 0,
            'cities_with_inserts': 0,
            'total_permits_updated': 0,
            'current_city': None,
            'last_error': None,
            'log': [],
        })
        try:
            from city_registry_data import CITY_REGISTRY
            from accela_playwright_collector import fetch_accela_details_batch
        except Exception as e:
            _V521_STATE['last_error'] = f'import: {e}'
            _V521_STATE['running'] = False
            _V521_STATE['finished_at'] = _dt.utcnow().isoformat()
            return

        accela_cities = []
        for key, cfg in CITY_REGISTRY.items():
            if cfg.get('platform') != 'accela':
                continue
            if not cfg.get('active', True):
                continue
            agency = cfg.get('agency_code') or cfg.get('_accela_city_key')
            slug = cfg.get('slug') or key
            if not agency:
                continue
            accela_cities.append({'key': key, 'slug': slug, 'agency': agency,
                                  'name': cfg.get('name', key)})

        accela_cities = accela_cities[:max_cities]
        _V521_STATE['log'].append(
            f"Iterating {len(accela_cities)} active Accela cities")

        for city in accela_cities:
            slug = city['slug']
            agency = city['agency']
            _V521_STATE['current_city'] = f"{slug} (/{agency}/)"
            _V521_STATE['cities_attempted'] += 1

            # Find permits with missing contractor on this slug
            try:
                _conn = permitdb.get_connection()
                sql = (
                    "SELECT permit_number FROM permits "
                    "WHERE source_city_key = ? AND permit_number IS NOT NULL "
                    "AND permit_number != ''"
                )
                if only_missing:
                    sql += " AND (contractor_name IS NULL OR contractor_name = '')"
                sql += " ORDER BY date DESC LIMIT ?"
                rows = _conn.execute(sql, (slug, limit_per_city)).fetchall()
                _conn.close()
                permit_numbers = [
                    r[0] if not isinstance(r, dict) else r['permit_number']
                    for r in rows
                ]
            except Exception as _qe:
                _V521_STATE['log'].append(f"  {slug}: query failed — {_qe}")
                _V521_STATE['cities_skipped'] += 1
                continue

            if not permit_numbers:
                _V521_STATE['log'].append(f"  {slug}: no missing-contractor permits, skip")
                _V521_STATE['cities_skipped'] += 1
                continue

            # Playwright batch fetch
            try:
                results = fetch_accela_details_batch(
                    agency, permit_numbers, max_permits=limit_per_city)
            except Exception as _be:
                _V521_STATE['log'].append(f"  {slug}: Playwright batch failed — {_be}")
                _V521_STATE['last_error'] = f"{slug}: {_be}"
                continue

            # Persist contractor names
            updated = 0
            try:
                _conn = permitdb.get_connection()
                for pn, info in (results or {}).items():
                    cn = (info or {}).get('contractor_name')
                    if not cn:
                        continue
                    rc = _conn.execute(
                        "UPDATE permits SET contractor_name = ? "
                        "WHERE source_city_key = ? AND permit_number = ?",
                        (cn, slug, pn),
                    ).rowcount
                    updated += (rc or 0)
                _conn.commit()
                _conn.close()
            except Exception as _ue:
                _V521_STATE['log'].append(f"  {slug}: update failed — {_ue}")
                continue

            _V521_STATE['total_permits_updated'] += updated
            if updated > 0:
                _V521_STATE['cities_with_inserts'] += 1
            _V521_STATE['log'].append(
                f"  {slug} (/{agency}/): processed={len(permit_numbers)} "
                f"updated={updated}")
            # cap log size
            if len(_V521_STATE['log']) > 500:
                _V521_STATE['log'] = _V521_STATE['log'][-500:]

        _V521_STATE['running'] = False
        _V521_STATE['finished_at'] = _dt.utcnow().isoformat()
        _V521_STATE['current_city'] = None

    t = threading.Thread(target=_run, name='v521-accela-fleet-backfill', daemon=True)
    t.start()

    return jsonify({
        'status': 'started',
        'message': 'Background fleet-wide Accela Playwright backfill running. Poll /api/admin/v521/accela-fleet-status for progress.',
        'limit_per_city': limit_per_city,
        'max_cities': max_cities,
    })


# V518 (V511 STRIPE_CONNECTION) — diagnostic + reconciliation endpoints
# ---------------------------------------------------------------------

@admin_bp.route('/api/admin/stripe/status', methods=['GET'])
def admin_stripe_status():
    """V518: full subscriber + Stripe state snapshot for debugging.
    Returns every subscribers row with its Stripe linkage columns plus
    the 20 most recent webhook events with their dispatch outcomes."""
    valid, error = check_admin_key()
    if not valid:
        return error
    conn = permitdb.get_connection()
    try:
        rows = conn.execute("""
            SELECT id, email, name, plan, digest_cities, active,
                   created_at, last_digest_sent_at,
                   stripe_customer_id, stripe_subscription_id,
                   trial_end, current_period_end, cancelled_at
            FROM subscribers
            ORDER BY created_at DESC
        """).fetchall()
        recent_events = conn.execute("""
            SELECT event_id, event_type, processed_at, customer_id,
                   subscription_id, handler_status, handler_error
            FROM stripe_webhook_events
            ORDER BY processed_at DESC
            LIMIT 20
        """).fetchall()
        plan_rows = conn.execute(
            "SELECT plan, COUNT(*) AS n FROM subscribers GROUP BY plan"
        ).fetchall()
        return jsonify({
            'subscribers': [dict(r) for r in rows],
            'recent_webhook_events': [dict(r) for r in recent_events],
            'subscriber_count': len(rows),
            'plan_breakdown': {r['plan']: r['n'] for r in plan_rows},
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@admin_bp.route('/api/admin/stripe/backfill-existing', methods=['POST'])
def admin_stripe_backfill_existing():
    """V518: for every subscribers row with stripe_customer_id IS NULL,
    look up the customer in Stripe by email and populate
    stripe_customer_id + stripe_subscription_id + trial_end +
    current_period_end + plan."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        import os
        import stripe
        from datetime import datetime as _dt
        stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', '')
        if not stripe.api_key:
            return jsonify({'error': 'STRIPE_SECRET_KEY not set'}), 500
    except ImportError:
        return jsonify({'error': 'stripe library not installed'}), 500

    conn = permitdb.get_connection()
    out = []
    try:
        rows = conn.execute(
            "SELECT id, email, plan FROM subscribers WHERE stripe_customer_id IS NULL"
        ).fetchall()
        for row in rows:
            email = row['email'] if isinstance(row, dict) else row[1]
            row_id = row['id'] if isinstance(row, dict) else row[0]
            try:
                customers = stripe.Customer.list(email=email, limit=10).data
            except Exception as e:
                out.append({'email': email, 'status': 'error', 'detail': str(e)[:120]})
                continue
            if not customers:
                out.append({'email': email, 'status': 'no_stripe_match'})
                continue
            cust = customers[0]
            try:
                subs = stripe.Subscription.list(customer=cust.id, status='all', limit=10).data
            except Exception as e:
                subs = []
            active = [s for s in subs if s.status in ('active', 'trialing')]
            sub = active[0] if active else (subs[0] if subs else None)
            new_plan = sub.status if sub else 'free'
            te = (_dt.fromtimestamp(sub.trial_end).isoformat()
                  if sub and getattr(sub, 'trial_end', None) else None)
            pe = (_dt.fromtimestamp(sub.current_period_end).isoformat()
                  if sub and getattr(sub, 'current_period_end', None) else None)
            conn.execute(
                "UPDATE subscribers SET "
                "  stripe_customer_id = ?, stripe_subscription_id = ?, "
                "  plan = ?, trial_end = ?, current_period_end = ? "
                "WHERE id = ?",
                (cust.id, sub.id if sub else None, new_plan, te, pe, row_id)
            )
            out.append({
                'email': email, 'status': 'linked',
                'customer': cust.id,
                'subscription': sub.id if sub else None,
                'plan': new_plan,
            })
        conn.commit()
        return jsonify({'status': 'ok', 'processed': len(out), 'results': out})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@admin_bp.route('/api/admin/stripe/import-orphans', methods=['POST'])
def admin_stripe_import_orphans():
    """V518: discover Stripe customers (created within N days, default
    60) that don't have a subscribers row at all and create one. These
    are the pre-V494 paid signups (Higgins, Meyer) who landed in Stripe
    but never got synced to the local digest scheduler."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        import os
        import stripe
        from datetime import datetime as _dt, timedelta as _td
        stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', '')
        if not stripe.api_key:
            return jsonify({'error': 'STRIPE_SECRET_KEY not set'}), 500
    except ImportError:
        return jsonify({'error': 'stripe library not installed'}), 500

    body = request.get_json(silent=True) or {}
    days = int(body.get('days', 60))
    since_ts = int((_dt.utcnow() - _td(days=days)).timestamp())

    conn = permitdb.get_connection()
    out = []
    try:
        try:
            customers = stripe.Customer.list(
                limit=100, created={'gte': since_ts}
            ).data
        except Exception as e:
            return jsonify({'error': f'stripe list failed: {e}'}), 500

        for cust in customers:
            email = (cust.email or '').strip().lower()
            if not email:
                continue
            existing = conn.execute(
                "SELECT 1 FROM subscribers WHERE LOWER(email) = ? "
                "OR stripe_customer_id = ?",
                (email, cust.id)
            ).fetchone()
            if existing:
                continue
            try:
                subs = stripe.Subscription.list(
                    customer=cust.id, status='all', limit=10
                ).data
            except Exception:
                subs = []
            sub = subs[0] if subs else None
            te = (_dt.fromtimestamp(sub.trial_end).isoformat()
                  if sub and getattr(sub, 'trial_end', None) else None)
            pe = (_dt.fromtimestamp(sub.current_period_end).isoformat()
                  if sub and getattr(sub, 'current_period_end', None) else None)
            new_plan = sub.status if sub else 'free'
            conn.execute(
                "INSERT INTO subscribers "
                "(email, name, plan, digest_cities, active, created_at, "
                " stripe_customer_id, stripe_subscription_id, "
                " trial_end, current_period_end) "
                "VALUES (?, ?, ?, NULL, 1, ?, ?, ?, ?, ?)",
                (
                    email,
                    (cust.name or email.split('@')[0].title()),
                    new_plan,
                    _dt.fromtimestamp(cust.created).isoformat(),
                    cust.id,
                    sub.id if sub else None,
                    te, pe,
                )
            )
            out.append({
                'email': email,
                'customer': cust.id,
                'subscription': sub.id if sub else None,
                'plan': new_plan,
                'note': 'imported (digest_cities NULL — needs follow-up)',
            })
        conn.commit()
        return jsonify({'status': 'ok', 'imported': len(out), 'results': out})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


