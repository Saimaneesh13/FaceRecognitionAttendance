import cv2
import face_recognition
import numpy as np
import sqlite3
import pickle
import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
from datetime import datetime, timedelta
import pandas as pd
from functools import wraps
import hashlib
import io
import time
import base64
from PIL import Image
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Stable secret key so admin login survives Gunicorn worker restarts
app.secret_key = os.environ.get("SECRET_KEY", "veriface-institutional-secret-key-2026")

# Configuration (Supports Render Persistent Disk if RENDER_DISK_PATH is set)
DATA_DIR = os.environ.get("RENDER_DISK_PATH", ".")
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE = os.path.join(DATA_DIR, "database.db")
ENCODING_FILE = os.path.join(DATA_DIR, "encodings.pkl")
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
UPLOAD_FOLDER = os.path.join(DATA_DIR, 'static/uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# -----------------------
# DATABASE SETUP
# -----------------------
def init_db():
    """Initialize database with all required tables"""
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()

    # Students table with enhanced fields
    c.execute("""
    CREATE TABLE IF NOT EXISTS students(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        student_id TEXT UNIQUE NOT NULL,
        email TEXT,
        department TEXT,
        year INTEGER,
        registration_date TEXT,
        total_attendance INTEGER DEFAULT 0,
        last_attendance TEXT,
        face_quality REAL DEFAULT 1.0
    )
    """)

    # Attendance table with status and timestamp
    c.execute("""
    CREATE TABLE IF NOT EXISTS attendance(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        name TEXT NOT NULL,
        date TEXT NOT NULL,
        time TEXT NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'Present',
        confidence REAL,
        FOREIGN KEY (student_id) REFERENCES students(student_id)
    )
    """)

    # Admin table
    c.execute("""
    CREATE TABLE IF NOT EXISTS admin(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        email TEXT,
        last_login TEXT
    )
    """)

    # Settings table
    c.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT
    )
    """)

    # Insert default admin if not exists
    c.execute("SELECT * FROM admin WHERE username=?", ('admin',))
    if not c.fetchone():
        hashed_password = hashlib.sha256('admin123'.encode()).hexdigest()
        c.execute("""
        INSERT INTO admin (username, password, email, last_login) 
        VALUES (?, ?, ?, ?)
        """, ('admin', hashed_password, 'admin@system.com', datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    # Insert default settings
    default_settings = [
        ('camera_id', '0'),
        ('detection_threshold', '0.6'),
        ('auto_mark', 'true'),
        ('attendance_window', '5')
    ]

    for key, value in default_settings:
        c.execute("INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                 (key, value, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

def get_setting(key, default=None):
    """Helper to fetch a setting value from the database"""
    try:
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default

# -----------------------
# ENCODING MANAGEMENT
# -----------------------
def load_encodings():
    """Load face encodings from file, create if not exists"""
    if not os.path.exists(ENCODING_FILE):
        logger.info("Creating new encodings file")
        data = {"encodings": [], "names": [], "ids": []}
        save_encodings(data)
        return data

    try:
        with open(ENCODING_FILE, "rb") as f:
            data = pickle.load(f)
            logger.info(f"Loaded {len(data['encodings'])} face encodings")
            return data
    except Exception as e:
        logger.error(f"Error loading encodings: {e}")
        return {"encodings": [], "names": [], "ids": []}

def save_encodings(data):
    """Save face encodings to file"""
    try:
        with open(ENCODING_FILE, "wb") as f:
            pickle.dump(data, f)
        logger.info(f"Saved {len(data['encodings'])} face encodings")
        return True
    except Exception as e:
        logger.error(f"Error saving encodings: {e}")
        return False

# -----------------------
# LOGIN REQUIRED DECORATOR
# -----------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session:
            flash('Please login first!', 'warning')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# -----------------------
# CAMERA & FACE FUNCTIONS
# -----------------------
def capture_face(camera_id=0, warmup_frames=10):
    """Capture face with camera warmup and error handling"""
    try:
        cap = cv2.VideoCapture(camera_id, cv2.CAP_DSHOW)

        if not cap.isOpened():
            logger.error("Failed to open camera")
            return None, "Camera not accessible"

        for i in range(warmup_frames):
            ret, frame = cap.read()
            if not ret:
                logger.warning(f"Frame {i} capture failed")
            time.sleep(0.1)

        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            logger.error("Failed to capture final frame")
            return None, "Failed to capture image"

        logger.info(f"Frame captured successfully: {frame.shape}")
        return frame, None

    except Exception as e:
        logger.error(f"Camera error: {e}")
        return None, str(e)

def detect_faces(frame):
    """Detect faces in frame and return locations and encodings"""
    if frame is None:
        return [], []

    try:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb)

        if not face_locations:
            return [], []

        face_encodings = face_recognition.face_encodings(rgb, face_locations)
        logger.info(f"Detected {len(face_locations)} faces")
        return face_locations, face_encodings

    except Exception as e:
        logger.error(f"Face detection error: {e}")
        return [], []

# -----------------------
# ROUTES
# -----------------------

@app.route("/")
def index():
    """Home page"""
    return render_template("index.html")

@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    """Admin login page"""
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        hashed_password = hashlib.sha256(password.encode()).hexdigest()

        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT * FROM admin WHERE username=? AND password=?",
                 (username, hashed_password))
        admin = c.fetchone()

        if admin:
            c.execute("UPDATE admin SET last_login=? WHERE username=?",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username))
            conn.commit()

            session['admin_logged_in'] = True
            session['admin_username'] = username
            flash(f'Welcome back, {username}!', 'success')
            conn.close()
            return redirect(url_for('admin_panel'))
        else:
            flash('Invalid credentials!', 'danger')

        conn.close()

    return render_template("admin_login.html")

@app.route("/logout")
def logout():
    """Logout admin"""
    session.clear()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('index'))

@app.route("/admin-panel")
@login_required
def admin_panel():
    """Admin dashboard"""
    conn = sqlite3.connect(DATABASE)

    total_students = pd.read_sql_query("SELECT COUNT(*) as count FROM students", conn).iloc[0]['count']
    total_attendance = pd.read_sql_query("SELECT COUNT(*) as count FROM attendance", conn).iloc[0]['count']

    today = datetime.now().strftime("%Y-%m-%d")
    today_attendance = pd.read_sql_query(
        "SELECT COUNT(*) as count FROM attendance WHERE date=?",
        conn, params=[today]
    ).iloc[0]['count']

    recent = pd.read_sql_query("""
        SELECT * FROM attendance 
        ORDER BY timestamp DESC LIMIT 10
    """, conn)

    conn.close()

    return render_template("admin_panel.html",
                         total_students=total_students,
                         total_attendance=total_attendance,
                         today_attendance=today_attendance,
                         recent=recent.to_dict('records'))

@app.route("/manage-students")
@login_required
def manage_students():
    """Manage students page"""
    conn = sqlite3.connect(DATABASE)
    students = pd.read_sql_query("""
        SELECT *, 
        (SELECT COUNT(*) FROM attendance WHERE student_id=students.student_id) as attendance_count 
        FROM students ORDER BY id DESC
    """, conn)
    conn.close()
    return render_template("manage_students.html", students=students.to_dict('records'))

@app.route("/delete-student/<student_id>", methods=["POST"])
@login_required
def delete_student(student_id):
    """Delete student and all associated data"""
    try:
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()

        c.execute("SELECT name FROM students WHERE student_id=?", (student_id,))
        student = c.fetchone()

        if not student:
            conn.close()
            flash('Student not found!', 'danger')
            return redirect(url_for('manage_students'))

        c.execute("DELETE FROM students WHERE student_id=?", (student_id,))
        c.execute("DELETE FROM attendance WHERE student_id=?", (student_id,))

        conn.commit()
        conn.close()

        data = load_encodings()
        if student_id in data['ids']:
            idx = data['ids'].index(student_id)
            data['encodings'].pop(idx)
            data['names'].pop(idx)
            data['ids'].pop(idx)
            save_encodings(data)

        image_path = os.path.join(UPLOAD_FOLDER, f"{student_id}.jpg")
        if os.path.exists(image_path):
            os.remove(image_path)

        flash(f'Student {student[0]} deleted successfully!', 'success')

    except Exception as e:
        logger.error(f"Error deleting student: {e}")
        flash('Error deleting student!', 'danger')

    return redirect(url_for('manage_students'))

@app.route("/register", methods=["GET", "POST"])
def register():
    """Register new student with face capture"""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        student_id = request.form.get("student_id", "").strip()
        email = request.form.get("email", "").strip()
        department = request.form.get("department", "").strip()
        year = request.form.get("year", "").strip()

        if not name or not student_id:
            flash('Name and Student ID are required!', 'danger')
            return redirect(url_for('register'))

        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT * FROM students WHERE student_id=?", (student_id,))
        if c.fetchone():
            conn.close()
            flash('Student ID already exists!', 'danger')
            return redirect(url_for('register'))

        if 'image' not in request.files:
            conn.close()
            flash('No image received!', 'danger')
            return redirect(url_for('register'))

        file = request.files['image']
        img_bytes = file.read()
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            conn.close()
            flash('Invalid image!', 'danger')
            return redirect(url_for('register'))

        face_locations, face_encodings = detect_faces(frame)

        if len(face_locations) == 0:
            conn.close()
            flash('No face detected! Please ensure good lighting and try again.', 'danger')
            return redirect(url_for('register'))

        if len(face_locations) > 1:
            conn.close()
            flash('Multiple faces detected! Please ensure only one person in frame.', 'danger')
            return redirect(url_for('register'))

        encoding = face_encodings[0]

        registration_date = datetime.now().strftime("%Y-%m-%d")
        c.execute("""
        INSERT INTO students (name, student_id, email, department, year, registration_date)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (name, student_id, email, department, year, registration_date))
        conn.commit()
        conn.close()

        data = load_encodings()
        data["encodings"].append(encoding)
        data["names"].append(name)
        data["ids"].append(student_id)
        save_encodings(data)

        top, right, bottom, left = face_locations[0]
        face_image = frame[top:bottom, left:right].copy()
        cv2.imwrite(os.path.join(UPLOAD_FOLDER, f"{student_id}.jpg"), face_image)

        flash(f'✓ Student {name} registered successfully!', 'success')
        return redirect(url_for('index'))

    return render_template("register.html")

