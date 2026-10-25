"""
AttendSmart - Flask + SQLite + Bootstrap
Hackathon project: live timetable status + attendance tracking for students & lecturers.

Run locally:
    python app.py
    # then open http://127.0.0.1:5000

Demo accounts (seeded):
    Roll: HOD001   Password: admin123   Role: lecturer
    Roll: CR2025   Password: admin123   Role: cr
    Roll: 21CS001  Password: admin123   Role: student
    Roll: 21CS002  Password: admin123   Role: student
"""

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, abort
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
import os

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "attend-smart-dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///attend.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
PERIODS = 6  # periods 1..6
STATUS_CHOICES = ["scheduled", "attending", "cancelled"]

# ---------------------------------------------------------------------------
# Database models
# ---------------------------------------------------------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    roll_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # student / lecturer / cr

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(120), nullable=False)

    def __repr__(self):
        return f"<Subject {self.code} {self.name}>"


class TimetableSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    day_of_week = db.Column(db.String(20), nullable=False)
    period_number = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.String(10), nullable=False)
    end_time = db.Column(db.String(10), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False)
    subject = db.relationship("Subject")

    __table_args__ = (
        db.UniqueConstraint("day_of_week", "period_number", name="uq_day_period"),
    )


class ClassSession(db.Model):
    """One instance of a period on a specific date (today's timetable state)."""
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(20), nullable=False, index=True)  # YYYY-MM-DD
    day_of_week = db.Column(db.String(20), nullable=False)
    period_number = db.Column(db.Integer, nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False)
    subject = db.relationship("Subject")
    status = db.Column(db.String(20), nullable=False, default="scheduled")
    class_note = db.Column(db.Text, default="")
    updated_at = db.Column(db.String(30), default="")
    updated_by = db.Column(db.String(60), default="")

    __table_args__ = (
        db.UniqueConstraint("date", "period_number", name="uq_date_period"),
    )


class StudentAttendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_roll = db.Column(db.String(30), nullable=False, index=True)
    session_id = db.Column(db.Integer, db.ForeignKey("class_session.id"), nullable=False)
    present = db.Column(db.Boolean, default=False)
    marked_at = db.Column(db.String(30), default="")

    __table_args__ = (
        db.UniqueConstraint("student_roll", "session_id", name="uq_student_session"),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def login_required(role=None):
    def decorator(fn):
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))
            user = User.query.get(session["user_id"])
            if not user:
                session.clear()
                flash("Account not found. Please log in again.", "danger")
                return redirect(url_for("login"))
            if role and user.role != role and user.role != "cr":
                # CR can also do lecturer actions
                abort(403)
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def current_user():
    if "user_id" not in session:
        return None
    return User.query.get(session["user_id"])


def active_weekday():
    """Return the weekday to show for today's live timetable.

    If the current weekday has timetable slots, use it; otherwise fall back to
    Monday so the live demo works on weekends/holidays too. The date stays
    today's real date so attendance stays day-consistent.
    """
    today_weekday = date.today().strftime("%A")
    if TimetableSlot.query.filter_by(day_of_week=today_weekday).first():
        return today_weekday
    return "Monday"


def ensure_today_sessions():
    """Create ClassSession rows for today based on the active weekday slots."""
    today = date.today().isoformat()
    weekday = active_weekday()
    slots = TimetableSlot.query.filter_by(day_of_week=weekday).order_by(
        TimetableSlot.period_number
    ).all()
    for slot in slots:
        exists = ClassSession.query.filter_by(
            date=today, period_number=slot.period_number
        ).first()
        if not exists:
            sess = ClassSession(
                date=today,
                day_of_week=weekday,
                period_number=slot.period_number,
                subject_id=slot.subject_id,
                status="scheduled",
            )
            db.session.add(sess)
    db.session.commit()


def pct(attended, held):
    if held == 0:
        return 0.0
    return round((attended / held) * 100, 2)


