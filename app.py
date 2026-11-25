import os
import sqlite3
import csv
import io
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "kheiter_election_2026_online_secret_key")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.environ.get("DB_DIR", BASE_DIR)
os.makedirs(DB_DIR, exist_ok=True)

DB_PATH = os.path.join(DB_DIR, "election_attendance.db")
SQL_INIT_PATH = os.path.join(BASE_DIR, "init_db.sql")

TURNOUT_SLOTS = [
    ("slot_0930", "الفترة الأولى (09:30)"),
    ("slot_1400", "الفترة الثانية (14:00)"),
    ("slot_1700", "الفترة الثالثة (17:00)"),
    ("slot_1900", "الفترة الأخيرة (19:00)"),
]

USERS = {
    "admin": {"password": os.environ.get("ADMIN_PASS", "2026"), "role": "admin", "center_id": None, "title": "المندوب البلدي - بلدية الخيثر"},
    "center1": {"password": "101", "role": "center_head", "center_id": 1, "title": "رئيس مركز: ابتدائية حساني قدور"},
    "center2": {"password": "102", "role": "center_head", "center_id": 2, "title": "رئيس مركز: ابتدائية جلولي موسى"},
    "center3": {"password": "103", "role": "center_head", "center_id": 3, "title": "رئيس مركز: ابتدائية خلفاوي غياث"},
    "center4": {"password": "104", "role": "center_head", "center_id": 4, "title": "رئيس مركز: ابتدائية عتيق جلال"},
    "center5": {"password": "105", "role": "center_head", "center_id": 5, "title": "رئيس مركز: ابتدائية قرية سيدي خليفة"},
}

def get_db():
    init_database()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_turnout_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS office_turnout (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            center_id INTEGER NOT NULL,
            center_name TEXT NOT NULL,
            office_name TEXT NOT NULL,
            registered_voters INTEGER NOT NULL DEFAULT 0,
            voters_0930 INTEGER NOT NULL DEFAULT 0,
            voters_1400 INTEGER NOT NULL DEFAULT 0,
            voters_1700 INTEGER NOT NULL DEFAULT 0,
            voters_1900 INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT '',
            UNIQUE(center_id, office_name),
            FOREIGN KEY (center_id) REFERENCES centers(center_id)
        );
    """)
    offices = conn.execute("""
        SELECT DISTINCT center_id, center_name, office_name
        FROM election_staff
        WHERE office_name != 'مركز التصويت'
        ORDER BY center_id, id
    """).fetchall()
    for off in offices:
        conn.execute("""
            INSERT OR IGNORE INTO office_turnout (center_id, center_name, office_name)
            VALUES (?, ?, ?)
        """, (off["center_id"], off["center_name"], off["office_name"]))
    conn.commit()

def init_database():
    if not os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH, timeout=30)
        with open(SQL_INIT_PATH, "r", encoding="utf-8") as f:
            sql_script = f.read()
        sql_script = sql_script.replace("SET NAMES utf8mb4;", "")
        conn.executescript(sql_script)
        conn.executescript("""
            ALTER TABLE election_staff ADD COLUMN status TEXT NOT NULL DEFAULT 'حاضر';
            ALTER TABLE election_staff ADD COLUMN absence_reason TEXT DEFAULT '';
            ALTER TABLE election_staff ADD COLUMN replacement_name TEXT DEFAULT '';
            ALTER TABLE election_staff ADD COLUMN updated_at TEXT DEFAULT '';
        """)
        ensure_turnout_schema(conn)
        conn.commit()
        conn.close()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        ensure_turnout_schema(conn)
        conn.close()

init_database()

@app.route("/", methods=["GET", "POST"])
def login():
    init_database()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = USERS.get(username)
        if user and user["password"] == password:
            session["username"] = username
            session["role"] = user["role"]
            session["center_id"] = user["center_id"]
            session["title"] = user["title"]
            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("center_dashboard", view="absences"))
        flash("اسم المستخدم أو كلمة المرور غير صحيحة", "danger")
    return render_template("login.html", users=USERS)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/center")
def center_dashboard():
    if session.get("role") != "center_head":
        return redirect(url_for("login"))

    center_id = session["center_id"]
    active_view = request.args.get("view", "absences")
    if active_view not in ("absences", "turnout"):
        active_view = "absences"

    office_filter = request.args.get("office", "all")
    status_filter = request.args.get("status", "all")
    search_q = request.args.get("q", "").strip()

    conn = get_db()
    ensure_turnout_schema(conn)
    center = conn.execute("SELECT * FROM centers WHERE center_id = ?", (center_id,)).fetchone()
    offices = conn.execute(
        "SELECT DISTINCT office_name FROM election_staff WHERE center_id = ? ORDER BY id",
        (center_id,)
    ).fetchall()

    query = "SELECT * FROM election_staff WHERE center_id = ?"
    params = [center_id]

    if office_filter != "all":
        query += " AND office_name = ?"
        params.append(office_filter)
    if status_filter != "all":
        query += " AND status = ?"
        params.append(status_filter)
    if search_q:
        query += " AND (last_name_ar LIKE ? OR first_name_ar LIKE ? OR nom_fr LIKE ? OR prenom_fr LIKE ?)"
        like_q = f"%{search_q}%"
        params.extend([like_q, like_q, like_q, like_q])

    query += " ORDER BY page_number, id"
    staff_list = conn.execute(query, params).fetchall()

    stats = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'حاضر' THEN 1 ELSE 0 END) as present_count,
            SUM(CASE WHEN status = 'غائب' THEN 1 ELSE 0 END) as absent_count
        FROM election_staff WHERE center_id = ?
    """, (center_id,)).fetchone()

    turnout_rows = conn.execute("""
        SELECT id, center_id, center_name, office_name,
               voters_0930, voters_1400, voters_1700, voters_1900, updated_at
        FROM office_turnout
        WHERE center_id = ?
        ORDER BY id
    """, (center_id,)).fetchall()
    conn.close()

    turnout_list = []
    sum_0930 = sum_1400 = sum_1700 = sum_1900 = 0
    for r in turnout_rows:
        v0930 = r["voters_0930"] or 0
        v1400 = r["voters_1400"] or 0
        v1700 = r["voters_1700"] or 0
        v1900 = r["voters_1900"] or 0
        sum_0930 += v0930
        sum_1400 += v1400
        sum_1700 += v1700
        sum_1900 += v1900
        turnout_list.append({
            "id": r["id"],
            "office_name": r["office_name"],
            "voters_0930": v0930,
            "voters_1400": v1400,
            "voters_1700": v1700,
            "voters_1900": v1900,
            "updated_at": r["updated_at"],
        })

    center_turnout_totals = {
        "voters_0930": sum_0930,
        "voters_1400": sum_1400,
        "voters_1700": sum_1700,
        "voters_1900": sum_1900,
    }

    return render_template(
        "center_dashboard.html",
        center=center,
        active_view=active_view,
        offices=offices,
        staff_list=staff_list,
        stats=stats,
        office_filter=office_filter,
        status_filter=status_filter,
        search_q=search_q,
        turnout_list=turnout_list,
        center_turnout_totals=center_turnout_totals,
        turnout_slots=TURNOUT_SLOTS,
    )

