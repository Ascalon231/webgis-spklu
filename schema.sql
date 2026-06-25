-- =====================================================================
-- 🌐 SCHEMA DATABASE SPASIAL & KUERI POSTGIS
-- Proyek: Cas.in – WebGIS Sebaran SPKLU Indonesia
-- Pengembang: Ripan Nursalam (GitHub: @Ascalon231 / LinkedIn: @ripan-nursalam)
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Defenisi Struktur Tabel sebaran_spklu
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sebaran_spklu (
    gid SERIAL PRIMARY KEY,
    nama_spklu VARCHAR(255),
    alamat TEXT,
    operator VARCHAR(100),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    wadmkk VARCHAR(100), -- Kabupaten/Kota (Terisi otomatis secara spasial)
    wadmpr VARCHAR(100), -- Provinsi (Terisi otomatis secara spasial)
    geom GEOMETRY(Point, 4326) -- Geometri titik koordinat SPKLU
);

-- ---------------------------------------------------------------------
-- 2. Pembuatan Indeks Spasial (GIST) untuk Optimasi Kecepatan Query
-- ---------------------------------------------------------------------
-- Indeks GIST sangat krusial untuk mempercepat kueri spasial (seperti ST_Contains, ST_Distance, ST_DWithin)
CREATE INDEX IF NOT EXISTS idx_sebaran_spklu_geom ON sebaran_spklu USING gist(geom);

-- ---------------------------------------------------------------------
-- 3. Pembersihan & Standardisasi Wilayah Spasial (Spatial Point-in-Polygon Join)
-- ---------------------------------------------------------------------
-- Melakukan intersect spasial titik SPKLU terhadap poligon kabupaten/kota dari batas BPS
-- untuk mengatasi error input manual (salah tulis provinsi, typo, dll.)
UPDATE sebaran_spklu s
SET 
    wadmkk = p.wadmkk,
    wadmpr = p.wadmpr
FROM jumlah_penduduk_kabkot p
WHERE ST_Contains(p.geom, s.geom) 
  OR ST_Intersects(p.geom, s.geom);

-- ---------------------------------------------------------------------
-- 4. Kueri Deterministik Jittering Spasial (SQL Window Function)
-- ---------------------------------------------------------------------
-- Kueri ini digunakan di backend Flask (/api/spklu) untuk mendeteksi koordinat 
-- yang bertumpuk persis (stasiun yang sama dengan charger berbeda) dan menggesernya 
-- secara melingkar (spiral) menggunakan fungsi trigonometri PostgreSQL.
WITH ranked_spklu AS (
    SELECT 
        gid, 
        nama_spklu, 
        operator, 
        alamat, 
        latitude, 
        longitude, 
        wadmkk, 
        wadmpr,
        geom,
        ROW_NUMBER() OVER (PARTITION BY latitude, longitude ORDER BY gid) AS rank
    FROM sebaran_spklu
)
SELECT 
    gid, 
    nama_spklu, 
    operator, 
    alamat,
    wadmkk AS city, 
    wadmpr AS province,
    latitude AS orig_lat,
    longitude AS orig_lng,
    rank,
    -- Jika rank = 1, gunakan geometri asli. 
    -- Jika rank > 1, geser geometri melingkar dengan radius 0.00008 derajat (~8.8 meter)
    ST_AsGeoJSON(
        CASE 
            WHEN rank = 1 THEN geom 
            ELSE ST_SetSRID(
                ST_MakePoint(
                    longitude + 0.00008 * cos((rank - 1) * 1.04719755), 
                    latitude + 0.00008 * sin((rank - 1) * 1.04719755)
                ), 
                4326
            ) 
        END
    )::json AS geometry
FROM ranked_spklu;

-- ---------------------------------------------------------------------
-- 5. Kueri Buffer Spasial untuk Pencarian Koridor Rute Jalan
-- ---------------------------------------------------------------------
-- Mengambil SPKLU yang berada di sepanjang rute perjalanan dalam koridor buffer 5 km.
-- Query ini dipanggil di backend saat menerima data linestring rute dari OSRM.
SELECT 
    gid, 
    nama_spklu, 
    operator, 
    alamat, 
    latitude, 
    longitude
FROM sebaran_spklu
WHERE ST_DWithin(
    geom::geography, 
    ST_GeomFromText('LINESTRING(106.8227 -6.1751, 107.6191 -6.9175)', 4326)::geography, 
    5000 -- Jarak radius penyangga (5.000 meter / 5 km)
);
