import uuid
from datetime import datetime, timedelta
import math
import sqlite3
import pandas as pd
import os
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from authlib.integrations.flask_client import OAuth
import json # เพิ่ม import json

app = Flask(__name__)
app.secret_key = 'super_secret_key_change_this'

# ... (ส่วนตั้งค่า Google OAuth เหมือนเดิม) ...
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
    
    # [อัปเดต] เพิ่ม ip_address และ device_info
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
            ip_address TEXT,
            device_info TEXT,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ... (Global Var & haversine_distance เหมือนเดิม) ...
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

# ... (Routes พื้นฐาน /, /login, /authorize, /logout, /student, /teacher เหมือนเดิม) ...
# (เพื่อให้ประหยัดพื้นที่ ผมขอละไว้ในฐานที่เข้าใจ ให้คงโค้ดส่วน Login/User ไว้เหมือนเดิมนะครับ)
# ... COPY ส่วน Login/Teacher/Student จากไฟล์เดิมมาวางตรงนี้ ... 

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
    session['user'] = user_info
    email = user_info['email']
    try:
        temp_id = email.split('@')[0]
        student_id = temp_id[:12]
    except:
        student_id = email[:12]
    session['student_id'] = student_id
    return redirect('/setup_profile')

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('student_id', None)
    return redirect('/')

@app.route('/student')
def student_page():
    user = session.get('user')
    if not user: return redirect('/login') 
    
    student_id = session.get('student_id')
    conn = get_db()
    
    # 1. ดึงประวัติการเข้าเรียน (เหมือนเดิม)
    history = conn.execute('''
        SELECT attendance.*, sessions.subject_id, sessions.created_at as class_date
        FROM attendance
        JOIN sessions ON attendance.session_id = sessions.id
        WHERE attendance.student_id = ?
        ORDER BY sessions.created_at DESC
    ''', (student_id,)).fetchall()

    # 2. [เพิ่มใหม่] คำนวณสถิติ
    # หาวิชาที่นักศึกษาคนนี้ลงเรียน (เอาเฉพาะวิชาที่เคยเช็คชื่ออย่างน้อย 1 ครั้ง)
    my_subjects_query = conn.execute('''
        SELECT DISTINCT sessions.subject_id
        FROM attendance
        JOIN sessions ON attendance.session_id = sessions.id
        WHERE attendance.student_id = ?
    ''', (student_id,)).fetchall()

    my_subjects = [row['subject_id'] for row in my_subjects_query]

    total_classes = 0
    attended_count = len(history)
    
    if my_subjects:
        # นับจำนวนคาบ "ทั้งหมด" ที่เปิดสอน ของวิชาที่นักศึกษาคนนี้เรียน
        # (เพื่อให้ตัวหารถูกต้องตามรายวิชาของเขา)
        placeholders = ','.join(['?'] * len(my_subjects))
        sql = f'SELECT COUNT(*) FROM sessions WHERE subject_id IN ({placeholders})'
        total_classes = conn.execute(sql, my_subjects).fetchone()[0]
    
    conn.close()

    # คำนวณเปอร์เซ็นต์
    percent = 0
    if total_classes > 0:
        percent = (attended_count / total_classes) * 100

    # สร้างตัวแปร stats ส่งไปหน้าเว็บ
    stats = {
        'attended': attended_count,
        'total': total_classes,
        'percent': int(percent)
    }

    return render_template('student.html', user=user, student_id=student_id, history=history, stats=stats)

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

@app.route('/history/<int:session_id>')
def history_detail(session_id):
    conn = get_db()
    session_data = conn.execute('SELECT * FROM sessions WHERE id = ?', (session_id,)).fetchone()
    students = conn.execute('SELECT * FROM attendance WHERE session_id = ?', (session_id,)).fetchall()
    conn.close()
    if not session_data: return "ไม่พบข้อมูลวิชานี้", 404
    return render_template('history_detail.html', session=session_data, students=students)

@app.route('/setup_profile')
def setup_profile_page():
    user = session.get('user')
    if not user: return redirect('/login')
    return render_template('setup_profile.html', user=user, student_id=session.get('student_id'))

@app.route('/save_profile', methods=['POST'])
def save_profile():
    if 'user' not in session: return redirect('/login')
    fname = request.form.get('fname')
    lname = request.form.get('lname')
    full_name = f"{fname} {lname}"
    user_info = session['user']
    user_info['name'] = full_name
    session['user'] = user_info
    return redirect('/student')

