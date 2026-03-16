"""
City API Tester - Rapidly test and verify city permit API endpoints.
"""

import requests
import json
import time

# List of potential city APIs to test
# Format: (city_key, city_name, state, endpoint, date_field)
CITIES_TO_TEST = [
    # Socrata APIs - Test with simple query
    ("hartford", "Hartford", "CT", "https://data.hartford.gov/resource/ay68-d6gf.json", "permit_date"),
    ("new_haven", "New Haven", "CT", "https://data.newhavenct.gov/resource/f2zn-f7ky.json", "date_issued"),
    ("bridgeport", "Bridgeport", "CT", "https://data.bridgeportct.gov/resource/9nzw-c25y.json", "issue_date"),
    ("providence", "Providence", "RI", "https://data.providenceri.gov/resource/pnxs-wa27.json", "issue_date"),
    ("worcester", "Worcester", "MA", "https://data.worcesterma.gov/resource/7h2u-5jxx.json", "issue_date"),
    ("springfield_ma", "Springfield", "MA", "https://data.springfield-ma.gov/resource/cxay-8wzx.json", "issue_date"),
    ("knoxville", "Knoxville", "TN", "https://data.knoxvilletn.gov/resource/8k4g-kp4s.json", "issued_date"),
    ("chattanooga", "Chattanooga", "TN", "https://data.chattlibrary.org/resource/5v8x-qh3r.json", "issued_date"),
    ("durham", "Durham", "NC", "https://live-durhamnc.opendata.arcgis.com/resource/7q65-gv4c.json", "issue_date"),
    ("greensboro", "Greensboro", "NC", "https://data.greensboro-nc.gov/resource/mdbw-5j5n.json", "issued_date"),
    ("reno", "Reno", "NV", "https://data.reno.gov/resource/5hzs-6g39.json", "issue_date"),
    ("henderson", "Henderson", "NV", "https://data.cityofhenderson.com/resource/n3h5-jg4v.json", "issue_date"),
    ("lincoln", "Lincoln", "NE", "https://opendata.lincoln.ne.gov/resource/vgxm-k9m2.json", "issue_date"),
    ("des_moines", "Des Moines", "IA", "https://data.desmoines.gov/resource/6m4y-h3pc.json", "issue_date"),
    ("madison", "Madison", "WI", "https://data.cityofmadison.com/resource/py3f-gvn5.json", "issue_date"),
    ("boise", "Boise", "ID", "https://opendata.cityofboise.org/resource/j4mq-c5hk.json", "issue_date"),
    ("spokane", "Spokane", "WA", "https://data.spokanecity.org/resource/nh7g-nx4y.json", "issue_date"),
    ("tacoma", "Tacoma", "WA", "https://data.cityoftacoma.org/resource/8fh2-5d3c.json", "issue_date"),
    ("anchorage", "Anchorage", "AK", "https://data.muni.org/resource/y5zs-7jxf.json", "issue_date"),
    ("honolulu_new", "Honolulu", "HI", "https://data.honolulu.gov/resource/f2jf-kqkc.json", "issue_date"),
    ("santa_barbara", "Santa Barbara", "CA", "https://data.santabarbara.gov/resource/mf6h-gyj2.json", "issue_date"),
    ("santa_rosa", "Santa Rosa", "CA", "https://data.srcity.org/resource/v5p7-bzwc.json", "issue_date"),
    ("berkeley", "Berkeley", "CA", "https://data.cityofberkeley.info/resource/p6nj-g5r2.json", "issue_date"),
    ("pasadena", "Pasadena", "CA", "https://data.cityofpasadena.net/resource/4y6x-g8v5.json", "issue_date"),
    ("santa_monica", "Santa Monica", "CA", "https://data.smgov.net/resource/a3em-6ahy.json", "issue_date"),
    ("glendale_ca", "Glendale", "CA", "https://data.glendaleca.gov/resource/q3hv-5kf8.json", "issue_date"),
    ("long_beach_new", "Long Beach", "CA", "https://data.longbeach.gov/resource/a5g6-fczp.json", "issue_date"),
    ("oakland_new", "Oakland", "CA", "https://data.oaklandca.gov/resource/u5u7-8jdc.json", "issue_date"),
    ("san_jose_new", "San Jose", "CA", "https://data.sanjoseca.gov/resource/8k3j-ph5d.json", "issue_date"),
    ("alexandria", "Alexandria", "VA", "https://data.alexandriava.gov/resource/w3yj-qk7x.json", "issue_date"),
    ("arlington_va", "Arlington", "VA", "https://data.arlingtonva.us/resource/mdu5-8gxq.json", "issue_date"),
    ("richmond", "Richmond", "VA", "https://data.richmondgov.com/resource/7fwk-p9cb.json", "issue_date"),
    ("norfolk", "Norfolk", "VA", "https://data.norfolk.gov/resource/d4ht-kh3e.json", "issue_date"),
    ("charleston", "Charleston", "SC", "https://data.charleston-sc.gov/resource/p5c7-m9n4.json", "issue_date"),
    ("columbia_sc", "Columbia", "SC", "https://data.columbiasc.gov/resource/jhdk-4nf7.json", "issue_date"),
    ("savannah", "Savannah", "GA", "https://data.savannahga.gov/resource/vm4c-y8k2.json", "issue_date"),
    ("augusta", "Augusta", "GA", "https://data.augustaga.gov/resource/n4gm-p3h7.json", "issue_date"),
    ("mobile", "Mobile", "AL", "https://data.mobileal.gov/resource/r8nf-6wv3.json", "issue_date"),
    ("montgomery", "Montgomery", "AL", "https://data.montgomeryal.gov/resource/k3jx-n8v5.json", "issue_date"),
    ("jackson_ms", "Jackson", "MS", "https://data.jacksonms.gov/resource/h4rx-m7n2.json", "issue_date"),
    ("little_rock", "Little Rock", "AR", "https://data.littlerock.gov/resource/5g7v-n4h8.json", "issue_date"),
    ("tulsa", "Tulsa", "OK", "https://data.tulsaok.gov/resource/u8p4-jnv5.json", "issue_date"),
    ("wichita", "Wichita", "KS", "https://data.wichita.gov/resource/k7n4-m3g6.json", "issue_date"),
    ("colorado_springs", "Colorado Springs", "CO", "https://data.coloradosprings.gov/resource/m5g7-h4v8.json", "issue_date"),
    ("aurora_co", "Aurora", "CO", "https://data.auroragov.org/resource/j8n4-k5v7.json", "issue_date"),
    ("albuquerque_new", "Albuquerque", "NM", "https://data.cabq.gov/resource/j7n4-p5h8.json", "issue_date"),
    ("el_paso", "El Paso", "TX", "https://data.elpasotexas.gov/resource/m3n7-k4v5.json", "issue_date"),
    ("lubbock", "Lubbock", "TX", "https://data.lubbocktx.gov/resource/g4v7-n5h8.json", "issue_date"),
    ("amarillo", "Amarillo", "TX", "https://data.amarillo.gov/resource/h5n8-m4v7.json", "issue_date"),
    ("laredo", "Laredo", "TX", "https://data.ci.laredo.tx.us/resource/j3n4-k8v5.json", "issue_date"),
]