@app.route("/attendance", methods=["GET", "POST"])
def attendance():
    """Mark attendance page"""
    if request.method == "POST":
        data = load_encodings()

        if len(data["encodings"]) == 0:
            flash('No registered students found! Please register first.', 'warning')
            return redirect(url_for('register'))

        if 'image' not in request.files:
            flash("No image received!", "danger")
            return redirect(url_for("attendance"))

        file = request.files['image']
        img_bytes = file.read()
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            flash("Invalid image!", "danger")
            return redirect(url_for("attendance"))

        face_locations, face_encodings = detect_faces(frame)

        if len(face_encodings) == 0:
            flash('No face detected! Please ensure good lighting and try again.', 'warning')
            return redirect(url_for('attendance'))

        threshold = float(get_setting('detection_threshold', '0.6'))
        marked = False
        results = []

        for face_encoding in face_encodings:
            matches = face_recognition.compare_faces(data["encodings"], face_encoding)
            face_distances = face_recognition.face_distance(data["encodings"], face_encoding)

            if True in matches:
                best_match = np.argmin(face_distances)
                confidence = 1 - face_distances[best_match]

                if matches[best_match] and confidence > threshold:
                    name = data["names"][best_match]
                    student_id = data["ids"][best_match]

                    date = datetime.now().strftime("%Y-%m-%d")
                    time_str = datetime.now().strftime("%H:%M:%S")

                    conn = sqlite3.connect(DATABASE)
                    c = conn.cursor()

                    c.execute("""
                    SELECT * FROM attendance 
                    WHERE student_id=? AND date=?
                    """, (student_id, date))

                    existing = c.fetchone()

                    if not existing:
                        c.execute("""
                        INSERT INTO attendance (student_id, name, date, time, confidence)
                        VALUES (?, ?, ?, ?, ?)
                        """, (student_id, name, date, time_str, float(confidence)))

                        c.execute("""
                        UPDATE students 
                        SET total_attendance = total_attendance + 1,
                            last_attendance = ?
                        WHERE student_id=?
                        """, (f"{date} {time_str}", student_id))

                        conn.commit()
                        marked = True
                        results.append({"name": name, "status": "marked", "confidence": confidence})
                    else:
                        results.append({"name": name, "status": "already_marked", "confidence": confidence})

                    conn.close()

        if marked:
            flash(f'✓ Attendance marked for {len(results)} student(s)!', 'success')
        else:
            if results:
                flash('Students already marked attendance today!', 'info')
            else:
                flash('Face not recognized! Please register first.', 'danger')

        return redirect(url_for('index'))

    return render_template("attendance.html")

