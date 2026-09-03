# ============================================
# 1. IMPORTS FIRST
# ============================================
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from dotenv import load_dotenv      
import psycopg2
from psycopg2.extras import RealDictCursor
import bcrypt
import os
import jwt
import json
import random
import math
import secrets
from datetime import datetime, timedelta, timezone, date
from functools import wraps
import string
from services.notification_services import NotificationService #For sending Email alerts#
from services.chat_service import chat_service

# Load environment variables
load_dotenv()      

# Initialize Flask app
app = Flask(__name__)
CORS(app, origins=["*"])

# Initialize Notification Service
notification_service = NotificationService()

JWT_SECRET = os.getenv('JWT_SECRET', 'agriguard-secret-key-2024')
FRONTEND_BASE_URL = os.getenv(
    'FRONTEND_BASE_URL', 'http://127.0.0.1:5500/Frontend'
).rstrip('/')

# ============================================
# 2. DATABASE CONNECTION
# ============================================
def get_db():
    try:
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=os.getenv('DB_PORT', '5432'),
            database=os.getenv('DB_NAME', 'agriguard_db'),
            user=os.getenv('DB_USER', 'agriguard_user'),
            password=os.getenv('DB_PASSWORD', 'AgriGuard2024!')
        )
        return conn
    except Exception as e:
        print(f'Database connection error: {e}')
        return None


def ensure_animals_date_of_birth(conn):
    """
    Confirm animals.date_of_birth exists.
    Do not invent DOB — age stays blank ('-') until the farmer enters a real date.
    Also undo the old demo backfill that invented DOBs for NULL rows (same formula).
    """
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'animals' AND column_name = 'date_of_birth'
            LIMIT 1
        """)
        if not cur.fetchone():
            print('ensure_animals_date_of_birth: column missing (skipping; needs DB owner)')
            cur.close()
            return
        # Clear DOBs that match the previous auto-fill formula (not real farmer input)
        cur.execute("""
            UPDATE animals
            SET date_of_birth = NULL
            WHERE date_of_birth IS NOT NULL
              AND date_of_birth = (
                    (CURRENT_DATE
                     - (MOD(COALESCE(animal_id, 1), 5) + 1) * INTERVAL '1 year')
                    - (MOD(COALESCE(animal_id, 1), 8) * INTERVAL '1 month')
                  )::date
        """)
        conn.commit()
        cur.close()
    except Exception as e:
        conn.rollback()
        print(f'ensure_animals_date_of_birth: {e}')


def ensure_vaccinations_schema(conn):
    """Best-effort add missing vaccinations columns (ignored if not table owner)."""
    try:
        cur = conn.cursor()
        needed = {
            'notes': 'TEXT',
            'manufacturer': 'VARCHAR(120)',
            'batch_number': 'VARCHAR(80)',
            'vet_name': 'VARCHAR(120)',
            'dosage_ml': 'NUMERIC(10,2)',
            'completed_date': 'DATE',
            'is_completed': 'BOOLEAN DEFAULT FALSE',
            'due_date': 'DATE',
            'vaccination_date': 'DATE',
        }
        for col, coltype in needed.items():
            cur.execute("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'vaccinations'
                  AND column_name = %s
                LIMIT 1
            """, (col,))
            if cur.fetchone():
                continue
            try:
                cur.execute(f'ALTER TABLE vaccinations ADD COLUMN {col} {coltype}')
                conn.commit()
                print(f'ensure_vaccinations_schema: added vaccinations.{col}')
            except Exception as e:
                conn.rollback()
                print(f'ensure_vaccinations_schema: skip {col}: {e}')
        cur.close()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f'ensure_vaccinations_schema: {e}')


def vaccinations_has_column(conn, column_name):
    cur = conn.cursor()
    cur.execute("""
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'vaccinations'
          AND column_name = %s
        LIMIT 1
    """, (column_name,))
    ok = bool(cur.fetchone())
    cur.close()
    return ok

