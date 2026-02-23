import uuid
from datetime import datetime, timedelta
import math
import psycopg2
import psycopg2.extras
import pandas as pd
import os
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from authlib.integrations.flask_client import OAuth
import json

app = Flask(__name__)
app.secret_key = 'super_secret_key_change_this'

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

def get_thai_now():
    return datetime.utcnow() + timedelta(hours=7)

# URL สำหรับเชื่อมต่อ Database Neon ของคุณ
DATABASE_URL = "postgresql://neondb_owner:npg_zmaLVEd9vt8C@ep-holy-breeze-a1p4sqrq-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.DictCursor)
    conn.autocommit = True 
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS sessions (id SERIAL PRIMARY KEY, subject_id TEXT, created_at TEXT)')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id SERIAL PRIMARY KEY, 
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
    cursor.close()
    conn.close()

init_db()

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
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT attendance.*, sessions.subject_id, sessions.created_at as class_date
        FROM attendance
        JOIN sessions ON attendance.session_id = sessions.id
        WHERE attendance.student_id = %s
        ORDER BY sessions.created_at DESC
    ''', (student_id,))
    history = cursor.fetchall()

    cursor.execute('''
        SELECT DISTINCT sessions.subject_id
        FROM attendance
        JOIN sessions ON attendance.session_id = sessions.id
        WHERE attendance.student_id = %s
    ''', (student_id,))
    my_subjects_query = cursor.fetchall()

    my_subjects = [row['subject_id'] for row in my_subjects_query]

    total_classes = 0
    attended_count = len(history)
    
    if my_subjects:
        placeholders = ','.join(['%s'] * len(my_subjects))
        sql = f'SELECT COUNT(*) FROM sessions WHERE subject_id IN ({placeholders})'
        cursor.execute(sql, my_subjects)
        total_classes = cursor.fetchone()[0]
    
    cursor.close()
    conn.close()

    percent = 0
    if total_classes > 0:
        percent = (attended_count / total_classes) * 100

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
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM sessions ORDER BY id DESC')
    sessions = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('history.html', sessions=sessions)

@app.route('/history/<int:session_id>')
def history_detail(session_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM sessions WHERE id = %s', (session_id,))
    session_data = cursor.fetchone()
    
    cursor.execute('SELECT * FROM attendance WHERE session_id = %s', (session_id,))
    students = cursor.fetchall()
    
    cursor.close()
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

@app.route('/export_history/<int:session_id>')
def export_history(session_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT subject_id, created_at FROM sessions WHERE id = %s', (session_id,))
    session_info = cursor.fetchone()
    
    cursor.execute('SELECT student_id, name, check_in_time, distance, status FROM attendance WHERE session_id = %s', (session_id,))
    students = cursor.fetchall()
    cursor.close()
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
    cursor = conn.cursor()
    cursor.execute('DELETE FROM attendance WHERE session_id = %s', (data['id'],))
    cursor.execute('DELETE FROM sessions WHERE id = %s', (data['id'],))
    cursor.close()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/edit_session', methods=['POST'])
def edit_session():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE sessions SET subject_id = %s WHERE id = %s', (data['new_name'], data['id']))
    cursor.close()
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
    
    # [แก้ไข] วิธีการดึง ID ล่าสุดใน Postgres
    cursor.execute('INSERT INTO sessions (subject_id, created_at) VALUES (%s, %s) RETURNING id', (data['subject_id'], now_str))
    new_db_id = cursor.fetchone()[0]
    
    cursor.close()
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

    if any(s['id'] == student_id for s in current_session['attendees']): return jsonify({"status": "error", "message": "คุณเช็คชื่อไปแล้ว"})

    if request.headers.getlist("X-Forwarded-For"):
        client_ip = request.headers.getlist("X-Forwarded-For")[0].split(',')[0].strip()
    else:
        client_ip = request.remote_addr

    user_agent = request.headers.get('User-Agent')

    print(f"DEBUG Check-in: ID={student_id}, IP={client_ip}, UA={user_agent}")

    for s in current_session['attendees']:
        saved_ip = s.get('ip')
        saved_ua = s.get('ua')
        if saved_ip == client_ip and saved_ua == user_agent:
             return jsonify({
                 "status": "error", 
                 "message": "⛔ ไม่สามารถเช็คชื่อได้: ตรวจพบการใช้อุปกรณ์ซ้ำกับรหัส " + s['id']
             })

    now_thai = get_thai_now()
    elapsed_minutes = (now_thai - current_session['start_time']).total_seconds() / 60
    status = "late" if elapsed_minutes > 15 else "present"
    time_str = now_thai.strftime("%H:%M:%S")
    
    student_record = {
        "id": student_id, "time": time_str, "dist": f"{dist:.0f}m",
        "name": user.get('name', 'ไม่ระบุชื่อ'), "picture": user.get('picture', ''), 
        "status": status,
        "ip": client_ip,      
        "ua": user_agent      
    }
    current_session['attendees'].append(student_record)
    current_session['current_qr_token'] = str(uuid.uuid4())[:8]

    if current_session['db_id']:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO attendance (session_id, student_id, check_in_time, distance, email, name, picture, status, ip_address, device_info) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            current_session['db_id'], student_id, time_str, f"{dist:.0f}m", 
            user.get('email', ''), user.get('name', ''), user.get('picture', ''), status,
            client_ip, user_agent
        ))
        cursor.close()
        conn.close()

    return jsonify({"status": "checked_in"})

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
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM sessions')
    total_sessions = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM attendance')
    total_checkins = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(DISTINCT student_id) FROM attendance')
    unique_students = cursor.fetchone()[0]
    
    stats = {
        'total_sessions': total_sessions,
        'total_checkins': total_checkins,
        'unique_students': unique_students
    }
    
    cursor.execute('SELECT * FROM sessions ORDER BY created_at DESC')
    sessions = cursor.fetchall()
    
    risk_students = []
    if total_classes := stats['total_sessions']:
        cursor.execute('''
            SELECT student_id, name, COUNT(*) as attended_count
            FROM attendance
            GROUP BY student_id, name
        ''')
        student_stats = cursor.fetchall()
        
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

    cursor.execute('''
        SELECT substr(created_at, 1, 10) as date, COUNT(*) as count 
        FROM sessions 
        GROUP BY date 
        ORDER BY date DESC LIMIT 7
    ''')
    graph_data = cursor.fetchall()
    
    cursor.execute('''
        SELECT sessions.subject_id, attendance.check_in_time as created_at, attendance.ip_address, COUNT(DISTINCT attendance.student_id) as dup_count
        FROM attendance
        JOIN sessions ON attendance.session_id = sessions.id
        GROUP BY attendance.session_id, attendance.ip_address, sessions.subject_id, attendance.check_in_time
        HAVING COUNT(DISTINCT attendance.student_id) > 1
        ORDER BY attendance.check_in_time DESC
    ''')
    cheating_logs = cursor.fetchall()

    cursor.close()
    conn.close()
    
    chart_labels = [row['date'] for row in graph_data][::-1]
    chart_values = [row['count'] for row in graph_data][::-1]

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
        cursor = conn.cursor()
        # [แก้ไข] คำสั่งล้างตารางของ Postgres
        cursor.execute('TRUNCATE TABLE attendance, sessions RESTART IDENTITY CASCADE')
        cursor.close()
        conn.close()
        return jsonify({"status": "success", "message": "ล้างข้อมูลเรียบร้อยแล้ว"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    
if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(host='0.0.0.0', port=5000)