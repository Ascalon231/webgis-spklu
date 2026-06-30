import os
import sys
import time
import logging
import functools
import socket
import threading
import pg8000.dbapi as pg
from flask import Flask, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

# ─── Env Validation ───
REQUIRED_ENV = {
    'DB_HOST': 'Database host',
    'DB_PORT': 'Database port',
    'DB_NAME': 'Database name',
    'DB_USER': 'Database user',
    'DB_PASSWORD': 'Database password',
}
missing = [f"{k} ({v})" for k, v in REQUIRED_ENV.items() if not os.getenv(k)]
if missing:
    log.critical("Missing required environment variables:\n  - %s", "\n  - ".join(missing))
    sys.exit(1)

app = Flask(__name__)
CORS(app)

redis_url = os.getenv("REDIS_URL") or os.getenv("KV_URL")
storage_uri = redis_url if redis_url else "memory://"

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=storage_uri
)

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# ─── In-memory TTL Cache ───
_cache = {}
_cache_lock = threading.Lock()

def cached(ttl_seconds=300):
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            key = (f.__name__, request.url if 'request' in dir() else '', args, tuple(sorted(kwargs.items())))
            with _cache_lock:
                hit = _cache.get(key)
                if hit and time.time() - hit['ts'] < ttl_seconds:
                    return hit['data']
            result = f(*args, **kwargs)
            with _cache_lock:
                _cache[key] = {'data': result, 'ts': time.time()}
            return result
        return wrapper
    return decorator

def clear_cache():
    with _cache_lock:
        _cache.clear()

# ─── DB ───
def get_db_connection():
    return pg.connect(
        host=DB_HOST,
        port=int(DB_PORT),
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )

# ─── Structured Error Response ───
def err_response(message, status=400, details=None):
    resp = {"error": True, "message": message}
    if details:
        resp["details"] = details
    return jsonify(resp), status

# ─── Routes ───
@app.route('/')
def index():
    try:
        return render_template('index.html')
    except Exception as e:
        log.error("index(): %s", e)
        return err_response("Gagal memuat halaman utama", 500)

@app.route('/robots.txt')
def serve_robots():
    return app.send_static_file('robots.txt')

@app.route('/sitemap.xml')
def serve_sitemap():
    return app.send_static_file('sitemap.xml')

@app.route('/api/spklu', methods=['GET'])
@limiter.limit("30 per minute")
def get_spklu():
    conn = None
    try:
        page = request.args.get('page', 1, type=int)
        limit = request.args.get('limit', 500, type=int)
        limit = min(limit, 2000)
        offset = (page - 1) * limit
        conn = get_db_connection()
        cur = conn.cursor()
        # Get total count first
        cur.execute("SELECT count(*) FROM sebaran_spklu;")
        total_count = cur.fetchone()[0]
        # Then paginated data
        query = """
        WITH ranked_spklu AS (
            SELECT gid, nama_spklu, alamat, wadmkk, wadmpr, latitude, longitude, geom,
                   provider, ROW_NUMBER() OVER (PARTITION BY latitude, longitude ORDER BY gid) AS rank,
                   COUNT(*) OVER (PARTITION BY latitude, longitude) AS unit_count
            FROM sebaran_spklu
        )
        SELECT jsonb_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(jsonb_agg(feature), '[]'::jsonb),
            'total', CAST(%s AS int),
            'page', CAST(%s AS int),
            'limit', CAST(%s AS int)
        )
        FROM (
            SELECT jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(
                    CASE WHEN rank = 1 THEN geom
                    ELSE ST_SetSRID(ST_MakePoint(
                        longitude + 0.00008 * cos((rank - 1) * 1.04719755),
                        latitude + 0.00008 * sin((rank - 1) * 1.04719755)
                    ), 4326) END
                )::jsonb,
                'properties', jsonb_build_object(
                    'gid', gid, 'name', nama_spklu, 'address', alamat,
                    'city', wadmkk, 'province', wadmpr, 'provider', COALESCE(provider, ''),
                    'unit_count', unit_count,
                    'lat', latitude + CASE WHEN rank = 1 THEN 0 ELSE 0.00008 * sin((rank - 1) * 1.04719755) END,
                    'lon', longitude + CASE WHEN rank = 1 THEN 0 ELSE 0.00008 * cos((rank - 1) * 1.04719755) END
                )
            ) AS feature
            FROM ranked_spklu
            ORDER BY gid
            OFFSET CAST(%s AS int) LIMIT CAST(%s AS int)
        ) features;
        """
        cur.execute(query, (total_count, page, limit, offset, limit))
        geojson = cur.fetchone()[0]
        return jsonify(geojson)
    except Exception as e:
        log.error("get_spklu(): %s", e)
        return err_response("Gagal memuat data SPKLU", 500, str(e))
    finally:
        if conn: conn.close()