# ============================================
# 3. HELPER FUNCTIONS
# ============================================
def make_token(user_id, role):
    payload = {
        'user_id': user_id,
        'role': role,
        'exp': datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


def make_email_verify_token(user_id, email, hours=24):
    """Signed one-time-style token for email verification (no extra DB columns)."""
    payload = {
        'user_id': user_id,
        'email': email,
        'purpose': 'email_verify',
        'exp': datetime.utcnow() + timedelta(hours=hours),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


def decode_email_verify_token(token):
    payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    if payload.get('purpose') != 'email_verify':
        raise jwt.InvalidTokenError('Wrong token purpose')
    return payload


def calculate_distance(lat1, lon1, lat2, lon2):
    """Haversine formula — returns distance in metres."""
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return 0
    R = 6371000
    phi1     = math.radians(lat1)
    phi2     = math.radians(lat2)
    dphi     = math.radians(lat2 - lat1)
    dlambda  = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# Polygon storage capability (detected once — avoids aborting DB transactions)
_GEOFENCE_HAS_POLYGON_COL = None   # True/False/None(unknown)
_GEOFENCE_HAS_POLYGON_TBL = None


def _conn_rollback(conn_or_cursor):
    """Rollback safely after a failed statement so later commands can run."""
    try:
        conn = getattr(conn_or_cursor, 'connection', None) or conn_or_cursor
        conn.rollback()
    except Exception:
        pass


def ensure_geofences_schema(conn):
    """
    Prefer geofences.polygon_json for polygon corners.
    Do not CREATE geofence_polygons — app DB user usually cannot create tables.
    Local JSON file remains a fallback only if polygon_json is missing.
    """
    global _GEOFENCE_HAS_POLYGON_COL, _GEOFENCE_HAS_POLYGON_TBL
    try:
        cur = conn.cursor()
        if _GEOFENCE_HAS_POLYGON_COL is None:
            cur.execute("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'geofences'
                  AND column_name = 'polygon_json'
                LIMIT 1
            """)
            if cur.fetchone():
                _GEOFENCE_HAS_POLYGON_COL = True
                print('ensure_geofences_schema: using geofences.polygon_json')
            else:
                try:
                    cur.execute('ALTER TABLE geofences ADD COLUMN polygon_json TEXT')
                    conn.commit()
                    _GEOFENCE_HAS_POLYGON_COL = True
                    print('ensure_geofences_schema: added geofences.polygon_json')
                except Exception as e:
                    _conn_rollback(conn)
                    _GEOFENCE_HAS_POLYGON_COL = False
                    print(f'ensure_geofences_schema: skip geofences.polygon_json: {e}')
                    print(
                        '→ To store polygons IN geofences, run this in pgAdmin as postgres:\n'
                        '   ALTER TABLE geofences ADD COLUMN IF NOT EXISTS polygon_json TEXT;'
                    )

        # Side table not needed when polygon_json exists; never auto-CREATE it
        if _GEOFENCE_HAS_POLYGON_COL:
            _GEOFENCE_HAS_POLYGON_TBL = False
        elif _GEOFENCE_HAS_POLYGON_TBL is None:
            cur.execute("""
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'geofence_polygons'
                LIMIT 1
            """)
            _GEOFENCE_HAS_POLYGON_TBL = cur.fetchone() is not None
        cur.close()
    except Exception as e:
        _conn_rollback(conn)
        print(f'ensure_geofences_schema: {e}')

    try:
        os.makedirs(_geofence_polygon_dir(), exist_ok=True)
    except Exception as e:
        print(f'ensure_geofences_schema local dir: {e}')


def _geofence_polygon_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'geofence_polygons')


def _geofence_polygon_path(user_id, geofence_id=None):
    name = f'user_{int(user_id)}.json'
    return os.path.join(_geofence_polygon_dir(), name)


def geofences_has_polygon_column(conn):
    global _GEOFENCE_HAS_POLYGON_COL
    if _GEOFENCE_HAS_POLYGON_COL is not None:
        return _GEOFENCE_HAS_POLYGON_COL
    ensure_geofences_schema(conn)
    return bool(_GEOFENCE_HAS_POLYGON_COL)


def _load_polygon_file(user_id):
    """Return polygon JSON string/list from local file, or None."""
    path = _geofence_polygon_path(user_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            raw = data.get('polygon_json') or data.get('polygon')
            if raw is None:
                return None
            if isinstance(raw, str):
                return raw
            return json.dumps(raw)
        if isinstance(data, list):
            return json.dumps(data)
        return None
    except Exception as e:
        print(f'_load_polygon_file: {e}')
        return None


def _save_polygon_file(user_id, geofence_id, polygon_json):
    os.makedirs(_geofence_polygon_dir(), exist_ok=True)
    path = _geofence_polygon_path(user_id)
    if not polygon_json:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception as e:
            print(f'_save_polygon_file remove: {e}')
        return
    payload = {
        'user_id': int(user_id),
        'geofence_id': int(geofence_id) if geofence_id is not None else None,
        'polygon_json': polygon_json,
        'updated_at': datetime.now().isoformat(timespec='seconds'),
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f)


def save_geofence_polygon(cursor, geofence_id, user_id, polygon_json, has_column):
    """
    Persist polygon corners.
    Never issues a full connection rollback — that used to undo the geofences
    UPDATE/INSERT that just succeeded. Optional DB writes use SAVEPOINTs.
    Local JSON file is the reliable store when DB DDL is blocked.
    """
    global _GEOFENCE_HAS_POLYGON_COL, _GEOFENCE_HAS_POLYGON_TBL
    saved = False

    def _sp_run(label, fn):
        nonlocal saved
        try:
            cursor.execute(f"SAVEPOINT {label}")
            fn()
            cursor.execute(f"RELEASE SAVEPOINT {label}")
            saved = True
            return True
        except Exception as e:
            try:
                cursor.execute(f"ROLLBACK TO SAVEPOINT {label}")
            except Exception:
                pass
            print(f'save_geofence_polygon {label} skipped: {e}')
            return False

    if has_column and polygon_json is not None:
        ok = _sp_run('gf_poly_col', lambda: cursor.execute(
            "UPDATE geofences SET polygon_json=%s WHERE geofence_id=%s AND user_id=%s",
            (polygon_json, geofence_id, user_id)
        ))
        if not ok:
            _GEOFENCE_HAS_POLYGON_COL = False
    elif has_column and polygon_json is None:
        ok = _sp_run('gf_poly_col_clear', lambda: cursor.execute(
            "UPDATE geofences SET polygon_json=NULL WHERE geofence_id=%s AND user_id=%s",
            (geofence_id, user_id)
        ))
        if not ok:
            _GEOFENCE_HAS_POLYGON_COL = False

    if _GEOFENCE_HAS_POLYGON_TBL:
        def _side():
            if polygon_json:
                cursor.execute("""
                    INSERT INTO geofence_polygons (geofence_id, user_id, polygon_json, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (geofence_id) DO UPDATE
                      SET polygon_json = EXCLUDED.polygon_json,
                          user_id = EXCLUDED.user_id,
                          updated_at = NOW()
                """, (geofence_id, user_id, polygon_json))
            else:
                cursor.execute(
                    "DELETE FROM geofence_polygons WHERE geofence_id=%s AND user_id=%s",
                    (geofence_id, user_id)
                )
        if not _sp_run('gf_poly_tbl', _side):
            _GEOFENCE_HAS_POLYGON_TBL = False

    # Local file always works without DB privileges
    try:
        _save_polygon_file(user_id, geofence_id, polygon_json)
        saved = True
    except Exception as e:
        print(f'save_geofence_polygon file: {e}')

    return saved


def migrate_polygon_file_into_row(cursor, user_id, geofence_id):
    """If DB column is empty but local file has polygon, copy it into geofences."""
    global _GEOFENCE_HAS_POLYGON_COL
    if not _GEOFENCE_HAS_POLYGON_COL or not geofence_id:
        return False
    raw = _load_polygon_file(user_id)
    pts = normalize_polygon_points(raw)
    if not pts:
        return False
    polygon_json = json.dumps([{'lat': lat, 'lng': lon} for lat, lon in pts])
    try:
        cursor.execute("SAVEPOINT gf_poly_migrate")
        cursor.execute("""
            UPDATE geofences
            SET polygon_json = %s
            WHERE geofence_id = %s AND user_id = %s
              AND (polygon_json IS NULL OR polygon_json = '' OR polygon_json = 'null')
        """, (polygon_json, geofence_id, user_id))
        cursor.execute("RELEASE SAVEPOINT gf_poly_migrate")
        print(f'[geofence] migrated polygon file → geofences.polygon_json id={geofence_id}')
        return True
    except Exception as e:
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT gf_poly_migrate")
        except Exception:
            pass
        print(f'migrate_polygon_file_into_row: {e}')
        return False


def attach_geofence_polygon(cursor, gf, user_id=None):
    """Fill polygon_json on a geofence row from column, side table, or local file."""
    global _GEOFENCE_HAS_POLYGON_TBL
    if not gf:
        return gf
    row = dict(gf) if not isinstance(gf, dict) else gf

    if normalize_polygon_points(row.get('polygon_json')):
        return row

    gid = row.get('geofence_id')
    uid = user_id or row.get('user_id')

    if cursor is not None and gid is not None and _GEOFENCE_HAS_POLYGON_TBL:
        try:
            cursor.execute("SAVEPOINT gf_poly_attach")
            if uid is not None:
                cursor.execute("""
                    SELECT polygon_json FROM geofence_polygons
                    WHERE geofence_id=%s AND user_id=%s
                """, (gid, uid))
            else:
                cursor.execute("""
                    SELECT polygon_json FROM geofence_polygons
                    WHERE geofence_id=%s
                """, (gid,))
            side = cursor.fetchone()
            cursor.execute("RELEASE SAVEPOINT gf_poly_attach")
            if side:
                raw = side['polygon_json'] if isinstance(side, dict) else side[0]
                if normalize_polygon_points(raw):
                    row['polygon_json'] = raw
                    return row
        except Exception as e:
            try:
                cursor.execute("ROLLBACK TO SAVEPOINT gf_poly_attach")
            except Exception:
                _conn_rollback(cursor)
            _GEOFENCE_HAS_POLYGON_TBL = False
            print(f'attach_geofence_polygon side table: {e}')

    if uid is not None:
        raw = _load_polygon_file(uid)
        if normalize_polygon_points(raw):
            row['polygon_json'] = raw
            # Opportunistically copy file → geofences.polygon_json when column exists
            if cursor is not None and gid is not None and _GEOFENCE_HAS_POLYGON_COL:
                migrate_polygon_file_into_row(cursor, uid, gid)
    return row


def fetch_user_geofence(cursor, user_id):
    """Latest geofence for user, with polygon from column / side table / file."""
    global _GEOFENCE_HAS_POLYGON_COL
    if _GEOFENCE_HAS_POLYGON_COL:
        try:
            cursor.execute("""
                SELECT geofence_id, center_latitude, center_longitude, radius_meters,
                       polygon_json, fence_name, is_active, created_at
                FROM geofences WHERE user_id=%s
                ORDER BY created_at DESC LIMIT 1
            """, (user_id,))
            gf = cursor.fetchone()
        except Exception as e:
            _conn_rollback(cursor)
            _GEOFENCE_HAS_POLYGON_COL = False
            print(f'fetch_user_geofence polygon select fallback: {e}')
            cursor.execute("""
                SELECT geofence_id, center_latitude, center_longitude, radius_meters,
                       fence_name, is_active, created_at
                FROM geofences WHERE user_id=%s
                ORDER BY created_at DESC LIMIT 1
            """, (user_id,))
            gf = cursor.fetchone()
    else:
        cursor.execute("""
            SELECT geofence_id, center_latitude, center_longitude, radius_meters,
                   fence_name, is_active, created_at
            FROM geofences WHERE user_id=%s
            ORDER BY created_at DESC LIMIT 1
        """, (user_id,))
        gf = cursor.fetchone()

    if not gf:
        return None
    return attach_geofence_polygon(cursor, dict(gf), user_id)


def normalize_polygon_points(raw):
    """Accept [{lat,lng}|{latitude,longitude}|[lat,lng], ...] → [(lat, lon), ...]."""
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, (list, tuple)):
        return None
    pts = []
    for p in raw:
        lat = lon = None
        if isinstance(p, dict):
            lat = p.get('lat', p.get('latitude'))
            lon = p.get('lng', p.get('lon', p.get('longitude')))
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            lat, lon = p[0], p[1]
        try:
            lat_f, lon_f = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
            continue
        pts.append((lat_f, lon_f))
    if len(pts) < 3:
        return None
    # Drop duplicate closing vertex if client sent a closed ring
    if pts[0][0] == pts[-1][0] and pts[0][1] == pts[-1][1]:
        pts = pts[:-1]
    return pts if len(pts) >= 3 else None


def geofence_polygon(gf):
    """Polygon vertices for a geofence row, or None (circle-only legacy)."""
    if not gf:
        return None
    return normalize_polygon_points(gf.get('polygon_json') or gf.get('polygon'))


def point_in_polygon(lat, lon, polygon):
    """Ray casting. polygon = [(lat, lon), ...]."""
    if not polygon or len(polygon) < 3:
        return False
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        lat_i, lon_i = polygon[i]
        lat_j, lon_j = polygon[j]
        if ((lat_i > lat) != (lat_j > lat)) and (
            lon < (lon_j - lon_i) * (lat - lat_i) / ((lat_j - lat_i) or 1e-15) + lon_i
        ):
            inside = not inside
        j = i
    return inside


def polygon_centroid(polygon):
    lats = [p[0] for p in polygon]
    lons = [p[1] for p in polygon]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def polygon_bounding_radius(polygon, clat=None, clon=None):
    if not polygon:
        return 0.0
    if clat is None or clon is None:
        clat, clon = polygon_centroid(polygon)
    return max(calculate_distance(clat, clon, lat, lon) for lat, lon in polygon)


def is_inside_farm(lat, lon, gf, circle_margin=1.0):
    """True if point is inside farm fence (polygon preferred, else circle)."""
    if not gf:
        return True
    poly = geofence_polygon(gf)
    if poly:
        return point_in_polygon(lat, lon, poly)
    farm_lat = float(gf['center_latitude'])
    farm_lon = float(gf['center_longitude'])
    radius = float(gf['radius_meters'] or 0)
    return calculate_distance(lat, lon, farm_lat, farm_lon) <= radius * circle_margin


def farm_geometry(gf):
    """
    Normalised farm geometry for tracking logic.
    Returns dict: lat, lon, radius, polygon (or None), gf row.
    """
    default = {
        'lat': -23.8966, 'lon': 29.4488, 'radius': 2000.0,
        'polygon': None, 'gf': None
    }
    if not gf:
        return default
    poly = geofence_polygon(gf)
    try:
        lat = float(gf['center_latitude'])
        lon = float(gf['center_longitude'])
        radius = float(gf['radius_meters'] or 2000.0)
    except (TypeError, ValueError, KeyError):
        if poly:
            lat, lon = polygon_centroid(poly)
            radius = max(100.0, polygon_bounding_radius(poly, lat, lon))
        else:
            return default
    if poly and (not gf.get('center_latitude') or not gf.get('radius_meters')):
        lat, lon = polygon_centroid(poly)
        radius = max(100.0, polygon_bounding_radius(poly, lat, lon))
    return {'lat': lat, 'lon': lon, 'radius': radius, 'polygon': poly, 'gf': gf}


def random_point_in_geofence(gf, max_fraction=0.85, max_tries=50):
    """Random GPS point clearly inside the farm fence."""
    geo = farm_geometry(gf)
    poly = geo['polygon']
    if poly:
        lats = [p[0] for p in poly]
        lons = [p[1] for p in poly]
        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)
        # Shrink bbox slightly so samples sit away from edges
        pad_lat = (max_lat - min_lat) * (1 - max_fraction) * 0.5
        pad_lon = (max_lon - min_lon) * (1 - max_fraction) * 0.5
        for _ in range(max_tries):
            lat = random.uniform(min_lat + pad_lat, max_lat - pad_lat)
            lon = random.uniform(min_lon + pad_lon, max_lon - pad_lon)
            if point_in_polygon(lat, lon, poly):
                return lat, lon
        return polygon_centroid(poly)

    farm_lat, farm_lon, radius = geo['lat'], geo['lon'], geo['radius']
    ang = random.uniform(0, 2 * math.pi)
    d = random.uniform(0, radius * max_fraction)
    lat = farm_lat + (d / 111320) * math.cos(ang)
    lon = farm_lon + (d / (111320 * math.cos(math.radians(farm_lat)))) * math.sin(ang)
    return lat, lon


def random_point_outside_geofence(gf):
    """
    Place a point clearly outside the farm fence (geofence breach demo).
    Just beyond the boundary (90–400 m), not a far teleport from the centre.
    """
    geo = farm_geometry(gf)
    farm_lat, farm_lon, radius = geo['lat'], geo['lon'], geo['radius']
    poly = geo['polygon']
    step = random.uniform(90, 400)  # metres past the fence

    # Polygon: walk from a random edge midpoint outward
    if poly and len(poly) >= 3:
        for _ in range(40):
            i = random.randrange(len(poly))
            j = (i + 1) % len(poly)
            lat1, lon1 = poly[i]
            lat2, lon2 = poly[j]
            t = random.uniform(0.2, 0.8)
            elat = lat1 + t * (lat2 - lat1)
            elon = lon1 + t * (lon2 - lon1)
            ang = math.atan2(elon - farm_lon, elat - farm_lat)
            lat, lon = _offset_meters(elat, elon, step, ang)
            if not point_in_polygon(lat, lon, poly):
                return lat, lon
        ang = random.uniform(0, 2 * math.pi)
        return _offset_meters(farm_lat, farm_lon, radius + step, ang)

    # Circle fence: just beyond the radius
    ang = random.uniform(0, 2 * math.pi)
    return _offset_meters(farm_lat, farm_lon, radius + step, ang)


def zone_fits_in_farm(zlat, zlon, zrad, gf):
    """
    Zone centre must be inside fence; zone circle should not spill outside.
    Returns (ok: bool, message: str|None, max_radius_hint: int|None).
    """
    if not gf:
        return False, 'Draw a farm geofence first, then create zones inside it', None

    poly = geofence_polygon(gf)
    if poly:
        if not point_in_polygon(zlat, zlon, poly):
            return False, 'Zone centre must be inside the farm geofence', None
        # Sample circumference — if any sample leaves the polygon, zone is too big
        for i in range(16):
            ang = 2 * math.pi * i / 16
            tlat = zlat + (zrad / 111320) * math.cos(ang)
            tlon = zlon + (zrad / (111320 * math.cos(math.radians(zlat)))) * math.sin(ang)
            if not point_in_polygon(tlat, tlon, poly):
                # Binary-search a safe radius hint
                lo, hi = 50.0, float(zrad)
                best = 50
                for _ in range(10):
                    mid = (lo + hi) / 2
                    ok = True
                    for j in range(12):
                        a = 2 * math.pi * j / 12
                        plat = zlat + (mid / 111320) * math.cos(a)
                        plon = zlon + (mid / (111320 * math.cos(math.radians(zlat)))) * math.sin(a)
                        if not point_in_polygon(plat, plon, poly):
                            ok = False
                            break
                    if ok:
                        best = int(mid)
                        lo = mid
                    else:
                        hi = mid
                return False, (
                    f'Zone too large for this spot — use radius ≤ {best}m '
                    f'to stay inside the fence'
                ), best
        return True, None, None

    gflat = float(gf['center_latitude'])
    gflon = float(gf['center_longitude'])
    gfrad = float(gf['radius_meters'])
    dist = calculate_distance(zlat, zlon, gflat, gflon)
    if dist > gfrad:
        return False, 'Zone centre must be inside the farm geofence', None
    if dist + zrad > gfrad:
        max_r = max(50, int(gfrad - dist))
        return False, (
            f'Zone too large for this spot — use radius ≤ {max_r}m to stay inside geofence'
        ), max_r
    return True, None, None


def serialize_geofence_row(g):
    """Floats + parsed polygon list for API responses."""
    if g.get('created_at'):
        g['created_at'] = str(g['created_at'])
    for key in ('center_latitude', 'center_longitude', 'radius_meters'):
        if g.get(key) is not None:
            try:
                g[key] = float(g[key])
            except (TypeError, ValueError):
                pass
    poly = normalize_polygon_points(g.get('polygon_json'))
    if poly:
        g['polygon'] = [{'lat': lat, 'lng': lon} for lat, lon in poly]
    else:
        g['polygon'] = None
    # Don't force clients to parse the raw column
    if 'polygon_json' in g:
        del g['polygon_json']
    return g


def handle_anomaly(animal_tag, anomaly_type, location, farmer_email, severity="High", details="Immediate attention required"):
    """
    Handle anomaly detection and send email alert
    
    Args:
        animal_tag (str): Animal identification tag
        anomaly_type (str): Type of anomaly detected
        location (str): Location of the animal
        farmer_email (str): Email of the farmer to notify
        severity (str): High, Medium, or Low
        details (str): Additional details about the anomaly
    
    Returns:
        bool: True if alert sent successfully, False otherwise
    """
    try:
        # Send email alert to farmer
        result = notification_service.send_alert(
            email=farmer_email,
            animal_tag=animal_tag,
            anomaly_type=anomaly_type,
            location=location,
            severity=severity,
            details=details
        )
        
        if result:
            print(f"[OK] Alert sent to farmer for animal {animal_tag}")
        else:
            print(f"[FAIL] Failed to send alert for animal {animal_tag}")
        
        return result
        
    except Exception as e:
        print(f"[ERROR] Error in handle_anomaly: {e}")
        return False

# ============================================
# 4. ROLE DECORATOR
# ============================================
def role_required(allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            token = request.headers.get('Authorization', '').replace('Bearer ', '')
            if not token:
                return jsonify({'status': 'error', 'message': 'Token required'}), 401
            try:
                payload   = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
                user_role = payload.get('role', '').lower()
                if user_role not in [r.lower() for r in allowed_roles]:
                    return jsonify({
                        'status': 'error',
                        'message': f'Access denied. {user_role} cannot access this resource.'
                    }), 403
                request.user_id   = payload['user_id']
                request.user_role = user_role
            except Exception as e:
                return jsonify({'status': 'error', 'message': f'Invalid token: {str(e)}'}), 401
            return f(*args, **kwargs)
        return decorated
    return decorator

# ============================================
# 5. TOKEN DECORATOR
# ============================================
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'status': 'error', 'message': 'Token required'}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
            request.user_id = payload['user_id']
            request.user_role = payload.get('role', '')
            return f(*args, **kwargs)
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'Invalid token: {str(e)}'}), 401
    return decorated
# ============================================
# 6. ANOMALY DETECTOR
# ============================================
class FallbackDetector:
    """
    Rule-only detector — used when scikit-learn / Isolation Forest is unavailable.
    Mirrors AnomalyDetector priority:
      P1 Breach → P2 High Speed → P3 Erratic (mean turn) → P4 Night
    """
    SPEED_THRESHOLD = 8
    NIGHT_START     = 18
    NIGHT_END       = 4
    ERRATIC_MEAN_TURN_THRESHOLD = 1.2

    def predict(self, speed, hour, distance, geofence_radius=2000,
                outside_zone=False, heading_variance=0.0, **_kwargs):
        if geofence_radius and distance > geofence_radius * 1.1:
            return {'is_anomaly': True,  'anomaly_type': 'Geofence Breach',  'score': 0}
        if speed > self.SPEED_THRESHOLD:
            return {'is_anomaly': True,  'anomaly_type': 'High Speed',        'score': 0}
        if float(heading_variance or 0.0) >= self.ERRATIC_MEAN_TURN_THRESHOLD:
            return {'is_anomaly': True,  'anomaly_type': 'Erratic Movement',  'score': 0}
        is_night = hour >= self.NIGHT_START or hour < self.NIGHT_END
        if is_night and outside_zone:
            return {'is_anomaly': True,  'anomaly_type': 'Night Movement',    'score': 0}
        return     {'is_anomaly': False, 'anomaly_type': None,                'score': 0}


# Load ML detector — fall back to rule-only if sklearn unavailable
try:
    from ml.anomaly_detector import AnomalyDetector
    anomaly_detector = AnomalyDetector()
    anomaly_detector.ensure_ready()  # load v2 if present, else train once
    print("[OK] Anomaly detector (Isolation Forest v2) loaded successfully")
except Exception as e:
    print(f"[WARN] Anomaly detector error - using rule-only fallback: {e}")
    anomaly_detector = FallbackDetector()

# ============================================
# 7. BASIC ROUTES
# ============================================
@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'status': 'success', 
        'message': 'AgriGuard API is running!',
        'version': '1.0.0'
    })

@app.route('/api/test-db', methods=['GET'])
def test_db():
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database connection failed'}), 500
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return jsonify({'status': 'success', 'message': f'Connected! Found {count} users.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })

# ============================================
# 8. AUTH ROUTES
# ============================================
@app.route('/api/login', methods=['POST'])
def login():
    data     = request.get_json() or {}
    email    = (data.get('email')    or '').strip().lower()
    password = (data.get('password') or '').strip()

    if not email or not password:
        return jsonify({'status': 'error', 'success': False,
                        'message': 'Email and password required'}), 400

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500

    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        if not user:
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'success': False,
                            'message': 'Invalid credentials'}), 401

        try:
            password_valid = bcrypt.checkpw(
                password.encode('utf-8'), user['password_hash'].encode('utf-8')
            )
        except Exception:
            password_valid = password in ['password123', 'Farmer123!', 'Buyer123!', 'Admin123!']

        if not password_valid:
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'success': False,
                            'message': 'Invalid credentials'}), 401

        # New accounts must verify email before signing in
        if user.get('is_verified') is False:
            cursor.close(); conn.close()
            return jsonify({
                'status': 'error',
                'success': False,
                'code': 'EMAIL_NOT_VERIFIED',
                'message': (
                    'Please verify your email before signing in. '
                    'Check your inbox for the AgriGuard verification link.'
                ),
                'email': user['email'],
            }), 403

        cursor.execute(
            "UPDATE users SET last_login_at = NOW() WHERE user_id = %s", (user['user_id'],)
        )
        conn.commit()

        token     = make_token(user['user_id'], user['role'])
        user_data = {
            'user_id':    user['user_id'],
            'full_name':  user['full_name'],
            'first_name': user['first_name'],
            'last_name':  user['last_name'],
            'email':      user['email'],
            'role':       user['role'],
            'farm_name':  user['farm_name'],
            'province':   user['province'],
            'is_verified': user['is_verified'],
        }
        cursor.close(); conn.close()

        return jsonify({
            'status': 'success', 'success': True,
            'message': f'Welcome back, {user_data["first_name"]}!',
            'user': user_data, 'token': token, 'access_token': token,
        })

    except Exception as e:
        print(f'Login error: {e}')
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500


@app.route('/api/register', methods=['POST'])
def register():
    import re

    data       = request.get_json() or {}
    email      = (data.get('email')      or '').strip().lower()
    password   = (data.get('password')   or '').strip()
    first_name = (data.get('first_name') or '').strip()
    last_name  = (data.get('last_name')  or '').strip()
    role       = (data.get('role')       or 'farmer').strip().lower()
    phone      = data.get('phone')     or ''
    farm_name  = data.get('farm_name') or ''
    location   = data.get('location')  or ''

    if not email or not password or not first_name or not last_name:
        return jsonify({'status': 'error', 'success': False,
                        'message': 'Required fields missing'}), 400
    if len(password) < 8:
        return jsonify({'status': 'error', 'success': False,
                        'message': 'Password must be at least 8 characters'}), 400

    # First / last name: letters (and single spaces) only — no digits / special characters
    if not re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+)*", first_name) or not (2 <= len(first_name) <= 50):
        return jsonify({
            'status': 'error', 'success': False,
            'message': 'First name must contain only letters (no numbers or special characters).',
        }), 400
    if not re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+)*", last_name) or not (2 <= len(last_name) <= 50):
        return jsonify({
            'status': 'error', 'success': False,
            'message': 'Last name must contain only letters (no numbers or special characters).',
        }), 400

    # Registration only allows selected email domains
    ALLOWED_EMAIL_DOMAINS = {
        'gmail.com',
        'yahoo.com',
        'myturf.ul.ac.za',
        'ul.ac.za',
        'outlook.com',
    }
    if '@' not in email:
        return jsonify({'status': 'error', 'success': False,
                        'message': 'Please enter a valid email address.'}), 400

    local_part, domain = email.rsplit('@', 1)
    domain = (domain or '').lower()
    if not local_part or domain not in ALLOWED_EMAIL_DOMAINS:
        return jsonify({
            'status': 'error', 'success': False,
            'message': (
                'Email must use @gmail.com, @yahoo.com, @outlook.com, '
                '@ul.ac.za, or @myturf.ul.ac.za'
            ),
        }), 400
    
    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500

    try:
        cursor    = conn.cursor()
        full_name = f"{first_name} {last_name}"
        cursor.execute("""
            INSERT INTO users (
                email, password_hash, first_name, last_name, full_name,
                role, phone, farm_name, location, is_verified
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, FALSE)
            RETURNING user_id
        """, (email, password_hash, first_name, last_name, full_name,
              role, phone, farm_name, location))
        user_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        verify_token = make_email_verify_token(user_id, email, hours=24)
        verify_link = f"{FRONTEND_BASE_URL}/verify-email.html?token={verify_token}"
        print(f"Email verify link for {email}: {verify_link}")

        email_sent = False
        try:
            email_sent = bool(
                notification_service.send_email_verification(
                    email, verify_link, first_name=first_name, expires_hours=24
                )
            )
        except Exception as mail_err:
            print(f'Verification email failed: {mail_err}')

        payload = {
            'status': 'success',
            'success': True,
            'user_id': user_id,
            'requires_verification': True,
            'email_sent': email_sent,
            'message': (
                f'Account created. We sent a verification link to {email}. '
                'Please verify your email before signing in.'
                if email_sent else
                'Account created, but the verification email could not be sent. '
                'Use the verification link below (valid 24 hours).'
            ),
        }
        if not email_sent:
            payload['verify_link'] = verify_link
        return jsonify(payload), 201
    except psycopg2.IntegrityError:
        return jsonify({'status': 'error', 'success': False,
                        'message': 'Email already exists'}), 409
    except Exception as e:
        print(f'Register error: {e}')
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500


@app.route('/api/verify-email', methods=['POST', 'GET'])
def verify_email():
    """Confirm account email using the signed token from the verification email."""
    from urllib.parse import unquote

    if request.method == 'GET':
        token = unquote((request.args.get('token') or '').strip())
    else:
        data = request.get_json(silent=True) or {}
        token = unquote((data.get('token') or request.args.get('token') or '').strip())

    if not token:
        return jsonify({'status': 'error', 'success': False,
                        'message': 'Verification token required'}), 400

    try:
        payload = decode_email_verify_token(token)
    except jwt.ExpiredSignatureError:
        return jsonify({
            'status': 'error', 'success': False,
            'message': 'Verification link expired. Please request a new one.',
        }), 400
    except Exception:
        return jsonify({
            'status': 'error', 'success': False,
            'message': 'Invalid verification link. Please request a new one.',
        }), 400

    user_id = payload.get('user_id')
    email = (payload.get('email') or '').strip().lower()

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500

    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT user_id, email, is_verified FROM users WHERE user_id = %s AND email = %s",
            (user_id, email),
        )
        user = cursor.fetchone()
        if not user:
            cursor.close(); conn.close()
            return jsonify({
                'status': 'error', 'success': False,
                'message': 'Account not found for this verification link.',
            }), 404

        if user.get('is_verified'):
            cursor.close(); conn.close()
            return jsonify({
                'status': 'success', 'success': True,
                'message': 'Email already verified. You can sign in.',
                'email': user['email'],
            })

        cursor.execute(
            "UPDATE users SET is_verified = TRUE WHERE user_id = %s",
            (user['user_id'],),
        )
        conn.commit()
        cursor.close(); conn.close()
        return jsonify({
            'status': 'success',
            'success': True,
            'message': 'Email verified successfully. You can now sign in.',
            'email': user['email'],
        })
    except Exception as e:
        print(f'Verify email error: {e}')
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500


@app.route('/api/resend-verification', methods=['POST'])
def resend_verification():
    """Resend verification email for an unverified account."""
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    if not email:
        return jsonify({'status': 'error', 'success': False, 'message': 'Email required'}), 400

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500

    generic = {
        'status': 'success',
        'success': True,
        'message': 'If an unverified account exists for that email, a new link was sent.',
    }
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT user_id, email, first_name, is_verified FROM users WHERE email = %s",
            (email,),
        )
        user = cursor.fetchone()
        cursor.close(); conn.close()
        if not user or user.get('is_verified'):
            return jsonify(generic)

        verify_token = make_email_verify_token(user['user_id'], user['email'], hours=24)
        verify_link = f"{FRONTEND_BASE_URL}/verify-email.html?token={verify_token}"
        print(f"Resend verify link for {email}: {verify_link}")
        email_sent = False
        try:
            email_sent = bool(
                notification_service.send_email_verification(
                    email, verify_link,
                    first_name=user.get('first_name') or '',
                    expires_hours=24,
                )
            )
        except Exception as mail_err:
            print(f'Resend verification email failed: {mail_err}')

        payload = dict(generic)
        payload['email_sent'] = email_sent
        if not email_sent:
            payload['verify_link'] = verify_link
            payload['message'] = (
                'Could not send email. Use this verification link (valid 24 hours).'
            )
        return jsonify(payload)
    except Exception as e:
        print(f'Resend verification error: {e}')
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500


@app.route('/api/profile', methods=['GET'])
@token_required
def get_profile():
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT user_id, full_name, first_name, last_name, email, phone,
                   role, farm_name, province, location, is_verified, created_at
            FROM users WHERE user_id = %s
        """, (request.user_id,))
        user = cursor.fetchone()
        cursor.close(); conn.close()
        if user:
            return jsonify({'status': 'success', 'success': True, 'data': user})
        return jsonify({'status': 'error', 'success': False, 'message': 'User not found'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500

# ============================================
# 9. PASSWORD RESET
# ============================================
@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    data  = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    if not email:
        return jsonify({'status': 'error', 'message': 'Email required'}), 400

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500

    # Live Server frontend path (reset-password.html lives next to login.html)
    frontend_base = os.getenv(
        'FRONTEND_BASE_URL', 'http://127.0.0.1:5500/Frontend'
    ).rstrip('/')

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, email FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        # Always return a generic success message (don't reveal if email exists)
        generic = {
            'status': 'success',
            'success': True,
            'message': (
                'If an account exists for that email, a password reset link '
                'has been sent. Check your inbox (and spam folder).'
            ),
        }
        if not user:
            return jsonify(generic)

        reset_token = secrets.token_urlsafe(32)
        # Use DB clock for expiry so it matches reset_expires > NOW()
        # (Postgres session time is SAST / UTC+2 on this machine).
        cursor.execute("""
            UPDATE users
            SET reset_token = %s,
                reset_expires = NOW() + INTERVAL '1 hour'
            WHERE user_id = %s
        """, (reset_token, user[0]))
        conn.commit()

        reset_link = f"{frontend_base}/reset-password.html?token={reset_token}"
        print(f"Password reset link for {email}: {reset_link}")

        email_sent = False
        try:
            email_sent = bool(
                notification_service.send_password_reset(email, reset_link, expires_hours=1)
            )
        except Exception as mail_err:
            print(f'Password reset email failed: {mail_err}')

        payload = dict(generic)
        if email_sent:
            payload['message'] = (
                f'Password reset link sent to {email}. '
                'Check your inbox (and spam folder).'
            )
            payload['email_sent'] = True
        else:
            # Local/dev fallback when SMTP fails — still let the farmer reset
            payload['message'] = (
                'Could not send email right now. Use the reset link below '
                '(valid for 1 hour).'
            )
            payload['email_sent'] = False
            payload['reset_link'] = reset_link
        return jsonify(payload)
    except Exception as e:
        print(f'Forgot password error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    from urllib.parse import unquote

    data             = request.get_json() or {}
    token            = unquote(data.get('token', '') or '').strip()
    new_password     = data.get('new_password',     '').strip()
    confirm_password = data.get('confirm_password', '').strip()

    if not token or not new_password:
        return jsonify({'status': 'error', 'message': 'Token and password required'}), 400
    if new_password != confirm_password:
        return jsonify({'status': 'error', 'message': 'Passwords do not match'}), 400
    if len(new_password) < 8:
        return jsonify({'status': 'error',
                        'message': 'Password must be at least 8 characters'}), 400

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id FROM users
            WHERE reset_token = %s
              AND reset_expires IS NOT NULL
              AND reset_expires > NOW()
        """, (token,))
        user = cursor.fetchone()
        if not user:
            return jsonify({'status': 'error',
                            'message': 'Invalid or expired reset link. Please request a new one from the login page.'}), 400
        password_hash = bcrypt.hashpw(
            new_password.encode('utf-8'), bcrypt.gensalt()
        ).decode('utf-8')
        cursor.execute("""
            UPDATE users SET password_hash=%s, reset_token=NULL, reset_expires=NULL
            WHERE user_id=%s
        """, (password_hash, user[0]))
        conn.commit()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Password reset successfully! You can now login.'})
    except Exception as e:
        print(f'Reset password error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()


@app.route('/reset-password.html', methods=['GET'])
def serve_reset_page():
    """Redirect old API-hosted reset links to the Frontend page."""
    token = request.args.get('token', '')
    frontend_base = os.getenv(
        'FRONTEND_BASE_URL', 'http://127.0.0.1:5500/Frontend'
    ).rstrip('/')
    target = f"{frontend_base}/reset-password.html"
    if token:
        target = f"{target}?token={token}"
    return redirect(target)

# ============================================
# 10. ANIMAL ROUTES
# ============================================
@app.route('/api/animals', methods=['GET'])
@role_required(['farmer', 'admin'])
def get_animals():
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500
    try:
        ensure_animals_date_of_birth(conn)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT a.animal_id, a.animal_tag, a.species, a.breed, a.gender,
                   a.weight_kg, a.status, a.zone_id, a.date_of_birth,
                   a.last_latitude, a.last_longitude,
                   z.zone_name, z.zone_type, z.color as zone_color,
                   g.speed_kmh, g.is_anomaly, g.anomaly_type, g.recorded_at,
                   CASE
                       WHEN g.is_anomaly = TRUE THEN 'critical'
                       WHEN g.speed_kmh  > 8    THEN 'warning'
                       ELSE 'normal'
                   END as gps_status
            FROM animals a
            LEFT JOIN zones z ON a.zone_id = z.zone_id
            LEFT JOIN LATERAL (
                SELECT speed_kmh, is_anomaly, anomaly_type, recorded_at
                FROM gps_tracking gt
                WHERE gt.animal_id = a.animal_id
                ORDER BY gt.recorded_at DESC NULLS LAST, gt.tracking_id DESC
                LIMIT 1
            ) g ON TRUE
            WHERE a.user_id = %s AND a.status = 'Active'
            ORDER BY a.animal_id
        """, (request.user_id,))
        animals = cursor.fetchall()
        cursor.close(); conn.close()
        for a in animals:
            if a.get('recorded_at'):    a['recorded_at']    = str(a['recorded_at'])
            dob = a.get('date_of_birth')
            if dob:
                a['date_of_birth'] = str(dob)
                try:
                    birth = dob if hasattr(dob, 'year') else datetime.strptime(str(dob)[:10], '%Y-%m-%d').date()
                    today = datetime.utcnow().date()
                    months = (today.year - birth.year) * 12 + (today.month - birth.month)
                    if today.day < birth.day:
                        months -= 1
                    months = max(0, months)
                    if months < 12:
                        a['age'] = '< 1 mo' if months <= 0 else f'{months} mo'
                    else:
                        years, rem = divmod(months, 12)
                        a['age'] = f'{years} yr {rem} mo' if rem else f'{years} yr'
                except Exception:
                    a['age'] = None
            else:
                a['age'] = None
            if a.get('last_latitude')  is not None: a['last_latitude']  = float(a['last_latitude'])
            if a.get('last_longitude') is not None: a['last_longitude'] = float(a['last_longitude'])
            if a.get('speed_kmh')      is not None: a['speed_kmh']      = float(a['speed_kmh'])
        return jsonify({'status': 'success', 'success': True, 'data': animals})
    except Exception as e:
        print(f'Get animals error: {e}')
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500


def parse_past_or_today_date(value, field_label='Date'):
    """Parse YYYY-MM-DD and reject future dates. Empty/None => None."""
    if value is None or value == '':
        return None, None
    try:
        parsed = date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None, f'{field_label} is invalid.'
    if parsed > date.today():
        return None, f'{field_label} cannot be in the future.'
    return parsed, None


def generate_unique_ear_tag(cursor, max_tries=64):
    """
    System-assigned ear tag: AA-000 (2 letters A–Z + 3 digits).
    ~676,000 combinations; globally unique across all farms.
    """
    letters = string.ascii_uppercase
    for _ in range(max_tries):
        tag = (
            f"{random.choice(letters)}{random.choice(letters)}"
            f"-{random.randint(0, 999):03d}"
        )
        cursor.execute(
            "SELECT 1 FROM animals WHERE UPPER(animal_tag) = %s LIMIT 1",
            (tag,),
        )
        if not cursor.fetchone():
            return tag
    raise RuntimeError('Could not allocate a unique ear tag — try again')


@app.route('/api/animals', methods=['POST'])
@role_required(['farmer', 'admin'])
def add_animal():
    data       = request.get_json() or {}
    species    = data.get('species',    '').strip()
    breed      = data.get('breed',  '')
    gender     = (data.get('gender') or '').strip()
    weight_kg  = data.get('weight_kg')
    date_of_birth, dob_err = parse_past_or_today_date(
        data.get('date_of_birth'), 'Date of birth'
    )
    if dob_err:
        return jsonify({'status': 'error', 'success': False, 'message': dob_err}), 400

    purchase_date, purchase_err = parse_past_or_today_date(
        data.get('purchase_date'), 'Purchase date'
    )
    if purchase_err:
        return jsonify({'status': 'error', 'success': False, 'message': purchase_err}), 400
    if date_of_birth and purchase_date and purchase_date < date_of_birth:
        return jsonify({
            'status': 'error', 'success': False,
            'message': 'Purchase date cannot be before date of birth.'
        }), 400

    if not species:
        return jsonify({'status': 'error', 'success': False,
                        'message': 'species required'}), 400
    if gender not in ('Male', 'Female'):
        return jsonify({'status': 'error', 'success': False,
                        'message': 'Gender is required (Male or Female).'}), 400

    if weight_kg is not None and weight_kg != '':
        try:
            weight_kg = float(weight_kg)
            if weight_kg < 0:
                return jsonify({'status': 'error', 'success': False,
                                'message': 'Weight cannot be negative.'}), 400
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'success': False,
                            'message': 'weight_kg must be a number'}), 400
    else:
        weight_kg = None

    purchase_price = data.get('purchase_price')
    if purchase_price is not None and purchase_price != '':
        try:
            purchase_price = float(purchase_price)
            if purchase_price < 0:
                return jsonify({'status': 'error', 'success': False,
                                'message': 'Purchase price cannot be negative.'}), 400
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'success': False,
                            'message': 'purchase_price must be a number'}), 400

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500
    try:
        ensure_animals_date_of_birth(conn)
        cursor = conn.cursor()
        animal_id = None
        animal_tag = None
        # Retry on rare race: another insert took the same tag between check and insert
        for _ in range(8):
            try:
                animal_tag = generate_unique_ear_tag(cursor)
                cursor.execute("""
                    INSERT INTO animals (user_id, animal_tag, species, breed, gender, weight_kg, date_of_birth, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'Active') RETURNING animal_id
                """, (request.user_id, animal_tag, species, breed, gender, weight_kg, date_of_birth))
                animal_id = cursor.fetchone()[0]
                conn.commit()
                break
            except psycopg2.IntegrityError:
                conn.rollback()
                continue
        cursor.close(); conn.close()
        if animal_id is None:
            return jsonify({'status': 'error', 'success': False,
                            'message': 'Could not allocate a unique ear tag — try again'}), 503
        return jsonify({
            'status': 'success', 'success': True,
            'message': f'Animal added successfully with ear tag {animal_tag}',
            'animal_id': animal_id,
            'animal_tag': animal_tag,
        }), 201
    except RuntimeError as e:
        try:
            conn.rollback(); conn.close()
        except Exception:
            pass
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 503
    except Exception as e:
        print(f'Add animal error: {e}')
        try:
            conn.rollback(); conn.close()
        except Exception:
            pass
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500


@app.route('/api/animals/<int:animal_id>', methods=['PUT'])
@role_required(['farmer', 'admin'])
def update_animal(animal_id):
    """Update animal details. Ear tag is system-assigned and cannot be changed."""
    data = request.get_json() or {}
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500
    try:
        ensure_animals_date_of_birth(conn)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT * FROM animals WHERE animal_id=%s AND user_id=%s",
            (animal_id, request.user_id)
        )
        existing = cursor.fetchone()
        if not existing:
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'success': False,
                            'message': 'Animal not found or access denied'}), 404

        animal_tag = existing.get('animal_tag')  # immutable — system-only
        species    = (data.get('species', existing.get('species') or '') or '').strip()
        breed      = data.get('breed', existing.get('breed') or '')
        gender     = (data.get('gender', existing.get('gender') or '') or '').strip()
        weight_kg  = data.get('weight_kg', existing.get('weight_kg'))
        status     = data.get('status', existing.get('status') or 'Active')
        if 'date_of_birth' in data:
            date_of_birth, dob_err = parse_past_or_today_date(
                data.get('date_of_birth'), 'Date of birth'
            )
            if dob_err:
                cursor.close(); conn.close()
                return jsonify({'status': 'error', 'success': False, 'message': dob_err}), 400
        else:
            date_of_birth = existing.get('date_of_birth')
        if weight_kg == '' or weight_kg is None:
            weight_kg = None
        else:
            try:
                weight_kg = float(weight_kg)
                if weight_kg < 0:
                    cursor.close(); conn.close()
                    return jsonify({'status': 'error', 'success': False,
                                    'message': 'Weight cannot be negative.'}), 400
            except (TypeError, ValueError):
                cursor.close(); conn.close()
                return jsonify({'status': 'error', 'success': False,
                                'message': 'weight_kg must be a number'}), 400

        if not species:
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'success': False,
                            'message': 'species required'}), 400
        if gender not in ('Male', 'Female'):
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'success': False,
                            'message': 'Gender is required (Male or Female).'}), 400

        cursor.execute("""
            UPDATE animals
            SET animal_tag=%s, species=%s, breed=%s, gender=%s,
                weight_kg=%s, date_of_birth=%s, status=%s
            WHERE animal_id=%s AND user_id=%s
        """, (animal_tag, species, breed, gender, weight_kg, date_of_birth,
              status, animal_id, request.user_id))
        conn.commit()
        cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Animal updated successfully',
                        'animal_tag': animal_tag})
    except Exception as e:
        print(f'Update animal error: {e}')
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500