@app.route("/dashboard")
def dashboard():
    """View attendance dashboard"""
    conn = sqlite3.connect(DATABASE)

    query = """
    SELECT a.*, s.department, s.year 
    FROM attendance a
    LEFT JOIN students s ON a.student_id = s.student_id
    ORDER BY a.date DESC, a.time DESC
    LIMIT 100
    """

    df = pd.read_sql_query(query, conn)

    today = datetime.now().strftime("%Y-%m-%d")
    today_count = len(df[df['date'] == today])

    conn.close()

    return render_template("dashboard.html",
                         records=df.to_dict('records'),
                         total_records=len(df),
                         today_count=today_count)

@app.route("/export-excel")
@login_required
def export_excel():
    """Export attendance data to Excel"""
    try:
        conn = sqlite3.connect(DATABASE)

        query = """
        SELECT a.date, a.time, a.name, a.student_id, s.department, s.year, a.confidence
        FROM attendance a
        LEFT JOIN students s ON a.student_id = s.student_id
        ORDER BY a.date DESC, a.time DESC
        """

        df = pd.read_sql_query(query, conn)
        conn.close()

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Attendance', index=False)

            worksheet = writer.sheets['Attendance']
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except Exception:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width

        output.seek(0)
        filename = f"attendance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        logger.error(f"Export error: {e}")
        flash('Error exporting data!', 'danger')
        return redirect(url_for('dashboard'))