@app.route('/api/spklu/all', methods=['GET'])
@limiter.limit("10 per minute")
@cached(600)
def get_spklu_all():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = """
        WITH ranked_spklu AS (
            SELECT gid, nama_spklu, alamat, wadmkk, wadmpr, latitude, longitude, geom,
                   provider, ROW_NUMBER() OVER (PARTITION BY latitude, longitude ORDER BY gid) AS rank,
                   COUNT(*) OVER (PARTITION BY latitude, longitude) AS unit_count
            FROM sebaran_spklu
        )
        SELECT jsonb_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(jsonb_agg(feature), '[]'::jsonb)
        )
        FROM (
            SELECT jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(
                    CASE WHEN rank = 1 THEN geom
                    ELSE ST_SetSRID(ST_MakePoint(
                        longitude + 0.00008 * cos((rank - 1) * 1.04719755),
                        latitude + 0.00008 * sin((rank - 1) * 1.04719755)
                    ), 4326) END
                )::jsonb,
                'properties', jsonb_build_object(
                    'gid', gid, 'name', nama_spklu, 'address', alamat,
                    'city', wadmkk, 'province', wadmpr, 'provider', COALESCE(provider, ''),
                    'unit_count', unit_count,
                    'lat', latitude + CASE WHEN rank = 1 THEN 0 ELSE 0.00008 * sin((rank - 1) * 1.04719755) END,
                    'lon', longitude + CASE WHEN rank = 1 THEN 0 ELSE 0.00008 * cos((rank - 1) * 1.04719755) END
                )
            ) AS feature
            FROM ranked_spklu
            ORDER BY gid
        ) features;
        """
        cur.execute(query)
        geojson = cur.fetchone()[0]
        return jsonify(geojson)
    except Exception as e:
        log.error("get_spklu_all(): %s", e)
        return err_response("Gagal memuat data SPKLU", 500, str(e))
    finally:
        if conn: conn.close()

@app.route('/api/routes/road', methods=['GET'])
@limiter.limit("60 per minute")
def get_road_routes():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        zoom_str = request.args.get('zoom', '5')
        try:
            zoom = int(float(zoom_str))
        except ValueError:
            zoom = 5

        if zoom < 7:
            tolerance = 0.0025
        elif zoom < 10:
            tolerance = 0.001
        elif zoom < 13:
            tolerance = 0.0003
        else:
            tolerance = 0.00005

        bbox_str = request.args.get('bbox')
        bbox_filter = ""
        params = []

        if bbox_str and zoom >= 8:
            try:
                coords = [float(c) for c in bbox_str.split(',')]
                if len(coords) == 4:
                    bbox_filter = "AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)"
                    params = coords
            except ValueError:
                pass

        query = f"""
        SELECT jsonb_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(jsonb_agg(feature), '[]'::jsonb)
        )
        FROM (
            SELECT jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(ST_Simplify(geom, %s))::jsonb,
                'properties', jsonb_build_object(
                    'gid', gid, 'name', COALESCE(link_name, 'Jalan Nasional'),
                    'class', kelas_jalan, 'function', fungsi_jalan,
                    'status', status_jalan, 'length', panjang
                )
            ) AS feature
            FROM jalan_nasional
            WHERE 1=1 {bbox_filter}
        ) features;
        """
        cur.execute(query, [tolerance] + params)
        geojson = cur.fetchone()[0]
        return jsonify(geojson)
    except Exception as e:
        log.error("get_road_routes(): %s", e)
        return err_response("Gagal memuat data jalan", 500, str(e))
    finally:
        if conn: conn.close()