# ... (Routes Export และ API อื่นๆ คงเดิม) ...
# (ส่วน Export Excel, Delete Session, Edit Session คงเดิม)
@app.route('/export_history/<int:session_id>')
def export_history(session_id):
    conn = get_db()
    session_info = conn.execute('SELECT subject_id, created_at FROM sessions WHERE id = ?', (session_id,)).fetchone()
    students = conn.execute('SELECT student_id, name, check_in_time, distance, status FROM attendance WHERE session_id = ?', (session_id,)).fetchall()
    conn.close()
    if not students: return "ไม่มีข้อมูลให้ Export"
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

@app.route('/export_excel')
def export_live_excel():
    if not current_session['attendees']: return "ไม่มีข้อมูลให้ Export"
    df = pd.DataFrame(current_session['attendees'])
    subject_name = current_session.get('subject_id', 'Unknown')
    df.insert(0, 'subject_id', subject_name)
    columns_map = {'subject_id': 'วิชา', 'id': 'รหัสนักศึกษา', 'name': 'ชื่อ-สกุล', 'time': 'เวลาที่มา', 'dist': 'ระยะห่าง', 'status': 'สถานะ'}
    existing_cols = [c for c in columns_map.keys() if c in df.columns]
    df = df[existing_cols]
    df.rename(columns=columns_map, inplace=True)
    filename = f"Attendance_{subject_name}_{get_thai_now().strftime('%Y-%m-%d_%H-%M')}.xlsx"
    df.to_excel(filename, index=False)
    return send_file(filename, as_attachment=True)

@app.route('/api/start_class', methods=['POST'])
def start_class():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    now_thai = get_thai_now() 
    now_str = now_thai.strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('INSERT INTO sessions (subject_id, created_at) VALUES (?, ?)', (data['subject_id'], now_str))
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

