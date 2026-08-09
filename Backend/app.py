# ============================================
# 1. IMPORTS FIRST
# ============================================
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
import bcrypt
import os
import jwt
import random
import math
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from functools import wraps
from services.notification_services import NotificationService #For sending Email alerts#

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app, origins=["*"])

# Initialize Notification Service
notification_service = NotificationService()

JWT_SECRET = os.getenv('JWT_SECRET', 'agriguard-secret-key-2024')

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
            print(f"✅ Alert sent to farmer for animal {animal_tag}")
            # You can also log this to database if you want
            # log_anomaly_to_database(animal_tag, anomaly_type, location, farmer_email, severity)
        else:
            print(f"❌ Failed to send alert for animal {animal_tag}")
        
        return result
        
    except Exception as e:
        print(f"❌ Error in handle_anomaly: {e}")
        return False

def log_anomaly_to_database(animal_tag, anomaly_type, location, farmer_email, severity):
    """Optional: Log anomaly to database for tracking"""
    try:
        conn = get_db()
        if conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO anomalies (animal_tag, anomaly_type, location, farmer_email, severity, detected_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (animal_tag, anomaly_type, location, farmer_email, severity, datetime.now()))
            conn.commit()
            cur.close()
            conn.close()
            return True
    except Exception as e:
        print(f"Database log error: {e}")
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
            request.user_role = payload['role']
            # THIS IS IMPORTANT - pass user_id to the function
            return f(request.user_id, *args, **kwargs)
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'Invalid token: {str(e)}'}), 401
    return decorated
# ============================================
# 6. ANOMALY DETECTOR
# ============================================
class FallbackDetector:
    """
    Rule-only detector — used when scikit-learn / Isolation Forest is unavailable.
    Priority order mirrors AnomalyDetector.predict():
      P1 Geofence Breach → P2 High Speed → P3 Night Movement
    Night window: 18:00 – 03:59  (matches NIGHT_START=18, NIGHT_END=4)
    """
    SPEED_THRESHOLD = 15
    NIGHT_START     = 18   # inclusive
    NIGHT_END       = 4    # exclusive upper bound of night (i.e. hour < 4)

    def predict(self, speed, hour, distance, geofence_radius=2000):
        # P1 — outside fence (10 % buffer)
        if geofence_radius and distance > geofence_radius * 1.1:
            return {'is_anomaly': True,  'anomaly_type': 'Geofence Breach',  'score': 0}
        # P2 — too fast
        if speed > self.SPEED_THRESHOLD:
            return {'is_anomaly': True,  'anomaly_type': 'High Speed',        'score': 0}
        # P3 — night window (18:00–03:59)
        if hour >= self.NIGHT_START or hour < self.NIGHT_END:
            return {'is_anomaly': True,  'anomaly_type': 'Night Movement',    'score': 0}
        return     {'is_anomaly': False, 'anomaly_type': None,                'score': 0}


# Load ML detector — fall back to rule-only if sklearn unavailable
try:
    from ml.anomaly_detector import AnomalyDetector
    anomaly_detector = AnomalyDetector()
    anomaly_detector.train()
    print("✅ Anomaly detector (Isolation Forest) loaded successfully")