@app.route("/center/update_turnout/<int:turnout_id>", methods=["POST"])
def update_turnout(turnout_id):
    if session.get("role") != "center_head":
        return redirect(url_for("login"))

    conn = get_db()
    ensure_turnout_schema(conn)
    row = conn.execute("SELECT * FROM office_turnout WHERE id = ?", (turnout_id,)).fetchone()
    if not row or row["center_id"] != session["center_id"]:
        conn.close()
        flash("غير مصرح لك بتعديل بيانات مركز آخر!", "danger")
        return redirect(url_for("center_dashboard", view="turnout"))

    def parse_non_neg_int(field_name, fallback):
        raw_val = request.form.get(field_name, "").strip()
        if raw_val == "":
            return fallback
        try:
            val = int(raw_val)
            return val if val >= 0 else fallback
        except ValueError:
            return fallback

    v0930 = parse_non_neg_int("voters_0930", row["voters_0930"] or 0)
    v1400 = parse_non_neg_int("voters_1400", row["voters_1400"] or 0)
    v1700 = parse_non_neg_int("voters_1700", row["voters_1700"] or 0)
    v1900 = parse_non_neg_int("voters_1900", row["voters_1900"] or 0)

    # تحقق من التسلسل التراكمي للفترات (09:30 <= 14:00 <= 17:00 <= 19:00)
    if not (v0930 <= v1400 <= v1700 <= v1900):
        conn.close()
        flash(f"تنبيه للمكتب ({row['office_name']}): يجب أن يكون عدد المصوتين تصاعدياً أو مساوياً عبر الفترات (09:30 ثم 14:00 ثم 17:00 ثم 19:00).", "warning")
        return redirect(url_for("center_dashboard", view="turnout"))

    # تحقق من عدم تجاوز العدد المسجل (الرقم المحجوز) إذا كان محدداً من طرف المندوب البلدي
    reg = row["registered_voters"] or 0
    if reg > 0 and v1900 > reg:
        conn.close()
        flash(f"تنبيه للمكتب ({row['office_name']}): عدد المصوتين يتجاوز العدد المسجل في المكتب ({reg}). يرجى مراجعة الرقم.", "warning")
        return redirect(url_for("center_dashboard", view="turnout"))

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        UPDATE office_turnout
        SET voters_0930 = ?, voters_1400 = ?, voters_1700 = ?, voters_1900 = ?, updated_at = ?
        WHERE id = ?
    """, (v0930, v1400, v1700, v1900, now_str, turnout_id))
    conn.commit()
    conn.close()

    flash(f"تم حفظ عدد المصوتين للمكتب ({row['office_name']}) بنجاح.", "success")
    return redirect(url_for("center_dashboard", view="turnout"))

@app.route("/toggle_status/<int:staff_id>", methods=["POST"])
def toggle_status(staff_id):
    if "role" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    staff = conn.execute("SELECT * FROM election_staff WHERE id = ?", (staff_id,)).fetchone()
    if not staff:
        conn.close()
        return redirect(url_for("login"))

    if session["role"] == "center_head" and staff["center_id"] != session["center_id"]:
        conn.close()
        flash("غير مصرح لك بتعديل بيانات مركز آخر!", "danger")
        return redirect(url_for("center_dashboard", view="absences"))

    new_status = request.form.get("status", "غائب")
    reason = request.form.get("absence_reason", "").strip()
    replacement = request.form.get("replacement_name", "").strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if new_status == "حاضر":
        reason = ""
        replacement = ""

    conn.execute("""
        UPDATE election_staff 
        SET status = ?, absence_reason = ?, replacement_name = ?, updated_at = ?
        WHERE id = ?
    """, (new_status, reason, replacement, now_str, staff_id))
    conn.commit()
    conn.close()

    flash(f"تم تحديث وضعية ({staff['last_name_ar']} {staff['first_name_ar']}) إلى: {new_status}", "success")
    if session["role"] == "admin":
        return redirect(url_for("admin_dashboard", tab=request.form.get("current_tab", "absences")))
    return redirect(url_for("center_dashboard", view="absences", office=request.form.get("current_office", "all")))

@app.route("/admin")
def admin_dashboard():
    if session.get("role") != "admin":
        return redirect(url_for("login"))

    active_tab = request.args.get("tab", "absences")
    if active_tab not in ("absences", "turnout"):
        active_tab = "absences"

    center_filter = request.args.get("center_id", "all")
    conn = get_db()
    ensure_turnout_schema(conn)

    summary = conn.execute("""
        SELECT 
            c.center_id,
            c.center_name,
            COUNT(s.id) as total,
            SUM(CASE WHEN s.status = 'حاضر' THEN 1 ELSE 0 END) as present_count,
            SUM(CASE WHEN s.status = 'غائب' THEN 1 ELSE 0 END) as absent_count
        FROM centers c
        LEFT JOIN election_staff s ON c.center_id = s.center_id
        GROUP BY c.center_id, c.center_name
        ORDER BY c.center_id
    """).fetchall()

    totals = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'حاضر' THEN 1 ELSE 0 END) as present_count,
            SUM(CASE WHEN status = 'غائب' THEN 1 ELSE 0 END) as absent_count
        FROM election_staff
    """).fetchone()

    query = "SELECT * FROM election_staff WHERE status = 'غائب'"
    params = []
    if center_filter != "all":
        query += " AND center_id = ?"
        params.append(int(center_filter))
    query += " ORDER BY center_id, office_name, id"
    absentees = conn.execute(query, params).fetchall()

    t_query = "SELECT * FROM office_turnout WHERE 1=1"
    t_params = []
    if center_filter != "all":
        t_query += " AND center_id = ?"
        t_params.append(int(center_filter))
    t_query += " ORDER BY center_id, id"
    raw_turnout = conn.execute(t_query, t_params).fetchall()

    all_turnout = conn.execute("SELECT * FROM office_turnout ORDER BY center_id, id").fetchall()
    centers_list = conn.execute("SELECT * FROM centers ORDER BY center_id").fetchall()
    conn.close()

    def calc_pct(voters, reg):
        if not reg or reg <= 0:
            return None
        return round((voters / reg) * 100, 2)

    turnout_offices = []
    total_reg = 0
    total_0930 = total_1400 = total_1700 = total_1900 = 0
    reported_0930 = reported_1400 = reported_1700 = reported_1900 = 0

    for r in raw_turnout:
        reg = r["registered_voters"] or 0
        v0930 = r["voters_0930"] or 0
        v1400 = r["voters_1400"] or 0
        v1700 = r["voters_1700"] or 0
        v1900 = r["voters_1900"] or 0
        total_reg += reg
        total_0930 += v0930
        total_1400 += v1400
        total_1700 += v1700
        total_1900 += v1900
        if v0930 > 0:
            reported_0930 += 1
        if v1400 > 0:
            reported_1400 += 1
        if v1700 > 0:
            reported_1700 += 1
        if v1900 > 0:
            reported_1900 += 1

        turnout_offices.append({
            "id": r["id"],
            "center_id": r["center_id"],
            "center_name": r["center_name"],
            "office_name": r["office_name"],
            "registered_voters": reg,
            "voters_0930": v0930,
            "pct_0930": calc_pct(v0930, reg),
            "voters_1400": v1400,
            "pct_1400": calc_pct(v1400, reg),
            "voters_1700": v1700,
            "pct_1700": calc_pct(v1700, reg),
            "voters_1900": v1900,
            "pct_1900": calc_pct(v1900, reg),
            "updated_at": r["updated_at"],
        })

    center_turnout_summary = []
    for c in centers_list:
        c_rows = [x for x in all_turnout if x["center_id"] == c["center_id"]]
        c_reg = sum(x["registered_voters"] or 0 for x in c_rows)
        c_0930 = sum(x["voters_0930"] or 0 for x in c_rows)
        c_1400 = sum(x["voters_1400"] or 0 for x in c_rows)
        c_1700 = sum(x["voters_1700"] or 0 for x in c_rows)
        c_1900 = sum(x["voters_1900"] or 0 for x in c_rows)
        center_turnout_summary.append({
            "center_id": c["center_id"],
            "center_name": c["center_name"],
            "registered_voters": c_reg,
            "voters_0930": c_0930,
            "pct_0930": calc_pct(c_0930, c_reg),
            "voters_1400": c_1400,
            "pct_1400": calc_pct(c_1400, c_reg),
            "voters_1700": c_1700,
            "pct_1700": calc_pct(c_1700, c_reg),
            "voters_1900": c_1900,
            "pct_1900": calc_pct(c_1900, c_reg),
        })

    turnout_overall = {
        "offices_count": len(turnout_offices),
        "reported_0930": reported_0930,
        "reported_1400": reported_1400,
        "reported_1700": reported_1700,
        "reported_1900": reported_1900,
        "registered_voters": total_reg,
        "voters_0930": total_0930,
        "pct_0930": calc_pct(total_0930, total_reg),
        "voters_1400": total_1400,
        "pct_1400": calc_pct(total_1400, total_reg),
        "voters_1700": total_1700,
        "pct_1700": calc_pct(total_1700, total_reg),
        "voters_1900": total_1900,
        "pct_1900": calc_pct(total_1900, total_reg),
    }

    return render_template(
        "admin_dashboard.html",
        active_tab=active_tab,
        summary=summary,
        totals=totals,
        absentees=absentees,
        center_filter=center_filter,
        turnout_offices=turnout_offices,
        center_turnout_summary=center_turnout_summary,
        turnout_overall=turnout_overall,
    )