@app.route("/statistics")
@login_required
def statistics():
    """View attendance statistics"""
    conn = sqlite3.connect(DATABASE)

    daily_query = """
    SELECT date, COUNT(*) as count
    FROM attendance
    WHERE date >= date('now', '-30 days')
    GROUP BY date
    ORDER BY date
    """
    daily_stats = pd.read_sql_query(daily_query, conn).to_dict('records')

    dept_query = """
    SELECT s.department, COUNT(a.id) as attendance_count, COUNT(DISTINCT s.student_id) as student_count
    FROM students s
    LEFT JOIN attendance a ON s.student_id = a.student_id
    WHERE s.department IS NOT NULL AND s.department != ''
    GROUP BY s.department
    """
    dept_stats = pd.read_sql_query(dept_query, conn).to_dict('records')

    top_query = """
    SELECT s.name, s.student_id, COUNT(a.id) as attendance_count
    FROM students s
    LEFT JOIN attendance a ON s.student_id = a.student_id
    GROUP BY s.student_id
    ORDER BY attendance_count DESC
    LIMIT 10
    """
    top_students = pd.read_sql_query(top_query, conn).to_dict('records')

    conn.close()

    return render_template("statistics.html",
                         daily_stats=daily_stats,
                         dept_stats=dept_stats,
                         top_students=top_students)

@app.route("/api/check-attendance", methods=["POST"])
def check_attendance():
    """API endpoint for checking & auto-marking attendance in Live Stream Kiosk"""
    try:
        data = load_encodings()
        if len(data["encodings"]) == 0:
            return jsonify({"results": [], "message": "No students enrolled"}), 200

        if 'image' not in request.files:
            return jsonify({"error": "No image provided"}), 400

        file = request.files['image']
        if file.filename == '':
            return jsonify({"error": "No image selected"}), 400

        img_bytes = file.read()
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            return jsonify({"error": "Invalid image"}), 400

        face_locations, face_encodings = detect_faces(frame)

        if len(face_encodings) == 0:
            return jsonify({"results": []}), 200

        threshold = float(get_setting('detection_threshold', '0.6'))
        auto_mark = get_setting('auto_mark', 'true') == 'true'

        results = []
        for face_encoding in face_encodings:
            matches = face_recognition.compare_faces(data["encodings"], face_encoding)
            face_distances = face_recognition.face_distance(data["encodings"], face_encoding)

            if True in matches:
                best_match = np.argmin(face_distances)
                confidence = 1 - face_distances[best_match]

                if matches[best_match] and confidence > threshold:
                    name = data["names"][best_match]
                    student_id = data["ids"][best_match]
                    today = datetime.now().strftime("%Y-%m-%d")
                    time_str = datetime.now().strftime("%H:%M:%S")

                    conn = sqlite3.connect(DATABASE)
                    c = conn.cursor()
                    c.execute("""
                    SELECT * FROM attendance 
                    WHERE student_id=? AND date=?
                    """, (student_id, today))

                    if not c.fetchone():
                        if auto_mark:
                            c.execute("""
                            INSERT INTO attendance (student_id, name, date, time, confidence)
                            VALUES (?, ?, ?, ?, ?)
                            """, (student_id, name, today, time_str, float(confidence)))

                            c.execute("""
                            UPDATE students 
                            SET total_attendance = total_attendance + 1,
                                last_attendance = ?
                            WHERE student_id=?
                            """, (f"{today} {time_str}", student_id))
                            conn.commit()

                        results.append({
                            "name": name,
                            "student_id": student_id,
                            "confidence": float(confidence),
                            "status": "new"
                        })
                    else:
                        results.append({
                            "name": name,
                            "student_id": student_id,
                            "confidence": float(confidence),
                            "status": "already_marked"
                        })
                    conn.close()

        return jsonify({"results": results})

    except Exception as e:
        logger.error(f"API error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/live-attendance")