def test_endpoint(city_key, city_name, state, endpoint, date_field):
    """Test if an endpoint returns data."""
    try:
        # Simple request with just limit
        params = {"$limit": 5}
        resp = requests.get(endpoint, params=params, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                # Check what fields are available
                fields = list(data[0].keys())
                has_address = any('address' in f.lower() for f in fields)
                return {
                    "status": "OK",
                    "count": len(data),
                    "fields": fields[:10],  # First 10 fields
                    "has_address": has_address,
                }
            else:
                return {"status": "EMPTY", "count": 0}
        else:
            return {"status": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": f"ERROR: {str(e)[:50]}"}


def main():
    print("Testing city permit APIs...")
    print("=" * 60)

    working = []
    failed = []

    for city_key, city_name, state, endpoint, date_field in CITIES_TO_TEST:
        result = test_endpoint(city_key, city_name, state, endpoint, date_field)

        if result["status"] == "OK" and result.get("has_address"):
            print(f"[OK] {city_name}, {state} - {result['count']} records, has address")
            working.append({
                "key": city_key,
                "name": city_name,
                "state": state,
                "endpoint": endpoint,
                "date_field": date_field,
                "fields": result["fields"],
            })
        else:
            print(f"[FAIL] {city_name}, {state} - {result['status']}")
            failed.append((city_name, state, result["status"]))

        time.sleep(0.3)  # Rate limit

    print("\n" + "=" * 60)
    print(f"RESULTS: {len(working)} working, {len(failed)} failed")

    if working:
        print("\nWORKING CITIES:")
        for city in working:
            print(f"  - {city['name']}, {city['state']}: {city['endpoint']}")
            print(f"    Fields: {', '.join(city['fields'][:5])}")

    return working


if __name__ == "__main__":
    main()