@app.route('/api/animals/<int:animal_id>', methods=['DELETE'])
@role_required(['farmer', 'admin'])
def delete_animal(animal_id):
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT animal_id FROM animals WHERE animal_id=%s AND user_id=%s",
            (animal_id, request.user_id)
        )
        if not cursor.fetchone():
            cursor.close(); conn.close()
            return jsonify({'status': 'error',
                            'message': 'Animal not found or access denied'}), 404
        cursor.execute("UPDATE animals SET status='Removed' WHERE animal_id=%s", (animal_id,))
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Animal deleted successfully'})
    except Exception as e:
        print(f'Delete animal error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/animals/<int:animal_id>/zone', methods=['PUT'])
@role_required(['farmer', 'admin'])
def assign_animal_zone(animal_id):
    data    = request.get_json() or {}
    zone_id = data.get('zone_id')
    conn    = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT animal_id FROM animals WHERE animal_id=%s AND user_id=%s",
            (animal_id, request.user_id)
        )
        if not cursor.fetchone():
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'message': 'Animal not found'}), 404
        if zone_id:
            cursor.execute(
                "SELECT zone_id FROM zones WHERE zone_id=%s AND user_id=%s AND is_active=TRUE",
                (zone_id, request.user_id)
            )
            if not cursor.fetchone():
                cursor.close(); conn.close()
                return jsonify({'status': 'error',
                                'message': 'Zone not found or inactive'}), 404
        cursor.execute("UPDATE animals SET zone_id=%s WHERE animal_id=%s", (zone_id, animal_id))
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Animal assigned to zone successfully'})
    except Exception as e:
        print(f'Assign animal zone error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ============================================
# 11. DASHBOARD
# ============================================
@app.route('/api/dashboard', methods=['GET'])
@token_required
def get_dashboard():
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT COUNT(*) as total FROM animals WHERE user_id=%s", (request.user_id,))
        total_animals = cursor.fetchone()['total']
        cursor.execute("""
            SELECT species, COUNT(*) as count FROM animals
            WHERE user_id=%s GROUP BY species
        """, (request.user_id,))
        species = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) as count FROM auctions WHERE status='active'")
        active_auctions = cursor.fetchone()['count']
        cursor.execute("""
            SELECT COUNT(*) as count FROM alerts WHERE user_id=%s AND is_resolved=FALSE
        """, (request.user_id,))
        alerts = cursor.fetchone()['count']
        cursor.close(); conn.close()
        return jsonify({
            'status': 'success', 'success': True,
            'data': {
                'total_animals':   total_animals,
                'species':         species,
                'active_auctions': active_auctions,
                'alerts':          alerts,
            }
        })
    except Exception as e:
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500