def live_attendance():
    """Live attendance marking page with camera preview"""
    return render_template("live_attendance.html")

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    """System settings page"""
    if request.method == "POST":
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()

        for key in ['camera_id', 'detection_threshold', 'auto_mark', 'attendance_window']:
            if key in request.form:
                c.execute("""
                UPDATE settings SET value=?, updated_at=?
                WHERE key=?
                """, (request.form[key], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), key))

        conn.commit()
        conn.close()
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('settings'))

    conn = sqlite3.connect(DATABASE)
    settings_data = pd.read_sql_query("SELECT * FROM settings", conn)
    conn.close()

    settings_dict = {row['key']: row['value'] for _, row in settings_data.iterrows()}

    return render_template("settings.html", settings=settings_dict)

@app.route("/api/student/<student_id>")
def get_student(student_id):
    """API endpoint to get student details"""
    try:
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("""
            SELECT name, student_id, email, department, year, 
                   registration_date, total_attendance, last_attendance 
            FROM students WHERE student_id=?
        """, (student_id,))
        student = c.fetchone()
        conn.close()

        if student:
            return jsonify({
                'name': student[0],
                'student_id': student[1],
                'email': student[2],
                'department': student[3],
                'year': student[4],
                'registration_date': student[5],
                'attendance_count': student[6],
                'last_attendance': student[7]
            })
        return jsonify({'error': 'Student not found'}), 404

    except Exception as e:
        logger.error(f"Error fetching student: {e}")
        return jsonify({'error': str(e)}), 500

@app.route("/api/attendance/today")
def api_today_attendance():
    """API endpoint to get today's attendance"""
    try:
        conn = sqlite3.connect(DATABASE)
        today = datetime.now().strftime("%Y-%m-%d")
        query = """
        SELECT a.*, s.department, s.year 
        FROM attendance a
        LEFT JOIN students s ON a.student_id = s.student_id
        WHERE a.date = ?
        ORDER BY a.time DESC
        """
        df = pd.read_sql_query(query, conn, params=[today])
        conn.close()

        return jsonify({
            'date': today,
            'count': len(df),
            'records': df.to_dict('records')
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/api/statistics/summary")
def api_statistics_summary():
    """API endpoint for statistics summary"""
    try:
        conn = sqlite3.connect(DATABASE)

        total_students = pd.read_sql_query("SELECT COUNT(*) as count FROM students", conn).iloc[0]['count']
        total_attendance = pd.read_sql_query("SELECT COUNT(*) as count FROM attendance", conn).iloc[0]['count']

        today = datetime.now().strftime("%Y-%m-%d")
        today_attendance = pd.read_sql_query(
            "SELECT COUNT(*) as count FROM attendance WHERE date=?",
            conn, params=[today]
        ).iloc[0]['count']

        conn.close()

        return jsonify({
            'total_students': int(total_students),
            'total_attendance': int(total_attendance),
            'today_attendance': int(today_attendance),
            'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# -----------------------
# TEMPLATE CONTEXT PROCESSORS
# -----------------------
@app.context_processor
def utility_processor():
    def now():
        return datetime.now()
    return dict(now=now)

# -----------------------
# MAIN
# -----------------------
init_db()

if not os.path.exists(ENCODING_FILE):
    save_encodings({"encodings": [], "names": [], "ids": []})

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

if __name__ == "__main__":
    init_db()

    if not os.path.exists(ENCODING_FILE):
        save_encodings({"encodings": [], "names": [], "ids": []})

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    logger.info("Starting Face Recognition Attendance System...")
    logger.info("Access the application at: http://127.0.0.1:5000")
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)