# ==========================================
# 🛡️ [UPDATE] API Check-in (เพิ่ม Anti-Cheating)
# ==========================================
@app.route('/api/check_in', methods=['POST'])
def check_in():
    user = session.get('user')
    student_id = session.get('student_id')
    
    if not user or not student_id: return jsonify({"status": "error", "message": "กรุณาล็อกอินใหม่"})
    data = request.json
    if not current_session['is_active']: return jsonify({"status": "error", "message": "คลาสเรียนปิดแล้ว"})
    if data.get('qr_token') != current_session['current_qr_token']: return jsonify({"status": "error", "message": "QR Code ไม่ถูกต้อง/หมดอายุ"})

    dist = haversine_distance(current_session['teacher_lat'], current_session['teacher_long'], float(data['lat']), float(data['lng']))
    if dist > current_session['radius']: return jsonify({"status": "error", "message": f"อยู่นอกพื้นที่ ({dist:.0f} เมตร)"})

    # เช็คว่าตัวเองเคยเช็คไปหรือยัง
    if any(s['id'] == student_id for s in current_session['attendees']): return jsonify({"status": "error", "message": "คุณเช็คชื่อไปแล้ว"})

    # ======================================================
    # 🕵️‍♂️ [เพิ่มใหม่] ระบบป้องกันการใช้อุปกรณ์เดิมเช็คชื่อให้เพื่อน
    # ======================================================
    # ======================================================
    # 🕵️‍♂️ [แก้ไข] ระบบดึง IP ให้แม่นยำขึ้น (ตัดส่วนเกินออก)
    # ======================================================
    # ดึง IP จาก Header (เผื่อผ่าน Proxy/Cloud)
    if request.headers.getlist("X-Forwarded-For"):
        client_ip = request.headers.getlist("X-Forwarded-For")[0].split(',')[0].strip()
    else:
        client_ip = request.remote_addr

    user_agent = request.headers.get('User-Agent')

    # [เพิ่ม] สั่งปริ้นดูใน Log ของ Render เลยว่าใครใช้ IP อะไร
    print(f"DEBUG Check-in: ID={student_id}, IP={client_ip}, UA={user_agent}")

    for s in current_session['attendees']:
        # เปรียบเทียบข้อมูล
        saved_ip = s.get('ip')
        saved_ua = s.get('ua')
        
        # ปริ้นเทียบกันให้เห็นชัดๆ
        print(f"   -> Compare with {s['id']}: IP={saved_ip}, UA={saved_ua}")

        if saved_ip == client_ip and saved_ua == user_agent:
             print("   !!! DUPLICATE DETECTED !!!")
             return jsonify({
                 "status": "error", 
                 "message": "⛔ ไม่สามารถเช็คชื่อได้: ตรวจพบการใช้อุปกรณ์ซ้ำกับรหัส " + s['id']
             })

    now_thai = get_thai_now()
    elapsed_minutes = (now_thai - current_session['start_time']).total_seconds() / 60
    status = "late" if elapsed_minutes > 15 else "present"
    time_str = now_thai.strftime("%H:%M:%S")
    
    # เพิ่ม ip และ ua ลงใน Memory เพื่อใช้ตรวจคนต่อไป
    student_record = {
        "id": student_id, "time": time_str, "dist": f"{dist:.0f}m",
        "name": user.get('name', 'ไม่ระบุชื่อ'), "picture": user.get('picture', ''), 
        "status": status,
        "ip": client_ip,      # [เพิ่ม]
        "ua": user_agent      # [เพิ่ม]
    }
    current_session['attendees'].append(student_record)
    current_session['current_qr_token'] = str(uuid.uuid4())[:8]

    if current_session['db_id']:
        conn = get_db()
        conn.execute('''
            INSERT INTO attendance (session_id, student_id, check_in_time, distance, email, name, picture, status, ip_address, device_info) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            current_session['db_id'], student_id, time_str, f"{dist:.0f}m", 
            user.get('email', ''), user.get('name', ''), user.get('picture', ''), status,
            client_ip, user_agent
        ))
        conn.commit()
        conn.close()

    return jsonify({"status": "checked_in"})


# ==========================================
# 📊 [UPDATE] Admin Analytics (ส่วนวิเคราะห์ผล)
# ==========================================
ADMIN_PASSWORD = "1234"

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect('/admin')
        else: return render_template('admin_login.html', error="รหัสผ่านไม่ถูกต้อง")
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect('/admin/login')

@app.route('/admin')
def admin_dashboard():
    if not session.get('is_admin'): return redirect('/admin/login')
    
    conn = get_db()
    
    # 1. ข้อมูลพื้นฐาน
    stats = {
        'total_sessions': conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],
        'total_checkins': conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0],
        'unique_students': conn.execute('SELECT COUNT(DISTINCT student_id) FROM attendance').fetchone()[0]
    }
    sessions = conn.execute('SELECT * FROM sessions ORDER BY created_at DESC').fetchall()
    
    # 2. [NEW] คำนวณความเสี่ยง (Attendance < 80%)
    # สูตร: (จำนวนคาบที่มา / จำนวนคาบทั้งหมดที่มีการเปิดสอน) * 100
    # หมายเหตุ: นี่คิดรวมทุกวิชา ถ้าจะแยกวิชาต้อง Group by subject_id เพิ่ม
    total_classes = stats['total_sessions']
    risk_students = []
    
    if total_classes > 0:
        student_stats = conn.execute('''
            SELECT student_id, name, COUNT(*) as attended_count
            FROM attendance
            GROUP BY student_id
        ''').fetchall()
        
        for s in student_stats:
            percent = (s['attended_count'] / total_classes) * 100
            if percent < 80:
                risk_students.append({
                    'id': s['student_id'],
                    'name': s['name'],
                    'attended': s['attended_count'],
                    'total': total_classes,
                    'percent': int(percent)
                })

    # 3. [NEW] ข้อมูลสำหรับกราฟ (Attendance per Date)
    # ดึงข้อมูล 7 วันล่าสุดมาแสดง
    graph_data = conn.execute('''
        SELECT substr(created_at, 1, 10) as date, COUNT(*) as count 
        FROM sessions 
        GROUP BY date 
        ORDER BY date DESC LIMIT 7
    ''').fetchall()
    
    # 4. [NEW] ตรวจจับการโกง (Duplicate IP in same session)
    # หา Session ไหนที่มี IP ซ้ำกันเกิน 1 คน
    cheating_logs = conn.execute('''
        SELECT sessions.subject_id, attendance.created_at, attendance.ip_address, COUNT(DISTINCT attendance.student_id) as dup_count
        FROM attendance
        JOIN sessions ON attendance.session_id = sessions.id
        GROUP BY attendance.session_id, attendance.ip_address
        HAVING dup_count > 1
        ORDER BY attendance.id DESC
    ''').fetchall()

    conn.close()
    
    # แปลงกราฟเป็น List เพื่อส่งไป JS
    chart_labels = [row['date'] for row in graph_data][::-1]
    chart_values = [row['count'] for row in graph_data][::-1] # อันนี้จำนวนวิชาที่เปิด ถ้าอยากได้จำนวนคนเข้าต้อง Join attendance

    return render_template('admin.html', 
                           stats=stats, 
                           sessions=sessions, 
                           risk_students=risk_students,
                           cheating_logs=cheating_logs,
                           chart_labels=json.dumps(chart_labels),
                           chart_values=json.dumps(chart_values))

@app.route('/api/admin/reset_database', methods=['POST'])
def admin_reset_db():
    if not session.get('is_admin'): return jsonify({"status": "error", "message": "Unauthorized"}), 403
    try:
        conn = get_db()
        conn.execute('DELETE FROM attendance')
        conn.execute('DELETE FROM sessions')
        conn.execute('DELETE FROM sqlite_sequence') 
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "ล้างข้อมูลเรียบร้อยแล้ว"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    
if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(host='0.0.0.0', port=5000)