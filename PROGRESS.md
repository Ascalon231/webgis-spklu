# 📋 PROGRESS.md — Cas.in WebGIS SPKLU

> **Project:** Cas.in – Charging Indonesia  
> **Deskripsi:** WebGIS sebaran Stasiun Pengisian Kendaraan Listrik Umum (SPKLU) di Indonesia  
> **Stack:** Python Flask + PostGIS (Supabase) + Leaflet.js  
> **Terakhir diupdate:** 25 Juni 2026

---

## 📁 Struktur Project

```
webgis-spklu/
├── app.py                      ← Backend Flask (259 baris)
├── .env                        ← Konfigurasi koneksi DB (jangan di-push ke git!)
├── requirements.txt            ← Dependensi Python
├── PROGRESS.md                 ← File ini
├── geoserver_capabilities.xml  ← Referensi capabilities GeoServer (tidak dipakai aktif)
├── templates/
│   ├── index.html              ← Halaman peta utama (3.562 baris)
│   └── analytics.html          ← Halaman analitik (974 baris)
├── static/
│   └── logo/
│       └── LogoCasin.png       ← Logo aplikasi
└── .venv/                      ← Virtual environment Python
```

---

## ✅ SUDAH SELESAI

### 🗄️ Backend — `app.py`

| Endpoint | Fungsi | Status |
|----------|--------|--------|
| `GET /` | Serve halaman peta utama | ✅ |
| `GET /analytics` | Serve halaman analitik | ✅ |
| `GET /api/spklu` | Data semua SPKLU sebagai GeoJSON dari PostGIS | ✅ |
| `GET /api/spklu/list` | List SPKLU sorted by provinsi > kota > nama | ✅ |
| `GET /api/routes/road` | Jalan nasional dengan dynamic simplification (zoom + bbox) | ✅ |
| `GET /api/stats` | Statistik total, per-provinsi, per-kota, density per 100k penduduk | ✅ |

**Fitur backend lainnya:**
- Auto-select port 8500–8600 jika port bentrok
- Koneksi ke **Supabase** (PostgreSQL + PostGIS cloud)
- Dynamic bbox filter untuk query jalan (hemat bandwidth)
- Dynamic simplification tolerance berdasarkan zoom level

---

### 🗺️ Frontend — `templates/index.html`

**Peta & Layer:**
- ✅ Leaflet.js interactive map (basemap CARTO Dark)
- ✅ Toggle layer SPKLU on/off
- ✅ Toggle layer Jalan Nasional on/off
- ✅ MarkerCluster (pengelompokan marker otomatis)
- ✅ Legend peta (warna marker by aksesibilitas rute)
- ✅ Glassmorphism UI (backdrop-filter blur)

**Sidebar & Filter:**
- ✅ Collapsible sidebar (bisa disembunyikan/tampilkan)
- ✅ Logo "Cas.in – Charging Indonesia"
- ✅ Search bar real-time (filter nama SPKLU)
- ✅ Filter Provinsi (dropdown, auto-populated dari data)
- ✅ Filter Provider/Operator (dropdown)
- ✅ Sidebar tabs (panel lokasi | panel rute | panel analitik)
- ✅ List SPKLU scrollable di sidebar dengan highlight saat popup dibuka

**Popup Marker:**
- ✅ Info nama, alamat, kota, provinsi, koordinat
- ✅ Tombol "Mulai Rute" dan "Ke Sini" di setiap popup

**Routing & Navigasi:**
- ✅ Routing engine (Leaflet Routing Machine via OSRM)
- ✅ Pick start/end point lewat klik peta
- ✅ GPS tracking — deteksi lokasi user (geolocation API)
- ✅ Hitung rute & clear rute
- ✅ Filter SPKLU di sepanjang rute (radius 5 km dari jalur)
- ✅ Timeline bottom sheet — daftar SPKLU terurut di rute
- ✅ Haversine distance & distance-from-route calculation

**Road Network:**
- ✅ Dual-source: Overpass API (OSM) untuk rute pendek,
  PostGIS lokal untuk rute jauh/nasional
- ✅ Fallback otomatis ke PostGIS jika Overpass gagal

**Analitik (Chart.js):**
- ✅ Chart statistik per provinsi & per kota
- ✅ Density SPKLU per 100.000 penduduk
- ✅ Halaman `analytics.html` terpisah

---

## 🔧 BUG YANG SUDAH DIPERBAIKI

### [FIX-01] ✅ Connection Leak di `/api/stats`
- **Masalah:** `get_stats()` tidak ada `finally: conn.close()` → koneksi DB tidak pernah ditutup
- **Dampak:** Koneksi menumpuk, bisa crash saat ramai
- **Fix:** Tambah `finally` block di `app.py`
- **Tanggal:** 25 Juni 2026

### [FIX-02] ✅ Popup Crash untuk Nama dengan Tanda Kutip
- **Masalah:** 26 SPKLU punya nama berisi `'` (McDonald's, L'Avenue, JAMI' ASSA'ADAH, dll)
  → string diinjeksikan langsung ke `onclick="..."` → syntax error JavaScript
- **Dampak:** Klik popup di 26 SPKLU tersebut crash
- **Fix:** Ganti `safeName.replace()` dengan **global `spkluNameStore` object** (lookup by GID)
- **Tanggal:** 25 Juni 2026

---

## ❌ MASALAH BELUM DIPERBAIKI

### 🔴 BLOCKING — Wajib Fix Sebelum Deploy