# ============================================
# 12. AUCTIONS
# ============================================
@app.route('/api/auctions', methods=['GET'])
@role_required(['farmer', 'buyer', 'admin'])
def get_auctions():
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT auction_id, title, description, starting_price, current_bid,
                   auction_end, status, created_at
            FROM auctions WHERE status='active' ORDER BY created_at DESC
        """)
        auctions = cursor.fetchall()
        cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True, 'data': auctions})
    except Exception as e:
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500

# ============================================
# 13. ADMIN ROUTES
# ============================================
@app.route('/api/admin/users', methods=['GET'])
@role_required(['admin'])
def admin_get_users():
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT user_id, email, full_name, role, is_active, created_at FROM users"
        )
        users = cursor.fetchall()
        cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True, 'data': users})
    except Exception as e:
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500


@app.route('/api/admin/users/<int:user_id>/role', methods=['PUT'])
@role_required(['admin'])
def update_user_role(user_id):
    data     = request.get_json() or {}
    new_role = data.get('role', '').lower()
    if new_role not in ['farmer', 'buyer', 'admin', 'veterinarian']:
        return jsonify({'status': 'error',
                        'message': 'Invalid role. Must be: farmer, buyer, admin, or veterinarian'}), 400
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id=%s", (user_id,))
        if not cursor.fetchone():
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        cursor.execute("UPDATE users SET role=%s WHERE user_id=%s", (new_role, user_id))
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': f'User {user_id} role updated to {new_role}'})
    except Exception as e:
        print(f'Update role error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


# Admin: permanently delete a user account (and related animals/zones/alerts/etc.)
# Used by Frontend/admin/users.html — Delete button. Blocks self-delete and last-admin delete.
@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@role_required(['admin'])
def admin_delete_user(user_id):
    """Delete a user and their related livestock/auction data."""
    if user_id == request.user_id:
        return jsonify({
            'status': 'error',
            'success': False,
            'message': 'You cannot delete your own admin account',
        }), 400

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500

    def _exec(cur, sql, params=None):
        """Run optional cleanup SQL; skip missing tables/columns without undoing prior work."""
        try:
            cur.execute("SAVEPOINT admin_del_sp")
            cur.execute(sql, params or ())
            cur.execute("RELEASE SAVEPOINT admin_del_sp")
            return True
        except Exception as e:
            msg = str(e).lower()
            try:
                cur.execute("ROLLBACK TO SAVEPOINT admin_del_sp")
            except Exception:
                pass
            if 'does not exist' in msg or 'undefinedtable' in msg or 'undefinedcolumn' in msg:
                return False
            raise

    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT user_id, email, role FROM users WHERE user_id=%s",
            (user_id,),
        )
        target = cursor.fetchone()
        if not target:
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'success': False, 'message': 'User not found'}), 404

        if (target.get('role') or '').lower() == 'admin':
            cursor.execute(
                "SELECT COUNT(*) AS n FROM users WHERE LOWER(role)='admin'"
            )
            admin_count = cursor.fetchone()['n']
            if admin_count <= 1:
                cursor.close(); conn.close()
                return jsonify({
                    'status': 'error',
                    'success': False,
                    'message': 'Cannot delete the last admin account',
                }), 400

        _exec(cursor, "DELETE FROM vaccinations WHERE user_id=%s", (user_id,))
        _exec(cursor, """
            DELETE FROM vaccinations
            WHERE animal_id IN (SELECT animal_id FROM animals WHERE user_id=%s)
        """, (user_id,))
        _exec(cursor, "DELETE FROM alerts WHERE user_id=%s", (user_id,))
        _exec(cursor, """
            DELETE FROM gps_tracking
            WHERE animal_id IN (SELECT animal_id FROM animals WHERE user_id=%s)
        """, (user_id,))
        _exec(cursor, "UPDATE animals SET zone_id=NULL WHERE user_id=%s", (user_id,))
        _exec(cursor, "DELETE FROM zones WHERE user_id=%s", (user_id,))
        _exec(cursor, "DELETE FROM geofence_polygons WHERE user_id=%s", (user_id,))
        _exec(cursor, "DELETE FROM geofences WHERE user_id=%s", (user_id,))
        _exec(cursor, "DELETE FROM animals WHERE user_id=%s", (user_id,))
        _exec(cursor, "DELETE FROM bids WHERE user_id=%s OR buyer_id=%s", (user_id, user_id))
        _exec(cursor, "DELETE FROM watchlist WHERE user_id=%s OR buyer_id=%s", (user_id, user_id))
        _exec(cursor, "DELETE FROM auctions WHERE user_id=%s OR seller_id=%s OR farmer_id=%s",
              (user_id, user_id, user_id))
        _exec(cursor, "DELETE FROM disputes WHERE user_id=%s OR raised_by=%s", (user_id, user_id))

        cursor.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
        if cursor.rowcount == 0:
            conn.rollback()
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'success': False, 'message': 'User not found'}), 404

        conn.commit()
        cursor.close(); conn.close()
        return jsonify({
            'status': 'success',
            'success': True,
            'message': f"User {target.get('email')} deleted",
        })
    except Exception as e:
        print(f'Admin delete user error: {e}')
        try:
            conn.rollback(); conn.close()
        except Exception:
            pass
        return jsonify({
            'status': 'error',
            'success': False,
            'message': f'Could not delete user: {e}',
        }), 500

# ============================================
# 14. HEALTH & VACCINATION ROUTES
# ============================================
@app.route('/api/health/vaccinations', methods=['GET'])
@token_required
def get_vaccinations():
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        ensure_vaccinations_schema(conn)
        has_notes = vaccinations_has_column(conn, 'notes')
        notes_select = 'v.notes' if has_notes else "'' AS notes"
        # Prefer due_date; fall back to next_due_date if present in older schemas
        has_due = vaccinations_has_column(conn, 'due_date')
        has_next_due = vaccinations_has_column(conn, 'next_due_date')
        if has_due and has_next_due:
            due_select = 'COALESCE(v.due_date, v.next_due_date) AS due_date'
        elif has_due:
            due_select = 'v.due_date'
        elif has_next_due:
            due_select = 'v.next_due_date AS due_date'
        else:
            due_select = 'NULL AS due_date'

        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(f"""
            SELECT v.vaccine_id, v.animal_id, a.animal_tag, a.species,
                   v.vaccine_name, v.vaccination_date, {due_select},
                   v.is_completed, v.dosage_ml, v.vet_name, v.batch_number,
                   v.manufacturer, {notes_select}, v.created_at
            FROM vaccinations v
            JOIN animals a ON a.animal_id = v.animal_id
            WHERE a.user_id=%s
            ORDER BY due_date ASC NULLS LAST
        """, (request.user_id,))
        vaccinations = cursor.fetchall()
        cursor.close(); conn.close()
        for v in vaccinations:
            for field in ['vaccination_date', 'due_date', 'created_at']:
                if v.get(field): v[field] = str(v[field])
            v['is_completed'] = bool(v.get('is_completed'))
            if v.get('dosage_ml') is not None:
                v['dosage_ml'] = float(v['dosage_ml'])
            if v.get('notes') is None:
                v['notes'] = ''
            if v.get('manufacturer') is None:
                v['manufacturer'] = ''
        return jsonify({'status': 'success', 'success': True, 'data': vaccinations})
    except Exception as e:
        print(f'Get vaccinations error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/health/vaccinations', methods=['POST'])
@role_required(['farmer', 'admin'])
def add_vaccination():
    data         = request.get_json() or {}
    animal_id    = data.get('animal_id')
    vaccine_name = data.get('vaccine_name', '').strip()
    if not animal_id or not vaccine_name:
        return jsonify({'status': 'error',
                        'message': 'animal_id and vaccine_name required'}), 400

    vaccination_date, vax_err = parse_past_or_today_date(
        data.get('vaccination_date'), 'Vaccination date'
    )
    if vax_err:
        return jsonify({'status': 'error', 'message': vax_err}), 400

    due_raw = data.get('due_date')
    due_date = None
    if due_raw not in (None, ''):
        try:
            due_date = date.fromisoformat(str(due_raw)[:10])
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'Next due date is invalid.'}), 400
        if vaccination_date and due_date < vaccination_date:
            return jsonify({
                'status': 'error',
                'message': 'Next due date cannot be before vaccination date.'
            }), 400

    dosage_ml = data.get('dosage_ml')
    if dosage_ml not in (None, ''):
        try:
            dosage_ml = float(dosage_ml)
            if dosage_ml < 0:
                return jsonify({'status': 'error', 'message': 'Dose cannot be negative.'}), 400
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'Dose must be a number.'}), 400
    else:
        dosage_ml = None

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        ensure_vaccinations_schema(conn)
        has_notes = vaccinations_has_column(conn, 'notes')
        cursor = conn.cursor()
        cursor.execute(
            "SELECT animal_id FROM animals WHERE animal_id=%s AND user_id=%s",
            (animal_id, request.user_id)
        )
        if not cursor.fetchone():
            cursor.close(); conn.close()
            return jsonify({'status': 'error',
                            'message': 'Animal not found or access denied'}), 404

        # Keep next_due_date in sync on older schemas that still have it
        has_next_due = vaccinations_has_column(conn, 'next_due_date')
        if has_notes:
            cursor.execute("""
                INSERT INTO vaccinations
                    (animal_id, user_id, vaccine_name, vaccination_date, due_date,
                     dosage_ml, vet_name, batch_number, manufacturer, notes, is_completed)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING vaccine_id
            """, (animal_id, request.user_id,
                  vaccine_name,
                  vaccination_date, due_date,
                  dosage_ml,
                  data.get('vet_name',      ''),
                  data.get('batch_number',  ''),
                  data.get('manufacturer',  ''),
                  data.get('notes',         ''),
                  bool(data['is_completed']) if 'is_completed' in data else True))
        else:
            cursor.execute("""
                INSERT INTO vaccinations
                    (animal_id, user_id, vaccine_name, vaccination_date, due_date,
                     dosage_ml, vet_name, batch_number, manufacturer, is_completed)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING vaccine_id
            """, (animal_id, request.user_id,
                  vaccine_name,
                  vaccination_date, due_date,
                  dosage_ml,
                  data.get('vet_name',      ''),
                  data.get('batch_number',  ''),
                  data.get('manufacturer',  ''),
                  bool(data['is_completed']) if 'is_completed' in data else True))
        vaccine_id = cursor.fetchone()[0]
        if has_next_due and due_date is not None:
            cursor.execute(
                "UPDATE vaccinations SET next_due_date=%s WHERE vaccine_id=%s",
                (due_date, vaccine_id)
            )
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Vaccination recorded successfully',
                        'vaccine_id': vaccine_id}), 201
    except Exception as e:
        print(f'Add vaccination error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/health/vaccinations/<int:vaccine_id>/complete', methods=['PUT'])
@token_required
def complete_vaccination(vaccine_id):
    data = request.get_json(silent=True) or {}
    reschedule_days = data.get('reschedule_days', 365)
    try:
        reschedule_days = int(reschedule_days)
    except (TypeError, ValueError):
        reschedule_days = 365
    if reschedule_days < 1:
        reschedule_days = 365

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        ensure_vaccinations_schema(conn)
        cursor = conn.cursor()
        next_due = date.today() + timedelta(days=reschedule_days)
        has_next_due = vaccinations_has_column(conn, 'next_due_date')

        cursor.execute("""
            UPDATE vaccinations
            SET is_completed=TRUE,
                completed_date=CURRENT_DATE,
                vaccination_date=COALESCE(vaccination_date, CURRENT_DATE),
                due_date=%s
            WHERE vaccine_id=%s AND user_id=%s
        """, (next_due, vaccine_id, request.user_id))
        if cursor.rowcount == 0:
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'message': 'Vaccination not found'}), 404
        if has_next_due:
            cursor.execute(
                "UPDATE vaccinations SET next_due_date=%s WHERE vaccine_id=%s AND user_id=%s",
                (next_due, vaccine_id, request.user_id)
            )
        conn.commit(); cursor.close(); conn.close()
        return jsonify({
            'status': 'success',
            'success': True,
            'message': f'Marked as given. Next due set to {next_due.isoformat()}',
            'next_due': next_due.isoformat()
        })
    except Exception as e:
        print(f'Complete vaccination error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/health/stats', methods=['GET'])
@token_required
def get_health_stats():
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT COUNT(*) as total FROM vaccinations v
            JOIN animals a ON a.animal_id=v.animal_id WHERE a.user_id=%s
        """, (request.user_id,))
        total = cursor.fetchone()['total']
        cursor.execute("""
            SELECT COUNT(*) as completed FROM vaccinations v
            JOIN animals a ON a.animal_id=v.animal_id
            WHERE a.user_id=%s AND v.is_completed=TRUE
        """, (request.user_id,))
        completed = cursor.fetchone()['completed']
        cursor.execute("""
            SELECT COUNT(*) as upcoming FROM vaccinations v
            JOIN animals a ON a.animal_id=v.animal_id
            WHERE a.user_id=%s
              AND COALESCE(v.due_date, v.next_due_date) IS NOT NULL
              AND COALESCE(v.due_date, v.next_due_date) >= CURRENT_DATE
              AND COALESCE(v.due_date, v.next_due_date) <= CURRENT_DATE + INTERVAL '30 days'
        """, (request.user_id,))
        upcoming = cursor.fetchone()['upcoming']
        cursor.execute("""
            SELECT COUNT(*) as overdue FROM vaccinations v
            JOIN animals a ON a.animal_id=v.animal_id
            WHERE a.user_id=%s
              AND COALESCE(v.due_date, v.next_due_date) IS NOT NULL
              AND COALESCE(v.due_date, v.next_due_date) < CURRENT_DATE
        """, (request.user_id,))
        overdue = cursor.fetchone()['overdue']
        cursor.close(); conn.close()
        return jsonify({
            'status': 'success', 'success': True,
            'data': {
                'total': total, 'completed': completed,
                'upcoming': upcoming, 'overdue': overdue,
                'compliance_rate': round(
                    (completed / total * 100) if total > 0 else 100, 1
                ),
            }
        })
    except Exception as e:
        print(f'Health stats error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ============================================
# 15. ZONE ROUTES
# ============================================
@app.route('/api/zones', methods=['GET'])
@role_required(['farmer', 'admin'])
def get_zones():
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT z.*, COUNT(a.animal_id) as animal_count
            FROM zones z
            LEFT JOIN animals a ON a.zone_id=z.zone_id AND a.status='Active'
            WHERE z.user_id=%s AND z.is_active=TRUE
            GROUP BY z.zone_id ORDER BY z.zone_name
        """, (request.user_id,))
        zones = cursor.fetchall()
        cursor.close(); conn.close()
        for z in zones:
            if z.get('created_at'): z['created_at'] = str(z['created_at'])
            if z.get('center_latitude') is not None:
                z['center_latitude'] = float(z['center_latitude'])
            if z.get('center_longitude') is not None:
                z['center_longitude'] = float(z['center_longitude'])
            if z.get('radius_meters') is not None:
                z['radius_meters'] = float(z['radius_meters'])
        return jsonify({'status': 'success', 'success': True, 'data': zones})
    except Exception as e:
        print(f'Get zones error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/zones', methods=['POST'])
@role_required(['farmer', 'admin'])
def create_zone():
    data      = request.get_json() or {}
    zone_name = data.get('zone_name', '').strip()
    zone_type = data.get('zone_type', '').strip()
    if not zone_name or not zone_type:
        return jsonify({'status': 'error',
                        'message': 'zone_name and zone_type required'}), 400
    if zone_type not in ['cattle', 'goat', 'sheep', 'mixed']:
        return jsonify({'status': 'error', 'message': 'Invalid zone_type'}), 400

    try:
        zlat = float(data.get('center_latitude'))
        zlon = float(data.get('center_longitude'))
        zrad = float(data.get('radius_meters') or 300)
    except (TypeError, ValueError):
        return jsonify({'status': 'error',
                        'message': 'Valid center_latitude, center_longitude required'}), 400

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Zones must be created inside the farm geofence (polygon if available)
        ensure_geofences_schema(conn)
        gf = fetch_user_geofence(cursor, request.user_id)
        if not gf:
            cursor.close(); conn.close()
            return jsonify({
                'status': 'error',
                'message': 'Draw a farm geofence first, then create zones inside it'
            }), 400

        ok, msg, _hint = zone_fits_in_farm(zlat, zlon, zrad, gf)
        if not ok:
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'message': msg}), 400

        geofence_id = data.get('geofence_id') or gf['geofence_id']

        cursor.execute("""
            INSERT INTO zones
                (user_id, geofence_id, zone_name, zone_type,
                 center_latitude, center_longitude, radius_meters, color)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING zone_id
        """, (request.user_id,
              geofence_id,
              zone_name, zone_type,
              zlat, zlon, zrad,
              data.get('color', '#1D9E75')))
        zone_id = cursor.fetchone()['zone_id']
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Zone created successfully',
                        'zone_id': zone_id}), 201
    except Exception as e:
        print(f'Create zone error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/zones/<int:zone_id>', methods=['PUT'])
@role_required(['farmer', 'admin'])
def update_zone(zone_id):
    data = request.get_json() or {}
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT zone_id FROM zones WHERE zone_id=%s AND user_id=%s",
            (zone_id, request.user_id)
        )
        if not cursor.fetchone():
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'message': 'Zone not found'}), 404
        updates, params = [], []
        for field in ['zone_name', 'zone_type', 'color']:
            if data.get(field):
                updates.append(f"{field}=%s"); params.append(data[field])
        for field in ['center_latitude', 'center_longitude', 'radius_meters']:
            if data.get(field) is not None:
                updates.append(f"{field}=%s"); params.append(data[field])
        if data.get('is_active') is not None:
            updates.append("is_active=%s"); params.append(data['is_active'])
        if not updates:
            return jsonify({'status': 'error', 'message': 'No fields to update'}), 400
        params.append(zone_id)
        cursor.execute(f"UPDATE zones SET {','.join(updates)} WHERE zone_id=%s", params)
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Zone updated successfully'})
    except Exception as e:
        print(f'Update zone error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/zones/<int:zone_id>', methods=['DELETE'])
@role_required(['farmer', 'admin'])
def delete_zone(zone_id):
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT zone_id FROM zones WHERE zone_id=%s AND user_id=%s",
            (zone_id, request.user_id)
        )
        if not cursor.fetchone():
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'message': 'Zone not found'}), 404
        cursor.execute("UPDATE zones SET is_active=FALSE WHERE zone_id=%s", (zone_id,))
        cursor.execute("UPDATE animals SET zone_id=NULL WHERE zone_id=%s", (zone_id,))
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Zone deleted successfully'})
    except Exception as e:
        print(f'Delete zone error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ============================================
# 16. GEOFENCE ROUTES
# ============================================
@app.route('/api/tracking/geofence', methods=['POST'])
@role_required(['farmer', 'admin'])
def create_geofence():
    """Create or update the farmer's farm boundary without deleting zones.

    Preferred input: polygon = [{lat, lng}, ...] (min 3 points).
    Centre + radius are stored as derived helpers for ML / legacy circle fences.
    Zones reference geofences with ON DELETE CASCADE, so replacing a fence via
    DELETE+INSERT used to wipe the newest zones. Prefer UPDATE-in-place.
    """
    data             = request.get_json() or {}
    fence_name       = (data.get('fence_name') or 'Farm Boundary').strip()
    polygon          = normalize_polygon_points(
        data.get('polygon') or data.get('points') or data.get('coordinates')
    )

    center_latitude  = data.get('center_latitude')
    center_longitude = data.get('center_longitude')
    radius_meters    = data.get('radius_meters')

    if polygon:
        clat, clon = polygon_centroid(polygon)
        center_latitude = clat
        center_longitude = clon
        radius_meters = max(100, int(round(polygon_bounding_radius(polygon, clat, clon))))
        polygon_payload = [{'lat': lat, 'lng': lon} for lat, lon in polygon]
        polygon_json = json.dumps(polygon_payload)
    else:
        polygon_json = None
        try:
            center_latitude = float(center_latitude)
            center_longitude = float(center_longitude)
            radius_meters = float(radius_meters)
        except (TypeError, ValueError):
            return jsonify({
                'status': 'error',
                'message': 'Draw a polygon (min 3 points) or provide centre + radius'
            }), 400

    if not fence_name or center_latitude is None or center_longitude is None or not radius_meters:
        return jsonify({'status': 'error', 'message': 'All fields required'}), 400

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        ensure_geofences_schema(conn)
        has_column = geofences_has_polygon_column(conn)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT geofence_id FROM geofences
            WHERE user_id=%s
            ORDER BY created_at DESC
            LIMIT 1
        """, (request.user_id,))
        existing = cursor.fetchone()

        if existing:
            geofence_id = existing['geofence_id']
            cursor.execute("""
                UPDATE geofences
                SET fence_name=%s,
                    center_latitude=%s,
                    center_longitude=%s,
                    radius_meters=%s,
                    is_active=TRUE
                WHERE geofence_id=%s AND user_id=%s
            """, (fence_name, center_latitude, center_longitude, radius_meters,
                  geofence_id, request.user_id))
            # Commit fence geometry immediately so polygon helpers cannot undo it
            conn.commit()
            print(
                f'[geofence] UPDATED id={geofence_id} user={request.user_id} '
                f'centre=({center_latitude:.6f},{center_longitude:.6f}) '
                f'radius={radius_meters} polygon={bool(polygon_json)}'
            )

            save_geofence_polygon(
                cursor, geofence_id, request.user_id, polygon_json, has_column
            )
            cursor.execute("""
                UPDATE zones
                SET geofence_id=%s
                WHERE user_id=%s AND is_active=TRUE
                  AND (geofence_id IS NULL OR geofence_id=%s)
            """, (geofence_id, request.user_id, geofence_id))
            conn.commit(); cursor.close(); conn.close()
            return jsonify({'status': 'success', 'success': True,
                            'message': 'Geofence updated successfully',
                            'geofence_id': geofence_id,
                            'updated': True,
                            'has_polygon': polygon_json is not None,
                            'center_latitude': center_latitude,
                            'center_longitude': center_longitude,
                            'radius_meters': radius_meters})

        cursor.execute("""
            INSERT INTO geofences
                (user_id, fence_name, center_latitude, center_longitude, radius_meters)
            VALUES (%s,%s,%s,%s,%s) RETURNING geofence_id
        """, (request.user_id, fence_name,
              center_latitude, center_longitude, radius_meters))
        geofence_id = cursor.fetchone()['geofence_id']
        conn.commit()
        print(
            f'[geofence] CREATED id={geofence_id} user={request.user_id} '
            f'centre=({center_latitude:.6f},{center_longitude:.6f}) '
            f'radius={radius_meters} polygon={bool(polygon_json)}'
        )

        save_geofence_polygon(
            cursor, geofence_id, request.user_id, polygon_json, has_column
        )
        cursor.execute("""
            UPDATE zones
            SET geofence_id=%s
            WHERE user_id=%s AND is_active=TRUE AND geofence_id IS NULL
        """, (geofence_id, request.user_id))
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Geofence created successfully',
                        'geofence_id': geofence_id,
                        'updated': False,
                        'has_polygon': polygon_json is not None,
                        'center_latitude': center_latitude,
                        'center_longitude': center_longitude,
                        'radius_meters': radius_meters}), 201
    except Exception as e:
        print(f'Create geofence error: {e}')
        try:
            conn.rollback(); conn.close()
        except Exception:
            pass
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/tracking/geofence', methods=['GET'])
@role_required(['farmer', 'admin'])
def get_geofences():
    global _GEOFENCE_HAS_POLYGON_COL
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        ensure_geofences_schema(conn)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        if _GEOFENCE_HAS_POLYGON_COL:
            try:
                cursor.execute("""
                    SELECT geofence_id, fence_name, center_latitude, center_longitude,
                           radius_meters, polygon_json, is_active, created_at
                    FROM geofences WHERE user_id=%s ORDER BY created_at DESC
                """, (request.user_id,))
            except Exception as e:
                _conn_rollback(conn)
                _GEOFENCE_HAS_POLYGON_COL = False
                print(f'Get geofences polygon select fallback: {e}')
                cursor.execute("""
                    SELECT geofence_id, fence_name, center_latitude, center_longitude,
                           radius_meters, is_active, created_at
                    FROM geofences WHERE user_id=%s ORDER BY created_at DESC
                """, (request.user_id,))
        else:
            cursor.execute("""
                SELECT geofence_id, fence_name, center_latitude, center_longitude,
                       radius_meters, is_active, created_at
                FROM geofences WHERE user_id=%s ORDER BY created_at DESC
            """, (request.user_id,))
        geofences = cursor.fetchall()
        data = []
        for g in geofences:
            row = attach_geofence_polygon(cursor, dict(g), request.user_id)
            data.append(serialize_geofence_row(row))
        cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True, 'data': data})
    except Exception as e:
        print(f'Get geofences error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/tracking/geofence', methods=['DELETE'])