def skip_or_needed(attended, held):
    """
    Returns (kind, count, message)
    kind: 'safe' | 'need' | 'none'
    If >= 75%: how many more classes can be safely skipped.
    If < 75%: how many of the next classes must be attended to reach 75%.
    Cancelled classes are excluded from 'held'.
    """
    if held == 0:
        return "none", 0, "No classes held yet"
    if attended / held >= 0.75:
        # (attended) / (held + x) >= 0.75  ->  x <= (attended - 0.75*held) / 0.75
        import math
        x = (attended - 0.75 * held) / 0.75
        count = max(0, math.floor(x + 1e-9))
        if count == 0:
            return "safe", 0, "You're at 75%. Attend the next class to stay safe."
        return "safe", count, f"You can safely skip {count} more class{'es' if count != 1 else ''}"
    else:
        # (attended + y) / (held + y) >= 0.75 -> y >= (0.75*held - attended) / 0.25
        import math
        y = (0.75 * held - attended) / 0.25
        count = max(1, math.ceil(y - 1e-9))
        return "need", count, f"Attend the next {count} class{'es' if count != 1 else ''} to reach 75%"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        roll = (request.form.get("roll_number") or "").strip()
        pw = request.form.get("password") or ""
        user = User.query.filter_by(roll_number=roll).first()
        if user and user.check_password(pw):
            session["user_id"] = user.id
            session["user_name"] = user.name
            session["user_role"] = user.role
            flash(f"Welcome back, {user.name}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid roll number or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    if user.role in ("lecturer", "cr"):
        return redirect(url_for("lecturer_dashboard"))
    return redirect(url_for("student_dashboard"))


# ------------------------------------------------- lecturer / CR ----------
@app.route("/lecturer")
@login_required()
def lecturer_dashboard():
    user = current_user()
    ensure_today_sessions()
    today = date.today().isoformat()
    weekday = active_weekday()
    slots = TimetableSlot.query.filter_by(day_of_week=weekday).order_by(
        TimetableSlot.period_number
    ).all()
    # map period -> session
    sessions = {}
    for s in ClassSession.query.filter_by(date=today).all():
        sessions[s.period_number] = s
    return render_template(
        "lecturer_dashboard.html",
        user=user,
        days=[weekday],
        slots=slots,
        sessions=sessions,
        today=today,
        status_choices=STATUS_CHOICES,
    )


@app.route("/lecturer/timetable")
@login_required()
def lecturer_timetable():
    """Full weekly editable timetable (slots)."""
    user = current_user()
    days = DAYS
    grid = {}
    for day in days:
        grid[day] = (
            TimetableSlot.query.filter_by(day_of_week=day)
            .order_by(TimetableSlot.period_number)
            .all()
        )
    subjects = Subject.query.order_by(Subject.code).all()
    return render_template(
        "timetable_editor.html", user=user, days=days, grid=grid, subjects=subjects
    )


@app.route("/lecturer/timetable/add", methods=["POST"])
@login_required()
def timetable_add():
    day = request.form.get("day_of_week")
    period = int(request.form.get("period_number"))
    start = request.form.get("start_time")
    end = request.form.get("end_time")
    subject_id = int(request.form.get("subject_id"))
    slot = TimetableSlot(
        day_of_week=day,
        period_number=period,
        start_time=start,
        end_time=end,
        subject_id=subject_id,
    )
    db.session.add(slot)
    db.session.commit()
    flash("Period added to timetable.", "success")
    return redirect(url_for("lecturer_timetable"))


@app.route("/lecturer/timetable/edit/<int:slot_id>", methods=["POST"])
@login_required()
def timetable_edit(slot_id):
    slot = TimetableSlot.query.get_or_404(slot_id)
    slot.start_time = request.form.get("start_time")
    slot.end_time = request.form.get("end_time")
    slot.subject_id = int(request.form.get("subject_id"))
    db.session.commit()
    flash("Timetable period updated.", "success")
    return redirect(url_for("lecturer_timetable"))


@app.route("/lecturer/timetable/delete/<int:slot_id>", methods=["POST"])
@login_required()
def timetable_delete(slot_id):
    slot = TimetableSlot.query.get_or_404(slot_id)
    db.session.delete(slot)
    db.session.commit()
    flash("Period removed from timetable.", "success")
    return redirect(url_for("lecturer_timetable"))


@app.route("/api/session/<int:session_id>/status", methods=["POST"])
@login_required()
def set_session_status(session_id):
    user = current_user()
    sess = ClassSession.query.get_or_404(session_id)
    status = request.form.get("status") or request.json.get("status")
    if status not in STATUS_CHOICES:
        return jsonify({"error": "invalid status"}), 400
    sess.status = status
    sess.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sess.updated_by = f"{user.roll_number} ({user.name})"
    db.session.commit()
    return jsonify({"ok": True, "session_id": sess.id, "status": sess.status})


@app.route("/api/session/<int:session_id>/note", methods=["POST"])
@login_required()
def set_session_note(session_id):
    user = current_user()
    sess = ClassSession.query.get_or_404(session_id)
    note = request.form.get("note") or (request.json or {}).get("note", "")
    sess.class_note = note
    sess.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sess.updated_by = f"{user.roll_number} ({user.name})"
    db.session.commit()
    return jsonify({"ok": True, "session_id": sess.id})


# ------------------------------------------------- student ---------------
@app.route("/student")
@login_required()
def student_dashboard():
    user = current_user()
    ensure_today_sessions()
    today = date.today().isoformat()
    weekday = active_weekday()
    sessions = (
        ClassSession.query.filter_by(date=today)
        .order_by(ClassSession.period_number)
        .all()
    )
    # Day-wise attendance for this student (last 7 days, non-cancelled only)
    day_rows = []
    total_held = 0
    total_attended = 0
    # collect held dates
    held_dates = (
        db.session.query(ClassSession.date)
        .filter(ClassSession.status != "cancelled")
        .distinct()
        .order_by(ClassSession.date.desc())
        .limit(7)
        .all()
    )
    for (d,) in held_dates:
        sessions_d = ClassSession.query.filter_by(date=d).all()
        held = 0
        attended = 0
        for s in sessions_d:
            if s.status == "cancelled":
                continue
            held += 1
            rec = StudentAttendance.query.filter_by(
                student_roll=user.roll_number, session_id=s.id
            ).first()
            if rec and rec.present:
                attended += 1
        day_rows.append(
            {
                "date": d,
                "held": held,
                "attended": attended,
                "pct": pct(attended, held),
            }
        )
        total_held += held
        total_attended += attended

    # Subject-wise attendance
    subject_rows = []
    subj_query = (
        db.session.query(Subject.id, Subject.code, Subject.name)
        .join(ClassSession, ClassSession.subject_id == Subject.id)
        .filter(ClassSession.status != "cancelled")
        .distinct(Subject.id)
        .all()
    )
    for sid, code, name in subj_query:
        sess_list = ClassSession.query.filter_by(subject_id=sid).filter(
            ClassSession.status != "cancelled"
        ).all()
        held = len(sess_list)
        attended = 0
        for s in sess_list:
            rec = StudentAttendance.query.filter_by(
                student_roll=user.roll_number, session_id=s.id
            ).first()
            if rec and rec.present:
                attended += 1
        kind, count, msg = skip_or_needed(attended, held)
        subject_rows.append(
            {
                "code": code,
                "name": name,
                "held": held,
                "attended": attended,
                "pct": pct(attended, held),
                "kind": kind,
                "count": count,
                "msg": msg,
            }
        )
    subject_rows.sort(key=lambda r: r["pct"])

    overall_pct = pct(total_attended, total_held)
    return render_template(
        "student_dashboard.html",
        user=user,
        sessions=sessions,
        today=today,
        weekday=weekday,
        day_rows=day_rows,
        subject_rows=subject_rows,
        total_held=total_held,
        total_attended=total_attended,
        overall_pct=overall_pct,
    )


@app.route("/api/timetable")
@login_required()
def api_timetable():
    """JSON endpoint polled by the student page every 10 seconds."""
    today = date.today().isoformat()
    sessions = (
        ClassSession.query.filter_by(date=today)
        .order_by(ClassSession.period_number)
        .all()
    )
    data = []
    for s in sessions:
        data.append(
            {
                "id": s.id,
                "period": s.period_number,
                "subject": f"{s.subject.code} - {s.subject.name}",
                "status": s.status,
                "note": s.class_note,
                "updated_at": s.updated_at,
                "updated_by": s.updated_by,
            }
        )
    return jsonify({"date": today, "sessions": data})


@app.route("/api/attendance/mark", methods=["POST"])
@login_required()
def mark_attendance():
    user = current_user()
    if user.role != "student":
        return jsonify({"error": "students only"}), 403
    session_id = int(request.form.get("session_id") or (request.json or {}).get("session_id"))
    present = request.form.get("present", "true").lower() == "true"
    sess = ClassSession.query.get_or_404(session_id)
    rec = StudentAttendance.query.filter_by(
        student_roll=user.roll_number, session_id=session_id
    ).first()
    if not rec:
        rec = StudentAttendance(
            student_roll=user.roll_number,
            session_id=session_id,
            present=present,
            marked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        db.session.add(rec)
    else:
        rec.present = present
        rec.marked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.session.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Seed / init
# ---------------------------------------------------------------------------
def seed_database():
    """Seed demo data so the app never looks empty."""
    if User.query.first():
        return  # already seeded

    users = [
        ("HOD001", "Dr. Sharma", "lecturer", "admin123"),
        ("CR2025", "Riya Mehta", "cr", "admin123"),
        ("21CS001", "Arjun Patel", "student", "admin123"),
        ("21CS002", "Sneha Reddy", "student", "admin123"),
        ("21CS003", "Kabir Singh", "student", "admin123"),
    ]
    for roll, name, role, pw in users:
        u = User(roll_number=roll, name=name, role=role)
        u.set_password(pw)
        db.session.add(u)

    subjects = [
        ("CS101", "Data Structures"),
        ("CS102", "Operating Systems"),
        ("CS103", "Database Management"),
        ("EE201", "Circuits & Signals"),
        ("MA101", "Discrete Mathematics"),
        ("HS101", "Technical Communication"),
    ]
    subj_objs = {}
    for code, name in subjects:
        s = Subject(code=code, name=name)
        db.session.add(s)
        db.session.flush()
        subj_objs[code] = s

    # A sample weekly timetable (Monday-Friday, 6 periods)
    sample = [
        ("Monday", 1, "09:00", "09:50", "CS101"),
        ("Monday", 2, "09:55", "10:45", "CS102"),
        ("Monday", 3, "10:50", "11:40", "CS103"),
        ("Monday", 4, "11:45", "12:35", "EE201"),
        ("Monday", 5, "13:30", "14:20", "MA101"),
        ("Monday", 6, "14:25", "15:15", "HS101"),
        ("Tuesday", 1, "09:00", "09:50", "CS102"),
        ("Tuesday", 2, "09:55", "10:45", "MA101"),
        ("Tuesday", 3, "10:50", "11:40", "EE201"),
        ("Tuesday", 4, "11:45", "12:35", "CS103"),
        ("Tuesday", 5, "13:30", "14:20", "CS101"),
        ("Tuesday", 6, "14:25", "15:15", "HS101"),
        ("Wednesday", 1, "09:00", "09:50", "CS103"),
        ("Wednesday", 2, "09:55", "10:45", "HS101"),
        ("Wednesday", 3, "10:50", "11:40", "CS101"),
        ("Wednesday", 4, "11:45", "12:35", "CS102"),
        ("Wednesday", 5, "13:30", "14:20", "EE201"),
        ("Wednesday", 6, "14:25", "15:15", "MA101"),
        ("Thursday", 1, "09:00", "09:50", "MA101"),
        ("Thursday", 2, "09:55", "10:45", "CS101"),
        ("Thursday", 3, "10:50", "11:40", "CS102"),
        ("Thursday", 4, "11:45", "12:35", "HS101"),
        ("Thursday", 5, "13:30", "14:20", "EE201"),
        ("Thursday", 6, "14:25", "15:15", "CS103"),
        ("Friday", 1, "09:00", "09:50", "EE201"),
        ("Friday", 2, "09:55", "10:45", "MA101"),
        ("Friday", 3, "10:50", "11:40", "HS101"),
        ("Friday", 4, "11:45", "12:35", "CS103"),
        ("Friday", 5, "13:30", "14:20", "CS101"),
        ("Friday", 6, "14:25", "15:15", "CS102"),
    ]
    for day, period, start, end, code in sample:
        slot = TimetableSlot(
            day_of_week=day,
            period_number=period,
            start_time=start,
            end_time=end,
            subject_id=subj_objs[code].id,
        )
        db.session.add(slot)

    db.session.commit()

    # Seed some past attendance (last 10 weekdays) so subject-wise has data
    from datetime import timedelta

    today = date.today()
    past_days = []
    d = today
    while len(past_days) < 10:
        d -= timedelta(days=1)
        if d.strftime("%A") in DAYS:
            past_days.append(d)

    import random
    random.seed(42)
    students = ["21CS001", "21CS002", "21CS003"]

    for d in past_days:
        weekday = d.strftime("%A")
        slots = TimetableSlot.query.filter_by(day_of_week=weekday).order_by(
            TimetableSlot.period_number
        ).all()
        for slot in slots:
            # 20% chance the class was cancelled
            is_cancelled = random.random() < 0.2
            status = "cancelled" if is_cancelled else "attending"
            sess = ClassSession(
                date=d.isoformat(),
                day_of_week=weekday,
                period_number=slot.period_number,
                subject_id=slot.subject_id,
                status=status,
                class_note=(
                    "Covered chapter 3, up to sorting algorithms."
                    if slot.subject.code == "CS101" and not is_cancelled
                    else ""
                ),
                updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                updated_by="seed",
            )
            db.session.add(sess)
            db.session.flush()
            if not is_cancelled:
                for roll in students:
                    # ~85% present
                    present = random.random() < 0.85
                    rec = StudentAttendance(
                        student_roll=roll,
                        session_id=sess.id,
                        present=present,
                        marked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    db.session.add(rec)

    db.session.commit()
    print("Database seeded with demo data.")


# ---------------------------------------------------------------------------
# Create tables + seed on startup
# ---------------------------------------------------------------------------
with app.app_context():
    db.create_all()
    seed_database()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