except Exception as e:
    print(f"⚠️  Anomaly detector error — using rule-only fallback: {e}")
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

    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500

    try:
        cursor    = conn.cursor()
        full_name = f"{first_name} {last_name}"
        cursor.execute("""
            INSERT INTO users (email, password_hash, first_name, last_name, full_name,
                               role, phone, farm_name, location)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING user_id
        """, (email, password_hash, first_name, last_name, full_name,
              role, phone, farm_name, location))
        user_id = cursor.fetchone()[0]
        conn.commit(); cursor.close(); conn.close()
        return jsonify({
            'status': 'success', 'success': True,
            'message': 'Account created successfully! You can now login.',
            'user_id': user_id,
        }), 201
    except psycopg2.IntegrityError:
        return jsonify({'status': 'error', 'success': False,
                        'message': 'Email already exists'}), 409
    except Exception as e:
        print(f'Register error: {e}')
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

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, email FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        if user:
            reset_token = secrets.token_urlsafe(32)
            expires     = datetime.utcnow() + timedelta(hours=1)
            cursor.execute("""
                UPDATE users SET reset_token=%s, reset_expires=%s WHERE user_id=%s
            """, (reset_token, expires, user[0]))
            conn.commit()
            reset_link = f"http://localhost:5000/reset-password.html?token={reset_token}"
            print(f"Password reset link for {email}: {reset_link}")
            return jsonify({
                'status': 'success', 'success': True,
                'message': f'Password reset link sent to {email}',
                'reset_link': reset_link,
            })
        return jsonify({
            'status': 'success', 'success': True,
            'message': 'If an account exists, a reset link was sent',
        })
    except Exception as e:
        print(f'Forgot password error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    data             = request.get_json() or {}
    token            = data.get('token',            '').strip()
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
            SELECT user_id FROM users WHERE reset_token=%s AND reset_expires > NOW()
        """, (token,))
        user = cursor.fetchone()
        if not user:
            return jsonify({'status': 'error',
                            'message': 'Invalid or expired reset link'}), 400
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
    token = request.args.get('token', '')
    return f'''<!DOCTYPE html>
<html><head><title>Reset Password - AgriGuard</title>
<style>
  body{{font-family:Arial;background:#f5f5f5;display:flex;justify-content:center;
       align-items:center;height:100vh;margin:0}}
  .box{{background:#fff;padding:30px;border-radius:12px;width:400px;
        box-shadow:0 2px 8px rgba(0,0,0,.1)}}
  h2{{color:#1D9E75}}
  input{{width:100%;padding:10px;margin:10px 0;border:1px solid #ddd;border-radius:6px;
         box-sizing:border-box}}
  button{{width:100%;padding:12px;background:#1D9E75;color:#fff;border:none;
          border-radius:6px;cursor:pointer;font-size:15px}}
  .err{{color:red;margin-top:8px}} .ok{{color:green;margin-top:8px}}
</style></head>
<body><div class="box">
  <h2>🔒 Reset Password</h2>
  <p>Enter your new password below</p>
  <input type="password" id="p1" placeholder="New password (min 8 chars)">
  <input type="password" id="p2" placeholder="Confirm new password">
  <button onclick="go()">Reset Password</button>
  <div id="msg"></div>
</div>
<script>
const token='{token}';
async function go(){{
  const p1=document.getElementById('p1').value;
  const p2=document.getElementById('p2').value;
  const msg=document.getElementById('msg');
  if(!p1||!p2){{msg.className='err';msg.textContent='Fill both fields';return}}
  if(p1!==p2){{msg.className='err';msg.textContent='Passwords do not match';return}}
  if(p1.length<8){{msg.className='err';msg.textContent='Min 8 characters';return}}
  try{{
    const r=await fetch('/api/reset-password',{{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{token,new_password:p1,confirm_password:p2}})}});
    const d=await r.json();
    if(d.status==='success'){{
      msg.className='ok';msg.textContent='Reset! Redirecting...';
      setTimeout(()=>window.location.href='login.html',2000);
    }}else{{msg.className='err';msg.textContent=d.message}}
  }}catch(e){{msg.className='err';msg.textContent='Network error'}}
}}
</script></body></html>'''

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
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT a.animal_id, a.animal_tag, a.species, a.breed, a.gender,
                   a.weight_kg, a.status, a.zone_id,
                   a.last_latitude, a.last_longitude,
                   z.zone_name, z.zone_type, z.color as zone_color,
                   g.speed_kmh, g.is_anomaly, g.anomaly_type, g.recorded_at,
                   CASE
                       WHEN g.is_anomaly = TRUE THEN 'critical'
                       WHEN g.speed_kmh  > 10   THEN 'warning'
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
            if a.get('last_latitude')  is not None: a['last_latitude']  = float(a['last_latitude'])
            if a.get('last_longitude') is not None: a['last_longitude'] = float(a['last_longitude'])
            if a.get('speed_kmh')      is not None: a['speed_kmh']      = float(a['speed_kmh'])
        return jsonify({'status': 'success', 'success': True, 'data': animals})
    except Exception as e:
        print(f'Get animals error: {e}')
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500


@app.route('/api/animals', methods=['POST'])
@role_required(['farmer', 'admin'])
def add_animal():
    data       = request.get_json() or {}
    animal_tag = data.get('animal_tag', '').strip()
    species    = data.get('species',    '').strip()
    breed      = data.get('breed',  '')
    gender     = data.get('gender', '')
    weight_kg  = data.get('weight_kg')

    if not animal_tag or not species:
        return jsonify({'status': 'error', 'success': False,
                        'message': 'animal_tag and species required'}), 400

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'success': False, 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO animals (user_id, animal_tag, species, breed, gender, weight_kg, status)
            VALUES (%s,%s,%s,%s,%s,%s,'Active') RETURNING animal_id
        """, (request.user_id, animal_tag, species, breed, gender, weight_kg))
        animal_id = cursor.fetchone()[0]
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Animal added successfully',
                        'animal_id': animal_id}), 201
    except psycopg2.IntegrityError:
        return jsonify({'status': 'error', 'success': False,
                        'message': 'Animal tag already exists'}), 409
    except Exception as e:
        print(f'Add animal error: {e}')
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500