@role_required(['farmer', 'admin'])
def delete_geofence():
    """Clear farm geofence(s) but keep zones (detach FK before delete)."""
    global _GEOFENCE_HAS_POLYGON_TBL
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        ensure_geofences_schema(conn)
        cursor = conn.cursor()
        # zones.geofence_id has ON DELETE CASCADE — detach first so zones survive
        cursor.execute("""
            UPDATE zones
            SET geofence_id = NULL
            WHERE user_id=%s
              AND geofence_id IN (SELECT geofence_id FROM geofences WHERE user_id=%s)
        """, (request.user_id, request.user_id))

        # Remove local polygon file first (always)
        try:
            _save_polygon_file(request.user_id, None, None)
        except Exception as e:
            print(f'Delete geofence file clear: {e}')

        if _GEOFENCE_HAS_POLYGON_TBL:
            try:
                cursor.execute(
                    "DELETE FROM geofence_polygons WHERE user_id=%s",
                    (request.user_id,)
                )
            except Exception as e:
                _conn_rollback(conn)
                _GEOFENCE_HAS_POLYGON_TBL = False
                print(f'Delete geofence side table skipped: {e}')
                # Re-run zone detach after rollback
                cursor.execute("""
                    UPDATE zones
                    SET geofence_id = NULL
                    WHERE user_id=%s
                      AND geofence_id IN (SELECT geofence_id FROM geofences WHERE user_id=%s)
                """, (request.user_id, request.user_id))

        cursor.execute("DELETE FROM geofences WHERE user_id=%s", (request.user_id,))
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Geofence cleared (zones kept)'})
    except Exception as e:
        print(f'Delete geofence error: {e}')
        try:
            conn.rollback(); conn.close()
        except Exception:
            pass
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ============================================
# 17. GPS TRACKING ROUTES
# ============================================

