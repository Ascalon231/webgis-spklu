import os
import pg8000.dbapi as pg
from flask import Flask, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Initialize Limiter
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri="memory://"
)

# Database configuration from .env
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

def get_db_connection():
    conn = pg.connect(
        host=DB_HOST,
        port=int(DB_PORT),
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    return conn

@app.route('/')
def index():
    try:
        with open(os.path.join(app.root_path, 'templates', 'index.html'), 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error loading index.html: {str(e)}", 500

@app.route('/api/spklu', methods=['GET'])
@limiter.limit("10 per minute")
def get_spklu():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Advanced query with coordinate jittering for duplicate coordinate markers
        query = """
        WITH ranked_spklu AS (
            SELECT 
                gid,
                nama_spklu,
                alamat,
                wadmkk,
                wadmpr,
                latitude,
                longitude,
                geom,
                ROW_NUMBER() OVER (PARTITION BY latitude, longitude ORDER BY gid) AS rank
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
                    CASE 
                        WHEN rank = 1 THEN geom 
                        ELSE ST_SetSRID(ST_MakePoint(
                            longitude + 0.00008 * cos((rank - 1) * 1.04719755), 
                            latitude + 0.00008 * sin((rank - 1) * 1.04719755)
                        ), 4326) 
                    END
                )::jsonb,
                'properties', jsonb_build_object(
                    'gid', gid,
                    'name', nama_spklu,
                    'address', alamat,
                    'city', wadmkk,
                    'province', wadmpr,
                    'lat', latitude + CASE WHEN rank = 1 THEN 0 ELSE 0.00008 * sin((rank - 1) * 1.04719755) END,
                    'lon', longitude + CASE WHEN rank = 1 THEN 0 ELSE 0.00008 * cos((rank - 1) * 1.04719755) END
                )
            ) AS feature
            FROM ranked_spklu
        ) features;
        """
        cur.execute(query)
        geojson = cur.fetchone()[0]
        return jsonify(geojson)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/routes/road', methods=['GET'])
@limiter.limit("30 per minute")
def get_road_routes():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get zoom level (default 5)
        zoom_str = request.args.get('zoom', '5')
        try:
            zoom = int(float(zoom_str))
        except ValueError:
            zoom = 5
            
        # Determine PostGIS simplification tolerance based on zoom level
        if zoom < 7:
            tolerance = 0.0025  # High simplification for far view (faster rendering)
        elif 7 <= zoom < 10:
            tolerance = 0.001   # Medium simplification for regional view
        elif 10 <= zoom < 13:
            tolerance = 0.0003  # Low simplification for local view
        else:
            tolerance = 0.00005 # Minimal simplification for close-up view

        # Apply Bounding Box (bbox) filter if zoom >= 8 to optimize database query size
        bbox_str = request.args.get('bbox', None)
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
        
        # Query simplified national road lines as GeoJSON
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
                    'gid', gid,
                    'name', COALESCE(link_name, 'Jalan Nasional'),
                    'class', kelas_jalan,
                    'function', fungsi_jalan,
                    'status', status_jalan,
                    'length', panjang
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
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/stats', methods=['GET'])
@limiter.limit("30 per minute")
def get_stats():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get total SPKLU count
        cur.execute("SELECT count(*) FROM sebaran_spklu;")
        total_count = cur.fetchone()[0]
        
        # Get count per province
        cur.execute("""
            SELECT COALESCE(wadmpr, 'Tidak Diketahui') as prov, count(*) as count 
            FROM sebaran_spklu 
            GROUP BY wadmpr 
            ORDER BY count DESC;
        """)
        prov_stats = [{"province": row[0], "count": row[1]} for row in cur.fetchall()]
        
        # Get count per city (all cities with SPKLU)
        cur.execute("""
            SELECT COALESCE(wadmkk, 'Tidak Diketahui') as city, count(*) as count 
            FROM sebaran_spklu 
            GROUP BY wadmkk 
            ORDER BY count DESC;
        """)
        city_stats = [{"city": row[0], "count": row[1]} for row in cur.fetchall()]
        
        # Get density of SPKLU per 100k population (all cities/regencies, sorted by count & density)
        cur.execute("""
            SELECT 
                p.kabupaten_kota AS city, 
                p.jumlah_penduduk AS population, 
                count(s.gid) AS spklu_count,
                ROUND((count(s.gid)::numeric / p.jumlah_penduduk::numeric) * 100000, 2) AS spklu_per_100k
            FROM jumlah_penduduk_kabkot p
            LEFT JOIN sebaran_spklu s ON s.wadmkk = p.kabupaten_kota
            WHERE p.jumlah_penduduk > 0
            GROUP BY p.kabupaten_kota, p.jumlah_penduduk
            ORDER BY spklu_count DESC, spklu_per_100k DESC;
        """)
        density_stats = [
            {
                "city": row[0],
                "population": int(row[1]) if row[1] is not None else 0,
                "count": row[2],
                "ratio": float(row[3])
            } for row in cur.fetchall()
        ]
        
        return jsonify({
            "total": total_count,
            "provinces": prov_stats,
            "cities": city_stats,
            "densities": density_stats
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/analytics')
def analytics():
    try:
        with open(os.path.join(app.root_path, 'templates', 'analytics.html'), 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error loading analytics.html: {str(e)}", 500

@app.route('/api/spklu/list', methods=['GET'])
@limiter.limit("30 per minute")
def get_spklu_list():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT gid, nama_spklu, alamat, wadmkk, wadmpr, latitude, longitude 
            FROM sebaran_spklu 
            ORDER BY wadmpr, wadmkk, nama_spklu;
        """)
        rows = cur.fetchall()
        spklu_list = [
            {
                "gid": row[0],
                "name": row[1],
                "address": row[2],
                "city": row[3],
                "province": row[4],
                "lat": float(row[5]) if row[5] is not None else 0,
                "lon": float(row[6]) if row[6] is not None else 0
            } for row in rows
        ]
        return jsonify(spklu_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/health', methods=['GET'])
@limiter.exempt
def health_check():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.fetchone()
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "database": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify(error="ratelimit_exceeded", message="Terlalu banyak permintaan API. Silakan coba lagi nanti.", limit=str(e.description)), 429

if __name__ == '__main__':
    import socket
    port = 8500
    while port < 8600:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(('127.0.0.1', port))
            s.close()
            break
        except socket.error:
            port += 1
            
    print(f"🚀 Server WebGIS SPKLU aktif di http://localhost:{port}")
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