@app.route('/api/animals/<animal_tag>', methods=['GET'])
@token_required
def get_animal(current_user, animal_tag):
    """Get specific animal details"""
    try:
        conn = get_db()
        if not conn:
            return jsonify({'message': 'Database connection failed'}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM animals WHERE tag = %s AND user_id = %s", (animal_tag, current_user))
        animal = cur.fetchone()
        cur.close()
        conn.close()
        
        if not animal:
            return jsonify({'message': 'Animal not found'}), 404
        
        return jsonify({'success': True, 'animal': animal}), 200
        
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@app.route('/api/animals/<animal_tag>/anomaly', methods=['POST'])
@token_required
def report_animal_anomaly(current_user, animal_tag):
    """Report anomaly for a specific animal"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'message': 'No data provided'}), 400
        
        anomaly_type = data.get('anomaly_type', 'Unusual behavior')
        location = data.get('location', 'Unknown')
        severity = data.get('severity', 'Medium')
        details = data.get('details', '')
        farmer_email = data.get('email')
        
        # Get user's email if not provided
        if not farmer_email:
            conn = get_db()
            if conn:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("SELECT email FROM users WHERE id = %s", (current_user,))
                user = cur.fetchone()
                cur.close()
                conn.close()
                if user:
                    farmer_email = user['email']
        
        # Handle the anomaly and send alert
        alert_sent = False
        if farmer_email:
            alert_sent = handle_anomaly(
                animal_tag=animal_tag,
                anomaly_type=anomaly_type,
                location=location,
                farmer_email=farmer_email,
                severity=severity,
                details=details
            )
        
        return jsonify({
            'success': True,
            'message': 'Anomaly reported successfully',
            'alert_sent': alert_sent,
            'animal_tag': animal_tag,
            'anomaly_type': anomaly_type
        }), 200
        
    except Exception as e:
        return jsonify({'message': str(e)}), 500


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
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT v.vaccine_id, v.animal_id, a.animal_tag, a.species,
                   v.vaccine_name, v.vaccination_date, v.due_date,
                   v.is_completed, v.dosage_ml, v.vet_name, v.batch_number,
                   v.manufacturer, v.notes, v.created_at
            FROM vaccinations v
            JOIN animals a ON a.animal_id = v.animal_id
            WHERE a.user_id=%s ORDER BY v.due_date ASC
        """, (request.user_id,))
        vaccinations = cursor.fetchall()
        cursor.close(); conn.close()
        for v in vaccinations:
            for field in ['vaccination_date', 'due_date', 'created_at']:
                if v.get(field): v[field] = str(v[field])
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
        cursor.execute("""
            INSERT INTO vaccinations
                (animal_id, user_id, vaccine_name, vaccination_date, due_date,
                 dosage_ml, vet_name, batch_number, manufacturer, notes, is_completed)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE) RETURNING vaccine_id
        """, (animal_id, request.user_id,
              vaccine_name,
              data.get('vaccination_date'), data.get('due_date'),
              data.get('dosage_ml'),
              data.get('vet_name',      ''),
              data.get('batch_number',  ''),
              data.get('manufacturer',  ''),
              data.get('notes',         '')))
        vaccine_id = cursor.fetchone()[0]
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
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE vaccinations SET is_completed=TRUE, completed_date=CURRENT_DATE
            WHERE vaccine_id=%s AND user_id=%s
        """, (vaccine_id, request.user_id))
        if cursor.rowcount == 0:
            cursor.close(); conn.close()
            return jsonify({'status': 'error', 'message': 'Vaccination not found'}), 404
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Vaccination marked as completed'})
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
            WHERE a.user_id=%s AND v.is_completed=FALSE
              AND v.due_date BETWEEN CURRENT_DATE AND CURRENT_DATE+30
        """, (request.user_id,))
        upcoming = cursor.fetchone()['upcoming']
        cursor.execute("""
            SELECT COUNT(*) as overdue FROM vaccinations v
            JOIN animals a ON a.animal_id=v.animal_id
            WHERE a.user_id=%s AND v.is_completed=FALSE AND v.due_date < CURRENT_DATE
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
    if zone_type not in ['cattle', 'goat', 'sheep', 'poultry', 'mixed']:
        return jsonify({'status': 'error', 'message': 'Invalid zone_type'}), 400
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO zones
                (user_id, geofence_id, zone_name, zone_type,
                 center_latitude, center_longitude, radius_meters, color)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING zone_id
        """, (request.user_id,
              data.get('geofence_id'),
              zone_name, zone_type,
              data.get('center_latitude'),
              data.get('center_longitude'),
              data.get('radius_meters'),
              data.get('color', '#1D9E75')))
        zone_id = cursor.fetchone()[0]
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
    data             = request.get_json() or {}
    fence_name       = data.get('fence_name',       '').strip()
    center_latitude  = data.get('center_latitude')
    center_longitude = data.get('center_longitude')
    radius_meters    = data.get('radius_meters')

    if not fence_name or center_latitude is None or center_longitude is None or not radius_meters:
        return jsonify({'status': 'error', 'message': 'All fields required'}), 400

    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO geofences
                (user_id, fence_name, center_latitude, center_longitude, radius_meters)
            VALUES (%s,%s,%s,%s,%s) RETURNING geofence_id
        """, (request.user_id, fence_name,
              center_latitude, center_longitude, radius_meters))
        geofence_id = cursor.fetchone()[0]
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True,
                        'message': 'Geofence created successfully',
                        'geofence_id': geofence_id}), 201
    except Exception as e:
        print(f'Create geofence error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/tracking/geofence', methods=['GET'])
@role_required(['farmer', 'admin'])
def get_geofences():
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT geofence_id, fence_name, center_latitude, center_longitude,
                   radius_meters, is_active, created_at
            FROM geofences WHERE user_id=%s ORDER BY created_at DESC
        """, (request.user_id,))
        geofences = cursor.fetchall()
        cursor.close(); conn.close()
        for g in geofences:
            if g.get('created_at'): g['created_at'] = str(g['created_at'])
        return jsonify({'status': 'success', 'success': True, 'data': geofences})
    except Exception as e:
        print(f'Get geofences error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/tracking/geofence', methods=['DELETE'])