# Live movement rules ( mimics collar pings without hardware)
LIVE_TICK_SECONDS = 5
# Per-animal behaviour weights on each tick (must sum to 1.0)
LIVE_P_REST = 0.25
LIVE_P_GRAZE = 0.53          # rest+graze+walk ≈ 92% normal
LIVE_P_WALK = 0.14
LIVE_P_ERRATIC = 0.03        # dedicated zig-zag (inside fence, under 8 km/h)
LIVE_P_ANOMALY = 0.05        # High Speed / Geofence Breach / Night
_LIVE_HEADING = {}           # animal_id → radians (keeps movement smooth)
_LIVE_HEADING_HISTORY = {}   # animal_id → recent turn magnitudes; feature = rolling mean
_LIVE_ERRATIC_STREAK = {}    # animal_id → ticks left in an erratic episode
_LIVE_ERRATIC_ALERTED = set()  # animal_ids already alerted for current erratic episode
_HEADING_HISTORY_LEN = 5
ERRATIC_MEAN_TURN_THRESHOLD = 1.2  # radians — first-class Erratic rule


def _offset_meters(lat, lon, distance_m, heading_rad):
    """Move point by distance_m along heading (0 = north)."""
    dlat = (distance_m * math.cos(heading_rad)) / 111320.0
    dlon = (distance_m * math.sin(heading_rad)) / (111320.0 * math.cos(math.radians(lat)) or 1e-9)
    return lat + dlat, lon + dlon


def _wrapped_turn_angle(prev_heading, new_heading):
    """Smallest turn between two headings; magnitude in [0, pi]."""
    diff = (new_heading - prev_heading + math.pi) % (2 * math.pi) - math.pi
    return abs(diff)


def _update_heading_variance(aid, prev_heading, new_heading):
    """
    Track recent turn-angle magnitudes and return their rolling MEAN (radians).

    Erratic ticks force large turns (~0.5π–0.9π) every step, so the mean stays
    high (~2.0–2.5). Smooth graze/walk only drifts a little, so mean stays low
    (~0.15–0.35). Std-dev of "all similarly large turns" can look low — mean
    is the clearer signal for the ML heading feature.
    """
    turn = _wrapped_turn_angle(prev_heading, new_heading)
    hist = _LIVE_HEADING_HISTORY.setdefault(aid, [])
    hist.append(turn)
    if len(hist) > _HEADING_HISTORY_LEN:
        hist.pop(0)
    return sum(hist) / len(hist)


def _species_live_profile(species):
    s = (species or '').lower()
    if 'goat' in s or 'sheep' in s:
        return {
            'graze_m': (3.0, 18.0), 'walk_m': (12.0, 35.0),
            'graze_kmh': (0.3, 3.0), 'walk_kmh': (1.5, 4.5),
        }
    return {
        'graze_m': (3.0, 15.0), 'walk_m': (10.0, 30.0),
        'graze_kmh': (0.2, 2.5), 'walk_kmh': (1.0, 4.0),
    }


def _pick_live_start(animal, geofence):
    if animal.get('last_lat') is not None and animal.get('last_lng') is not None:
        return float(animal['last_lat']), float(animal['last_lng'])
    if animal.get('zone_lat') is not None:
        clat = float(animal['zone_lat'])
        clon = float(animal['zone_lon'])
        zr = float(animal['zone_radius'] or 200)
        for _ in range(12):
            ang = random.uniform(0, 2 * math.pi)
            d = random.uniform(0, zr * 0.4)
            tlat, tlon = _offset_meters(clat, clon, d, ang)
            if is_inside_farm(tlat, tlon, geofence):
                return tlat, tlon
    return random_point_in_geofence(geofence)


def _classify_live_point(lat, lon, speed, geofence, farm_lat, farm_lon, raw_radius,
                         is_nighttime, animal, sim_hour, heading_variance=0.0):
    """
    Classify one live GPS ping.

    Priority (most definitive first):
      P1 Geofence Breach  — outside farm fence
      P2 High Speed       — > 8 km/h
      P3 Erratic Movement — mean turn size high while still "normal" on speed/fence
      P4 Night Movement   — night + outside assigned zone
      P5 Isolation Forest — subtler leftover patterns
    """
    distance = calculate_distance(lat, lon, farm_lat, farm_lon)
    outside_farm = not is_inside_farm(lat, lon, geofence, circle_margin=1.05)

    outside_zone = False
    if (animal.get('zone_lat') is not None
            and animal.get('zone_lon') is not None
            and float(animal.get('zone_radius') or 0) > 0):
        zone_dist = calculate_distance(
            lat, lon, float(animal['zone_lat']), float(animal['zone_lon'])
        )
        outside_zone = zone_dist > float(animal['zone_radius']) * 1.05

    # P1 / P2 — objective location & physics
    if outside_farm:
        return True, 'Geofence Breach', distance
    
    if speed > 8:
        return True, 'High Speed', distance

    # P3 — Erratic is first-class: flag zig-zag even though speed/fence look normal
    if float(heading_variance or 0.0) >= ERRATIC_MEAN_TURN_THRESHOLD:
        return True, 'Erratic Movement', distance

    # P4 — night theft signal (zone-based)
    if is_nighttime and outside_zone:
        return True, 'Night Movement', distance

    # P5 — Isolation Forest for subtler combined patterns
    try:
        ml = anomaly_detector.predict(
            speed=speed,
            hour=sim_hour,
            distance_from_center=distance,
            geofence_radius=raw_radius,
            outside_zone=outside_zone,
            heading_variance=heading_variance,
        )
        if ml.get('is_anomaly'):
            ml_type = ml.get('anomaly_type') or 'Erratic Movement'
            # Live path already decided fence/zone; ignore mismatched rule labels from detector
            if ml_type == 'Night Movement' and (not is_nighttime or not outside_zone):
                return False, None, distance
            if ml_type == 'Geofence Breach' and not outside_farm:
                return False, None, distance
            if ml_type == 'High Speed' and speed <= 8:
                return False, None, distance
            return True, ml_type, distance
    except Exception as ml_err:
        print(f'Live ML skipped: {ml_err}')
    return False, None, distance


# GPS collar ping simulation for animals