#### [ISSUE-01] `debug=True` Masih Aktif
- **Fix:** Diubah menjadi dinamis membaca dari `FLASK_DEBUG` di `.env` (default `False` di prod).
- **Status:** ✅ Selesai

#### [ISSUE-02] Tidak Ada WSGI Server (Gunicorn)
- **Fix:** `gunicorn` telah ditambahkan ke `requirements.txt`.
- **Status:** ✅ Selesai

#### [ISSUE-03] Tidak Ada `.gitignore`
- **Fix:** File `.gitignore` telah dibuat untuk mengecualikan `.env`, `.venv`, cache, dan log.
- **Status:** ✅ Selesai

---

### 🟡 NON-BLOCKING — Disarankan Diperbaiki

#### [ISSUE-04] CDN Tanpa SRI Hash (3 Library)
- MarkerCluster, Chart.js, Leaflet Routing Machine tidak punya `integrity=` hash
- Potensi risiko supply-chain attack jika CDN dikompromikan

#### [ISSUE-05] Routing Server Publik (Rate-Limited)
- Memakai `routing.openstreetmap.de` — server publik dengan rate limit
- Bisa lambat/down sewaktu-waktu tanpa pemberitahuan

#### [ISSUE-06] Tidak Ada Health Check & Custom Error Page
- **Fix:** Menambahkan endpoint `/health` untuk pengecekan DB dan handler JSON untuk error status code.
- **Status:** ✅ Selesai

#### [ISSUE-07] Tidak Ada Rate Limiting di API
- **Fix:** Menambahkan `Flask-Limiter` untuk membatasi requests sebesar 10 request/menit pada `/api/spklu` guna mencegah abuse database.
- **Status:** ✅ Selesai

---

## 🔍 MASALAH DATA (Akurasi SPKLU)

> Koordinat GPS semua akurat (presisi 15 digit, 0 outlier). Masalah ada di metadata/atribut.

| Kode | Masalah | Jumlah | Status |
|------|---------|--------|--------|
| DATA-01 | Duplikat koordinat (multiple unit charger di titik persis sama) | — | ✅ Selesai (Deterministic spatial jittering diterapkan di backend) |
| DATA-02 | Encoding nama rusak (`Café` → `Caf?`, dll) | — | ✅ Selesai (Diperbaiki 10 nama SPKLU di database) |
| DATA-03 | Format nama kota berbeda (`KAB. TANGERANG` vs `Tangerang`) | — | ✅ Selesai (Migrasi & hapus kota_kabupaten ke wadmkk) |
| DATA-04 | Label `BALI` tapi koordinat di Jakarta (kemungkinan salah input) | — | ✅ Selesai (Migrasi & hapus provinsi ke wadmpr) |
| DATA-05 | Data stale — snapshot tunggal 16 Desember 2025 | Semua 4.515 | ⏳ Perlu update |

**GID terdampak encoding rusak:** 1476, 1477, 1506, 1763, 1792, 1793, 1794, 1838, 1839, 2295 (Telah diperbaiki)

---

## 📊 Status Database (Supabase)

| Tabel | Rows | Dipakai? |
|-------|------|----------|
| `sebaran_spklu` | 4.515 | ✅ Ya — data utama |
| `jalan_nasional` | 3.306 | ✅ Ya — layer jalan |
| `jumlah_penduduk_kabkot` | 514 | ✅ Ya — density analysis |
| `jalur_feri_osm` | — | ❌ Belum dipakai |
| `rel_kereta_api` | — | ❌ Belum dipakai |
| `rute_penerbangan_udara` | — | ❌ Belum dipakai |
| `sebaran_bandara` | — | ❌ Belum dipakai |
| `sebaran_pelabuhan_laut` | — | ❌ Belum dipakai |
| `sebaran_terminal_tipe_a` | — | ❌ Belum dipakai |
| `stasiun_kereta_api` | — | ❌ Belum dipakai |

---

## 🚀 Langkah Selanjutnya (To-Do List)

### Prioritas Tinggi — Sebelum Deploy
- [x] Fix `debug=True` → `debug=False` di `app.py` baris 259
- [x] Tambah `gunicorn` ke `requirements.txt`
- [x] Buat file `.gitignore`

### Prioritas Sedang — Data
- [x] Fix encoding 10 nama rusak di database (UPDATE SQL)
- [x] Cek manual 8 titik BALI yang koordinatnya di Jakarta (Telah diperbaiki via migrasi wadmpr/wadmkk)
- [x] Tambah jitter koordinat untuk duplikat agar tidak tumpuk di peta

### Prioritas Rendah — Fitur Tambahan
- [ ] Tambah info jumlah unit per lokasi di popup marker
- [x] Tambah `/health` endpoint
- [x] Tambah rate limiting (`flask-limiter`)
- [ ] Pertimbangkan self-host OSRM routing server
- [x] Buat dokumentasi database `schema.sql`
- [x] Buat dokumentasi `README.md` representatif di root folder

---

## 💻 Cara Menjalankan

### Development (sekarang)
```bash
cd /home/ripan231/Projects/webgis-spklu
source .venv/bin/activate
python app.py
# → Buka http://localhost:8500
```

### Production (setelah fix ISSUE-01 s/d 03)
```bash
cd /home/ripan231/Projects/webgis-spklu
source .venv/bin/activate
pip install gunicorn
FLASK_DEBUG=false gunicorn -w 4 -b 0.0.0.0:8500 app:app
```