@app.route('/api/spklu/route-buffer', methods=['POST'])
@limiter.limit("10 per minute")
def get_spklu_by_route_buffer():
    conn = None
    try:
        data = request.get_json()
        if not data or 'route' not in data:
            return err_response("Data 'route' tidak ditemukan dalam request", 400)
        route_coords = data['route']
        if not isinstance(route_coords, list) or len(route_coords) == 0:
            return err_response("Koordinat rute tidak valid", 400)
        coord_str = ", ".join(f"{lng} {lat}" for lat, lng in route_coords if isinstance(lat, (int, float)) and isinstance(lng, (int, float)))
        if not coord_str:
            return err_response("Tidak ada koordinat yang valid", 400)
        conn = get_db_connection()
        cur = conn.cursor()
        query = f"""
        WITH route_line AS (
            SELECT ST_MakeLine(ST_MakePoint({coord_str}))::geography AS geom
        )
        SELECT jsonb_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(jsonb_agg(feature), '[]'::jsonb)
        )
        FROM (
            SELECT jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(s.geom)::jsonb,
                'properties', jsonb_build_object(
                    'gid', s.gid, 'name', s.nama_spklu, 'address', s.alamat,
                    'city', s.wadmkk, 'province', s.wadmpr,
                    'lat', s.latitude, 'lon', s.longitude,
                    'provider', s.provider, 'connector', s.connector, 'power', s.power
                )
            ) AS feature
            FROM sebaran_spklu s, route_line rl
            WHERE ST_DWithin(s.geom::geography, rl.geom, 5000)
        ) features;
        """
        cur.execute(query)
        geojson = cur.fetchone()[0]
        return jsonify(geojson)
    except Exception as e:
        log.error("get_spklu_by_route_buffer(): %s", e)
        return err_response("Gagal memproses buffer rute", 500, str(e))
    finally:
        if conn: conn.close()

@app.route('/api/stats', methods=['GET'])
@limiter.limit("60 per minute")
@cached(120)
def get_stats():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM sebaran_spklu;")
        total_count = cur.fetchone()[0]

        cur.execute("""
            SELECT COALESCE(wadmpr, 'Tidak Diketahui'), count(*)
            FROM sebaran_spklu GROUP BY wadmpr ORDER BY count DESC;
        """)
        prov_stats = [{"province": r[0], "count": r[1]} for r in cur.fetchall()]

        cur.execute("""
            SELECT COALESCE(wadmkk, 'Tidak Diketahui'), count(*)
            FROM sebaran_spklu GROUP BY wadmkk ORDER BY count DESC;
        """)
        city_stats = [{"city": r[0], "count": r[1]} for r in cur.fetchall()]

        cur.execute("""
            SELECT p.kabupaten_kota, p.jumlah_penduduk,
                   count(s.gid) AS spklu_count,
                   ROUND((count(s.gid)::numeric / NULLIF(p.jumlah_penduduk::numeric, 0)) * 100000, 2) AS spklu_per_100k
            FROM jumlah_penduduk_kabkot p
            LEFT JOIN sebaran_spklu s ON s.wadmkk = p.kabupaten_kota
            WHERE p.jumlah_penduduk > 0
            GROUP BY p.kabupaten_kota, p.jumlah_penduduk
            ORDER BY spklu_count DESC, spklu_per_100k DESC;
        """)
        density_stats = [{"city": r[0], "population": int(r[1]) if r[1] else 0, "count": r[2], "ratio": float(r[3])} for r in cur.fetchall()]

        return jsonify({"total": total_count, "provinces": prov_stats, "cities": city_stats, "densities": density_stats})
    except Exception as e:
        log.error("get_stats(): %s", e)
        return err_response("Gagal memuat statistik", 500, str(e))
    finally:
        if conn: conn.close()