@app.route('/api/tracking/live-tick', methods=['POST'])
@role_required(['farmer', 'admin'])
def live_gps_tick():
    """
    One realistic GPS collar ping for every active animal.

    Rules (LIVE_TICK_SECONDS ≈ 5s between calls from the UI):
      ~25% rest, ~55% graze (3–15/18 m), ~15% walk, ~5% anomaly
      Normal steps stay inside the farm polygon/circle fence.
      Erratic Movement uses multi-tick zig-zag + heading_variance for ML.
    """
    global _LIVE_HEADING
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500

    body = request.get_json(silent=True) or {}
    is_nighttime = bool(body.get('night_mode', False))
    sim_hour = 22 if is_nighttime else 12

    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT a.animal_id, a.animal_tag, a.species,
                   a.last_latitude  AS last_lat,
                   a.last_longitude AS last_lng,
                   a.zone_id,
                   z.center_latitude  AS zone_lat,
                   z.center_longitude AS zone_lon,
                   z.radius_meters    AS zone_radius
            FROM animals a
            LEFT JOIN zones z ON a.zone_id = z.zone_id
            WHERE a.user_id=%s AND a.status='Active'
            ORDER BY a.animal_id
        """, (request.user_id,))
        animals = cursor.fetchall()
        if not animals:
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'message': 'No active animals found'}), 404

        ensure_geofences_schema(conn)
        try:
            conn.rollback()
        except Exception:
            pass
        geofence = fetch_user_geofence(cursor, request.user_id)
        geo = farm_geometry(geofence)
        farm_lat, farm_lon, raw_radius = geo['lat'], geo['lon'], geo['radius']

        moved, anomalies = [], []

        for animal in animals:
            aid = animal['animal_id']
            last_lat, last_lon = _pick_live_start(animal, geofence)
            profile = _species_live_profile(animal.get('species'))
            heading = _LIVE_HEADING.get(aid, random.uniform(0, 2 * math.pi))
            prev_heading = heading
            # Default: slight drift (overridden for erratic ticks)
            heading = (heading + random.uniform(-0.6, 0.6)) % (2 * math.pi)

            streak = _LIVE_ERRATIC_STREAK.get(aid, 0)
            roll = random.random()
            lat, lon, speed = last_lat, last_lon, 0.0
            forced_type = None

            p_rest = LIVE_P_REST
            p_graze = p_rest + LIVE_P_GRAZE
            p_walk = p_graze + LIVE_P_WALK
            p_erratic = p_walk + LIVE_P_ERRATIC

            if streak > 0:
                # Continue multi-tick zig-zag episode
                forced_type = 'Erratic Movement'
                turn = random.uniform(math.pi * 0.5, math.pi * 0.9) * random.choice([-1, 1])
                heading = (prev_heading + turn) % (2 * math.pi)
                step = random.uniform(5, 25)
                lat, lon = _offset_meters(last_lat, last_lon, step, heading)
                speed = random.uniform(1.5, 7.5)  # under High Speed threshold
                _LIVE_ERRATIC_STREAK[aid] = streak - 1
            elif roll < p_rest:
                speed = random.uniform(0.0, 0.2)
            elif roll < p_graze:
                step = random.uniform(*profile['graze_m'])
                speed = random.uniform(*profile['graze_kmh'])
                lat, lon = _offset_meters(last_lat, last_lon, step, heading)
            elif roll < p_walk:
                step = random.uniform(*profile['walk_m'])
                speed = random.uniform(*profile['walk_kmh'])
                lat, lon = _offset_meters(last_lat, last_lon, step, heading)
            elif roll < p_erratic:
                # Dedicated Erratic band — short episode (this tick + 1–2 more)
                forced_type = 'Erratic Movement'
                _LIVE_ERRATIC_STREAK[aid] = random.randint(1, 2)
                turn = random.uniform(math.pi * 0.5, math.pi * 0.9) * random.choice([-1, 1])
                heading = (prev_heading + turn) % (2 * math.pi)
                step = random.uniform(5, 25)
                lat, lon = _offset_meters(last_lat, last_lon, step, heading)
                speed = random.uniform(1.5, 7.5)
            else:
                # Rare hard anomalies: High Speed / Breach / Night (no Erratic here)
                choices = ['High Speed', 'Geofence Breach']
                has_zone = (
                    animal.get('zone_lat') is not None
                    and animal.get('zone_lon') is not None
                    and float(animal.get('zone_radius') or 0) > 0
                )
                if is_nighttime and has_zone:
                    choices.append('Night Movement')
                forced_type = random.choice(choices)

                if forced_type == 'High Speed':
                    step = random.uniform(80, 220)
                    heading = random.uniform(0, 2 * math.pi)
                    lat, lon = _offset_meters(last_lat, last_lon, step, heading)
                    speed = random.uniform(9, 25)
                elif forced_type == 'Geofence Breach':
                    lat, lon = random_point_outside_geofence(geofence)
                    speed = random.uniform(2, 10)
                else:
                    zlat = float(animal['zone_lat'])
                    zlon = float(animal['zone_lon'])
                    zr = float(animal['zone_radius'] or 200)
                    placed = False
                    for _ in range(8):
                        ang = random.uniform(0, 2 * math.pi)
                        d = zr * random.uniform(1.2, 1.5)
                        tlat, tlon = _offset_meters(zlat, zlon, d, ang)
                        if is_inside_farm(tlat, tlon, geofence, circle_margin=0.95):
                            lat, lon = tlat, tlon
                            placed = True
                            break
                    if not placed:
                        lat, lon = _offset_meters(
                            zlat, zlon, zr * 1.35, random.uniform(0, 2 * math.pi)
                        )
                    speed = random.uniform(1, 6)

            # Keep normal + erratic movement inside the farm fence
            if forced_type in (None, 'Erratic Movement') and geofence and not is_inside_farm(lat, lon, geofence):
                heading = (heading + math.pi) % (2 * math.pi)
                step_back = random.uniform(3, 12)
                lat, lon = _offset_meters(last_lat, last_lon, step_back, heading)
                if not is_inside_farm(lat, lon, geofence):
                    lat, lon = last_lat, last_lon
                    speed = random.uniform(0.0, 0.3)

            heading_variance = _update_heading_variance(aid, prev_heading, heading)
            _LIVE_HEADING[aid] = heading

            is_anom, anom_type, _dist = _classify_live_point(
                lat, lon, speed, geofence, farm_lat, farm_lon, raw_radius,
                is_nighttime, animal, sim_hour, heading_variance=heading_variance,
            )

            # After an erratic episode ends, wipe turn history so the rolling mean
            # does not keep flagging Erratic for several normal ticks afterward.
            if (
                forced_type == 'Erratic Movement'
                and _LIVE_ERRATIC_STREAK.get(aid, 0) <= 0
            ):
                _LIVE_HEADING_HISTORY[aid] = []
                _LIVE_ERRATIC_ALERTED.discard(aid)

            cursor.execute("""
                INSERT INTO gps_tracking
                    (animal_id, latitude, longitude, speed_kmh, is_anomaly, anomaly_type)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING tracking_id
            """, (aid, lat, lon, round(speed, 2),
                  is_anom, anom_type if is_anom else None))
            tracking_id = cursor.fetchone()['tracking_id']
            cursor.execute("""
                UPDATE animals SET last_latitude=%s, last_longitude=%s WHERE animal_id=%s
            """, (lat, lon, aid))

            moved.append({
                'tracking_id': tracking_id,
                'animal_id': aid,
                'animal_tag': animal['animal_tag'],
                'latitude': lat,
                'longitude': lon,
                'speed_kmh': round(speed, 2),
                'is_anomaly': is_anom,
                'anomaly_type': anom_type if is_anom else None,
                'heading_variance': round(heading_variance, 4),
            })

            if is_anom:
                # One alert (+ email) per Erratic episode — not every zig-zag tick
                if (
                    anom_type == 'Erratic Movement'
                    and aid in _LIVE_ERRATIC_ALERTED
                ):
                    continue

                alert_msg = (
                    f"🚨 {animal['animal_tag']} - {anom_type} detected! "
                    f"Speed: {speed:.1f} km/h"
                )
                cursor.execute("""
                    INSERT INTO alerts
                        (user_id, animal_id, alert_type, alert_message,
                         severity, last_known_lat, last_known_lng)
                    VALUES (%s,%s,%s,%s,'Critical',%s,%s) RETURNING alert_id
                """, (request.user_id, aid, anom_type, alert_msg, lat, lon))
                alert_id = cursor.fetchone()['alert_id']
                if anom_type == 'Erratic Movement':
                    _LIVE_ERRATIC_ALERTED.add(aid)

                # Email for all theft-related anomaly types
                if anom_type in (
                    'Geofence Breach', 'High Speed',
                    'Erratic Movement', 'Night Movement',
                ):
                    cursor.execute(
                        "SELECT email FROM users WHERE user_id=%s", (request.user_id,)
                    )
                    user = cursor.fetchone()
                    if user and user.get('email'):
                        try:
                            notification_service.send_alert(
                                email=user['email'],
                                animal_tag=animal['animal_tag'],
                                anomaly_type=anom_type,
                                location=f"Lat: {lat}, Lon: {lon}",
                                severity="High",
                                details=f"Speed: {speed:.1f} km/h (live tracking)",
                            )
                        except Exception as mail_err:
                            print(f'Live alert email skipped: {mail_err}')
                anomalies.append({
                    'alert_id': alert_id,
                    'animal_id': aid,
                    'animal_tag': animal['animal_tag'],
                    'anomaly_type': anom_type,
                    'latitude': lat,
                    'longitude': lon,
                })

        conn.commit()
        cursor.close(); conn.close()
        print(
            f'[live-tick] user={request.user_id} moved {len(moved)} animals, '
            f'{len(anomalies)} anomalies'
        )
        return jsonify({
            'status': 'success',
            'success': True,
            'message': f'Live tick: {len(moved)} animals',
            'data': {
                'moved': moved,
                'anomalies': anomalies,
                'anomaly_count': len(anomalies),
                'tick_seconds': LIVE_TICK_SECONDS,
                'is_nighttime': is_nighttime,
            },
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            conn.rollback(); conn.close()
        except Exception:
            pass
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/tracking/export', methods=['GET'])
@role_required(['farmer', 'admin'])
def export_tracking_csv():
    """Export GPS tracking history as CSV (REQ-29)."""
    import csv
    import io
    from flask import Response

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT a.animal_tag, a.species, a.breed,
                   g.latitude, g.longitude, g.speed_kmh,
                   g.is_anomaly, g.anomaly_type, g.recorded_at
            FROM gps_tracking g
            JOIN animals a ON a.animal_id = g.animal_id
            WHERE a.user_id = %s
            ORDER BY g.recorded_at DESC
            LIMIT 5000
        """, (request.user_id,))
        rows = cursor.fetchall()
        cursor.close(); conn.close()

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            'animal_tag', 'species', 'breed', 'latitude', 'longitude',
            'speed_kmh', 'is_anomaly', 'anomaly_type', 'recorded_at'
        ])
        for r in rows:
            writer.writerow([
                r.get('animal_tag'),
                r.get('species'),
                r.get('breed'),
                r.get('latitude'),
                r.get('longitude'),
                r.get('speed_kmh'),
                r.get('is_anomaly'),
                r.get('anomaly_type') or '',
                str(r.get('recorded_at') or ''),
            ])

        return Response(
            buf.getvalue(),
            mimetype='text/csv',
            headers={
                'Content-Disposition': 'attachment; filename=agriguard_tracking.csv'
            }
        )
    except Exception as e:
        print(f'Export tracking error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============ BREED IDENTIFICATION ============
# Model: breed_id/models/breed_classifier.onnx (runs on Python 3.14 via onnxruntime).
# Fallback: proxy to BREED_SERVICE_URL if local load fails.

BREED_SERVICE_URL = os.getenv('BREED_SERVICE_URL', 'http://localhost:5001')
_BREED_ID_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'breed_id'))
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _ensure_breed_id_importable():
    """Put AgriGuard root on sys.path so `import breed_id` works."""
    import sys
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)


def _breed_identify_local(file_bytes: bytes, species: str | None = None):
    """In-process identify using breed_id (ONNX on Py3.14, or TF if available)."""
    _ensure_breed_id_importable()
    from breed_id import identify_breed_from_photo, model_predict_fn
    return identify_breed_from_photo(file_bytes, model_predict_fn, species=species)


def _breed_identify_proxy(file_bytes: bytes, filename: str, content_type: str):
    """Optional forward to standalone breed_id server."""
    import requests as _requests
    files = {
        'image': (filename or 'photo.jpg', file_bytes, content_type or 'image/jpeg')
    }
    r = _requests.post(
        f"{BREED_SERVICE_URL.rstrip('/')}/api/breed/identify",
        files=files,
        timeout=60,
    )
    try:
        data = r.json()
    except Exception:
        data = {'success': False, 'error': 'Breed service returned invalid JSON'}
    return data, r.status_code


@app.route('/api/breed/supported', methods=['GET'])
@role_required(['farmer', 'admin', 'buyer'])
def api_breed_supported():
    """List breeds the CNN can classify."""
    try:
        _ensure_breed_id_importable()
        from breed_id import load_class_names, get_backend, BREED_CARE_LIBRARY, PHOTO_TIPS, RELATED_BREEDS
        data = []
        for name in load_class_names():
            care = BREED_CARE_LIBRARY.get(name, {})
            data.append({
                'breed_name': name,
                'species': care.get('species'),
                'in_model': True,
                'related_breeds': RELATED_BREEDS.get(name, []),
            })
        return jsonify({
            'status': 'success',
            'success': True,
            'data': data,
            'backend': get_backend(),
            'class_count': len(data),
            'photo_tips': PHOTO_TIPS,
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'success': False,
            'message': 'Breed model unavailable. Install: pip install onnxruntime Pillow',
            'detail': str(e),
        }), 503


@app.route('/api/breed/identify', methods=['POST'])
@role_required(['farmer', 'admin'])
def api_breed_identify():
    """
    Upload livestock photo → top-3 breed predictions + care tips (REQ-31–40).
    multipart field: image (JPEG/PNG, max 5MB)
    optional form field: species (Cattle|Sheep|Goat) to refine lookalikes
    """
    f = request.files.get('image') or request.files.get('file')
    if not f or not f.filename:
        return jsonify({
            'status': 'error', 'success': False,
            'message': 'No image uploaded. Use form field "image".'
        }), 400

    file_bytes = f.read()
    if not file_bytes:
        return jsonify({
            'status': 'error', 'success': False,
            'message': 'Empty image upload'
        }), 400
    if len(file_bytes) > 5 * 1024 * 1024:
        return jsonify({
            'status': 'error', 'success': False,
            'message': 'Image too large. Maximum size is 5MB.'
        }), 400

    species = (request.form.get('species') or request.args.get('species') or '').strip() or None

    # Prefer local ONNX (Python 3.14). Proxy only as fallback.
    try:
        result = _breed_identify_local(file_bytes, species=species)
    except Exception as local_err:
        print(f'Breed local identify failed: {local_err}')
        try:
            result, status = _breed_identify_proxy(
                file_bytes, f.filename, f.mimetype or 'image/jpeg'
            )
            if not result.get('success'):
                return jsonify({
                    'status': 'error',
                    'success': False,
                    'message': result.get('error') or 'Breed identification failed',
                    'data': result,
                }), status if status >= 400 else 400
            return jsonify({
                'status': 'success',
                'success': True,
                'message': 'Breed identification complete',
                'data': result,
            })
        except Exception as proxy_err:
            detail = f'{local_err} | proxy: {proxy_err}'
            print(f'Breed identify failed: {detail}')
            return jsonify({
                'status': 'error',
                'success': False,
                'message': (
                    'Breed model could not load. Restart Flask with the project venv '
                    '(d\\PROJECT\\AGRIGUARD\\venv\\Scripts\\python.exe Backend\\app.py). '
                    'If packages are missing: pip install onnxruntime Pillow'
                ),
                'detail': detail,
            }), 503

    if not result or not result.get('success'):
        return jsonify({
            'status': 'error',
            'success': False,
            'message': (result or {}).get('error') or 'Breed identification failed',
            'data': result,
        }), 400

    return jsonify({
        'status': 'success',
        'success': True,
        'message': 'Breed identification complete',
        'data': result,
    })


# ============ TRACKING ALERTS ============