@app.route("/admin/update_registered/<int:turnout_id>", methods=["POST"])
def update_registered_voters(turnout_id):
    if session.get("role") != "admin":
        return redirect(url_for("login"))

    raw_val = request.form.get("registered_voters", "").strip()
    try:
        reg_val = int(raw_val)
        if reg_val < 0:
            raise ValueError()
    except ValueError:
        flash("يرجى إدخال عدد صحيح موجب للناخبين المسجلين (الرقم المحجوز).", "danger")
        return redirect(url_for("admin_dashboard", tab="turnout", center_id=request.form.get("current_center_filter", "all")))

    conn = get_db()
    ensure_turnout_schema(conn)
    row = conn.execute("SELECT * FROM office_turnout WHERE id = ?", (turnout_id,)).fetchone()
    if row:
        conn.execute("""
            UPDATE office_turnout SET registered_voters = ? WHERE id = ?
        """, (reg_val, turnout_id))
        conn.commit()
        flash(f"تم تحديث العدد المسجل (الرقم المحجوز) للمكتب ({row['office_name']} - {row['center_name']}) إلى {reg_val}.", "success")
    conn.close()

    return redirect(url_for("admin_dashboard", tab="turnout", center_id=request.form.get("current_center_filter", "all")))

@app.route("/admin/reset_all", methods=["POST"])
def reset_all_data():
    if session.get("role") != "admin":
        return redirect(url_for("login"))

    confirm_password = request.form.get("confirm_password", "").strip()
    admin_password = USERS["admin"]["password"]

    if confirm_password != admin_password:
        flash("كلمة مرور المندوب البلدي غير صحيحة! لم يتم تصفير البيانات.", "danger")
        return redirect(url_for("admin_dashboard"))

    reset_scope = request.form.get("reset_scope", "all")

    conn = get_db()
    ensure_turnout_schema(conn)
    if reset_scope in ("all", "absences"):
        conn.execute("""
            UPDATE election_staff
            SET status = 'حاضر',
                absence_reason = '',
                replacement_name = '',
                updated_at = ''
        """)
    if reset_scope in ("all", "turnout"):
        conn.execute("""
            UPDATE office_turnout
            SET voters_0930 = 0,
                voters_1400 = 0,
                voters_1700 = 0,
                voters_1900 = 0,
                updated_at = ''
        """)
    conn.commit()
    conn.close()

    flash("تمت إعادة ضبط وتصفير البيانات بنجاح! كافة المؤطرين عادوا إلى وضعية (حاضر) وتم تصفير عدادات المصوتين.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/export_absences_csv")