@app.route('/analytics')
def analytics():
    try:
        return render_template('analytics.html')
    except Exception as e:
        log.error("analytics(): %s", e)
        return err_response("Gagal memuat halaman analitik", 500)

@app.route('/api/spklu/list', methods=['GET'])
@limiter.limit("60 per minute")
@cached(300)
def get_spklu_list():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT gid, nama_spklu, alamat, wadmkk, wadmpr, latitude, longitude
            FROM sebaran_spklu ORDER BY wadmpr, wadmkk, nama_spklu;
        """)
        rows = cur.fetchall()
        return jsonify([{"gid": r[0], "name": r[1], "address": r[2], "city": r[3], "province": r[4], "lat": float(r[5]) if r[5] else 0, "lon": float(r[6]) if r[6] else 0} for r in rows])
    except Exception as e:
        log.error("get_spklu_list(): %s", e)
        return err_response("Gagal memuat daftar SPKLU", 500, str(e))
    finally:
        if conn: conn.close()

@app.route('/api/analytics/distance-stats', methods=['GET'])
@limiter.limit("60 per minute")
@cached(300)
def get_distance_stats():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            WITH spklu_dist AS (
                SELECT s.gid, (SELECT ST_DistanceSphere(s.geom, s2.geom) / 1000
                    FROM sebaran_spklu s2 WHERE s2.gid != s.gid
                    ORDER BY s.geom <-> s2.geom LIMIT 1) AS distance_km
                FROM sebaran_spklu s
            )
            SELECT CASE
                WHEN distance_km < 1 THEN '< 1 km'
                WHEN distance_km < 5 THEN '1 - 5 km'
                WHEN distance_km < 10 THEN '5 - 10 km'
                WHEN distance_km < 50 THEN '10 - 50 km'
                ELSE '> 50 km'
            END AS bucket, COUNT(*)::int FROM spklu_dist
            GROUP BY bucket ORDER BY MIN(distance_km);
        """)
        distribution = [{"bucket": r[0], "count": r[1]} for r in cur.fetchall()]

        cur.execute("""
            WITH spklu_dist AS (
                SELECT s.gid, (SELECT ST_DistanceSphere(s.geom, s2.geom) / 1000
                    FROM sebaran_spklu s2 WHERE s2.gid != s.gid
                    ORDER BY s.geom <-> s2.geom LIMIT 1) AS distance_km
                FROM sebaran_spklu s
            )
            SELECT ROUND(AVG(distance_km)::numeric, 2), ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY distance_km)::numeric, 2),
                   ROUND(MIN(distance_km)::numeric, 2), ROUND(MAX(distance_km)::numeric, 2)
            FROM spklu_dist;
        """)
        row = cur.fetchone()
        aggregates = {"avg": float(row[0]) if row[0] else 0, "median": float(row[1]) if row[1] else 0, "min": float(row[2]) if row[2] else 0, "max": float(row[3]) if row[3] else 0}
        return jsonify({"distribution": distribution, "aggregates": aggregates})
    except Exception as e:
        log.error("get_distance_stats(): %s", e)
        return err_response("Gagal memuat statistik jarak", 500, str(e))
    finally:
        if conn: conn.close()

