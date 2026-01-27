import uuid
import datetime
import math
import sqlite3
import pandas as pd
import os
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from authlib.integrations.flask_client import OAuth # เรียกใช้ Authlib

app = Flask(__name__)
app.secret_key = 'super_secret_key_change_this' # จำเป็นสำหรับ Session

# ==========================================
# ⚙️ ตั้งค่า Google OAuth (เอาค่าจาก Google Cloud มาใส่ตรงนี้)
# ==========================================
app.config['GOOGLE_CLIENT_ID'] = '1055465619000-mi7kalvlqi6cuumuqholbqhm6bi5et7b.apps.googleusercontent.com'
app.config['GOOGLE_CLIENT_SECRET'] = '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'

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

DB_NAME = "attendance_system.db"
# (ส่วน Database Setup ... เหมือนเดิม ... ข้ามไปดูส่วน Routes)

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id TEXT, created_at TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS attendance (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, student_id TEXT, check_in_time TEXT, distance TEXT, email TEXT, FOREIGN KEY(session_id) REFERENCES sessions(id))')
    conn.commit()
    conn.close()

init_db()

# Global Var (เหมือนเดิม)
current_session = {
    "is_active": False, "db_id": None, "subject_id": None, "teacher_lat": None, "teacher_long": None,
    "radius": 50, "time_limit": 15, "start_time": None, "current_qr_token": None, "attendees": [] 
}

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c * 1000 

# ================= ROUTES =================

@app.route('/')
def index():
    return render_template('index.html')

# --- Login Routes ---
@app.route('/login')
def login():
    # ส่งผู้ใช้ไปหน้า Login ของ Google
    redirect_uri = url_for('authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/authorize')
def authorize():
    # Google ส่งข้อมูลกลับมาที่นี่
    token = google.authorize_access_token()
    user_info = token.get('userinfo')
    
    # เก็บข้อมูลลง Session
    session['user'] = user_info
    
    # 💡 Logic แปลง Email เป็นรหัสนักศึกษา (สมมติ Email คือ 640001@uni.ac.th)
    email = user_info['email']
    try:
        # ตัดเอาข้อความหน้าเครื่องหมาย @ มาเป็นรหัสนักศึกษา
        student_id = email.split('@')[0] 
    except:
        student_id = email # ถ้าตัดไม่ได้ ก็ใช้อีเมลไปเลย

    session['student_id'] = student_id
    return redirect('/student')

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('student_id', None)
    return redirect('/')

# --- Student Page (แก้ไขให้เช็ค Login) ---
@app.route('/student')
def student_page():
    user = session.get('user')
    if not user:
        return redirect('/login') # ถ้ายังไม่ล็อกอิน ให้เด้งไปล็อกอิน
    
    return render_template('student.html', user=user, student_id=session.get('student_id'))

@app.route('/teacher')
def teacher_page():
    return render_template('teacher.html')

# (Routes History, Attendance Records เหมือนเดิม... ข้าม)
@app.route('/attendance_records')
def attendance_records():
    # ... (โค้ดเดิม) ...
    return render_template('attendance_records.html', attendees=current_session['attendees'], subject=current_session.get('subject_id'), current_session=current_session)
    