def export_absences_csv():
    if session.get("role") != "admin":
        return redirect(url_for("login"))

    conn = get_db()
    rows = conn.execute("""
        SELECT center_name, office_name, list_type, role_title, last_name_ar, first_name_ar, 
               nom_fr, prenom_fr, birth_date, absence_reason, replacement_name, updated_at
        FROM election_staff
        WHERE status = 'غائب'
        ORDER BY center_id, office_name, id
    """).fetchall()
    conn.close()

    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow([
        "المركز", "المكتب", "نوع القائمة", "المهمة", "اللقب", "الاسم",
        "Nom", "Prenom", "تاريخ الميلاد", "سبب الغياب", "المستخلف (البديل)", "وقت التسجيل"
    ])
    for r in rows:
        writer.writerow([
            r["center_name"], r["office_name"], r["list_type"], r["role_title"],
            r["last_name_ar"], r["first_name_ar"], r["nom_fr"], r["prenom_fr"],
            r["birth_date"], r["absence_reason"], r["replacement_name"], r["updated_at"]
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=تقرير_الغيابات_الخيثر_28_نوفمبر_2026.csv"}
    )

@app.route("/export_turnout_csv")
def export_turnout_csv():
    if session.get("role") != "admin":
        return redirect(url_for("login"))

    conn = get_db()
    ensure_turnout_schema(conn)
    rows = conn.execute("""
        SELECT center_name, office_name, registered_voters,
               voters_0930, voters_1400, voters_1700, voters_1900, updated_at
        FROM office_turnout
        ORDER BY center_id, id
    """).fetchall()
    conn.close()

    def pct_str(v, reg):
        if not reg or reg <= 0:
            return "غير محدد"
        return f"{(v / reg) * 100:.2f}%"

    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow([
        "المركز", "المكتب", "العدد المسجل (الرقم المحجوز)",
        "المصوتون 09:30", "النسبة 09:30",
        "المصوتون 14:00", "النسبة 14:00",
        "المصوتون 17:00", "النسبة 17:00",
        "المصوتون 19:00", "النسبة 19:00",
        "آخر تحديث"
    ])
    for r in rows:
        reg = r["registered_voters"] or 0
        v0930 = r["voters_0930"] or 0
        v1400 = r["voters_1400"] or 0
        v1700 = r["voters_1700"] or 0
        v1900 = r["voters_1900"] or 0
        writer.writerow([
            r["center_name"], r["office_name"], reg,
            v0930, pct_str(v0930, reg),
            v1400, pct_str(v1400, reg),
            v1700, pct_str(v1700, reg),
            v1900, pct_str(v1900, reg),
            r["updated_at"] or "-"
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=تقرير_نسب_المشاركة_الخيثر_28_نوفمبر_2026.csv"}
    )

if __name__ == "__main__":
    init_database()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
