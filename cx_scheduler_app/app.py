import streamlit as st
import streamlit.components.v1 as st_components
import sqlite3
import pandas as pd
import datetime
import hashlib
import secrets
from pathlib import Path

st.set_page_config(
    page_title="CX Scheduler",
    page_icon="📅",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = Path(__file__).parent / "cx_scheduler.db"

ACTIVITY_TYPES = [
    ".", "Chat", "Phones",
    "CA - Studio", "CA - Remote", "HPO",
    "Support", "Design", "GW", "DC", "Advanced Services",
    "Retail", "Retail MOD", "Thank You Notes", "Bridge",
    "Meeting", "Admin", "Break",
    "Bereavement", "FMLA", "Training", "Holiday", "PTO", "VTO", "Sick",
]

TIMEOFF_TYPES = ["PTO", "Sick", "Personal", "Holiday", "Bereavement", "Vacation", "FMLA", "VTO"]

# (bg_hex, text_hex)
ACT_COLORS = {
    "Chat":              ("#DBEAFE", "#1E40AF"),
    "Phones":            ("#D1FAE5", "#065F46"),
    "CA - Studio":       ("#EDE9FE", "#4C1D95"),
    "CA - Remote":       ("#BAE6FD", "#0369A1"),
    "HPO":               ("#FEF9C3", "#854D0E"),
    "Support":           ("#BBF7D0", "#14532D"),
    "GW":                ("#FDE68A", "#92400E"),
    "Design":            ("#FBCFE8", "#9D174D"),
    "DC":                ("#E0E7FF", "#3730A3"),
    "Advanced Services": ("#CCFBF1", "#0F766E"),
    "Retail":            ("#FEE2E2", "#991B1B"),
    "Retail MOD":        ("#FECACA", "#7F1D1D"),
    "Thank You Notes":   ("#E9D5FF", "#6B21A8"),
    "Bridge":            ("#C7D2FE", "#3730A3"),
    "Meeting":           ("#FEF08A", "#713F12"),
    "Admin":             ("#E2E8F0", "#475569"),
    "Break":             ("#F1F5F9", "#94A3B8"),
    "Bereavement":       ("#FEE2E2", "#991B1B"),
    "FMLA":              ("#FEE2E2", "#7F1D1D"),
    "Training":          ("#BAE6FD", "#075985"),
    "Holiday":           ("#A7F3D0", "#064E3B"),
    "PTO":               ("#FED7AA", "#9A3412"),
    "VTO":               ("#FDE68A", "#78350F"),
    "Sick":              ("#FECACA", "#7F1D1D"),
    ".":                 ("#F8FAFC", "#CBD5E1"),
}

SLOT_W = 26   # px per 30-min slot in timeline

def _make_time_slots():
    slots = []
    t = datetime.time(6, 30)
    end = datetime.time(22, 0)
    while t <= end:
        slots.append(t.strftime("%I:%M %p").lstrip("0"))
        dt = datetime.datetime.combine(datetime.date.today(), t) + datetime.timedelta(minutes=30)
        t = dt.time()
    return slots

TIME_SLOTS = _make_time_slots()
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def _fmt_slot(slot_str):
    """'9:00 AM' → '9a',  '9:30 AM' → '930a',  '12:00 PM' → '12p'"""
    try:
        time_part, ampm = slot_str.split(" ")
        h, m = time_part.split(":")
        suffix = "a" if ampm == "AM" else "p"
        return f"{h}{suffix}" if m == "00" else f"{h}{m}{suffix}"
    except Exception:
        return slot_str


# ─── DATABASE ─────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            color TEXT NOT NULL DEFAULT '#2563EB',
            description TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            team_name TEXT NOT NULL DEFAULT 'Support',
            employment_type TEXT NOT NULL DEFAULT 'FT',
            weekly_hours INTEGER DEFAULT 40,
            work_days TEXT DEFAULT 'Mon,Tue,Wed,Thu,Fri',
            notes TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS schedule_cells (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            day_index INTEGER NOT NULL,
            time_slot TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            activity TEXT NOT NULL DEFAULT '.',
            UNIQUE(week_start, day_index, time_slot, agent_name)
        );
        CREATE TABLE IF NOT EXISTS time_off_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submitted_date TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            team_name TEXT NOT NULL DEFAULT '',
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'PTO',
            status TEXT NOT NULL DEFAULT 'Pending',
            approved_by TEXT DEFAULT '',
            notes TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'viewer',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            bg_color TEXT NOT NULL DEFAULT '#F1F5F9',
            fg_color TEXT NOT NULL DEFAULT '#64748B',
            is_default INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 99
        );
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS template_cells (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL,
            day_index INTEGER NOT NULL,
            time_slot TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            activity TEXT NOT NULL DEFAULT '.',
            UNIQUE(template_id, day_index, time_slot, agent_name),
            FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE CASCADE
        );
    """)
    conn.commit()

    def _col_names(table):
        return [row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()]

    if "team_name" not in _col_names("agents") and "team" in _col_names("agents"):
        c.executescript("""
            ALTER TABLE agents RENAME TO agents_old;
            CREATE TABLE agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                team_name TEXT NOT NULL DEFAULT 'Support',
                employment_type TEXT NOT NULL DEFAULT 'FT',
                weekly_hours INTEGER DEFAULT 40,
                work_days TEXT DEFAULT 'Mon,Tue,Wed,Thu,Fri',
                notes TEXT DEFAULT ''
            );
            INSERT INTO agents (id, name, team_name, employment_type, weekly_hours, work_days, notes)
                SELECT id, name, team, employment_type, weekly_hours, work_days, COALESCE(notes,'') FROM agents_old;
            DROP TABLE agents_old;
        """)
        conn.commit()

    if "team_name" not in _col_names("time_off_requests"):
        if "team" in _col_names("time_off_requests"):
            c.executescript("""
                ALTER TABLE time_off_requests RENAME TO time_off_requests_old;
                CREATE TABLE time_off_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    submitted_date TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    team_name TEXT NOT NULL DEFAULT '',
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'PTO',
                    status TEXT NOT NULL DEFAULT 'Pending',
                    approved_by TEXT DEFAULT '',
                    notes TEXT DEFAULT ''
                );
                INSERT INTO time_off_requests (id, submitted_date, agent_name, team_name, start_date, end_date, type, status, approved_by, notes)
                    SELECT id, submitted_date, agent_name, team, start_date, end_date, type, status, approved_by, COALESCE(notes,'') FROM time_off_requests_old;
                DROP TABLE time_off_requests_old;
            """)
        else:
            c.execute("ALTER TABLE time_off_requests ADD COLUMN team_name TEXT NOT NULL DEFAULT ''")
        conn.commit()

    if "notes" not in _col_names("agents"):
        c.execute("ALTER TABLE agents ADD COLUMN notes TEXT DEFAULT ''")
        conn.commit()

    if c.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 0:
        c.executemany(
            "INSERT OR IGNORE INTO teams (name, color, description) VALUES (?,?,?)",
            [
                ("Support", "#2563EB", "Customer support agents — chat, phones, and back-office"),
                ("Retail",  "#16A34A", "Retail support — studio and remote locations"),
            ]
        )
        conn.commit()

    if c.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0:
        sample = [
            ("Alex Chen",       "Support", "FT", 40, "Mon,Tue,Wed,Thu,Fri"),
            ("Maria Santos",    "Support", "FT", 40, "Mon,Tue,Wed,Thu,Fri"),
            ("Jordan Lee",      "Support", "PT", 25, "Mon,Tue,Wed,Thu,Fri"),
            ("Taylor Brown",    "Support", "FT", 40, "Tue,Wed,Thu,Fri,Sat"),
            ("Casey Williams",  "Support", "FT", 40, "Mon,Tue,Wed,Thu,Fri"),
            ("Sam Johnson",     "Retail",  "FT", 40, "Mon,Tue,Wed,Thu,Fri"),
            ("Morgan Davis",    "Retail",  "FT", 40, "Mon,Tue,Thu,Fri,Sat"),
            ("Riley Martinez",  "Retail",  "PT", 30, "Mon,Tue,Wed,Thu,Fri"),
            ("Devon Thompson",  "Retail",  "FT", 40, "Mon,Tue,Wed,Thu,Fri"),
            ("Jamie Wilson",    "Retail",  "FT", 40, "Tue,Wed,Thu,Fri,Sat"),
        ]
        c.executemany(
            "INSERT OR IGNORE INTO agents (name, team_name, employment_type, weekly_hours, work_days) VALUES (?,?,?,?,?)",
            sample
        )
        conn.commit()

    if c.execute("SELECT COUNT(*) FROM time_off_requests").fetchone()[0] == 0:
        today = datetime.date.today()
        nxt = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(weeks=1)
        reqs = [
            (str(today), "Alex Chen",      "Support", str(nxt),                             str(nxt),                             "PTO",      "Approved", "Scott M.", ""),
            (str(today), "Jordan Lee",     "Support", str(nxt+datetime.timedelta(4)),        str(nxt+datetime.timedelta(4)),        "Personal", "Pending",  "", ""),
            (str(today), "Sam Johnson",    "Retail",  str(nxt+datetime.timedelta(1)),        str(nxt+datetime.timedelta(1)),        "PTO",      "Approved", "Scott M.", ""),
            (str(today), "Devon Thompson", "Retail",  str(nxt+datetime.timedelta(7)),        str(nxt+datetime.timedelta(11)),       "Vacation", "Pending",  "", "Full week"),
        ]
        c.executemany(
            "INSERT INTO time_off_requests (submitted_date,agent_name,team_name,start_date,end_date,type,status,approved_by,notes) VALUES (?,?,?,?,?,?,?,?,?)",
            reqs
        )
        conn.commit()
    if c.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 0:
        seed_acts = [
            (n, bg, fg, 1, i)
            for i, (n, (bg, fg)) in enumerate(ACT_COLORS.items())
            if n != "."
        ]
        c.executemany(
            "INSERT OR IGNORE INTO activities (name,bg_color,fg_color,is_default,sort_order) VALUES (?,?,?,?,?)",
            seed_acts,
        )
        conn.commit()

    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        salt = secrets.token_hex(16)
        h = hashlib.sha256(f"{salt}:admin".encode()).hexdigest()
        c.execute(
            "INSERT OR IGNORE INTO users (username,password_hash,display_name,role,active,created_at) VALUES (?,?,?,?,?,?)",
            ("admin", f"{salt}:{h}", "Admin", "admin", 1, str(datetime.date.today())),
        )
        conn.commit()

    conn.close()


# ─── AUTH ─────────────────────────────────────────────────────────────────────

def _hash_pw(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{h}"

def _verify_pw(password, stored):
    try:
        salt, h = stored.split(":", 1)
        return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest() == h
    except Exception:
        return False

def current_user():
    return st.session_state.get("cx_user")

def is_admin():
    u = current_user()
    return bool(u and u["role"] == "admin")

def can_edit():
    u = current_user()
    return bool(u and u["role"] in ("admin", "editor"))

def get_user_by_username(username):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None

def list_users():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users ORDER BY role, username").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def create_user(username, password, display_name, role):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users (username,password_hash,display_name,role,active,created_at) VALUES (?,?,?,?,?,?)",
            (username.strip(), _hash_pw(password), display_name.strip(), role, 1, str(datetime.date.today())),
        )
        conn.commit()
        conn.close()
        return True, "User created."
    except sqlite3.IntegrityError:
        conn.close()
        return False, f"Username '{username}' already exists."

def update_user(user_id, display_name, role, active):
    conn = get_conn()
    conn.execute(
        "UPDATE users SET display_name=?,role=?,active=? WHERE id=?",
        (display_name, role, int(active), user_id),
    )
    conn.commit()
    conn.close()

def reset_password(user_id, new_password):
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (_hash_pw(new_password), user_id))
    conn.commit()
    conn.close()

def delete_user_db(user_id):
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

def show_login():
    st.markdown("""
    <style>
    .login-wrap{max-width:380px;margin:80px auto 0;background:white;
                border-radius:4px;padding:40px 36px;border:1px solid #D8D8D8;
                box-shadow:0 2px 16px rgba(29,32,25,0.06)}
    .login-logo{font-family:'Cheltenham',Georgia,serif;font-size:24px;font-weight:bold;
                color:#1D2019;margin-bottom:4px;letter-spacing:-0.01em}
    .login-brand{font-family:'DM Sans',Helvetica,sans-serif;font-size:10px;
                 color:#89AC9E;letter-spacing:0.15em;text-transform:uppercase;margin-bottom:28px}
    </style>
    <div class="login-wrap">
        <div class="login-logo">CX Scheduler</div>
        <div class="login-brand">Framebridge</div>
    </div>""", unsafe_allow_html=True)

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)

    if submitted:
        user = get_user_by_username(username)
        if user and user["active"] and _verify_pw(password, user["password_hash"]):
            st.session_state["cx_user"] = {
                "id": user["id"],
                "username": user["username"],
                "display_name": user["display_name"] or user["username"],
                "role": user["role"],
            }
            st.rerun()
        else:
            st.error("Invalid username or password.")

    st.stop()


# ─── ACTIVITIES ───────────────────────────────────────────────────────────────

def get_activities():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM activities ORDER BY sort_order, name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_activity_names():
    """Return [".", ...all activities ordered...]"""
    return ["."] + [a["name"] for a in get_activities()]

def get_act_colors():
    """Return {name: (bg_hex, fg_hex)} from DB, with '.' entry included."""
    colors = {".": ("#F8FAFC", "#CBD5E1")}
    for a in get_activities():
        colors[a["name"]] = (a["bg_color"], a["fg_color"])
    return colors

def upsert_activity(name, bg_color, fg_color, activity_id=None, sort_order=99):
    conn = get_conn()
    try:
        if activity_id:
            conn.execute(
                "UPDATE activities SET name=?,bg_color=?,fg_color=? WHERE id=?",
                (name, bg_color, fg_color, activity_id),
            )
        else:
            # New activity — put it after defaults
            conn.execute(
                "INSERT INTO activities (name,bg_color,fg_color,is_default,sort_order) VALUES (?,?,?,?,?)",
                (name.strip(), bg_color, fg_color, 0, sort_order),
            )
        conn.commit()
        conn.close()
        return True, "Saved."
    except sqlite3.IntegrityError:
        conn.close()
        return False, f"Activity '{name}' already exists."

def delete_activity_db(activity_id):
    conn = get_conn()
    conn.execute("DELETE FROM activities WHERE id=?", (activity_id,))
    conn.commit()
    conn.close()


def get_teams():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM teams ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_team_color(team_name):
    conn = get_conn()
    row = conn.execute("SELECT color FROM teams WHERE name=?", (team_name,)).fetchone()
    conn.close()
    return row["color"] if row else "#94A3B8"

def upsert_team(name, color, description, team_id=None):
    conn = get_conn()
    try:
        if team_id:
            conn.execute("UPDATE teams SET name=?,color=?,description=? WHERE id=?",
                         (name, color, description, team_id))
        else:
            conn.execute("INSERT INTO teams (name,color,description) VALUES (?,?,?)",
                         (name, color, description))
        conn.commit()
        conn.close()
        return True, "Saved."
    except sqlite3.IntegrityError:
        conn.close()
        return False, f"A team named '{name}' already exists."

def delete_team(team_id):
    conn = get_conn()
    conn.execute("DELETE FROM teams WHERE id=?", (team_id,))
    conn.commit()
    conn.close()

def get_agents(team_filter=None):
    conn = get_conn()
    if team_filter:
        rows = conn.execute("SELECT * FROM agents WHERE team_name=? ORDER BY name", (team_filter,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM agents ORDER BY team_name, name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_agent_names(team_filter=None):
    return [a["name"] for a in get_agents(team_filter)]

def upsert_agent(name, team_name, emp_type, hours, work_days, notes, agent_id=None):
    conn = get_conn()
    try:
        if agent_id:
            conn.execute(
                "UPDATE agents SET name=?,team_name=?,employment_type=?,weekly_hours=?,work_days=?,notes=? WHERE id=?",
                (name, team_name, emp_type, hours, work_days, notes, agent_id)
            )
        else:
            conn.execute(
                "INSERT INTO agents (name,team_name,employment_type,weekly_hours,work_days,notes) VALUES (?,?,?,?,?,?)",
                (name, team_name, emp_type, hours, work_days, notes)
            )
        conn.commit()
        conn.close()
        return True, "Saved."
    except sqlite3.IntegrityError:
        conn.close()
        return False, f"An agent named '{name}' already exists."

def delete_agent(agent_id):
    conn = get_conn()
    conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))
    conn.commit()
    conn.close()

def get_schedule_df(week_start, day_index, agent_names):
    conn = get_conn()
    rows = conn.execute(
        "SELECT time_slot, agent_name, activity FROM schedule_cells WHERE week_start=? AND day_index=?",
        (week_start, day_index)
    ).fetchall()
    conn.close()
    data = {n: {t: "." for t in TIME_SLOTS} for n in agent_names}
    for r in rows:
        if r["agent_name"] in data:
            data[r["agent_name"]][r["time_slot"]] = r["activity"]
    df = pd.DataFrame(data, index=TIME_SLOTS)
    df.index.name = "Time"
    return df

def save_schedule_df(week_start, day_index, df):
    conn = get_conn()
    c = conn.cursor()
    for slot in df.index:
        for agent in df.columns:
            act = str(df.at[slot, agent])
            c.execute("""
                INSERT INTO schedule_cells (week_start,day_index,time_slot,agent_name,activity)
                VALUES (?,?,?,?,?)
                ON CONFLICT(week_start,day_index,time_slot,agent_name)
                DO UPDATE SET activity=excluded.activity
            """, (week_start, day_index, slot, agent, act))
    conn.commit()
    conn.close()

def copy_week(src, tgt):
    conn = get_conn()
    c = conn.cursor()
    if c.execute("SELECT COUNT(*) FROM schedule_cells WHERE week_start=?", (tgt,)).fetchone()[0]:
        conn.close()
        return False, f"Week of {tgt} already has data."
    c.execute("""
        INSERT INTO schedule_cells (week_start,day_index,time_slot,agent_name,activity)
        SELECT ?,day_index,time_slot,agent_name,activity FROM schedule_cells WHERE week_start=?
    """, (tgt, src))
    conn.commit()
    conn.close()
    return True, f"Copied to week of {tgt}."

def apply_approved_timeoff(week_start):
    week_date = datetime.date.fromisoformat(week_start)
    conn = get_conn()
    c = conn.cursor()
    approved = c.execute("SELECT * FROM time_off_requests WHERE status='Approved'").fetchall()
    count = 0
    for req in approved:
        s = datetime.date.fromisoformat(req["start_date"])
        e = datetime.date.fromisoformat(req["end_date"])
        for di, dd in enumerate([week_date + datetime.timedelta(days=i) for i in range(7)]):
            if s <= dd <= e:
                for slot in TIME_SLOTS:
                    c.execute("""
                        INSERT INTO schedule_cells (week_start,day_index,time_slot,agent_name,activity)
                        VALUES (?,?,?,?,?)
                        ON CONFLICT(week_start,day_index,time_slot,agent_name)
                        DO UPDATE SET activity=excluded.activity
                    """, (week_start, di, slot, req["agent_name"], req["type"]))
                    count += 1
    conn.commit()
    conn.close()
    return count

# ─── TEMPLATES ────────────────────────────────────────────────────────────────

def get_templates():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM templates ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_template(template_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def create_template(name, description="", created_by=""):
    conn = get_conn()
    today = str(datetime.date.today())
    try:
        conn.execute(
            "INSERT INTO templates (name,description,created_by,created_at,updated_at) VALUES (?,?,?,?,?)",
            (name.strip(), description, created_by, today, today),
        )
        conn.commit()
        template_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return True, template_id, "Template created."
    except sqlite3.IntegrityError:
        conn.close()
        return False, None, f"A template named '{name}' already exists."

def update_template_meta(template_id, name, description):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE templates SET name=?,description=?,updated_at=? WHERE id=?",
            (name.strip(), description, str(datetime.date.today()), template_id),
        )
        conn.commit()
        conn.close()
        return True, "Saved."
    except sqlite3.IntegrityError:
        conn.close()
        return False, f"A template named '{name}' already exists."

def delete_template(template_id):
    conn = get_conn()
    conn.execute("DELETE FROM template_cells WHERE template_id=?", (template_id,))
    conn.execute("DELETE FROM templates WHERE id=?", (template_id,))
    conn.commit()
    conn.close()

def get_template_df(template_id, day_index, agent_names):
    conn = get_conn()
    rows = conn.execute(
        "SELECT time_slot, agent_name, activity FROM template_cells WHERE template_id=? AND day_index=?",
        (template_id, day_index),
    ).fetchall()
    conn.close()
    data = {n: {t: "." for t in TIME_SLOTS} for n in agent_names}
    for r in rows:
        if r["agent_name"] in data:
            data[r["agent_name"]][r["time_slot"]] = r["activity"]
    df = pd.DataFrame(data, index=TIME_SLOTS)
    df.index.name = "Time"
    return df

def save_template_df(template_id, day_index, df):
    conn = get_conn()
    c = conn.cursor()
    for slot in df.index:
        for agent in df.columns:
            act = str(df.at[slot, agent])
            c.execute("""
                INSERT INTO template_cells (template_id,day_index,time_slot,agent_name,activity)
                VALUES (?,?,?,?,?)
                ON CONFLICT(template_id,day_index,time_slot,agent_name)
                DO UPDATE SET activity=excluded.activity
            """, (template_id, day_index, slot, agent, act))
    conn.commit()
    conn.close()

def save_week_as_template(week_start, template_name, description="", created_by=""):
    """Copy all schedule_cells for week_start into a new template."""
    ok, template_id, msg = create_template(template_name, description, created_by)
    if not ok:
        return False, msg
    conn = get_conn()
    conn.execute("""
        INSERT INTO template_cells (template_id, day_index, time_slot, agent_name, activity)
        SELECT ?, day_index, time_slot, agent_name, activity
        FROM schedule_cells WHERE week_start=?
    """, (template_id, week_start))
    conn.commit()
    conn.close()
    return True, template_id, f"Saved as template '{template_name}'."

def apply_template_to_week(template_id, week_start):
    """Copy template_cells into schedule_cells for the given week, overwriting conflicts."""
    conn = get_conn()
    c = conn.cursor()
    rows = c.execute(
        "SELECT day_index, time_slot, agent_name, activity FROM template_cells WHERE template_id=?",
        (template_id,),
    ).fetchall()
    count = 0
    for row in rows:
        c.execute("""
            INSERT INTO schedule_cells (week_start, day_index, time_slot, agent_name, activity)
            VALUES (?,?,?,?,?)
            ON CONFLICT(week_start,day_index,time_slot,agent_name)
            DO UPDATE SET activity=excluded.activity
        """, (week_start, row["day_index"], row["time_slot"], row["agent_name"], row["activity"]))
        count += 1
    conn.commit()
    conn.close()
    return count

def duplicate_template(src_id, new_name):
    """Clone an existing template under a new name."""
    src = get_template(src_id)
    if not src:
        return False, None, "Source template not found."
    user = current_user()
    ok, new_id, msg = create_template(new_name, src["description"],
                                      user["display_name"] if user else "")
    if not ok:
        return False, None, msg
    conn = get_conn()
    conn.execute("""
        INSERT INTO template_cells (template_id, day_index, time_slot, agent_name, activity)
        SELECT ?, day_index, time_slot, agent_name, activity
        FROM template_cells WHERE template_id=?
    """, (new_id, src_id))
    conn.commit()
    conn.close()
    return True, new_id, f"Duplicated as '{new_name}'."


def get_time_off_requests(status=None):
    conn = get_conn()
    if status:
        rows = conn.execute("SELECT * FROM time_off_requests WHERE status=? ORDER BY submitted_date DESC", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM time_off_requests ORDER BY submitted_date DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_request_status(req_id, status, approved_by=""):
    conn = get_conn()
    conn.execute("UPDATE time_off_requests SET status=?,approved_by=? WHERE id=?", (status, approved_by, req_id))
    conn.commit()
    conn.close()

def add_time_off_request(agent, team, start, end, rtype, notes=""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO time_off_requests (submitted_date,agent_name,team_name,start_date,end_date,type,status,notes) VALUES (?,?,?,?,?,?,?,?)",
        (str(datetime.date.today()), agent, team, str(start), str(end), rtype, "Pending", notes)
    )
    conn.commit()
    conn.close()

# ─── TIMELINE HTML ────────────────────────────────────────────────────────────

def build_timeline_html(agents_info, schedule_data, act_colors=None):
    """
    Transposed grid layout: times down the left, agent names across the top.
    agents_info: list of {"name": str, "team_name": str, "color": str}
    schedule_data: {agent_name: {time_slot: activity}}
    act_colors: {name: (bg_hex, fg_hex)} — if None, falls back to module-level ACT_COLORS
    Returns a scrollable HTML table.
    """
    _colors = act_colors if act_colors is not None else ACT_COLORS
    _INACTIVE_LOCAL = {".", "Break", "Admin", "PTO", "VTO", "Sick",
                       "Holiday", "Bereavement", "FMLA", "Training", "Meeting"}
    _ON_QUEUE_LOCAL = {"Chat", "Phones"}

    TIME_COL_W  = 54   # px — left time-label column
    AGENT_COL_W = 96   # px — each agent column
    ROW_H       = 26   # px — each time-slot row
    FONT        = "'DM Sans','Apercu Pro',Helvetica,Arial,sans-serif"

    # ── Header row: one <th> per agent ────────────────────────────────────────
    agent_ths = ""
    for ag in agents_info:
        name = ag["name"]
        team_color = ag.get("color", "#89AC9E")
        parts = name.split()
        short = parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."
        initials = "".join(p[0] for p in parts[:2]).upper()
        agent_ths += (
            f'<th title="{name}" style="'
            f'position:sticky;top:0;z-index:2;'
            f'width:{AGENT_COL_W}px;min-width:{AGENT_COL_W}px;max-width:{AGENT_COL_W}px;'
            f'background:#1D2019;color:#FFF9F4;'
            f'font-size:10px;font-weight:600;font-family:{FONT};'
            f'text-align:center;padding:4px 2px;'
            f'border:1px solid rgba(255,255,255,0.14);'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
            f'box-sizing:border-box">'
            f'<div style="font-size:9px;color:{team_color};margin-bottom:2px;'
            f'font-weight:700;letter-spacing:0.05em">{initials}</div>'
            f'{short}'
            f'</th>'
        )

    # Summary column headers (Active count, On-Queue count)
    sum_th_style = (
        f'position:sticky;top:0;z-index:2;min-width:42px;'
        f'font-size:9px;font-weight:700;font-family:{FONT};'
        f'text-align:center;padding:4px 2px;border:1px solid rgba(255,255,255,0.14);'
        f'letter-spacing:0.06em;text-transform:uppercase'
    )
    summary_ths = (
        f'<th style="{sum_th_style};background:#1A3A6A;color:#BFDBFE">Active</th>'
        f'<th style="{sum_th_style};background:#0C3047;color:#BAE6FD">Queue</th>'
    )

    # ── Body: one <tr> per time slot ──────────────────────────────────────────
    rows_html = ""
    for slot in TIME_SLOTS:
        label   = _fmt_slot(slot)
        is_hour = slot.split(":")[1].startswith("00")

        # Time label cell (sticky left column)
        time_bg     = "#1D2019" if is_hour else "#252520"
        time_color  = "#FFF9F4" if is_hour else "#8A8880"
        time_weight = "700"     if is_hour else "400"
        time_fsize  = "10px"    if is_hour else "9px"
        border_top  = "border-top:2px solid rgba(255,255,255,0.22);" if is_hour else ""
        time_td = (
            f'<td style="position:sticky;left:0;z-index:1;'
            f'width:{TIME_COL_W}px;min-width:{TIME_COL_W}px;height:{ROW_H}px;'
            f'background:{time_bg};color:{time_color};'
            f'font-size:{time_fsize};font-weight:{time_weight};font-family:{FONT};'
            f'text-align:right;padding:0 8px;'
            f'border:1px solid rgba(255,255,255,0.1);{border_top}'
            f'white-space:nowrap;box-sizing:border-box">{label}</td>'
        )

        # One cell per agent
        active_count = 0
        queue_count  = 0
        agent_tds    = ""
        for ag in agents_info:
            act = schedule_data.get(ag["name"], {}).get(slot, ".")
            c_bg, c_fg = _colors.get(act, ("#F8F8F6", "#AAAAAA"))
            if act not in _INACTIVE_LOCAL:
                active_count += 1
            if act in _ON_QUEUE_LOCAL:
                queue_count += 1
            lbl = "" if act == "." else act
            agent_tds += (
                f'<td title="{ag["name"]}: {act}" style="'
                f'background:{c_bg};color:{c_fg};'
                f'font-size:8px;font-weight:700;font-family:{FONT};'
                f'text-align:center;vertical-align:middle;'
                f'height:{ROW_H}px;width:{AGENT_COL_W}px;'
                f'border:1px solid rgba(0,0,0,0.22);{border_top}'
                f'overflow:hidden;white-space:nowrap;box-sizing:border-box">{lbl}</td>'
            )

        # Summary cells
        n = max(len(agents_info), 1)
        a_op  = round(0.12 + 0.55 * min(active_count / n, 1.0), 3) if active_count else 0.06
        q_op  = round(0.12 + 0.55 * min(queue_count  / n, 1.0), 3) if queue_count  else 0.06
        a_bg  = f"rgba(29,78,216,{a_op})"
        q_bg  = f"rgba(3,105,161,{q_op})"
        sum_td_base = (
            f'font-size:9px;font-weight:700;font-family:{FONT};'
            f'text-align:center;vertical-align:middle;height:{ROW_H}px;'
            f'border:1px solid rgba(0,0,0,0.18);{border_top}'
            f'box-sizing:border-box'
        )
        summary_tds = (
            f'<td style="background:{a_bg};color:#1E3A8A;{sum_td_base}">'
            f'{active_count if active_count else ""}</td>'
            f'<td style="background:{q_bg};color:#0C4A6E;{sum_td_base}">'
            f'{queue_count if queue_count else ""}</td>'
        )

        rows_html += f"<tr>{time_td}{agent_tds}{summary_tds}</tr>\n"

    # Corner cell for the sticky top-left intersection
    corner = (
        f'<th style="position:sticky;top:0;left:0;z-index:4;'
        f'width:{TIME_COL_W}px;min-width:{TIME_COL_W}px;background:#1D2019;'
        f'color:#6B7280;font-size:9px;font-weight:600;font-family:{FONT};'
        f'text-align:right;padding:0 8px;'
        f'border:1px solid rgba(255,255,255,0.14);'
        f'letter-spacing:0.08em;text-transform:uppercase">TIME</th>'
    )

    css = f"""
    <style>
    .tl-wrap {{
        border:1px solid #D8D8D8;
        border-radius:10px;
        overflow:hidden;
        background:#FFFFFF;
        font-family:{FONT};
    }}
    .tl-scroll {{
        overflow:auto;
        max-height:640px;
    }}
    .tl-table {{
        border-collapse:collapse;
        table-layout:fixed;
    }}
    </style>"""

    html = f"""{css}
    <div class="tl-wrap">
        <div class="tl-scroll">
            <table class="tl-table">
                <thead>
                    <tr>{corner}{agent_ths}{summary_ths}</tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
        </div>
    </div>"""
    return html

# ─── COVERAGE BAR ────────────────────────────────────────────────────────────

# Activities shown as rows in the coverage bar (label, fg_color, bg_color)
COVERAGE_ROWS = [
    ("Chat",        "#1E40AF", "#BFDBFE"),
    ("Phones",      "#065F46", "#A7F3D0"),
    ("CA - Studio", "#4C1D95", "#DDD6FE"),
    ("CA - Remote", "#0369A1", "#BAE6FD"),
    ("Support",     "#14532D", "#A7F3D0"),
    ("GW",          "#92400E", "#FDE68A"),
    ("Retail",      "#991B1B", "#FECACA"),
]
_ON_QUEUE  = {"Chat", "Phones"}
_INACTIVE  = {".", "Break", "Admin", "PTO", "VTO", "Sick",
              "Holiday", "Bereavement", "FMLA", "Training"}

def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def _blend(hex_color, intensity):
    """Blend hex_color with white by intensity (0=white, 1=full color)."""
    r, g, b = _hex_to_rgb(hex_color)
    r2 = int(255 + (r - 255) * intensity)
    g2 = int(255 + (g - 255) * intensity)
    b2 = int(255 + (b - 255) * intensity)
    return f"#{r2:02x}{g2:02x}{b2:02x}"

def build_coverage_bar_html(sched_data, act_colors=None):
    """
    sched_data: {agent_name: {time_slot: activity}}
    act_colors: {name: (bg_hex, fg_hex)} — if None, falls back to module-level ACT_COLORS
    Returns a compact coverage bar showing per-slot counts for key activities.
    """
    _colors = act_colors if act_colors is not None else ACT_COLORS
    # Build dynamic coverage rows from act_colors (exclude off/leave types)
    _EXCLUDE = {".", "Break", "Admin", "PTO", "VTO", "Sick",
                "Holiday", "Bereavement", "FMLA", "Training", "Meeting"}
    _dyn_rows = [
        (label, fg, bg)
        for label, (bg, fg) in _colors.items()
        if label not in _EXCLUDE and label != "."
    ]
    # Preserve legacy ordering if COVERAGE_ROWS labels are present
    _legacy_order = [r[0] for r in COVERAGE_ROWS]
    _dyn_rows.sort(key=lambda r: _legacy_order.index(r[0]) if r[0] in _legacy_order else 999)
    AGENT_COL_W = 172
    n_agents = len(sched_data)
    if n_agents == 0:
        return ""

    # Pre-compute per-slot counts for every activity
    slot_counts = {slot: {} for slot in TIME_SLOTS}
    for ag_slots in sched_data.values():
        for slot, act in ag_slots.items():
            if slot in slot_counts and act and act != ".":
                slot_counts[slot][act] = slot_counts[slot].get(act, 0) + 1

    def count(slot, label):
        return slot_counts.get(slot, {}).get(label, 0)

    # ── Hour labels — every 30-min slot, compact format ───────────────────────
    hour_labels = ""
    for i, slot in enumerate(TIME_SLOTS):
        lx      = i * SLOT_W
        label   = _fmt_slot(slot)
        is_hour = slot.split(":")[1].startswith("00")
        color   = "#C8C5C0" if is_hour else "#686560"
        fsize   = "9px"     if is_hour else "8px"
        hour_labels += (
            f'<div style="position:absolute;left:{lx}px;top:0;width:{SLOT_W}px;'
            f'font-size:{fsize};color:{color};overflow:hidden;white-space:nowrap;'
            f'padding-left:3px;line-height:20px;font-family:\'DM Sans\',sans-serif">'
            f'{label}</div>'
        )

    # ── Activity rows ──────────────────────────────────────────────────────────
    rows_html = ""
    for label, fg, bg in _dyn_rows:
        max_c = max((count(s, label) for s in TIME_SLOTS), default=0) or 1
        cells = ""
        for slot in TIME_SLOTS:
            c = count(slot, label)
            if c == 0:
                cell_style = f"background:#F8FAFC;color:#CBD5E1"
                txt = ""
            else:
                intensity = 0.25 + 0.75 * min(c / max(max_c, 1), 1.0)
                cell_bg = _blend(bg, intensity)
                cell_style = f"background:{cell_bg};color:{fg}"
                txt = str(c)
            cells += (
                f'<div title="{label} @ {slot}: {c} agent(s)" '
                f'style="display:inline-block;width:{SLOT_W}px;height:22px;{cell_style};'
                f'font-size:9px;font-weight:600;line-height:22px;text-align:center;'
                f'box-sizing:border-box;border-right:1px solid rgba(0,0,0,0.22)">{txt}</div>'
            )
        rows_html += f"""
        <div style="display:flex;align-items:stretch;height:22px;border-bottom:1px solid #F1F5F9">
            <div style="width:{AGENT_COL_W}px;flex-shrink:0;display:flex;align-items:center;
                        padding:0 10px;border-right:1px solid #E2E8F0;background:#F8FAFC">
                <div style="width:7px;height:7px;border-radius:50%;background:{fg};
                            margin-right:6px;flex-shrink:0"></div>
                <span style="font-size:10px;font-weight:600;color:#475569;
                             white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{label}</span>
            </div>
            <div>{cells}</div>
        </div>"""

    # ── On-queue total (Chat + Phones) ─────────────────────────────────────────
    oq_cells = ""
    max_oq = max(
        (sum(count(s, a) for a in _ON_QUEUE) for s in TIME_SLOTS), default=0
    ) or 1
    for slot in TIME_SLOTS:
        oq = sum(count(slot, a) for a in _ON_QUEUE)
        if oq == 0:
            cs = "background:#EFF6FF;color:#CBD5E1"; txt = ""
        else:
            intensity = 0.3 + 0.7 * min(oq / max(max_oq, 1), 1.0)
            cs = f"background:{_blend('#3B82F6', intensity)};color:#1E3A8A"
            txt = str(oq)
        oq_cells += (
            f'<div title="On queue @ {slot}: {oq}" '
            f'style="display:inline-block;width:{SLOT_W}px;height:26px;{cs};'
            f'font-size:9px;font-weight:700;line-height:26px;text-align:center;'
            f'box-sizing:border-box;border-right:1px solid rgba(0,0,0,0.22)">{txt}</div>'
        )
    rows_html += f"""
    <div style="display:flex;align-items:stretch;height:26px;border-bottom:2px solid #BFDBFE">
        <div style="width:{AGENT_COL_W}px;flex-shrink:0;display:flex;align-items:center;
                    padding:0 10px;border-right:1px solid #BFDBFE;background:#EFF6FF">
            <span style="font-size:10px;font-weight:700;color:#1D4ED8">🎧 On queue</span>
        </div>
        <div>{oq_cells}</div>
    </div>"""

    # ── Total active (non-off, non-break) ──────────────────────────────────────
    ta_cells = ""
    max_ta = max(
        (sum(1 for ag_s in sched_data.values() if ag_s.get(s, ".") not in _INACTIVE)
         for s in TIME_SLOTS),
        default=0,
    ) or 1
    for slot in TIME_SLOTS:
        ta = sum(1 for ag_s in sched_data.values() if ag_s.get(slot, ".") not in _INACTIVE)
        if ta == 0:
            cs = "background:#F0FDF4;color:#CBD5E1"; txt = ""
        else:
            intensity = 0.25 + 0.75 * min(ta / max(max_ta, 1), 1.0)
            cs = f"background:{_blend('#22C55E', intensity)};color:#14532D"
            txt = str(ta)
        ta_cells += (
            f'<div title="Active @ {slot}: {ta} of {n_agents}" '
            f'style="display:inline-block;width:{SLOT_W}px;height:26px;{cs};'
            f'font-size:9px;font-weight:700;line-height:26px;text-align:center;'
            f'box-sizing:border-box;border-right:1px solid rgba(0,0,0,0.22)">{txt}</div>'
        )
    rows_html += f"""
    <div style="display:flex;align-items:stretch;height:26px">
        <div style="width:{AGENT_COL_W}px;flex-shrink:0;display:flex;align-items:center;
                    padding:0 10px;border-right:1px solid #BBF7D0;background:#F0FDF4">
            <span style="font-size:10px;font-weight:700;color:#15803D">✅ Total active</span>
        </div>
        <div>{ta_cells}</div>
    </div>"""

    total_w = len(TIME_SLOTS) * SLOT_W
    return f"""
    <div style="border:1px solid #E2E8F0;border-radius:10px;overflow:hidden;
                background:white;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                margin-bottom:12px">
        <!-- header: hour labels -->
        <div style="display:flex;height:20px;background:#1D2019;position:sticky;top:0;z-index:2">
            <div style="width:{AGENT_COL_W}px;flex-shrink:0;padding:0 10px;display:flex;align-items:center;
                        font-size:9px;font-weight:600;color:#94A3B8;border-right:1px solid rgba(255,255,255,0.1)">
                COVERAGE
            </div>
            <div style="position:relative;flex:1;overflow:hidden">{hour_labels}</div>
        </div>
        <!-- rows (scrollable) -->
        <div style="overflow-x:auto">
            <div style="min-width:{AGENT_COL_W + total_w}px">{rows_html}</div>
        </div>
    </div>"""

# ─── GLOBAL CSS ───────────────────────────────────────────────────────────────

def _load_font_b64(filename):
    """Load a font file from the fonts/ folder next to app.py and return base64 string."""
    import base64 as _b64
    try:
        font_path = Path(__file__).parent / "fonts" / filename
        return _b64.b64encode(font_path.read_bytes()).decode()
    except Exception:
        return None

def inject_css():
    # ── Embed Cheltenham web fonts (brand-required) ────────────────────────────
    chelt_reg  = _load_font_b64("cheltenham_regular.woff2")
    chelt_bold = _load_font_b64("cheltenham_bold.woff2")

    font_faces = ""
    if chelt_reg:
        font_faces += f"""
        @font-face {{
            font-family: 'Cheltenham';
            font-weight: normal;
            font-style: normal;
            src: url("data:font/woff2;base64,{chelt_reg}") format("woff2");
        }}"""
    if chelt_bold:
        font_faces += f"""
        @font-face {{
            font-family: 'Cheltenham';
            font-weight: bold;
            font-style: normal;
            src: url("data:font/woff2;base64,{chelt_bold}") format("woff2");
        }}"""

    # DM Sans = approved Apercu Pro substitute for digital use
    gfonts_link = '<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;700&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">'

    st.markdown(gfonts_link, unsafe_allow_html=True)
    st.markdown(f"""<style>
    /* ── Framebridge brand: Cheltenham + DM Sans ── */
    {font_faces}

    /* ── CSS variables ── */
    :root {{
        --fb-black:      #1D2019;
        --fb-cream:      #FFF9F4;
        --fb-sand:       #F6F5F4;
        --fb-sage:       #89AC9E;
        --fb-sage-dark:  #689985;
        --fb-yellow:     #EEE171;
        --fb-iron:       #D8D8D8;
        --fb-mist:       #979797;
        --fb-charcoal:   #484848;
        --fb-blue:       #4D6B92;
        --font-headline: 'Cheltenham', Georgia, 'Times New Roman', serif;
        --font-ui:       'DM Sans', Helvetica, Arial, sans-serif;
        --font-mono:     'DM Mono', 'Courier New', monospace;
    }}

    /* ── Chrome ── */
    #MainMenu, footer {{ visibility: hidden }}
    /* Hide header decorations but NOT the header itself (it contains sidebar expand button) */
    [data-testid="stDecoration"] {{ display: none !important }}
    [data-testid="stToolbar"] {{ display: none !important }}
    [data-testid="stStatusWidget"] {{ display: none !important }}
    header {{ background: transparent !important; border-bottom: none !important }}

    /* ── Lock sidebar permanently open — no collapse/expand toggle ── */
    section[data-testid="stSidebar"] {{
        width: 18rem !important;
        min-width: 18rem !important;
        transform: none !important;
        margin-left: 0 !important;
        display: flex !important;
        visibility: visible !important;
    }}
    section[data-testid="stSidebar"][aria-expanded="false"] {{
        transform: none !important;
        margin-left: 0 !important;
        width: 18rem !important;
    }}
    [data-testid="stSidebarCollapseButton"],
    [data-testid="collapsedControl"] {{ display: none !important }}

    .stApp{{background:var(--fb-cream)!important}}
    div[data-testid="stMainBlockContainer"]{{padding:1.5rem 2rem}}

    /* ── Sidebar ── */
    [data-testid="stSidebar"]>div:first-child{{background:var(--fb-black)!important;padding-top:0}}
    [data-testid="stSidebar"] *{{color:#C8C5C0!important;font-family:var(--font-ui)!important}}
    [data-testid="stSidebar"] .stRadio>label{{display:none}}
    [data-testid="stSidebar"] .stRadio div[role="radiogroup"]{{gap:0!important}}
    [data-testid="stSidebar"] .stRadio label{{
        display:flex!important;align-items:center;
        padding:10px 16px!important;border-radius:4px!important;
        margin:1px 6px!important;cursor:pointer!important;
        transition:background 0.15s;font-size:13px!important;
        letter-spacing:0.03em!important;font-family:var(--font-ui)!important}}
    [data-testid="stSidebar"] .stRadio label:hover{{background:rgba(255,255,255,0.07)!important}}
    [data-testid="stSidebar"] .stRadio label[data-checked="true"]{{
        background:rgba(137,172,158,0.25)!important;
        border-left:2px solid var(--fb-sage)!important}}
    [data-testid="stSidebar"] .stRadio label[data-checked="true"] *{{
        color:white!important;font-weight:500!important}}
    [data-testid="stSidebar"] div[data-testid="stVerticalBlock"]{{gap:0!important}}

    /* ── Cards & containers ── */
    .scard{{
        background:white;border-radius:4px;padding:1.25rem;
        border:1px solid var(--fb-iron);margin-bottom:0}}

    /* ── Typography ── */
    .page-title{{
        font-size:24px;font-weight:bold;color:var(--fb-black);margin-bottom:4px;
        font-family:var(--font-headline)!important;letter-spacing:-0.01em;line-height:1.2}}
    .page-sub{{
        font-size:13px;color:var(--fb-mist);margin-bottom:1.5rem;
        font-family:var(--font-ui)!important;letter-spacing:0.01em}}
    .metric-num{{
        font-size:28px;font-weight:bold;color:var(--fb-black);
        font-family:var(--font-headline)!important}}
    .metric-lbl{{
        font-size:11px;color:var(--fb-mist);margin-bottom:4px;
        font-family:var(--font-ui)!important;text-transform:uppercase;letter-spacing:0.08em}}
    .metric-sub{{font-size:11px;color:var(--fb-iron);margin-top:2px;font-family:var(--font-ui)!important}}

    /* ── Pills & badges ── */
    .team-pill{{
        display:inline-flex;align-items:center;gap:5px;padding:2px 10px;
        border-radius:2px;font-size:11px;font-weight:600;font-family:var(--font-ui)!important}}
    .status-pill{{
        display:inline-block;padding:2px 10px;border-radius:2px;
        font-size:11px;font-weight:600;font-family:var(--font-ui)!important;
        text-transform:uppercase;letter-spacing:0.05em}}
    .pill-pending{{background:#FEF3C7;color:#92400E}}
    .pill-approved{{background:#D1FAE5;color:#065F46}}
    .pill-denied{{background:#FEE2E2;color:#991B1B}}

    /* ── Request & agent rows ── */
    .req-row{{
        background:white;border:1px solid var(--fb-iron);border-radius:4px;
        padding:12px 16px;margin-bottom:8px}}
    .agent-card{{
        background:white;border:1px solid var(--fb-iron);border-radius:4px;
        padding:14px;height:100%;transition:box-shadow 0.15s}}
    .agent-card:hover{{box-shadow:0 2px 8px rgba(29,32,25,0.1)}}

    /* ── Buttons ── */
    .stButton button{{
        border-radius:4px!important;font-weight:600!important;
        font-family:var(--font-ui)!important;letter-spacing:0.04em!important;
        text-transform:uppercase!important;font-size:12px!important}}
    [data-testid="stButton"] button[kind="primary"],
    button[kind="primary"]{{
        background:var(--fb-black)!important;color:var(--fb-cream)!important;
        border:none!important}}
    [data-testid="stButton"] button[kind="primary"]:hover,
    button[kind="primary"]:hover{{
        background:var(--fb-charcoal)!important}}

    /* ── Tabs ── */
    div[data-testid="stTabs"] button{{
        font-weight:500!important;font-family:var(--font-ui)!important;
        font-size:13px!important;letter-spacing:0.03em!important}}

    /* ── Inputs & selects ── */
    [data-testid="stTextInput"] input,
    [data-testid="stSelectbox"] div,
    [data-testid="stNumberInput"] input{{
        font-family:var(--font-ui)!important;border-radius:4px!important}}
    label[data-testid="stWidgetLabel"] p{{
        font-family:var(--font-ui)!important;font-size:12px!important;
        text-transform:uppercase!important;letter-spacing:0.06em!important;
        color:var(--fb-charcoal)!important}}

    /* ── Expanders & misc ── */
    [data-testid="stExpander"]{{border-radius:4px!important;border-color:var(--fb-iron)!important}}
    [data-testid="stExpander"] summary{{font-family:var(--font-ui)!important}}
    [data-testid="stToast"]{{font-family:var(--font-ui)!important}}
    </style>""", unsafe_allow_html=True)

def metric(label, val, sub=""):
    st.markdown(f"""<div class="scard">
        <div class="metric-lbl">{label}</div>
        <div class="metric-num">{val}</div>
        {"<div class='metric-sub'>"+sub+"</div>" if sub else ""}
    </div>""", unsafe_allow_html=True)

def team_pill(name, color):
    return f'<span class="team-pill" style="background:{color}22;color:{color}">{name}</span>'

def status_pill(status):
    cls = {"Approved": "pill-approved", "Pending": "pill-pending", "Denied": "pill-denied"}.get(status, "")
    return f'<span class="status-pill {cls}">{status}</span>'

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────

def sidebar():
    user = current_user()
    with st.sidebar:
        st.markdown("""
        <div style="padding:20px 16px 18px;border-bottom:1px solid rgba(255,255,255,0.07);margin-bottom:8px">
            <div style="font-family:'Cheltenham',Georgia,serif;font-size:17px;font-weight:bold;
                        color:#FFF9F4;letter-spacing:-0.01em;line-height:1.2">
                CX Scheduler
            </div>
            <div style="font-family:'DM Sans',Helvetica,sans-serif;font-size:10px;
                        color:#89AC9E;margin-top:4px;letter-spacing:0.15em;text-transform:uppercase">
                Framebridge
            </div>
        </div>""", unsafe_allow_html=True)

        # Logged-in user badge
        if user:
            role_colors = {"admin": "#EEE171", "editor": "#89AC9E", "viewer": "#979797"}
            rc = role_colors.get(user["role"], "#94A3B8")
            st.markdown(f"""
            <div style="margin:0 8px 12px;padding:8px 10px;background:rgba(255,255,255,0.06);
                        border-radius:8px;display:flex;align-items:center;gap:8px">
                <div style="width:28px;height:28px;border-radius:50%;background:{rc}22;color:{rc};
                            font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;
                            flex-shrink:0">
                    {"".join(p[0] for p in user["display_name"].split()[:2]).upper()}
                </div>
                <div style="flex:1;overflow:hidden">
                    <div style="font-size:12px;font-weight:600;color:#F1F5F9;
                                white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{user["display_name"]}</div>
                    <div style="font-size:10px;color:{rc};text-transform:capitalize">{user["role"]}</div>
                </div>
            </div>""", unsafe_allow_html=True)

        pending = len(get_time_off_requests("Pending"))

        # Build nav based on role
        nav_labels = ["⬛  Schedule"]
        if can_edit() or True:   # Time Off visible to all (viewers submit their own)
            nav_labels.append(f"📥  Time Off{'  ·  ' + str(pending) if pending and can_edit() else ''}")
        if can_edit():
            nav_labels += ["👤  Roster", "🏷️  Teams", "📋  Templates"]
        if is_admin():
            nav_labels.append("👥  Users")
            nav_labels.append("⚙️  Settings")
        nav_labels.append("📊  Reports")

        page = st.radio("nav", nav_labels, label_visibility="collapsed")

        if pending and can_edit():
            st.markdown(f"""
            <div style="margin:12px 8px 0;padding:10px 12px;background:rgba(238,225,113,0.12);
                        border-radius:4px;border:1px solid rgba(238,225,113,0.3)">
                <div style="font-size:11px;color:#EEE171;font-weight:600;font-family:'DM Sans',sans-serif;
                            text-transform:uppercase;letter-spacing:0.06em">⚠ {pending} pending</div>
                <div style="font-size:11px;color:#979797;margin-top:2px;font-family:'DM Sans',sans-serif">
                    Time off requests</div>
            </div>""", unsafe_allow_html=True)

        # Team legend
        teams = get_teams()
        if teams:
            st.markdown('<div style="margin:16px 8px 6px;font-size:10px;font-weight:600;color:#475569;letter-spacing:.06em">TEAMS</div>', unsafe_allow_html=True)
            for t in teams:
                agents_on_team = len(get_agent_names(t["name"]))
                st.markdown(f"""
                <div style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:6px">
                    <div style="width:8px;height:8px;border-radius:50%;background:{t['color']};flex-shrink:0"></div>
                    <span style="font-size:12px;color:#CBD5E1">{t['name']}</span>
                    <span style="font-size:10px;color:#475569;margin-left:auto">{agents_on_team}</span>
                </div>""", unsafe_allow_html=True)

        # Logout at the bottom
        st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
        if st.button("Sign out", use_container_width=True):
            st.session_state.pop("cx_user", None)
            st.rerun()

    page_key = page.split("  ")[1].split("  ")[0].strip()
    return page_key

# ─── PAGE: SCHEDULE ───────────────────────────────────────────────────────────

def page_schedule():
    st.markdown('<div class="page-title">Schedule</div>', unsafe_allow_html=True)

    today = datetime.date.today()
    default_mon = today - datetime.timedelta(days=today.weekday())

    c1, c2, c3, c4, c5 = st.columns([2, 1.2, 1.2, 1.2, 2])
    with c1:
        sel = st.date_input("Week starting (Monday)", value=default_mon, label_visibility="collapsed")
        if sel.weekday() != 0:
            sel = sel - datetime.timedelta(days=sel.weekday())
        week_start = str(sel)
        st.markdown(f'<div style="font-size:13px;color:#64748B;margin-top:2px">Week of <b style="color:#0F172A">{sel.strftime("%B %-d, %Y")}</b></div>', unsafe_allow_html=True)
    with c2:
        if st.button("⬅ Prev week", use_container_width=True):
            sel -= datetime.timedelta(weeks=1)
            st.rerun()
    with c3:
        if st.button("Next week ➡", use_container_width=True):
            sel += datetime.timedelta(weeks=1)
            st.rerun()
    with c4:
        if st.button("📋 Copy prev week", use_container_width=True):
            prev = str(sel - datetime.timedelta(weeks=1))
            ok, msg = copy_week(prev, week_start)
            st.toast(msg, icon="✅" if ok else "⚠️")
            st.rerun()
    with c5:
        if st.button("✨  Apply approved time off", use_container_width=True, type="primary"):
            n = apply_approved_timeoff(week_start)
            st.toast(f"Applied time off to {n} time slots.", icon="✅")
            st.rerun()

    # ── Template controls ─────────────────────────────────────────────────────
    if can_edit():
        all_templates = get_templates()
        with st.expander("📋  Templates", expanded=False):
            col_apply, col_save = st.columns(2)

            with col_apply:
                st.markdown(
                    '<div style="font-family:\'DM Sans\',sans-serif;font-size:10px;'
                    'font-weight:700;color:#689985;letter-spacing:0.12em;'
                    'text-transform:uppercase;margin-bottom:8px">Apply a template</div>',
                    unsafe_allow_html=True,
                )
                if all_templates:
                    tmpl_map = {t["name"]: t for t in all_templates}
                    sel_name = st.selectbox(
                        "Template", list(tmpl_map.keys()),
                        key=f"apply_sel_{week_start}",
                        label_visibility="collapsed",
                    )
                    sel_tmpl = tmpl_map[sel_name]
                    if sel_tmpl.get("description"):
                        st.caption(sel_tmpl["description"])
                    if st.button("Apply to this week", type="primary",
                                 key=f"apply_btn_{week_start}", use_container_width=True):
                        n = apply_template_to_week(sel_tmpl["id"], week_start)
                        st.toast(f"Applied '{sel_name}' — {n} slots filled.", icon="✅")
                        st.rerun()
                else:
                    st.caption("No templates yet — save one on the right.")

            with col_save:
                st.markdown(
                    '<div style="font-family:\'DM Sans\',sans-serif;font-size:10px;'
                    'font-weight:700;color:#689985;letter-spacing:0.12em;'
                    'text-transform:uppercase;margin-bottom:8px">Save week as template</div>',
                    unsafe_allow_html=True,
                )
                with st.form(f"save_tmpl_{week_start}"):
                    tmpl_name_inp = st.text_input(
                        "Template name", placeholder="e.g. Standard Mon–Fri",
                        label_visibility="collapsed",
                    )
                    tmpl_desc_inp = st.text_input("Description (optional)")
                    if st.form_submit_button("Save as template", use_container_width=True):
                        if not tmpl_name_inp.strip():
                            st.error("Name required.")
                        else:
                            u = current_user()
                            ok, _, msg = save_week_as_template(
                                week_start, tmpl_name_inp, tmpl_desc_inp,
                                u["display_name"] if u else "",
                            )
                            st.toast(msg, icon="✅" if ok else "⚠️")

    st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)

    day_tabs = st.tabs([
        f"{d[:3]}  {(sel + datetime.timedelta(days=i)).strftime('%-m/%-d')}"
        for i, d in enumerate(DAYS)
    ])

    agents_all  = get_agents()
    teams       = get_teams()
    team_colors = {t["name"]: t["color"] for t in teams}
    act_names   = get_activity_names()   # dynamic from DB
    act_colors  = get_act_colors()       # dynamic from DB

    for di, (tab, day_name) in enumerate(zip(day_tabs, DAYS)):
        with tab:
            # Load saved schedule data for all agents on this day
            sched_data = {}
            for ag in agents_all:
                df_tmp = get_schedule_df(week_start, di, [ag["name"]])
                sched_data[ag["name"]] = df_tmp[ag["name"]].to_dict()

            # ── Coverage bar — always visible at the top ───────────────────────
            if agents_all:
                n_scheduled = sum(
                    1 for ag_s in sched_data.values()
                    if any(v not in (".", "") for v in ag_s.values())
                )
                on_q_peak = max(
                    (sum(1 for ag_s in sched_data.values()
                         if ag_s.get(s, ".") in _ON_QUEUE)
                     for s in TIME_SLOTS),
                    default=0,
                )
                total_agents = len(agents_all)
                st.markdown(
                    f'<div style="display:flex;gap:16px;margin-bottom:8px;flex-wrap:wrap">'
                    f'<span style="font-size:12px;color:#475569">👥 <b style="color:#0F172A">{total_agents}</b> agents total</span>'
                    f'<span style="font-size:12px;color:#475569">📋 <b style="color:#0F172A">{n_scheduled}</b> have shifts entered</span>'
                    f'<span style="font-size:12px;color:#1D4ED8">🎧 Peak on queue: <b>{on_q_peak}</b></span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                cov_html = build_coverage_bar_html(sched_data, act_colors)
                if cov_html:
                    st.markdown(cov_html, unsafe_allow_html=True)
                else:
                    st.caption("No schedule data yet — use the Edit tab to build this day's schedule.")

            tab_list = ["👁  Timeline view"]
            if can_edit():
                tab_list.append("✏️  Edit schedule")
            tabs_out = st.tabs(tab_list)
            view_tab = tabs_out[0]
            edit_tab = tabs_out[1] if can_edit() else None

            with view_tab:
                if not agents_all:
                    st.info("Add agents in the Roster page to see the schedule.")
                else:
                    agents_info = [
                        {"name": a["name"], "team_name": a["team_name"],
                         "color": team_colors.get(a["team_name"], "#64748B")}
                        for a in agents_all
                    ]

                    # ── Team order (persisted in session state) ───────────────
                    teams_with_agents = [t for t in teams
                                         if any(a["team_name"] == t["name"] for a in agents_info)]
                    _tl_order_key = "timeline_team_order"
                    if _tl_order_key not in st.session_state:
                        st.session_state[_tl_order_key] = [t["name"] for t in teams_with_agents]
                    else:
                        # Keep order in sync: add new teams, drop removed ones
                        _cur = {t["name"] for t in teams_with_agents}
                        _saved = [n for n in st.session_state[_tl_order_key] if n in _cur]
                        _new   = [t["name"] for t in teams_with_agents
                                  if t["name"] not in set(_saved)]
                        st.session_state[_tl_order_key] = _saved + _new

                    _team_lookup  = {t["name"]: t for t in teams}
                    _ordered_teams = [_team_lookup[n] for n in st.session_state[_tl_order_key]
                                      if n in _team_lookup]

                    # Group by team with headers + reorder buttons
                    n_rows = len(TIME_SLOTS) * 26 + 60
                    for _i, team in enumerate(_ordered_teams):
                        team_agents = [a for a in agents_info if a["team_name"] == team["name"]]
                        if not team_agents:
                            continue

                        # Header row: dot + name + agent count + ↑ ↓ buttons
                        _hcol, _ucol, _dcol = st.columns([30, 1, 1])
                        with _hcol:
                            st.markdown(
                                f'<div style="display:flex;align-items:center;gap:8px;'
                                f'margin:10px 0 4px">'
                                f'<div style="width:10px;height:10px;border-radius:50%;'
                                f'background:{team["color"]}"></div>'
                                f'<span style="font-size:13px;font-weight:600;color:#1E293B">'
                                f'{team["name"]} Team</span>'
                                f'<span style="font-size:11px;color:#94A3B8">'
                                f'— {len(team_agents)} agents</span>'
                                f'</div>', unsafe_allow_html=True
                            )
                        with _ucol:
                            _up_disabled = (_i == 0)
                            if st.button("↑", key=f"tl_up_{di}_{team['name']}",
                                         disabled=_up_disabled,
                                         help="Move this team up"):
                                _order = st.session_state[_tl_order_key]
                                _idx   = _order.index(team["name"])
                                _order[_idx], _order[_idx - 1] = _order[_idx - 1], _order[_idx]
                                st.rerun()
                        with _dcol:
                            _dn_disabled = (_i == len(_ordered_teams) - 1)
                            if st.button("↓", key=f"tl_dn_{di}_{team['name']}",
                                         disabled=_dn_disabled,
                                         help="Move this team down"):
                                _order = st.session_state[_tl_order_key]
                                _idx   = _order.index(team["name"])
                                _order[_idx], _order[_idx + 1] = _order[_idx + 1], _order[_idx]
                                st.rerun()

                        team_sched = {a["name"]: sched_data.get(a["name"], {}) for a in team_agents}
                        timeline_html = build_timeline_html(team_agents, team_sched, act_colors)
                        st_components.html(timeline_html, height=min(n_rows, 680), scrolling=True)
                        st.markdown('<div style="height:4px"></div>', unsafe_allow_html=True)

            if can_edit() and edit_tab is not None:
              with edit_tab:
                # ── Quick Fill ──────────────────────────────────────────────────
                st.markdown(
                    '<div style="background:#F0F5F3;border:1px solid #C4D9D2;border-radius:4px;'
                    'padding:12px 16px;margin-bottom:14px">'
                    '<div style="font-size:10px;font-weight:700;color:#689985;margin-bottom:10px;'
                    'letter-spacing:0.12em;text-transform:uppercase;font-family:\'DM Sans\',sans-serif">'
                    'Quick Fill — set one activity across multiple slots and agents</div>',
                    unsafe_allow_html=True,
                )
                qf_c1, qf_c2, qf_c3, qf_c4, qf_c5 = st.columns([2, 2, 2, 2, 1])
                with qf_c1:
                    qf_act = st.selectbox(
                        "Activity",
                        [a for a in act_names if a != "."],
                        key=f"qf_act_{week_start}_{di}",
                    )
                with qf_c2:
                    qf_from_idx = TIME_SLOTS.index("9:00 AM") if "9:00 AM" in TIME_SLOTS else 0
                    qf_from = st.selectbox(
                        "From", TIME_SLOTS, index=qf_from_idx,
                        key=f"qf_from_{week_start}_{di}",
                    )
                with qf_c3:
                    qf_to_idx = TIME_SLOTS.index("5:00 PM") if "5:00 PM" in TIME_SLOTS else len(TIME_SLOTS) - 1
                    qf_to = st.selectbox(
                        "To", TIME_SLOTS, index=qf_to_idx,
                        key=f"qf_to_{week_start}_{di}",
                    )
                with qf_c4:
                    qf_agent = st.selectbox(
                        "Agent",
                        ["All agents"] + [a["name"] for a in agents_all],
                        key=f"qf_agent_{week_start}_{di}",
                    )
                with qf_c5:
                    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                    qf_go = st.button(
                        "Apply", key=f"qf_apply_{week_start}_{di}",
                        type="primary", use_container_width=True,
                    )
                st.markdown("</div>", unsafe_allow_html=True)

                if qf_go:
                    fi = TIME_SLOTS.index(qf_from)
                    ti = TIME_SLOTS.index(qf_to)
                    if fi > ti:
                        st.error("'From' must be before 'To'.")
                    else:
                        targets = (
                            [a["name"] for a in agents_all]
                            if qf_agent == "All agents"
                            else [qf_agent]
                        )
                        slots_to_fill = TIME_SLOTS[fi : ti + 1]
                        for agent_name in targets:
                            df_tmp = get_schedule_df(week_start, di, [agent_name])
                            for slot in slots_to_fill:
                                df_tmp.at[slot, agent_name] = qf_act
                            save_schedule_df(week_start, di, df_tmp)
                        st.toast(
                            f"Set {qf_act} for {len(targets)} agent(s) · {len(slots_to_fill)} slots.",
                            icon="✅",
                        )
                        st.rerun()

                # ── Per-team grid editor ────────────────────────────────────────
                for team in teams:
                    team_agents = [a["name"] for a in agents_all if a["team_name"] == team["name"]]
                    if not team_agents:
                        continue
                    clr = team_colors.get(team["name"], "#64748B")
                    st.markdown(
                        f'<div style="background:{clr};color:white;padding:6px 12px;border-radius:8px;'
                        f'font-weight:600;font-size:12px;margin:12px 0 6px">{team["name"]} Team '
                        f'<span style="opacity:0.7;font-weight:400">— {len(team_agents)} agents</span></div>',
                        unsafe_allow_html=True,
                    )
                    df = get_schedule_df(week_start, di, team_agents)
                    col_cfg = {
                        col: st.column_config.SelectboxColumn(
                            label=col.split()[0], options=act_names, default=".", width="small"
                        )
                        for col in df.columns
                    }
                    edited = st.data_editor(
                        df,
                        column_config=col_cfg,
                        use_container_width=True,
                        key=f"edit_{week_start}_{di}_{team['name']}",
                        height=min(420, 35 * len(TIME_SLOTS) + 38),
                    )
                    if st.button(
                        f"💾 Save {team['name']}",
                        key=f"save_{week_start}_{di}_{team['name']}",
                        use_container_width=True,
                        type="primary",
                    ):
                        save_schedule_df(week_start, di, edited)
                        st.toast(f"Saved {team['name']} for {day_name}.", icon="✅")
                        st.rerun()


# ─── PAGE: TIME OFF ───────────────────────────────────────────────────────────

def page_timeoff():
    st.markdown('<div class="page-title">Time Off</div>', unsafe_allow_html=True)

    # ── Viewer-only mode ───────────────────────────────────────────────────────
    if not can_edit():
        user = current_user()
        my_name = user["display_name"] if user else ""
        all_reqs = get_time_off_requests()
        my_reqs  = [r for r in all_reqs if r["agent_name"] == my_name]

        tab_mine, tab_submit = st.tabs(["My requests", "Submit request"])

        with tab_mine:
            if not my_reqs:
                st.info("You have no time-off requests on file.")
            else:
                team_colors_map = {t["name"]: t["color"] for t in get_teams()}
                for req in my_reqs:
                    tcolor = team_colors_map.get(req.get("team_name",""), "#94A3B8")
                    s = datetime.date.fromisoformat(req["start_date"])
                    e = datetime.date.fromisoformat(req["end_date"])
                    days = (e - s).days + 1
                    st.markdown(f"""<div class="req-row" style="display:flex;align-items:center;gap:12px">
                        <div style="flex:1">
                            <span style="font-size:13px;font-weight:600;color:#0F172A">{req["type"]}</span>
                            <span style="font-size:12px;color:#94A3B8;margin-left:8px">{s.strftime("%-m/%-d")} – {e.strftime("%-m/%-d, %Y")} · {days} day{"s" if days!=1 else ""}</span>
                        </div>
                        {status_pill(req["status"])}
                    </div>""", unsafe_allow_html=True)

        with tab_submit:
            agents_list = get_agent_names()
            default_idx = agents_list.index(my_name) if my_name in agents_list else 0
            with st.form("submit_to_viewer"):
                agent = st.selectbox("Your name", agents_list, index=default_idx)
                c1, c2 = st.columns(2)
                with c1: start = st.date_input("Start date", value=datetime.date.today()+datetime.timedelta(7))
                with c2: end   = st.date_input("End date",   value=datetime.date.today()+datetime.timedelta(7))
                rtype = st.selectbox("Type", TIMEOFF_TYPES)
                notes = st.text_input("Notes (optional)")
                if st.form_submit_button("Submit request", type="primary"):
                    if end < start:
                        st.error("End date must be on or after start date.")
                    else:
                        ag_data = next((a for a in get_agents() if a["name"]==agent), {})
                        add_time_off_request(agent, ag_data.get("team_name",""), start, end, rtype, notes)
                        st.success("Request submitted — your manager will review it soon.")
        return
    # ── Admin / Editor mode ────────────────────────────────────────────────────

    all_reqs = get_time_off_requests()
    pending  = [r for r in all_reqs if r["status"] == "Pending"]
    approved = [r for r in all_reqs if r["status"] == "Approved"]

    c1, c2, c3, c4 = st.columns(4)
    with c1: metric("Pending review", len(pending), "need your action")
    with c2: metric("Approved", len(approved))
    with c3: metric("Total requests", len(all_reqs))
    with c4:
        today = datetime.date.today()
        upcoming = [r for r in approved
                    if datetime.date.fromisoformat(r["end_date"]) >= today]
        metric("Upcoming (approved)", len(upcoming), "not yet passed")

    st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

    tab_pending, tab_all, tab_submit = st.tabs([
        f"⚠️  Pending ({len(pending)})", "All requests", "Submit request"
    ])

    with tab_pending:
        if not pending:
            st.success("You're all caught up — no pending requests.")
        for req in pending:
            s = datetime.date.fromisoformat(req["start_date"])
            e = datetime.date.fromisoformat(req["end_date"])
            days = (e - s).days + 1
            agent_team = next((a["team_name"] for a in get_agents() if a["name"] == req["agent_name"]), req.get("team_name", ""))
            tcolor = get_team_color(agent_team)

            st.markdown(f"""<div class="req-row">
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
                    <div style="width:32px;height:32px;border-radius:50%;background:{tcolor}22;color:{tcolor};
                                font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center">
                        {"".join(p[0] for p in req["agent_name"].split()[:2]).upper()}
                    </div>
                    <div>
                        <span style="font-size:14px;font-weight:600;color:#0F172A">{req["agent_name"]}</span>
                        {team_pill(agent_team, tcolor)}
                    </div>
                    <div style="margin-left:auto;font-size:12px;color:#64748B">
                        Submitted {req["submitted_date"]}
                    </div>
                </div>
                <div style="font-size:13px;color:#475569">
                    <b>{req["type"]}</b> &nbsp;·&nbsp; {s.strftime("%b %-d")} – {e.strftime("%b %-d, %Y")}
                    &nbsp;·&nbsp; {days} day{"s" if days!=1 else ""}
                    {"&nbsp;·&nbsp; <i>"+req['notes']+"</i>" if req["notes"] else ""}
                </div>
            </div>""", unsafe_allow_html=True)

            ca, cb, _ = st.columns([1, 1, 5])
            with ca:
                if st.button("✅ Approve", key=f"ap_{req['id']}", use_container_width=True, type="primary"):
                    update_request_status(req["id"], "Approved", "Scott M.")
                    st.toast(f"Approved {req['agent_name']}'s request.", icon="✅")
                    st.rerun()
            with cb:
                if st.button("✗ Deny", key=f"dn_{req['id']}", use_container_width=True):
                    update_request_status(req["id"], "Denied")
                    st.toast("Request denied.", icon="🚫")
                    st.rerun()

    with tab_all:
        if not all_reqs:
            st.info("No requests yet.")
        else:
            team_colors_map = {t["name"]: t["color"] for t in get_teams()}
            for req in all_reqs:
                tcolor = team_colors_map.get(req.get("team_name",""), "#94A3B8")
                s = datetime.date.fromisoformat(req["start_date"])
                e = datetime.date.fromisoformat(req["end_date"])
                days = (e - s).days + 1
                st.markdown(f"""<div class="req-row" style="display:flex;align-items:center;gap:12px">
                    <div style="width:28px;height:28px;border-radius:50%;background:{tcolor}22;color:{tcolor};
                                font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0">
                        {"".join(p[0] for p in req["agent_name"].split()[:2]).upper()}
                    </div>
                    <div style="flex:1">
                        <span style="font-size:13px;font-weight:600;color:#0F172A">{req["agent_name"]}</span>
                        <span style="font-size:12px;color:#94A3B8;margin-left:8px">{req["type"]} · {s.strftime("%-m/%-d")}–{e.strftime("%-m/%-d")} · {days}d</span>
                    </div>
                    {status_pill(req["status"])}
                </div>""", unsafe_allow_html=True)

    with tab_submit:
        agents_list = get_agent_names()
        if not agents_list:
            st.warning("Add agents to the Roster first.")
        else:
            with st.form("submit_to"):
                agent = st.selectbox("Agent", agents_list)
                c1, c2 = st.columns(2)
                with c1: start = st.date_input("Start date", value=datetime.date.today()+datetime.timedelta(7))
                with c2: end   = st.date_input("End date",   value=datetime.date.today()+datetime.timedelta(7))
                rtype = st.selectbox("Type", TIMEOFF_TYPES)
                notes = st.text_input("Notes (optional)")
                if st.form_submit_button("Submit request", type="primary"):
                    if end < start:
                        st.error("End date must be on or after start date.")
                    else:
                        ag_data = next((a for a in get_agents() if a["name"]==agent), {})
                        add_time_off_request(agent, ag_data.get("team_name",""), start, end, rtype, notes)
                        st.success(f"Request submitted for {agent}.")


# ─── PAGE: ROSTER ─────────────────────────────────────────────────────────────

def page_roster():
    st.markdown('<div class="page-title">Roster</div>', unsafe_allow_html=True)

    teams = get_teams()
    team_names = [t["name"] for t in teams]
    team_colors_map = {t["name"]: t["color"] for t in teams}

    add_tab, view_tab = st.tabs(["All agents", "Add agent"])

    with view_tab:
        st.subheader("Add new agent")
        with st.form("add_agent_form"):
            c1, c2 = st.columns(2)
            with c1: name = st.text_input("Full name")
            with c2: team_sel = st.selectbox("Team", team_names if team_names else ["(no teams — add a team first)"])
            c3, c4 = st.columns(2)
            with c3: emp = st.selectbox("Employment type", ["FT", "PT"])
            with c4: hrs = st.number_input("Weekly hours", 1, 40, 40)
            work_days = st.text_input("Work days", "Mon,Tue,Wed,Thu,Fri",
                                      help="Comma-separated. Options: Mon Tue Wed Thu Fri Sat Sun")
            notes = st.text_input("Notes (optional)")
            if st.form_submit_button("Add agent", type="primary"):
                if not name.strip():
                    st.error("Name required.")
                elif not team_names:
                    st.error("Create a team first (Teams page).")
                else:
                    ok, msg = upsert_agent(name.strip(), team_sel, emp, hrs, work_days, notes)
                    st.success(msg) if ok else st.error(msg)
                    if ok:
                        st.rerun()

    with add_tab:
        agents = get_agents()
        if not agents:
            st.info("No agents yet — use the Add Agent tab.")
        for team in teams:
            team_agents = [a for a in agents if a["team_name"] == team["name"]]
            if not team_agents:
                continue
            clr = team["color"]
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:10px;margin:20px 0 10px">'
                f'<div style="width:12px;height:12px;border-radius:50%;background:{clr}"></div>'
                f'<span style="font-size:15px;font-weight:700;color:#0F172A">{team["name"]} Team</span>'
                f'<span style="font-size:12px;color:#94A3B8">— {len(team_agents)} agents</span>'
                f'</div>', unsafe_allow_html=True
            )
            cols = st.columns(3)
            for i, ag in enumerate(team_agents):
                with cols[i % 3]:
                    initials = "".join(p[0] for p in ag["name"].split()[:2]).upper()
                    with st.expander(ag["name"]):
                        st.markdown(f"""
                        <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
                            <div style="width:40px;height:40px;border-radius:50%;background:{clr}22;color:{clr};
                                        font-size:14px;font-weight:700;display:flex;align-items:center;justify-content:center">
                                {initials}
                            </div>
                            <div>
                                <div style="font-weight:600;color:#0F172A">{ag["name"]}</div>
                                <div style="font-size:12px;color:#94A3B8">{ag["employment_type"]} · {ag["weekly_hours"]} hrs/wk</div>
                            </div>
                        </div>""", unsafe_allow_html=True)
                        with st.form(f"edit_ag_{ag['id']}"):
                            n = st.text_input("Name", ag["name"])
                            t_sel = st.selectbox("Team", team_names,
                                                 index=team_names.index(ag["team_name"]) if ag["team_name"] in team_names else 0)
                            e_sel = st.selectbox("Type", ["FT","PT"],
                                                 index=0 if ag["employment_type"]=="FT" else 1)
                            h = st.number_input("Hours", 1, 40, int(ag["weekly_hours"]))
                            wd = st.text_input("Work days", ag["work_days"])
                            nt = st.text_input("Notes", ag.get("notes",""))
                            cs, cd = st.columns(2)
                            with cs:
                                if st.form_submit_button("Save", use_container_width=True):
                                    ok, msg = upsert_agent(n, t_sel, e_sel, h, wd, nt, ag["id"])
                                    st.toast(msg, icon="✅" if ok else "❌")
                                    if ok: st.rerun()
                            with cd:
                                if st.form_submit_button("🗑 Remove", use_container_width=True):
                                    delete_agent(ag["id"])
                                    st.toast(f"Removed {ag['name']}.", icon="🗑️")
                                    st.rerun()

        # Unassigned agents
        known_teams = set(team_names)
        unassigned = [a for a in agents if a["team_name"] not in known_teams]
        if unassigned:
            st.warning(f"{len(unassigned)} agent(s) assigned to teams that no longer exist. Re-assign them below.")
            for ag in unassigned:
                with st.expander(f"⚠ {ag['name']} (team: {ag['team_name']})"):
                    with st.form(f"reassign_{ag['id']}"):
                        new_team = st.selectbox("Reassign to", team_names)
                        if st.form_submit_button("Reassign"):
                            upsert_agent(ag["name"], new_team, ag["employment_type"],
                                         ag["weekly_hours"], ag["work_days"], ag.get("notes",""), ag["id"])
                            st.rerun()


# ─── PAGE: TEAMS ──────────────────────────────────────────────────────────────

def page_teams():
    st.markdown('<div class="page-title">Teams</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Create and manage teams. Each team gets its own color used throughout the app.</div>', unsafe_allow_html=True)

    teams = get_teams()
    agents = get_agents()

    # Existing teams
    if teams:
        cols = st.columns(min(len(teams), 3))
        for i, team in enumerate(teams):
            with cols[i % 3]:
                agent_count = len([a for a in agents if a["team_name"] == team["name"]])
                st.markdown(f"""
                <div class="scard" style="border-left:4px solid {team['color']}">
                    <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
                        <div style="width:16px;height:16px;border-radius:4px;background:{team['color']}"></div>
                        <span style="font-size:15px;font-weight:700;color:#0F172A">{team['name']}</span>
                        <span style="margin-left:auto;font-size:11px;color:#94A3B8;background:#F1F5F9;
                                     padding:2px 8px;border-radius:99px">{agent_count} agents</span>
                    </div>
                    <div style="font-size:12px;color:#64748B">{team.get('description','') or '—'}</div>
                </div>""", unsafe_allow_html=True)
                with st.expander("Edit team"):
                    with st.form(f"edit_team_{team['id']}"):
                        tn = st.text_input("Team name", team["name"])
                        tc = st.color_picker("Color", team["color"])
                        td = st.text_input("Description", team.get("description", ""))
                        cs, cd = st.columns(2)
                        with cs:
                            if st.form_submit_button("Save", use_container_width=True):
                                ok, msg = upsert_team(tn, tc, td, team["id"])
                                st.toast(msg, icon="✅" if ok else "❌")
                                if ok: st.rerun()
                        with cd:
                            if st.form_submit_button("🗑 Delete", use_container_width=True):
                                if agent_count > 0:
                                    st.toast(f"Can't delete — {agent_count} agents still assigned. Reassign them first.", icon="⚠️")
                                else:
                                    delete_team(team["id"])
                                    st.toast(f"Deleted team {team['name']}.", icon="🗑️")
                                    st.rerun()

    st.divider()
    st.subheader("Create new team")
    with st.form("new_team"):
        c1, c2, c3 = st.columns([3, 1, 3])
        with c1: name  = st.text_input("Team name", placeholder="e.g. Escalations")
        with c2: color = st.color_picker("Color", "#7C3AED")
        with c3: desc  = st.text_input("Description (optional)")
        if st.form_submit_button("Create team", type="primary"):
            if not name.strip():
                st.error("Team name required.")
            else:
                ok, msg = upsert_team(name.strip(), color, desc)
                st.success(msg) if ok else st.error(msg)
                if ok: st.rerun()

    if teams:
        st.markdown("""
        <div style="background:#FFFBDE;border:1px solid #EEE171;border-radius:4px;
                    padding:12px 16px;margin-top:16px;font-size:12px;color:#484848;
                    font-family:'DM Sans',sans-serif">
            <b style="color:#1D2019">Tip:</b> Deleting a team is only allowed when no agents are assigned to it.
            Go to the Roster page to reassign agents before deleting a team.
        </div>""", unsafe_allow_html=True)


# ─── PAGE: TEMPLATES ─────────────────────────────────────────────────────────

def _template_editor(template_id):
    """Inline template editor — same look as the schedule editor but bound to a template."""
    tmpl = get_template(template_id)
    if not tmpl:
        st.session_state.pop("editing_template_id", None)
        st.rerun()

    # ── Header ────────────────────────────────────────────────────────────────
    hc1, hc2 = st.columns([5, 1])
    with hc1:
        st.markdown(
            f'<div class="page-title">{tmpl["name"]}</div>',
            unsafe_allow_html=True,
        )
        if tmpl.get("description"):
            st.markdown(
                f'<div class="page-sub">{tmpl["description"]}</div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            f'<div style="font-family:\'DM Sans\',sans-serif;font-size:11px;'
            f'color:#979797;margin-bottom:8px">'
            f'Created {tmpl["created_at"]}'
            f'{" by " + tmpl["created_by"] if tmpl["created_by"] else ""}'
            f'</div>',
            unsafe_allow_html=True,
        )
    with hc2:
        if st.button("← All templates", use_container_width=True):
            st.session_state.pop("editing_template_id", None)
            st.rerun()

    # ── Rename / edit meta ────────────────────────────────────────────────────
    with st.expander("Rename / edit description"):
        with st.form(f"rename_tmpl_{template_id}"):
            rc1, rc2 = st.columns(2)
            with rc1:
                new_nm = st.text_input("Name", tmpl["name"])
            with rc2:
                new_desc = st.text_input("Description", tmpl.get("description", ""))
            if st.form_submit_button("Save", type="primary"):
                ok, msg = update_template_meta(template_id, new_nm, new_desc)
                st.toast(msg, icon="✅" if ok else "⚠️")
                if ok:
                    st.rerun()

    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

    # ── Day tabs ──────────────────────────────────────────────────────────────
    agents_all  = get_agents()
    teams       = get_teams()
    team_colors = {t["name"]: t["color"] for t in teams}
    act_names   = get_activity_names()
    act_colors  = get_act_colors()

    day_tabs = st.tabs(DAYS)

    for di, (tab, day_name) in enumerate(zip(day_tabs, DAYS)):
        with tab:
            # Load template schedule for this day
            sched_data = {}
            for ag in agents_all:
                df_tmp = get_template_df(template_id, di, [ag["name"]])
                sched_data[ag["name"]] = df_tmp[ag["name"]].to_dict()

            # Coverage bar
            cov_html = build_coverage_bar_html(sched_data, act_colors)
            if cov_html:
                st.markdown(cov_html, unsafe_allow_html=True)
            else:
                st.caption("No schedule data yet — use the Edit tab below.")

            view_tab, edit_tab = st.tabs(["👁  Timeline view", "✏️  Edit"])

            with view_tab:
                if not agents_all:
                    st.info("Add agents in the Roster page to see the template.")
                else:
                    agents_info = [
                        {"name": a["name"], "team_name": a["team_name"],
                         "color": team_colors.get(a["team_name"], "#64748B")}
                        for a in agents_all
                    ]
                    # Reuse the same team order from session state (shared with schedule view)
                    _tl_order_key = "timeline_team_order"
                    teams_with_agents = [t for t in teams
                                         if any(a["team_name"] == t["name"] for a in agents_info)]
                    if _tl_order_key not in st.session_state:
                        st.session_state[_tl_order_key] = [t["name"] for t in teams_with_agents]
                    else:
                        _cur   = {t["name"] for t in teams_with_agents}
                        _saved = [n for n in st.session_state[_tl_order_key] if n in _cur]
                        _new   = [t["name"] for t in teams_with_agents if t["name"] not in set(_saved)]
                        st.session_state[_tl_order_key] = _saved + _new

                    _team_lookup   = {t["name"]: t for t in teams}
                    _ordered_teams = [_team_lookup[n] for n in st.session_state[_tl_order_key]
                                      if n in _team_lookup]
                    n_rows = len(TIME_SLOTS) * 26 + 60

                    for _i, team in enumerate(_ordered_teams):
                        team_agents = [a for a in agents_info if a["team_name"] == team["name"]]
                        if not team_agents:
                            continue

                        _hcol, _ucol, _dcol = st.columns([30, 1, 1])
                        with _hcol:
                            st.markdown(
                                f'<div style="display:flex;align-items:center;gap:8px;margin:10px 0 4px">'
                                f'<div style="width:10px;height:10px;border-radius:50%;background:{team["color"]}"></div>'
                                f'<span style="font-size:13px;font-weight:600;color:#1D2019">{team["name"]} Team</span>'
                                f'<span style="font-size:11px;color:#979797">— {len(team_agents)} agents</span>'
                                f'</div>', unsafe_allow_html=True
                            )
                        with _ucol:
                            if st.button("↑", key=f"tmpl_tl_up_{template_id}_{di}_{team['name']}",
                                         disabled=(_i == 0), help="Move this team up"):
                                _order = st.session_state[_tl_order_key]
                                _idx   = _order.index(team["name"])
                                _order[_idx], _order[_idx - 1] = _order[_idx - 1], _order[_idx]
                                st.rerun()
                        with _dcol:
                            if st.button("↓", key=f"tmpl_tl_dn_{template_id}_{di}_{team['name']}",
                                         disabled=(_i == len(_ordered_teams) - 1), help="Move this team down"):
                                _order = st.session_state[_tl_order_key]
                                _idx   = _order.index(team["name"])
                                _order[_idx], _order[_idx + 1] = _order[_idx + 1], _order[_idx]
                                st.rerun()

                        team_sched = {a["name"]: sched_data.get(a["name"], {}) for a in team_agents}
                        st_components.html(
                            build_timeline_html(team_agents, team_sched, act_colors),
                            height=min(n_rows, 680), scrolling=True
                        )
                        st.markdown('<div style="height:4px"></div>', unsafe_allow_html=True)

            with edit_tab:
                # ── Quick Fill ────────────────────────────────────────────────
                st.markdown(
                    '<div style="background:#F0F5F3;border:1px solid #C4D9D2;border-radius:4px;'
                    'padding:12px 16px;margin-bottom:14px">'
                    '<div style="font-size:10px;font-weight:700;color:#689985;margin-bottom:10px;'
                    'letter-spacing:0.12em;text-transform:uppercase;font-family:\'DM Sans\',sans-serif">'
                    'Quick Fill — set one activity across multiple slots and agents</div>',
                    unsafe_allow_html=True,
                )
                qc1, qc2, qc3, qc4, qc5 = st.columns([2, 2, 2, 2, 1])
                with qc1:
                    tqf_act = st.selectbox("Activity", [a for a in act_names if a != "."],
                                           key=f"tqf_act_{template_id}_{di}")
                with qc2:
                    fi_idx = TIME_SLOTS.index("9:00 AM") if "9:00 AM" in TIME_SLOTS else 0
                    tqf_from = st.selectbox("From", TIME_SLOTS, index=fi_idx,
                                            key=f"tqf_from_{template_id}_{di}")
                with qc3:
                    ti_idx = TIME_SLOTS.index("5:00 PM") if "5:00 PM" in TIME_SLOTS else len(TIME_SLOTS)-1
                    tqf_to = st.selectbox("To", TIME_SLOTS, index=ti_idx,
                                          key=f"tqf_to_{template_id}_{di}")
                with qc4:
                    tqf_agent = st.selectbox("Agent",
                                             ["All agents"] + [a["name"] for a in agents_all],
                                             key=f"tqf_agent_{template_id}_{di}")
                with qc5:
                    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                    tqf_go = st.button("Apply", key=f"tqf_apply_{template_id}_{di}",
                                       type="primary", use_container_width=True)
                st.markdown("</div>", unsafe_allow_html=True)

                if tqf_go:
                    fi = TIME_SLOTS.index(tqf_from)
                    ti = TIME_SLOTS.index(tqf_to)
                    if fi > ti:
                        st.error("'From' must be before 'To'.")
                    else:
                        tgts = ([a["name"] for a in agents_all] if tqf_agent == "All agents"
                                else [tqf_agent])
                        for aname in tgts:
                            df_tmp = get_template_df(template_id, di, [aname])
                            for slot in TIME_SLOTS[fi:ti+1]:
                                df_tmp.at[slot, aname] = tqf_act
                            save_template_df(template_id, di, df_tmp)
                        st.toast(f"Set {tqf_act} for {len(tgts)} agent(s).", icon="✅")
                        st.rerun()

                # ── Per-team grids ────────────────────────────────────────────
                for team in teams:
                    team_agents = [a["name"] for a in agents_all
                                   if a["team_name"] == team["name"]]
                    if not team_agents:
                        continue
                    clr = team_colors.get(team["name"], "#64748B")
                    st.markdown(
                        f'<div style="background:{clr};color:white;padding:6px 12px;'
                        f'border-radius:4px;font-weight:600;font-size:12px;margin:12px 0 6px">'
                        f'{team["name"]} Team '
                        f'<span style="opacity:0.7;font-weight:400">— {len(team_agents)} agents</span></div>',
                        unsafe_allow_html=True,
                    )
                    df = get_template_df(template_id, di, team_agents)
                    col_cfg = {
                        col: st.column_config.SelectboxColumn(
                            label=col.split()[0], options=act_names, default=".", width="small"
                        )
                        for col in df.columns
                    }
                    edited = st.data_editor(
                        df, column_config=col_cfg, use_container_width=True,
                        key=f"tmpl_edit_{template_id}_{di}_{team['name']}",
                        height=min(420, 35 * len(TIME_SLOTS) + 38),
                    )
                    if st.button(f"💾 Save {team['name']}",
                                 key=f"tmpl_save_{template_id}_{di}_{team['name']}",
                                 use_container_width=True, type="primary"):
                        save_template_df(template_id, di, edited)
                        st.toast(f"Saved {team['name']} for {day_name}.", icon="✅")
                        st.rerun()


def page_templates():
    if not can_edit():
        st.warning("Editor access required.")
        return

    # Route to editor if one is selected
    if st.session_state.get("editing_template_id"):
        _template_editor(st.session_state["editing_template_id"])
        return

    st.markdown('<div class="page-title">Templates</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="page-sub">Build reusable weekly schedules. '
        'Apply any template to a week from the Schedule page, then adjust as needed.</div>',
        unsafe_allow_html=True,
    )

    templates = get_templates()

    if not templates:
        st.markdown(
            '<div style="background:#F0F5F3;border:1px solid #C4D9D2;border-radius:4px;'
            'padding:20px 24px;text-align:center;color:#484848;font-family:\'DM Sans\',sans-serif">'
            '<div style="font-size:15px;font-weight:600;color:#1D2019;margin-bottom:6px">'
            'No templates yet</div>'
            '<div style="font-size:13px">Create one below, or go to the Schedule page, '
            'build a week, then use <b>Templates → Save week as template</b>.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        cols = st.columns(min(len(templates), 3))
        for i, tmpl in enumerate(templates):
            with cols[i % 3]:
                st.markdown(
                    f'<div class="scard" style="margin-bottom:12px">'
                    f'<div style="font-family:\'Cheltenham\',Georgia,serif;font-size:15px;'
                    f'font-weight:bold;color:#1D2019;margin-bottom:4px">{tmpl["name"]}</div>'
                    f'<div style="font-size:12px;color:#979797;margin-bottom:8px;'
                    f'font-family:\'DM Sans\',sans-serif">'
                    f'{tmpl["description"] or ""}</div>'
                    f'<div style="font-size:11px;color:#D8D8D8;font-family:\'DM Sans\',sans-serif">'
                    f'Created {tmpl["created_at"]}'
                    f'{" · " + tmpl["created_by"] if tmpl["created_by"] else ""}'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
                bc1, bc2, bc3 = st.columns(3)
                with bc1:
                    if st.button("Edit", key=f"edit_tmpl_{tmpl['id']}",
                                 use_container_width=True, type="primary"):
                        st.session_state["editing_template_id"] = tmpl["id"]
                        st.rerun()
                with bc2:
                    if st.button("Duplicate", key=f"dup_tmpl_{tmpl['id']}",
                                 use_container_width=True):
                        ok, new_id, msg = duplicate_template(tmpl["id"], tmpl["name"] + " (copy)")
                        st.toast(msg, icon="✅" if ok else "⚠️")
                        if ok:
                            st.rerun()
                with bc3:
                    if st.button("Delete", key=f"del_tmpl_{tmpl['id']}",
                                 use_container_width=True):
                        delete_template(tmpl["id"])
                        st.toast(f"Deleted '{tmpl['name']}'.", icon="🗑️")
                        st.rerun()

    st.divider()
    st.markdown(
        '<div style="font-family:\'Cheltenham\',Georgia,serif;font-size:16px;'
        'font-weight:bold;color:#1D2019;margin-bottom:12px">Create new template</div>',
        unsafe_allow_html=True,
    )

    with st.form("create_template_form"):
        nc1, nc2 = st.columns(2)
        with nc1:
            new_tmpl_name = st.text_input("Template name",
                                          placeholder="e.g. Standard Mon–Fri")
        with nc2:
            new_tmpl_desc = st.text_input("Description (optional)",
                                          placeholder="e.g. Default coverage pattern")
        if st.form_submit_button("Create template", type="primary"):
            if not new_tmpl_name.strip():
                st.error("Name required.")
            else:
                u = current_user()
                ok, new_id, msg = create_template(
                    new_tmpl_name, new_tmpl_desc,
                    u["display_name"] if u else "",
                )
                if ok:
                    st.session_state["editing_template_id"] = new_id
                    st.rerun()
                else:
                    st.error(msg)


# ─── PAGE: SETTINGS ──────────────────────────────────────────────────────────

def page_settings():
    if not is_admin():
        st.warning("Admin access required.")
        return

    st.markdown('<div class="page-title">Settings</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Add and color-code the activity types that appear in the schedule editor.</div>', unsafe_allow_html=True)

    acts = get_activities()

    # ── Color palette preview ──────────────────────────────────────────────────
    st.markdown('<div style="font-size:13px;font-weight:600;color:#0F172A;margin-bottom:8px">Current activity palette</div>', unsafe_allow_html=True)
    pills = "".join(
        f'<span style="background:{a["bg_color"]};color:{a["fg_color"]};'
        f'padding:4px 12px;border-radius:99px;font-size:12px;font-weight:600;'
        f'display:inline-block;margin:3px 4px 3px 0;border:1px solid rgba(0,0,0,0.06)">'
        f'{a["name"]}</span>'
        for a in acts
    )
    st.markdown(f'<div style="line-height:2;margin-bottom:20px">{pills}</div>', unsafe_allow_html=True)

    # ── Edit existing activities ───────────────────────────────────────────────
    st.markdown('<div style="font-size:13px;font-weight:600;color:#0F172A;margin-bottom:4px">Edit activity types</div>', unsafe_allow_html=True)
    st.caption("Change the name or colors of any activity. Changes apply immediately to the schedule view.")

    for act in acts:
        bg_cur, fg_cur = act["bg_color"], act["fg_color"]
        with st.expander(act["name"], expanded=False):
            # Live preview swatch
            st.markdown(
                f'<div style="background:{bg_cur};color:{fg_cur};padding:6px 14px;'
                f'border-radius:8px;display:inline-block;font-size:13px;font-weight:600;'
                f'margin-bottom:10px;border:1px solid rgba(0,0,0,0.08)">{act["name"]}</div>',
                unsafe_allow_html=True,
            )
            with st.form(f"edit_act_{act['id']}"):
                c1, c2, c3 = st.columns([3, 1, 1])
                with c1:
                    new_name = st.text_input("Name", act["name"])
                with c2:
                    new_bg = st.color_picker("Background", act["bg_color"],
                                             help="Cell background color")
                with c3:
                    new_fg = st.color_picker("Text color", act["fg_color"],
                                             help="Label text color")

                cs, cd = st.columns(2)
                with cs:
                    if st.form_submit_button("Save changes", use_container_width=True, type="primary"):
                        ok, msg = upsert_activity(new_name.strip(), new_bg, new_fg, act["id"])
                        st.toast(msg, icon="✅" if ok else "❌")
                        if ok:
                            st.rerun()
                with cd:
                    if st.form_submit_button("Delete", use_container_width=True):
                        delete_activity_db(act["id"])
                        st.toast(f"Deleted '{act['name']}'.", icon="🗑️")
                        st.rerun()

    # ── Add new activity ───────────────────────────────────────────────────────
    st.divider()
    st.markdown('<div style="font-size:13px;font-weight:600;color:#0F172A;margin-bottom:4px">Add a new activity type</div>', unsafe_allow_html=True)

    with st.form("new_activity"):
        c1, c2, c3 = st.columns([3, 1, 1])
        with c1:
            new_nm = st.text_input("Activity name", placeholder="e.g. Outbound Calls")
        with c2:
            new_bg2 = st.color_picker("Background", "#E0E7FF",
                                      help="The cell background color in the schedule")
        with c3:
            new_fg2 = st.color_picker("Text color", "#3730A3",
                                      help="The label text color")

        # Preview (static — shows the default until saved)
        st.markdown(
            '<div style="font-size:11px;color:#94A3B8;margin-top:4px">'
            'Tip: choose a light background with a dark matching text color for best readability.</div>',
            unsafe_allow_html=True,
        )
        if st.form_submit_button("Add activity type", type="primary"):
            if not new_nm.strip():
                st.error("Name required.")
            else:
                ok, msg = upsert_activity(new_nm.strip(), new_bg2, new_fg2)
                st.success(msg) if ok else st.error(msg)
                if ok:
                    st.rerun()

    # ── Color tips ─────────────────────────────────────────────────────────────
    st.markdown("""
    <div style="background:#F0F5F3;border:1px solid #C4D9D2;border-radius:4px;
                padding:12px 16px;margin-top:16px;font-size:12px;color:#484848;
                font-family:'DM Sans',sans-serif">
        <b style="color:#1D2019">Color tip:</b> Use a light pastel as the background (e.g. #DBEAFE)
        and a dark shade of the same hue as the text color (e.g. #1E40AF).
        This ensures cells are readable in both the timeline and the editor grid.
    </div>""", unsafe_allow_html=True)


# ─── PAGE: USERS ──────────────────────────────────────────────────────────────

def page_users():
    if not is_admin():
        st.warning("Admin access required.")
        return

    st.markdown('<div class="page-title">Users</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Manage who can access the scheduler and what they can do.</div>', unsafe_allow_html=True)

    users = list_users()
    role_colors = {"admin": "#FBBF24", "editor": "#60A5FA", "viewer": "#94A3B8"}
    role_descs  = {
        "admin":  "Full access — schedule, time off, roster, teams, users",
        "editor": "Edit schedules and approve time off, but cannot manage users",
        "viewer": "Read-only schedule + submit own time-off requests",
    }

    st.markdown('<div style="font-size:14px;font-weight:600;color:#0F172A;margin-bottom:12px">Current users</div>', unsafe_allow_html=True)

    for u in users:
        rc = role_colors.get(u["role"], "#94A3B8")
        initials = "".join(p[0] for p in (u["display_name"] or u["username"]).split()[:2]).upper()
        active_label = "Active" if u["active"] else "Inactive"
        with st.expander(f"{u['display_name'] or u['username']}  —  {u['role']}  {'·  ' + active_label if not u['active'] else ''}"):
            st.markdown(f"""
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
                <div style="width:36px;height:36px;border-radius:50%;background:{rc}22;color:{rc};
                            font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center">
                    {initials}
                </div>
                <div>
                    <div style="font-weight:600;color:#0F172A">{u["display_name"] or u["username"]}</div>
                    <div style="font-size:12px;color:#94A3B8">@{u["username"]} · {role_descs.get(u["role"],"")}</div>
                </div>
            </div>""", unsafe_allow_html=True)

            cu = current_user()
            is_self = cu and cu["id"] == u["id"]

            with st.form(f"edit_user_{u['id']}"):
                c1, c2 = st.columns(2)
                with c1:
                    dn = st.text_input("Display name", u["display_name"])
                with c2:
                    role_opts = ["admin", "editor", "viewer"]
                    role_idx  = role_opts.index(u["role"]) if u["role"] in role_opts else 2
                    new_role  = st.selectbox("Role", role_opts, index=role_idx,
                                             disabled=is_self)
                active_chk = st.checkbox("Active", value=bool(u["active"]),
                                         disabled=is_self,
                                         help="Inactive users cannot sign in.")
                new_pw = st.text_input("New password (leave blank to keep current)", type="password")

                cs, cd = st.columns(2)
                with cs:
                    if st.form_submit_button("Save changes", use_container_width=True):
                        update_user(u["id"], dn, new_role if not is_self else u["role"],
                                    active_chk if not is_self else u["active"])
                        if new_pw.strip():
                            reset_password(u["id"], new_pw.strip())
                        st.toast("User updated.", icon="✅")
                        st.rerun()
                with cd:
                    if st.form_submit_button("Delete user", use_container_width=True,
                                             disabled=is_self):
                        delete_user_db(u["id"])
                        st.toast(f"Deleted user {u['username']}.", icon="🗑️")
                        st.rerun()

    st.divider()
    st.markdown('<div style="font-size:14px;font-weight:600;color:#0F172A;margin-bottom:12px">Create new user</div>', unsafe_allow_html=True)

    with st.form("new_user"):
        c1, c2 = st.columns(2)
        with c1: new_uname = st.text_input("Username", placeholder="e.g. jsmith")
        with c2: new_dname = st.text_input("Display name", placeholder="e.g. Jordan Smith")
        c3, c4 = st.columns(2)
        with c3: new_pw2   = st.text_input("Password", type="password")
        with c4:
            new_role2 = st.selectbox("Role", ["viewer", "editor", "admin"],
                                     help="viewer = agents, editor = schedulers, admin = full access")
        if st.form_submit_button("Create user", type="primary"):
            if not new_uname.strip():
                st.error("Username required.")
            elif not new_pw2.strip():
                st.error("Password required.")
            else:
                ok, msg = create_user(new_uname, new_pw2, new_dname, new_role2)
                st.success(msg) if ok else st.error(msg)
                if ok: st.rerun()

    st.markdown(f"""
    <div style="background:#F0F5F3;border:1px solid #C4D9D2;border-radius:4px;
                padding:12px 16px;margin-top:16px;font-size:12px;color:#484848;
                font-family:'DM Sans',sans-serif">
        <b style="color:#1D2019">Role guide</b><br>
        <b>admin</b> — {role_descs["admin"]}<br>
        <b>editor</b> — {role_descs["editor"]}<br>
        <b>viewer</b> — {role_descs["viewer"]}
    </div>""", unsafe_allow_html=True)


# ─── PAGE: REPORTS ────────────────────────────────────────────────────────────

def page_reports():
    st.markdown('<div class="page-title">Reports</div>', unsafe_allow_html=True)

    agents      = get_agents()
    teams       = get_teams()
    dyn_colors  = get_act_colors()
    all_req = get_time_off_requests()
    today   = datetime.date.today()
    monday  = today - datetime.timedelta(days=today.weekday())
    week_end = monday + datetime.timedelta(days=6)

    # PTO days this week
    pto_days = sum(
        (min(datetime.date.fromisoformat(r["end_date"]), week_end)
         - max(datetime.date.fromisoformat(r["start_date"]), monday)).days + 1
        for r in all_req if r["status"] == "Approved"
        and datetime.date.fromisoformat(r["start_date"]) <= week_end
        and datetime.date.fromisoformat(r["end_date"]) >= monday
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1: metric("Total agents", len(agents))
    with c2: metric("Teams", len(teams))
    with c3: metric("PTO days this week", max(0, pto_days))
    with c4: metric("Pending approvals", len([r for r in all_req if r["status"]=="Pending"]))

    st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

    c_left, c_right = st.columns(2)

    with c_left:
        st.markdown('<div style="font-size:14px;font-weight:600;color:#0F172A;margin-bottom:8px">Agents by team</div>', unsafe_allow_html=True)
        for team in teams:
            count = len([a for a in agents if a["team_name"] == team["name"]])
            pct = int(count / len(agents) * 100) if agents else 0
            st.markdown(f"""
            <div style="margin-bottom:10px">
                <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px">
                    <span style="color:#1E293B;font-weight:500">{team["name"]}</span>
                    <span style="color:#94A3B8">{count} agents</span>
                </div>
                <div style="background:#F1F5F9;border-radius:99px;height:8px;overflow:hidden">
                    <div style="background:{team['color']};width:{pct}%;height:100%;border-radius:99px"></div>
                </div>
            </div>""", unsafe_allow_html=True)

    with c_right:
        st.markdown('<div style="font-size:14px;font-weight:600;color:#0F172A;margin-bottom:8px">Time off by type</div>', unsafe_allow_html=True)
        if all_req:
            df = pd.DataFrame(all_req)
            by_type = df.groupby("type").size().reset_index(name="count").sort_values("count", ascending=False)
            for _, row in by_type.iterrows():
                bg, fg = dyn_colors.get(row["type"], ("#F1F5F9", "#475569"))
                st.markdown(f"""
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
                    <span style="background:{bg};color:{fg};padding:2px 10px;border-radius:99px;
                                 font-size:12px;font-weight:600;min-width:80px;text-align:center">{row["type"]}</span>
                    <span style="font-size:13px;color:#1E293B">{row["count"]} request{"s" if row["count"]!=1 else ""}</span>
                </div>""", unsafe_allow_html=True)
        else:
            st.caption("No requests yet.")

    st.markdown('<div style="height:20px"></div>', unsafe_allow_html=True)
    st.markdown('<div style="font-size:14px;font-weight:600;color:#0F172A;margin-bottom:8px">Recent time off requests</div>', unsafe_allow_html=True)

    recent = sorted(all_req, key=lambda r: r["submitted_date"], reverse=True)[:10]
    if recent:
        df_show = pd.DataFrame([{
            "Agent": r["agent_name"],
            "Team":  r["team_name"],
            "Type":  r["type"],
            "Start": r["start_date"],
            "End":   r["end_date"],
            "Status": r["status"],
            "Approved by": r["approved_by"],
        } for r in recent])
        st.dataframe(df_show, use_container_width=True, hide_index=True)
    else:
        st.caption("No requests recorded yet.")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    init_db()
    inject_css()

    # ── Login gate ─────────────────────────────────────────────────────────────
    if not current_user():
        show_login()
        return   # show_login calls st.stop() but return is here for clarity

    page = sidebar()

    page_map = {
        "Schedule":  page_schedule,
        "Time Off":  page_timeoff,
        "Roster":    page_roster,
        "Teams":     page_teams,
        "Templates": page_templates,
        "Users":     page_users,
        "Settings":  page_settings,
        "Reports":   page_reports,
    }
    fn = page_map.get(page)
    if fn:
        fn()

if __name__ == "__main__":
    main()