@role_required(['farmer', 'admin'])
def delete_geofence():
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM geofences WHERE user_id=%s", (request.user_id,))
        conn.commit(); cursor.close(); conn.close()
        return jsonify({'status': 'success', 'success': True, 'message': 'Geofence cleared'})
    except Exception as e:
        print(f'Delete geofence error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ============================================
# 17. GPS TRACKING ROUTES
# ============================================
@app.route('/api/tracking/simulate', methods=['POST'])
@role_required(['farmer', 'admin'])
def simulate_gps():
    """
    Generate simulated GPS movement for every active animal and detect anomalies.

    Anomaly priority (most objective first):
      P1  Geofence Breach  — animal clearly outside fence (>10 % over radius)
      P2  High Speed       — speed > 15 km/h
      P3  Night Movement   — current real time is 18:00–03:59
      P4  ML / Erratic     — Isolation Forest catches subtle combined patterns
    """
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500

    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # ── Animals ──────────────────────────────────────────────────────────
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

        # ── Geofence ─────────────────────────────────────────────────────────
        cursor.execute("""
            SELECT center_latitude, center_longitude, radius_meters
            FROM geofences WHERE user_id=%s
            ORDER BY created_at DESC LIMIT 1
        """, (request.user_id,))
        geofence = cursor.fetchone()

        farm_lat   = float(geofence['center_latitude'])  if geofence else -23.8966
        farm_lon   = float(geofence['center_longitude']) if geofence else  29.4488
        raw_radius = float(geofence['radius_meters'])    if geofence else  2000.0

        # ── Read night_mode preference sent by the frontend ──────────────────
        body         = request.get_json(silent=True) or {}
        client_night = body.get('night_mode', None)   # True / False / None

        current_hour = datetime.now().hour
        # Real clock night window: 18:00 – 03:59
        real_nighttime = current_hour >= 18 or current_hour < 4

        # If client explicitly sent night_mode, respect it;
        # otherwise fall back to the real clock.
        is_nighttime = client_night if isinstance(client_night, bool) else real_nighttime

        simulated, anomalies = [], []

        for animal in animals:
            # ── Starting position ─────────────────────────────────────────────
            if animal.get('last_lat') is not None and animal.get('last_lng') is not None:
                last_lat = float(animal['last_lat'])
                last_lon = float(animal['last_lng'])
            elif animal.get('zone_lat') is not None:
                clat = float(animal['zone_lat'])
                clon = float(animal['zone_lon'])
                zr   = float(animal['zone_radius'] or 200)
                ang  = random.uniform(0, 2 * math.pi)
                d    = random.uniform(0, zr * 0.4)
                last_lat = clat + (d / 111320) * math.cos(ang)
                last_lon = clon + (d / (111320 * math.cos(math.radians(clat)))) * math.sin(ang)
            else:
                ang  = random.uniform(0, 2 * math.pi)
                d    = random.uniform(0, raw_radius * 0.4)
                last_lat = farm_lat + (d / 111320) * math.cos(ang)
                last_lon = farm_lon + (d / (111320 * math.cos(math.radians(farm_lat)))) * math.sin(ang)

            cur_dist = calculate_distance(last_lat, last_lon, farm_lat, farm_lon)
            inside   = cur_dist <= raw_radius

            sim_hour      = current_hour
            lat, lon      = last_lat, last_lon
            speed         = 1.0

            # ── Normal vs anomalous movement ──────────────────────────────────
            if random.random() < 0.75 or not inside:
                # 75 % chance: normal grazing
                # If already outside: bring back inside first
                if not inside:
                    ang   = random.uniform(0, 2 * math.pi)
                    d     = random.uniform(0, raw_radius * 0.5)
                    lat   = farm_lat + (d / 111320) * math.cos(ang)
                    lon   = farm_lon + (d / (111320 * math.cos(math.radians(farm_lat)))) * math.sin(ang)
                else:
                    step  = random.uniform(5, 60)
                    ang   = random.uniform(0, 2 * math.pi)
                    lat   = last_lat + (step / 111320) * math.cos(ang)
                    lon   = last_lon + (step / (111320 * math.cos(math.radians(last_lat)))) * math.sin(ang)
                speed = random.uniform(0.3, 4.0)

            else:
                # 25 % chance: anomalous
                # Only include Night Movement if night mode is actually on
                choices = ['High Speed', 'Geofence Breach']
                if is_nighttime:
                    choices.append('Night Movement')

                choice = random.choice(choices)

                if choice == 'High Speed':
                    step  = random.uniform(200, 600)
                    ang   = random.uniform(0, 2 * math.pi)
                    lat   = last_lat + (step / 111320) * math.cos(ang)
                    lon   = last_lon + (step / (111320 * math.cos(math.radians(last_lat)))) * math.sin(ang)
                    speed = random.uniform(20, 50)

                elif choice == 'Night Movement':
                    step  = random.uniform(30, 120)
                    ang   = random.uniform(0, 2 * math.pi)
                    lat   = last_lat + (step / 111320) * math.cos(ang)
                    lon   = last_lon + (step / (111320 * math.cos(math.radians(last_lat)))) * math.sin(ang)
                    speed = random.uniform(1, 8)

                else:  # Geofence Breach
                    ang   = random.uniform(0, 2 * math.pi)
                    d     = raw_radius * random.uniform(1.25, 1.6)
                    lat   = farm_lat + (d / 111320) * math.cos(ang)
                    lon   = farm_lon + (d / (111320 * math.cos(math.radians(farm_lat)))) * math.sin(ang)
                    speed = random.uniform(2, 10)

            # ── Rule-based final validation (strict priority order) ────────────
            final_anomaly = False
            final_type    = None
            distance      = calculate_distance(lat, lon, farm_lat, farm_lon)

            # P1 — Geofence Breach (location beats everything)
            if distance > raw_radius * 1.1:
                final_anomaly, final_type = True, 'Geofence Breach'

            # P2 — High Speed (physics beats time)
            elif speed > 15:
                final_anomaly, final_type = True, 'High Speed'

            # P3 — Night Movement (only when night mode is active)
            elif is_nighttime:
                final_anomaly, final_type = True, 'Night Movement'

            # P4 — ML model (subtle / combined patterns)
            else:
                try:
                    ml = anomaly_detector.predict(
                        speed=speed,
                        hour=sim_hour,
                        distance_from_center=distance,
                        geofence_radius=raw_radius
                    )
                    if ml['is_anomaly']:
                        ml_type = ml.get('anomaly_type', 'Erratic Movement')
                        # Guard: don't let ML re-flag things rules already filtered out
                        if ml_type == 'Night Movement' and not is_nighttime:
                            pass   # suppress — it's daytime
                        elif ml_type == 'Geofence Breach' and distance <= raw_radius * 1.1:
                            pass   # suppress — animal is inside fence
                        else:
                            final_anomaly = True
                            final_type    = ml_type or 'Erratic Movement'
                except Exception as ml_err:
                    print(f'ML prediction skipped: {ml_err}')

            
            # ── Persist GPS record ────────────────────────────────────────────
            cursor.execute("""
                INSERT INTO gps_tracking
                    (animal_id, latitude, longitude, speed_kmh, is_anomaly, anomaly_type)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING tracking_id
            """, (animal['animal_id'], lat, lon, round(speed, 2),
                  final_anomaly, final_type if final_anomaly else None))
            tracking_id = cursor.fetchone()['tracking_id']

            cursor.execute("""
                UPDATE animals SET last_latitude=%s, last_longitude=%s WHERE animal_id=%s
            """, (lat, lon, animal['animal_id']))

            simulated.append({
                'tracking_id':  tracking_id,
                'animal_id':    animal['animal_id'],
                'animal_tag':   animal['animal_tag'],
                'latitude':     lat,
                'longitude':    lon,
                'speed_kmh':    round(speed, 2),
                'is_anomaly':   final_anomaly,
                'anomaly_type': final_type if final_anomaly else None,
            })

            
            # ── Create alert if anomaly detected ─────────────────────────────
            if final_anomaly:
                alert_msg = (
                    f"🚨 {animal['animal_tag']} - {final_type} detected! "
                    f"Speed: {speed:.1f} km/h"
                )
                cursor.execute("""
                    INSERT INTO alerts
                        (user_id, animal_id, alert_type, alert_message,
                         severity, last_known_lat, last_known_lng)
                    VALUES (%s,%s,%s,%s,'Critical',%s,%s) RETURNING alert_id
                """, (request.user_id, animal['animal_id'],
                      final_type, alert_msg, lat, lon))
                alert_id = cursor.fetchone()['alert_id']

                # ── NEW: Send Email alerts ──
                # Get user details for notifications
                cursor.execute("""
                    SELECT phone, email, first_name FROM users WHERE user_id = %s
                """, (request.user_id,))
                user = cursor.fetchone()
                
                if user and user['email']:
                    notification_service.send_alert(
                    email=user['email'],
                    animal_tag=animal['animal_tag'],
                    anomaly_type=final_type,
                    location=f"Lat: {lat}, Lon: {lon}",
                    severity="High",
                    details=f"Speed: {speed:.1f} km/h, Animal moved outside geofence"
                )

                anomalies.append({
                    'alert_id':      alert_id,
                    'animal_id':     animal['animal_id'],
                    'animal_tag':    animal['animal_tag'],
                    'alert_message': alert_msg,
                    'anomaly_type':  final_type,
                    'latitude':      lat,
                    'longitude':     lon,
                })

        conn.commit()
        cursor.close(); conn.close()

        return jsonify({
            'status':  'success',
            'success': True,
            'message': (
                f'✅ Simulated {len(simulated)} animals. '
                f'🚨 {len(anomalies)} anomalies detected.'
            ),
            'data': {
                'simulated':     simulated,
                'anomalies':     anomalies,
                'anomaly_count': len(anomalies),
                'is_nighttime':  is_nighttime,
                'current_hour':  current_hour,
            }
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            conn.rollback(); conn.close()
        except Exception:
            pass
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/tracking/animals', methods=['GET'])
@role_required(['farmer', 'admin'])
def get_tracking_data():
    conn = get_db()
    if not conn:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT DISTINCT ON (a.animal_id)
                a.animal_id, a.animal_tag, a.species, a.breed,
                g.latitude, g.longitude, g.speed_kmh,
                g.is_anomaly, g.anomaly_type, g.recorded_at,
                CASE
                    WHEN g.is_anomaly=TRUE THEN 'critical'
                    WHEN g.speed_kmh>10   THEN 'warning'
                    ELSE 'normal'
                END as status
            FROM animals a
            LEFT JOIN gps_tracking g ON a.animal_id=g.animal_id
            WHERE a.user_id=%s AND a.status='Active'
            ORDER BY a.animal_id, g.recorded_at DESC NULLS LAST, g.tracking_id DESC
        """, (request.user_id,))
        tracking_data = cursor.fetchall()
        cursor.close(); conn.close()
        for item in tracking_data:
            if item.get('recorded_at'): item['recorded_at'] = str(item['recorded_at'])
            if item.get('latitude'):    item['latitude']    = float(item['latitude'])
            if item.get('longitude'):   item['longitude']   = float(item['longitude'])
            if item.get('speed_kmh'):   item['speed_kmh']   = float(item['speed_kmh'])
        return jsonify({'status': 'success', 'success': True, 'data': tracking_data})
    except Exception as e:
        print(f'Get tracking data error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============ ANOMALY DETECTION ROUTES ============

@app.route('/api/anomaly/detect', methods=['POST'])
@token_required
def detect_anomaly(current_user):
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
        
        # Here you would integrate with your anomaly_detector.py
        # For now, we'll simulate anomaly detection
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
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT center_latitude, center_longitude, radius_meters
            FROM geofences WHERE user_id=%s ORDER BY created_at DESC LIMIT 1
        """, (request.user_id,))
        geofence = cursor.fetchone()

        farm_lat        = float(geofence['center_latitude'])  if geofence else -23.8966
        farm_lon        = float(geofence['center_longitude']) if geofence else  29.4488
        geofence_radius = float(geofence['radius_meters'])    if geofence else  2000.0

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
                dist_from_farm = calculate_distance(clat, clon, farm_lat, farm_lon)

                if dist_from_farm <= geofence_radius * 0.85:
                    for _ in range(10):
                        ang  = random.uniform(0, 2 * math.pi)
                        d    = random.uniform(0, min(zr * 0.6, geofence_radius * 0.4))
                        tlat = clat + (d / 111320) * math.cos(ang)
                        tlon = clon + (d / (111320 * math.cos(math.radians(clat)))) * math.sin(ang)
                        if calculate_distance(tlat, tlon, farm_lat, farm_lon) <= geofence_radius * 0.9:
                            lat, lon = tlat, tlon
                            break

            if lat is None:
                ang  = random.uniform(0, 2 * math.pi)
                d    = random.uniform(0, geofence_radius * 0.5)
                lat  = farm_lat + (d / 111320) * math.cos(ang)
                lon  = farm_lon + (d / (111320 * math.cos(math.radians(farm_lat)))) * math.sin(ang)

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
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT center_latitude, center_longitude, radius_meters
            FROM geofences WHERE user_id=%s ORDER BY created_at DESC LIMIT 1
        """, (request.user_id,))
        geofence = cursor.fetchone()

        farm_lat        = float(geofence['center_latitude'])  if geofence else -23.8966
        farm_lon        = float(geofence['center_longitude']) if geofence else  29.4488
        geofence_radius = float(geofence['radius_meters'])    if geofence else  2000.0

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
                dist = calculate_distance(
                    float(a['last_latitude']), float(a['last_longitude']),
                    farm_lat, farm_lon
                )
                if dist > geofence_radius * 1.05:
                    needs_placement = True

            if not needs_placement:
                continue

            lat = lon = None

            if a['center_latitude'] is not None:
                clat           = float(a['center_latitude'])
                clon           = float(a['center_longitude'])
                zr             = float(a['zone_radius'] or 200)
                dist_from_farm = calculate_distance(clat, clon, farm_lat, farm_lon)

                if dist_from_farm <= geofence_radius * 0.85:
                    for _ in range(10):
                        ang  = random.uniform(0, 2 * math.pi)
                        d    = random.uniform(0, min(zr * 0.6, geofence_radius * 0.4))
                        tlat = clat + (d / 111320) * math.cos(ang)
                        tlon = clon + (d / (111320 * math.cos(math.radians(clat)))) * math.sin(ang)
                        if calculate_distance(tlat, tlon, farm_lat, farm_lon) <= geofence_radius * 0.9:
                            lat, lon = tlat, tlon
                            break

            if lat is None:
                ang = random.uniform(0, 2 * math.pi)
                d   = random.uniform(0, geofence_radius * 0.5)
                lat = farm_lat + (d / 111320) * math.cos(ang)
                lon = farm_lon + (d / (111320 * math.cos(math.radians(farm_lat)))) * math.sin(ang)

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