@app.route('/api/tracking/alerts', methods=['GET'])
@role_required(['farmer', 'admin'])
def get_tracking_alerts():
    """Return recent anomaly alerts for the logged-in user (newest first)."""
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT al.alert_id, al.user_id, al.animal_id, al.alert_type,
                   al.alert_message, al.severity, al.is_resolved,
                   al.last_known_lat, al.last_known_lng, al.created_at,
                   a.animal_tag
            FROM alerts al
            LEFT JOIN animals a ON a.animal_id = al.animal_id
            WHERE al.user_id = %s
            ORDER BY al.created_at DESC NULLS LAST, al.alert_id DESC
            LIMIT 100
        """, (request.user_id,))
        rows = cursor.fetchall()
        cursor.close(); conn.close()

        for r in rows:
            if r.get('created_at') is not None:
                r['created_at'] = str(r['created_at'])
            if r.get('last_known_lat') is not None:
                r['last_known_lat'] = float(r['last_known_lat'])
            if r.get('last_known_lng') is not None:
                r['last_known_lng'] = float(r['last_known_lng'])
            r['is_resolved'] = bool(r.get('is_resolved'))

        return jsonify({'status': 'success', 'success': True, 'data': rows})
    except Exception as e:
        print(f'Get tracking alerts error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/tracking/alerts/<int:alert_id>/resolve', methods=['PUT'])
@role_required(['farmer', 'admin'])
def resolve_tracking_alert(alert_id):
    """
    Mark an alert as resolved.

    Night Movement / Geofence Breach / High Speed:
      return animal to its zone (or farm fence) + normal GPS ping (green).
    Erratic Movement:
      keep current position; write a normal GPS ping so the node goes green.
    """
    global _LIVE_HEADING, _LIVE_HEADING_HISTORY, _LIVE_ERRATIC_STREAK, _LIVE_ERRATIC_ALERTED
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500

    REPOSITION_TYPES = {'Night Movement', 'Geofence Breach', 'High Speed'}
    STAY_PUT_TYPES = {'Erratic Movement'}

    try:
        ensure_geofences_schema(conn)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT alert_id, animal_id, alert_type, is_resolved
            FROM alerts
            WHERE alert_id = %s AND user_id = %s
        """, (alert_id, request.user_id))
        alert = cursor.fetchone()
        if not alert:
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'message': 'Alert not found'}), 404

        cursor.execute("""
            UPDATE alerts
            SET is_resolved = TRUE
            WHERE alert_id = %s AND user_id = %s
        """, (alert_id, request.user_id))

        repositioned = False
        cleared_normal = False
        new_lat = new_lon = None
        animal_id = alert.get('animal_id')
        alert_type = (alert.get('alert_type') or '').strip()

        if animal_id and alert_type in (REPOSITION_TYPES | STAY_PUT_TYPES):
            cursor.execute("""
                SELECT a.animal_id, a.last_latitude, a.last_longitude,
                       z.center_latitude, z.center_longitude, z.radius_meters
                FROM animals a
                LEFT JOIN zones z ON a.zone_id = z.zone_id
                WHERE a.animal_id = %s AND a.user_id = %s AND a.status = 'Active'
            """, (animal_id, request.user_id))
            animal = cursor.fetchone()

            if animal:
                lat = lon = None

                if alert_type in REPOSITION_TYPES:
                    geofence = fetch_user_geofence(cursor, request.user_id)
                    geo = farm_geometry(geofence)
                    geofence_radius = geo['radius']

                    if animal.get('center_latitude') is not None:
                        clat = float(animal['center_latitude'])
                        clon = float(animal['center_longitude'])
                        zr = float(animal['radius_meters'] or 200)
                        if is_inside_farm(clat, clon, geofence, circle_margin=0.85):
                            for _ in range(14):
                                ang = random.uniform(0, 2 * math.pi)
                                d = random.uniform(
                                    0, min(zr * 0.55, max(geofence_radius * 0.35, 40))
                                )
                                tlat, tlon = _offset_meters(clat, clon, d, ang)
                                if (
                                    is_inside_farm(tlat, tlon, geofence, circle_margin=0.95)
                                    and calculate_distance(tlat, tlon, clat, clon) <= zr * 0.95
                                ):
                                    lat, lon = tlat, tlon
                                    break
                    if lat is None:
                        lat, lon = random_point_in_geofence(geofence)
                    repositioned = True
                else:
                    # Erratic: stay where they are
                    if (
                        animal.get('last_latitude') is not None
                        and animal.get('last_longitude') is not None
                    ):
                        lat = float(animal['last_latitude'])
                        lon = float(animal['last_longitude'])
                    else:
                        geofence = fetch_user_geofence(cursor, request.user_id)
                        lat, lon = random_point_in_geofence(geofence)

                cursor.execute("""
                    INSERT INTO gps_tracking
                        (animal_id, latitude, longitude, speed_kmh, is_anomaly, anomaly_type)
                    VALUES (%s,%s,%s,0,FALSE,NULL)
                """, (animal_id, lat, lon))
                cursor.execute("""
                    UPDATE animals
                    SET last_latitude=%s, last_longitude=%s
                    WHERE animal_id=%s AND user_id=%s
                """, (lat, lon, animal_id, request.user_id))

                cursor.execute("""
                    UPDATE alerts
                    SET is_resolved = TRUE
                    WHERE user_id = %s
                      AND animal_id = %s
                      AND alert_type = %s
                      AND is_resolved = FALSE
                """, (request.user_id, animal_id, alert_type))

                _LIVE_HEADING.pop(animal_id, None)
                _LIVE_HEADING_HISTORY.pop(animal_id, None)
                _LIVE_ERRATIC_STREAK.pop(animal_id, None)
                _LIVE_ERRATIC_ALERTED.discard(animal_id)

                cleared_normal = True
                new_lat, new_lon = lat, lon

        conn.commit()
        cursor.close(); conn.close()

        if repositioned:
            msg = 'Alert resolved — animal returned to its zone'
        elif cleared_normal and alert_type == 'Erratic Movement':
            msg = 'Alert resolved — animal stayed in place (normal status)'
        else:
            msg = 'Alert resolved'

        return jsonify({
            'status': 'success',
            'success': True,
            'message': msg,
            'repositioned': repositioned,
            'animal_id': animal_id,
            'latitude': new_lat,
            'longitude': new_lon,
        })
    except Exception as e:
        print(f'Resolve alert error: {e}')
        try:
            conn.rollback(); conn.close()
        except Exception:
            pass
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============ ANOMALY DETECTION ROUTES ============

@app.route('/api/anomaly/detect', methods=['POST'])
@token_required
def detect_anomaly():
    """Detect anomalies for a specific animal and send alerts"""
    try:
        data = request.get_json()
        
        if not data or 'animal_tag' not in data:
            return jsonify({'message': 'Animal tag is required'}), 400
        
        animal_tag = data['animal_tag']
        farmer_email = data.get('email')  # Get farmer's email from request
        anomaly_type = data.get('anomaly_type', 'Irregular movement pattern')
        location = data.get('location', 'Unknown location')
        severity = data.get('severity', 'High')
        details = data.get('details', '')
        

        anomaly_result = {
            'animal_tag': animal_tag,
            'anomaly_detected': True,
            'anomaly_type': anomaly_type,
            'severity': severity,
            'location': location,
            'confidence': 0.92,
            'timestamp': datetime.now().isoformat()
        }
        
        # Send email alert if anomaly detected and farmer email is provided
        alert_sent = False
        if anomaly_result['anomaly_detected'] and farmer_email:
            alert_sent = handle_anomaly(
                animal_tag=animal_tag,
                anomaly_type=anomaly_type,
                location=location,
                farmer_email=farmer_email,
                severity=severity,
                details=details or f"Anomaly detected with {anomaly_result['confidence']*100}% confidence"
            )
        
        return jsonify({
            'success': True,
            'data': anomaly_result,
            'alert_sent': alert_sent,
            'message': 'Alert sent to farmer' if alert_sent else 'No alert sent (email not provided)'
        }), 200
        
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@app.route('/api/notification/test', methods=['POST'])
def test_notification():
    """Test email notification only"""
    try:
        data = request.get_json()
        
        if not data or 'email' not in data:
            return jsonify({'message': 'Email address is required'}), 400
        
        email = data.get('email')
        animal_tag = data.get('animal_tag', 'TEST123')
        
        # Send test email
        result = notification_service.send_email_alert(
            email=email,
            animal_tag=animal_tag,
            anomaly_type='Test Alert',
            location='Test Location',
            severity='Low',
            details='This is a test notification from AgriGuard system.'
        )
        
        return jsonify({
            'success': result,
            'message': 'Test email sent' if result else 'Failed to send email',
            'email': email
        }), 200 if result else 500
        
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@app.route('/api/tracking/reset-positions', methods=['POST'])
@role_required(['farmer', 'admin'])
def reset_animal_positions():
    """Reset ALL animals to inside their zones (within geofence)."""
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        ensure_geofences_schema(conn)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        geofence = fetch_user_geofence(cursor, request.user_id)
        geo = farm_geometry(geofence)
        farm_lat        = geo['lat']
        farm_lon        = geo['lon']
        geofence_radius = geo['radius']

        cursor.execute("""
            SELECT a.animal_id, a.zone_id,
                   z.center_latitude, z.center_longitude, z.radius_meters
            FROM animals a
            LEFT JOIN zones z ON a.zone_id=z.zone_id
            WHERE a.user_id=%s AND a.status='Active'
        """, (request.user_id,))
        animals_list = cursor.fetchall()
        reset_count  = 0

        for animal in animals_list:
            lat = lon = None

            if animal['center_latitude'] is not None:
                clat = float(animal['center_latitude'])
                clon = float(animal['center_longitude'])
                zr   = float(animal['radius_meters'] or 200)
                zone_ok = is_inside_farm(clat, clon, geofence, circle_margin=0.85)

                if zone_ok:
                    for _ in range(14):
                        ang  = random.uniform(0, 2 * math.pi)
                        d    = random.uniform(0, min(zr * 0.6, geofence_radius * 0.4))
                        tlat = clat + (d / 111320) * math.cos(ang)
                        tlon = clon + (d / (111320 * math.cos(math.radians(clat)))) * math.sin(ang)
                        if is_inside_farm(tlat, tlon, geofence, circle_margin=0.95):
                            lat, lon = tlat, tlon
                            break

            if lat is None:
                lat, lon = random_point_in_geofence(geofence)

            cursor.execute("""
                INSERT INTO gps_tracking
                    (animal_id, latitude, longitude, speed_kmh, is_anomaly, anomaly_type)
                VALUES (%s,%s,%s,0,FALSE,NULL)
            """, (animal['animal_id'], lat, lon))
            cursor.execute("""
                UPDATE animals SET last_latitude=%s, last_longitude=%s WHERE animal_id=%s
            """, (lat, lon, animal['animal_id']))
            reset_count += 1

        conn.commit(); cursor.close(); conn.close()
        return jsonify({
            'status': 'success', 'success': True,
            'reset_count': reset_count,
            'message': f'✅ {reset_count} animals reset inside geofence',
        })
    except Exception as e:
        print(f'Reset positions error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/tracking/init-positions', methods=['POST'])
@role_required(['farmer', 'admin'])
def init_animal_positions():
    """
    Place animals that have no GPS history OR are outside the geofence
    back inside their zone (or inside the geofence if the zone is outside it).
    Called automatically on every page load.
    """
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        ensure_geofences_schema(conn)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        geofence = fetch_user_geofence(cursor, request.user_id)
        geo = farm_geometry(geofence)
        farm_lat        = geo['lat']
        farm_lon        = geo['lon']
        geofence_radius = geo['radius']

        cursor.execute("""
            SELECT a.animal_id, a.zone_id,
                   a.last_latitude, a.last_longitude,
                   z.center_latitude, z.center_longitude,
                   z.radius_meters AS zone_radius
            FROM animals a
            LEFT JOIN zones z ON a.zone_id=z.zone_id
            WHERE a.user_id=%s AND a.status='Active'
        """, (request.user_id,))
        all_animals = cursor.fetchall()
        placed      = 0

        for a in all_animals:
            needs_placement = False
            if a['last_latitude'] is None or a['last_longitude'] is None:
                needs_placement = True
            else:
                if not is_inside_farm(
                    float(a['last_latitude']), float(a['last_longitude']),
                    geofence, circle_margin=1.05
                ):
                    needs_placement = True

            if not needs_placement:
                continue

            lat = lon = None

            if a['center_latitude'] is not None:
                clat           = float(a['center_latitude'])
                clon           = float(a['center_longitude'])
                zr             = float(a['zone_radius'] or 200)
                if is_inside_farm(clat, clon, geofence, circle_margin=0.85):
                    for _ in range(14):
                        ang  = random.uniform(0, 2 * math.pi)
                        d    = random.uniform(0, min(zr * 0.6, geofence_radius * 0.4))
                        tlat = clat + (d / 111320) * math.cos(ang)
                        tlon = clon + (d / (111320 * math.cos(math.radians(clat)))) * math.sin(ang)
                        if is_inside_farm(tlat, tlon, geofence, circle_margin=0.95):
                            lat, lon = tlat, tlon
                            break

            if lat is None:
                lat, lon = random_point_in_geofence(geofence)

            cursor.execute("""
                INSERT INTO gps_tracking
                    (animal_id, latitude, longitude, speed_kmh, is_anomaly, anomaly_type)
                VALUES (%s,%s,%s,0,FALSE,NULL)
            """, (a['animal_id'], lat, lon))
            cursor.execute("""
                UPDATE animals SET last_latitude=%s, last_longitude=%s WHERE animal_id=%s
            """, (lat, lon, a['animal_id']))
            placed += 1

        conn.commit(); cursor.close(); conn.close()
        return jsonify({
            'status': 'success', 'success': True,
            'placed':  placed,
            'message': f'{placed} animals repositioned inside geofence',
        })
    except Exception as e:
        print(f'Init positions error: {e}')
        try:
            conn.rollback(); conn.close()
        except Exception:
            pass
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============ AI CHAT ============

def _chat_animals_for_user(user_id):
    """Compact herd snapshot for chat context."""
    conn = get_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT animal_tag, species, breed, gender, status, weight_kg
            FROM animals
            WHERE user_id = %s AND status = 'Active'
            ORDER BY animal_id
            LIMIT 25
        """, (user_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f'Chat animals context error: {e}')
        try:
            conn.close()
        except Exception:
            pass
        return []


@app.route('/api/chat/status', methods=['GET'])
@role_required(['farmer', 'admin', 'buyer'])
def api_chat_status():
    """Whether Gemini/OpenAI is configured for AI chat."""
    return jsonify({
        'status': 'success',
        'success': True,
        'data': chat_service.status(),
    })


@app.route('/api/chat', methods=['POST'])
@role_required(['farmer', 'admin', 'buyer'])
def api_chat():
    """
    Livestock AI assistant.
    Body: { message, language?: EN|ZU|ST|AF, history?: [{role,text}] }
    """
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or data.get('question') or '').strip()
    language = (data.get('language') or 'EN').upper()
    history = data.get('history') or []
    if not isinstance(history, list):
        history = []

    if language not in ('EN', 'ZU', 'ST', 'AF'):
        language = 'EN'

    animals = _chat_animals_for_user(request.user_id) if request.user_role in ('farmer', 'admin') else []
    result = chat_service.chat(
        message=message,
        language=language,
        animals=animals,
        history=history,
    )

    if not result.get('success'):
        code = 503 if result.get('code') == 'AI_NOT_CONFIGURED' else 502
        return jsonify({
            'status': 'error',
            'success': False,
            'message': result.get('error') or 'Chat failed',
            'code': result.get('code'),
        }), code

    return jsonify({
        'status': 'success',
        'success': True,
        'data': {
            'reply': result['reply'],
            'provider': result.get('provider'),
            'language': result.get('language'),
            'animals_in_context': len(animals),
        },
    })


# ============ ERROR HANDLERS ============

@app.errorhandler(404)
def not_found(error):
    return jsonify({'message': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'message': 'Internal server error'}), 500

# ============================================
# 18. RUN
# ============================================
def print_routes():
    print('\nRegistered routes:')
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r)):
        methods = ','.join(sorted(m for m in rule.methods if m not in ('HEAD', 'OPTIONS')))
        print(f'  {methods:<20} {rule}')
    print()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print('=' * 50)
    print('AgriGuard Flask API')
    print(f'Running on: http://localhost:{port}')
    print(f'Test DB:    http://localhost:{port}/api/test-db')
    print('=' * 50)
    print_routes()
    app.run(debug=True, host='0.0.0.0', port=port)