@app.route('/history')
def history_page():
    conn = get_db()
    sessions = conn.execute('SELECT * FROM sessions ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('history.html', sessions=sessions)
    
@app.route('/history/<int:session_id>')
def history_detail(session_id):
    conn = get_db()
    session_data = conn.execute('SELECT * FROM sessions WHERE id = ?', (session_id,)).fetchone()
    students = conn.execute('SELECT * FROM attendance WHERE session_id = ?', (session_id,)).fetchall()
    conn.close()
    return render_template('history_detail.html', session=session_data, students=students)

# ================= API Check-in (แก้ใหม่) =================
@app.route('/api/check_in', methods=['POST'])
def check_in():
    # ตรวจสอบว่าล็อกอินหรือยัง?
    user = session.get('user')
    student_id = session.get('student_id')
    
    if not user or not student_id:
        return jsonify({"status": "error", "message": "กรุณาล็อกอินใหม่"})

    data = request.json
    
    if not current_session['is_active']:
        return jsonify({"status": "error", "message": "คลาสเรียนปิดแล้ว"})

    if data.get('qr_token') != current_session['current_qr_token']:
         return jsonify({"status": "error", "message": "QR Code ไม่ถูกต้อง/หมดอายุ"})

    dist = haversine_distance(
        current_session['teacher_lat'], current_session['teacher_long'],
        float(data['lat']), float(data['lng'])
    )
    if dist > current_session['radius']:
        return jsonify({"status": "error", "message": f"อยู่นอกพื้นที่ ({dist:.0f} เมตร)"})

    # เช็คซ้ำ
    if any(s['id'] == student_id for s in current_session['attendees']):
        return jsonify({"status": "error", "message": "คุณเช็คชื่อไปแล้ว"})

    # บันทึก
    time_str = datetime.datetime.now().strftime("%H:%M:%S")
    
    # 1. ใส่ใน Memory (สำหรับโชว์หน้าจออาจารย์ Real-time)
    student_record = {
        "id": student_id,
        "time": time_str,
        "dist": f"{dist:.0f}m",
        "name": user.get('name', 'ไม่ระบุชื่อ'),    # <--- เพิ่มบรรทัดนี้ (เก็บชื่อ)
        "picture": user.get('picture', '')          # <--- เพิ่มบรรทัดนี้ (เก็บรูป)
    }
    current_session['attendees'].append(student_record)
    current_session['current_qr_token'] = str(uuid.uuid4())[:8]

    # 2. Database (เพิ่ม field email)
    if current_session['db_id']:
        conn = get_db()
        # หมายเหตุ: ต้องไปแก้ Table attendance ให้มี column email ด้วยถ้าอยากเก็บ
        conn.execute('INSERT INTO attendance (session_id, student_id, check_in_time, distance, email) VALUES (?, ?, ?, ?, ?)',
                     (current_session['db_id'], student_id, time_str, f"{dist:.0f}m", user['email']))
        conn.commit()
        conn.close()

    return jsonify({"status": "checked_in"})

# API อื่นๆ (Start Class, Update QR) เหมือนเดิม...

@app.route('/api/start_class', methods=['POST'])
def start_class():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('INSERT INTO sessions (subject_id, created_at) VALUES (?, ?)', (data['subject_id'], now_str))
    conn.commit()
    new_db_id = cursor.lastrowid
    conn.close()
    current_session.update({
        "is_active": True, "db_id": new_db_id, "subject_id": data['subject_id'],
        "teacher_lat": float(data['lat']), "teacher_long": float(data['lng']),
        "radius": int(data['radius']), "time_limit": int(data['time_limit']),
        "start_time": datetime.datetime.now(), "attendees": [],
        "current_qr_token": str(uuid.uuid4())[:8]
    })
    return jsonify({"status": "success"})

@app.route('/api/update_qr_token', methods=['GET'])
def update_qr_token():
    if not current_session['is_active']: return jsonify({"status": "expired"})
    elapsed = (datetime.datetime.now() - current_session['start_time']).total_seconds() / 60
    if elapsed > current_session['time_limit']:
        current_session['is_active'] = False
        return jsonify({"status": "expired"})
    return jsonify({"qr_token": current_session['current_qr_token'], "time_left": current_session['time_limit'] - elapsed})

@app.route('/api/get_dashboard_data', methods=['GET'])
def get_dashboard_data():
    return jsonify({"attendees": current_session['attendees']})

if __name__ == '__main__':
    # บรรทัดนี้สำคัญสำหรับ Localhost เพื่อให้ OAuth ยอมทำงานบน HTTP
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(host='0.0.0.0', port=5000) #debug=True)