@app.route('/api/analytics/road-isodistance', methods=['GET'])
@limiter.limit("15 per minute")
@cached(600)
def get_road_isodistance():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        zoom_str = request.args.get('zoom', '6')
        try:
            zoom = int(float(zoom_str))
        except ValueError:
            zoom = 6
        tol = 0.01 if zoom < 7 else (0.005 if zoom < 9 else (0.001 if zoom < 12 else 0.0001))
        cur.execute(f"""
            SELECT jsonb_build_object('type', 'FeatureCollection', 'features', COALESCE(jsonb_agg(feature), '[]'::jsonb))
            FROM (
                SELECT jsonb_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(ST_Simplify(j.geom, {tol}))::jsonb,
                    'properties', jsonb_build_object(
                        'gid', j.gid, 'name', j.link_name, 'class', j.kelas_jalan, 'length', j.panjang,
                        'nearest_spklu_km', ROUND((SELECT ST_DistanceSphere(j.geom, s.geom) / 1000
                            FROM sebaran_spklu s ORDER BY j.geom <-> s.geom LIMIT 1)::numeric, 2)
                    )
                ) AS feature
                FROM jalan_nasional j WHERE j.geom IS NOT NULL
            ) features;
        """)
        return jsonify(cur.fetchone()[0])
    except Exception as e:
        log.error("get_road_isodistance(): %s", e)
        return err_response("Gagal memuat isodistance", 500, str(e))
    finally:
        if conn: conn.close()

