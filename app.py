import uuid
from datetime import datetime, timedelta # แก้แบบนี้
import math
import sqlite3
import pandas as pd
import os
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from authlib.integrations.flask_client import OAuth

app = Flask(__name__)
app.secret_key = 'super_secret_key_change_this' # เปลี่ยนเป็นคีย์ลับของคุณเอง

# ==========================================
# ⚙️ ตั้งค่า Google OAuth
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

# ฟังก์ชันดึงเวลาประเทศไทย (UTC+7)
def get_thai_now():
    return datetime.utcnow() + timedelta(hours=7)

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id TEXT, created_at TEXT)')
    
    # สร้างตาราง attendance (มี name, picture, status ครบ)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            session_id INTEGER, 
            student_id TEXT, 
            check_in_time TEXT, 
            distance TEXT, 
            email TEXT,
            name TEXT,
            picture TEXT,
            status TEXT,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Global Var
current_session = {
    "is_active": False, "db_id": None, "subject_id": None, "teacher_lat": None, "teacher_long": None,
    "radius": 50, "time_limit": 15, "start_time": None, "current_qr_token": None, 
    "attendees": [], "roster": []
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

@app.route('/login')
def login():
    redirect_uri = url_for('authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/authorize')
def authorize():
    token = google.authorize_access_token()
    user_info = token.get('userinfo')
    
    # เก็บข้อมูลพื้นฐานลง Session ก่อน
    session['user'] = user_info
    
    email = user_info['email']
    try:
        # ตัดเอาแค่หน้า @
        temp_id = email.split('@')[0]
        
        student_id = temp_id[:12]
    except:
        student_id = email[:12] # กันเหนียว

    session['student_id'] = student_id
    
    # [แก้ใหม่] แทนที่จะไป /student เลย ให้ไปหน้าตั้งชื่อก่อน
    return redirect('/setup_profile')

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('student_id', None)
    return redirect('/')

@app.route('/student')
def student_page():
    user = session.get('user')
    if not user:
        return redirect('/login') 
    
    student_id = session.get('student_id')
    conn = get_db()
    history = conn.execute('''
        SELECT attendance.*, sessions.subject_id, sessions.created_at as class_date
        FROM attendance
        JOIN sessions ON attendance.session_id = sessions.id
        WHERE attendance.student_id = ?
        ORDER BY sessions.created_at DESC
    ''', (student_id,)).fetchall()
    conn.close()
    return render_template('student.html', user=user, student_id=student_id, history=history)

@app.route('/teacher')
def teacher_page():
    return render_template('teacher.html')

@app.route('/attendance_records')
def attendance_records():
    return render_template('attendance_records.html', attendees=current_session['attendees'], subject=current_session.get('subject_id'), current_session=current_session)
    
@app.route('/history')
def history_page():
    conn = get_db()
    sessions = conn.execute('SELECT * FROM sessions ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('history.html', sessions=sessions)

# [✅ แก้ไขใหม่] เพิ่ม Route นี้เพื่อให้กดดูประวัติแล้วไม่เจอ 404
@app.route('/history/<int:session_id>')
def history_detail(session_id):
    conn = get_db()
    session_data = conn.execute('SELECT * FROM sessions WHERE id = ?', (session_id,)).fetchone()
    students = conn.execute('SELECT * FROM attendance WHERE session_id = ?', (session_id,)).fetchall()
    conn.close()
    
    if not session_data:
        return "ไม่พบข้อมูลวิชานี้", 404
        
    return render_template('history_detail.html', session=session_data, students=students)

@app.route('/export_history/<int:session_id>')
def export_history(session_id):
    conn = get_db()
    session_info = conn.execute('SELECT subject_id, created_at FROM sessions WHERE id = ?', (session_id,)).fetchone()
    students = conn.execute('''
        SELECT student_id, name, check_in_time, distance, status
        FROM attendance 
        WHERE session_id = ?
    ''', (session_id,)).fetchall()
    conn.close()

    if not students:
        return "ไม่มีข้อมูลให้ Export"

    data_list = []
    for row in students:
        data_list.append({
            "รหัสนักศึกษา": row['student_id'],
            "ชื่อ-นามสกุล": row['name'] if 'name' in row.keys() and row['name'] else "ไม่ระบุ",
            "เวลาที่เช็คชื่อ": row['check_in_time'],
            "ระยะห่าง": row['distance'],
            "สถานะ": row['status'] if 'status' in row.keys() else 'present'
        })

    df = pd.DataFrame(data_list)
    subject_name = session_info['subject_id'] if session_info else "Class"
    filename = f"History_{subject_name}_{session_id}.xlsx"
    df.to_excel(filename, index=False)
    return send_file(filename, as_attachment=True)

# ================= API Check-in =================
@app.route('/api/check_in', methods=['POST'])
def check_in():
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

    if any(s['id'] == student_id for s in current_session['attendees']):
        return jsonify({"status": "error", "message": "คุณเช็คชื่อไปแล้ว"})

    now_thai = get_thai_now()
    elapsed_minutes = (now_thai - current_session['start_time']).total_seconds() / 60
    status = "late" if elapsed_minutes > 15 else "present"
    time_str = now_thai.strftime("%H:%M:%S")
    
    student_record = {
        "id": student_id,
        "time": time_str,
        "dist": f"{dist:.0f}m",
        "name": user.get('name', 'ไม่ระบุชื่อ'),
        "picture": user.get('picture', ''),
        "status": status
    }
    current_session['attendees'].append(student_record)
    current_session['current_qr_token'] = str(uuid.uuid4())[:8]

    if current_session['db_id']:
        conn = get_db()
        # เช็คคอลัมน์ก่อน insert เพื่อความชัวร์ (เผื่อ DB เก่ายังไม่อัปเดต)
        try:
            conn.execute('''
                INSERT INTO attendance (session_id, student_id, check_in_time, distance, email, name, picture, status) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                current_session['db_id'], 
                student_id, 
                time_str, 
                f"{dist:.0f}m", 
                user.get('email', ''),
                user.get('name', ''),
                user.get('picture', ''),
                status
            ))
            conn.commit()
        except Exception as e:
            print(f"Database Error: {e}")
        conn.close()

    return jsonify({"status": "checked_in"})

@app.route('/api/start_class', methods=['POST'])
def start_class():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    now_thai = get_thai_now() 
    now_str = now_thai.strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('INSERT INTO sessions (subject_id, created_at) VALUES (?, ?)', 
                   (data['subject_id'], now_str))
    conn.commit()
    new_db_id = cursor.lastrowid
    conn.close()

    raw_roster = data.get('roster', '')
    roster_list = [x.strip() for x in raw_roster.replace(',', '\n').split('\n') if x.strip()]

    current_session.update({
        "is_active": True, "db_id": new_db_id, "subject_id": data['subject_id'],
        "teacher_lat": float(data['lat']), "teacher_long": float(data['lng']),
        "radius": int(data['radius']), "time_limit": int(data['time_limit']),
        "start_time": now_thai,
        "current_qr_token": str(uuid.uuid4())[:8], "roster": roster_list
    })
    return jsonify({"status": "success", "message": "Class Started"})

@app.route('/api/update_qr_token', methods=['GET'])
def update_qr_token():
    if not current_session['is_active']: return jsonify({"status": "expired"})
    elapsed = (get_thai_now() - current_session['start_time']).total_seconds() / 60
    if elapsed > current_session['time_limit']:
        current_session['is_active'] = False
        return jsonify({"status": "expired"})
    return jsonify({"qr_token": current_session['current_qr_token'], "time_left": current_session['time_limit'] - elapsed})

@app.route('/api/get_dashboard_data', methods=['GET'])
def get_dashboard_data():
    present_ids = [s['id'] for s in current_session['attendees']]
    absent_list = [uid for uid in current_session['roster'] if uid not in present_ids]
    return jsonify({
        "attendees": current_session['attendees'],
        "absent_list": absent_list,
        "total_students": len(current_session['roster'])
    })

@app.route('/export_excel')
def export_live_excel():
    if not current_session['attendees']:
        return "ไม่มีข้อมูลให้ Export (ยังไม่มีใครเช็คชื่อ)"
    
    df = pd.DataFrame(current_session['attendees'])
    columns_map = {'id': 'รหัสนักศึกษา', 'name': 'ชื่อ-สกุล', 'time': 'เวลาที่มา', 'dist': 'ระยะห่าง', 'status': 'สถานะ'}
    existing_cols = [c for c in columns_map.keys() if c in df.columns]
    df = df[existing_cols]
    df.rename(columns=columns_map, inplace=True)
    
    filename = f"Attendance_{current_session.get('subject_id', 'Live')}_{get_thai_now().strftime('%H-%M')}.xlsx"
    df.to_excel(filename, index=False)
    return send_file(filename, as_attachment=True)

# API สำหรับลบและแก้ไข (เพื่อให้ history.html ทำงานได้)
@app.route('/api/delete_session', methods=['POST'])
def delete_session():
    data = request.json
    conn = get_db()
    conn.execute('DELETE FROM attendance WHERE session_id = ?', (data['id'],))
    conn.execute('DELETE FROM sessions WHERE id = ?', (data['id'],))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/edit_session', methods=['POST'])
def edit_session():
    data = request.json
    conn = get_db()
    conn.execute('UPDATE sessions SET subject_id = ? WHERE id = ?', (data['new_name'], data['id']))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

# [เพิ่มใหม่] หน้าสำหรับกรอกชื่อ-นามสกุล
@app.route('/setup_profile')
def setup_profile_page():
    user = session.get('user')
    if not user: return redirect('/login')
    
    return render_template('setup_profile.html', 
                         user=user, 
                         student_id=session.get('student_id'))

# [เพิ่มใหม่] บันทึกชื่อลง Session แล้วค่อยเข้าหน้าหลัก
@app.route('/save_profile', methods=['POST'])
def save_profile():
    if 'user' not in session: return redirect('/login')
    
    fname = request.form.get('fname')
    lname = request.form.get('lname')
    full_name = f"{fname} {lname}" # รวมชื่อนามสกุล
    
    # อัปเดตชื่อใน Session ใหม่ (ทับชื่อจาก Google ไปเลย)
    user_info = session['user']
    user_info['name'] = full_name
    session['user'] = user_info # บันทึกกลับลง Session
    
    return redirect('/student')

    # ==========================================
# 👮‍♂️ ADMIN ROUTES (ส่วนของผู้ดูแลระบบ)
# ==========================================

# กำหนดรหัสผ่าน Admin (เปลี่ยนได้ตามใจชอบ)
ADMIN_PASSWORD = "admin_password_1234"

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == 123456789: # เปลี่ยนรหัสผ่านตามที่คุณต้องการ
            session['is_admin'] = True
            return redirect('/admin')
        else:
            return render_template('admin_login.html', error="รหัสผ่านไม่ถูกต้อง")
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect('/admin/login')

@app.route('/admin')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect('/admin/login')
    
    conn = get_db()
    
    # 1. สถิติรวม
    stats = {
        'total_sessions': conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],
        'total_checkins': conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0],
        'unique_students': conn.execute('SELECT COUNT(DISTINCT student_id) FROM attendance').fetchone()[0]
    }
    
    # 2. ดึงข้อมูล Sessions ทั้งหมด
    sessions = conn.execute('SELECT * FROM sessions ORDER BY created_at DESC').fetchall()
    
    # 3. ดึงรายชื่อนักศึกษา (Unique)
    students = conn.execute('''
        SELECT DISTINCT student_id, name, email, MAX(check_in_time) as last_seen 
        FROM attendance 
        GROUP BY student_id 
        ORDER BY last_seen DESC
    ''').fetchall()
    
    conn.close()
    
    return render_template('admin.html', stats=stats, sessions=sessions, students=students)

@app.route('/api/admin/reset_database', methods=['POST'])
def admin_reset_db():
    if not session.get('is_admin'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    try:
        conn = get_db()
        conn.execute('DELETE FROM attendance')
        conn.execute('DELETE FROM sessions')
        # Reset Auto Increment
        conn.execute('DELETE FROM sqlite_sequence') 
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "ล้างข้อมูลเรียบร้อยแล้ว"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    
if __name__ == '__main__':
    # บรรทัดนี้สำคัญสำหรับ Localhost/Ngrok
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(host='0.0.0.0', port=5000)