@app.route('/api/analytics/comprehensive', methods=['GET'])
@limiter.limit("10 per minute")
@cached(600)
def comprehensive_analytics():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            WITH spklu_cities AS (SELECT DISTINCT wadmkk FROM sebaran_spklu WHERE wadmkk IS NOT NULL)
            SELECT COALESCE(SUM(p.jumlah_penduduk::numeric), 0)::int, COUNT(p.kabupaten_kota)::int
            FROM jumlah_penduduk_kabkot p WHERE p.kabupaten_kota IN (SELECT wadmkk FROM spklu_cities);
        """)
        row = cur.fetchone()
        served_pop, covered_cities = int(row[0]) if row[0] else 0, int(row[1]) if row[1] else 0

        cur.execute("SELECT SUM(jumlah_penduduk::numeric)::int, COUNT(*)::int FROM jumlah_penduduk_kabkot;")
        row = cur.fetchone()
        total_pop, total_cities = int(row[0]) if row[0] else 1, int(row[1]) if row[1] else 1

        cur.execute("""
            SELECT p.kabupaten_kota, p.provinsi, p.jumlah_penduduk,
                   COALESCE(ST_X(ST_Centroid(p.geom)), 0), COALESCE(ST_Y(ST_Centroid(p.geom)), 0)
            FROM jumlah_penduduk_kabkot p
            LEFT JOIN sebaran_spklu s ON s.wadmkk = p.kabupaten_kota
            WHERE p.jumlah_penduduk > 100000
            GROUP BY p.kabupaten_kota, p.provinsi, p.jumlah_penduduk, p.geom
            HAVING COUNT(s.gid) = 0
            ORDER BY p.jumlah_penduduk DESC LIMIT 20;
        """)
        priority_cities = [{"city": r[0], "province": r[1], "population": int(r[2]), "lon": float(r[3]) if r[3] else 0, "lat": float(r[4]) if r[4] else 0} for r in cur.fetchall()]

        cur.execute("""
            SELECT COALESCE(s.wadmpr, p.provinsi) AS province, COUNT(s.gid) AS spklu_count,
                   SUM(p.jumlah_penduduk) AS population,
                   ROUND((COUNT(s.gid)::numeric / NULLIF(SUM(p.jumlah_penduduk)::numeric, 0)) * 100000, 2) AS ratio
            FROM jumlah_penduduk_kabkot p
            LEFT JOIN sebaran_spklu s ON s.wadmkk = p.kabupaten_kota
            GROUP BY province ORDER BY ratio DESC;
        """)
        prov_stats = [{"province": r[0], "spklu": r[1], "population": int(r[2]) if r[2] else 0, "ratio": float(r[3])} for r in cur.fetchall() if r[0] is not None]

        counts = sorted([p["spklu"] for p in prov_stats])
        n = len(counts)
        if n > 0 and sum(counts) > 0:
            cum = sum((i + 1) * c for i, c in enumerate(counts))
            gini = round((2 * cum) / (n * sum(counts)) - (n + 1) / n, 4)
        else:
            gini = 0

        dki = next((p for p in prov_stats if p["province"] and "DKI" in p["province"]), prov_stats[0] if prov_stats else None)
        dki_ratio = dki["ratio"] if dki else 0
        projections = [{"province": p["province"], "current": p["spklu"], "needed": max(0, int(round(dki_ratio * p["population"] / 100000, 0)) - p["spklu"]), "gap_pct": round(max(0, int(round(dki_ratio * p["population"] / 100000, 0)) - p["spklu"]) / max(p["spklu"], 1) * 100, 0) if p["spklu"] > 0 else 999} for p in prov_stats]

        return jsonify({
            "coverage": {"served_population": served_pop, "total_population": total_pop, "coverage_pct": round(served_pop / total_pop * 100, 2), "cities_with_spklu": covered_cities, "total_cities": total_cities},
            "priority_cities": priority_cities, "provinces": prov_stats, "gini": gini, "projections": projections
        })
    except Exception as e:
        log.error("comprehensive_analytics(): %s", e)
        return err_response("Gagal memuat analitik komprehensif", 500, str(e))
    finally:
        if conn: conn.close()

@app.route('/health', methods=['GET'])
@limiter.exempt
def health_check():
    results = {"status": "healthy", "timestamp": time.time()}
    all_ok = True
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.fetchone()
        cur.execute("SELECT count(*) FROM sebaran_spklu;")
        spklu_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM jalan_nasional;")
        road_count = cur.fetchone()[0]
        conn.close()
        results["database"] = "connected"
        results["spklu_count"] = spklu_count
        results["road_count"] = road_count
    except Exception as e:
        results["database"] = f"error: {e}"
        results["status"] = "unhealthy"
        all_ok = False
    results["cache_entries"] = len(_cache)
    return jsonify(results), 200 if all_ok else 500

@app.route('/api/analytics/suggest-spklu', methods=['GET'])
@limiter.limit("15 per minute")
@cached(600)
def suggest_spklu():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            WITH road_dist AS (
                SELECT j.gid, j.link_name, j.kelas_jalan, j.panjang, j.geom,
                       ROUND((SELECT ST_DistanceSphere(j.geom, s.geom) / 1000
                           FROM sebaran_spklu s ORDER BY j.geom <-> s.geom LIMIT 1)::numeric, 2) AS nearest_km
                FROM jalan_nasional j WHERE j.geom IS NOT NULL
            ), far_roads AS (SELECT * FROM road_dist WHERE nearest_km > 10)
            SELECT jsonb_build_object('type', 'FeatureCollection', 'features', COALESCE(jsonb_agg(feature), '[]'::jsonb))
            FROM (
                SELECT jsonb_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(ST_LineInterpolatePoint(geom, 0.5))::jsonb,
                    'properties', jsonb_build_object(
                        'gid', gid, 'road', link_name, 'jarak_km', nearest_km,
                        'prioritas', CASE WHEN nearest_km > 50 THEN 'Kritis' WHEN nearest_km > 20 THEN 'Tinggi' ELSE 'Sedang' END
                    )
                ) AS feature
                FROM far_roads ORDER BY nearest_km DESC
            ) features;
        """)
        return jsonify(cur.fetchone()[0])
    except Exception as e:
        log.error("suggest_spklu(): %s", e)
        return err_response("Gagal memuat saran SPKLU", 500, str(e))
    finally:
        if conn: conn.close()

@app.route('/api/cache/clear', methods=['POST'])
def clear_cache_endpoint():
    clear_cache()
    log.info("Cache cleared manually")
    return jsonify({"message": "Cache cleared"})

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify(error="ratelimit_exceeded", message="Terlalu banyak permintaan, silakan coba lagi nanti.", limit=str(e.description)), 429

@app.errorhandler(404)
def not_found(e):
    return err_response("Endpoint tidak ditemukan", 404)

@app.errorhandler(500)
def server_error(e):
    return err_response("Terjadi kesalahan server", 500)

if __name__ == '__main__':
    port = 8500
    while port < 8600:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(('127.0.0.1', port))
            s.close()
            break
        except socket.error:
            port += 1
    log.info("Server WebGIS SPKLU aktif di http://localhost:%s", port)
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
