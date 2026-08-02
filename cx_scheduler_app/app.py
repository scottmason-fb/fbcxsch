import streamlit as st
import streamlit.components.v1 as st_components
import sqlite3
import pandas as pd
import datetime
import hashlib
import secrets
import math
from pathlib import Path
_COMPONENT_PATH = str(Path(__file__).parent / "cx_component")

st.set_page_config(
    page_title="CX Schedule",
    page_icon="📅",
    layout="wide",
    initial_sidebar_state="auto",
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

TIMEOFF_TYPES = ["VTO", "Shift Swap", "Sick Leave (Please submit in Kronos)", "Paid Time Off (Please submit in Kronos)", "Jury Duty", "Bereavement"]

# Maps time-off request type → schedule cell activity label
TIMEOFF_TO_ACTIVITY = {
    "Paid Time Off (Please submit in Kronos)": "PTO",
    "Sick Leave (Please submit in Kronos)":    "Sick",
    "VTO":          "VTO",
    "Bereavement":  "Bereavement",
    "Jury Duty":    "Jury Duty",
    "Shift Swap":   None,  # handled separately via swap logic
}

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

def _default_to_today_tab(week_start, key_prefix="sched_day_tab__"):
    """
    When viewing the current week, inject a one-shot JS snippet that clicks
    today's day tab.  sessionStorage prevents re-clicking on Streamlit reruns.
    Pass a unique key_prefix per page so different views don't share the flag.
    """
    today = datetime.date.today()
    current_monday = str(today - datetime.timedelta(days=today.weekday()))
    if week_start != current_monday:
        return
    label = DAYS[today.weekday()][:3]   # "Mon", "Tue", …
    ss_key = f"{key_prefix}{week_start}"
    st_components.html(
        f"""<script>
(function(){{
  var KEY   = "{ss_key}";
  var LABEL = "{label}";
  if (window.parent.sessionStorage.getItem(KEY)) return;
  function tryClick() {{
    var tabs = window.parent.document.querySelectorAll('[data-baseweb="tab"]');
    for (var i = 0; i < tabs.length; i++) {{
      if (tabs[i].textContent.trim().startsWith(LABEL)) {{
        tabs[i].click();
        window.parent.sessionStorage.setItem(KEY, "1");
        return true;
      }}
    }}
    return false;
  }}
  var n = 0;
  var iv = setInterval(function() {{
    if (tryClick() || ++n > 40) clearInterval(iv);
  }}, 75);
}})();
</script>""",
        height=0,
    )


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
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            read INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS agent_work_hours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            day_index INTEGER NOT NULL,
            start_slot TEXT NOT NULL DEFAULT '9:00 AM',
            end_slot TEXT NOT NULL DEFAULT '5:00 PM',
            is_active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(agent_name, day_index)
        );
        CREATE TABLE IF NOT EXISTS agent_coverage_rules (
            agent_name TEXT PRIMARY KEY,
            allowed_channels TEXT NOT NULL DEFAULT 'both',
            lunch_slot TEXT DEFAULT NULL,
            lunch_duration INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS coverage_global_rules (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '0'
        );
        INSERT OR IGNORE INTO coverage_global_rules (key, value) VALUES ('no_back_to_back', '1');
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

    if "start_time" not in _col_names("time_off_requests"):
        c.execute("ALTER TABLE time_off_requests ADD COLUMN start_time TEXT DEFAULT ''")
        conn.commit()
    if "end_time" not in _col_names("time_off_requests"):
        c.execute("ALTER TABLE time_off_requests ADD COLUMN end_time TEXT DEFAULT ''")
        conn.commit()
    if "swap_from_date" not in _col_names("time_off_requests"):
        c.execute("ALTER TABLE time_off_requests ADD COLUMN swap_from_date TEXT DEFAULT ''")
        conn.commit()
    if "swap_from_start" not in _col_names("time_off_requests"):
        c.execute("ALTER TABLE time_off_requests ADD COLUMN swap_from_start TEXT DEFAULT ''")
        conn.commit()
    if "swap_from_end" not in _col_names("time_off_requests"):
        c.execute("ALTER TABLE time_off_requests ADD COLUMN swap_from_end TEXT DEFAULT ''")
        conn.commit()

    if "team_order" not in _col_names("users"):
        c.execute("ALTER TABLE users ADD COLUMN team_order TEXT DEFAULT ''")
        conn.commit()

    if "notes" not in _col_names("agents"):
        c.execute("ALTER TABLE agents ADD COLUMN notes TEXT DEFAULT ''")
        conn.commit()

    if "default_activity" not in _col_names("teams"):
        c.execute("ALTER TABLE teams ADD COLUMN default_activity TEXT DEFAULT ''")
        conn.commit()

    if "default_activity" not in _col_names("agents"):
        c.execute("ALTER TABLE agents ADD COLUMN default_activity TEXT DEFAULT ''")
        conn.commit()

    if "linked_user_id" not in _col_names("agents"):
        c.execute("ALTER TABLE agents ADD COLUMN linked_user_id INTEGER DEFAULT NULL")
        conn.commit()
    if "slack_user_id" not in _col_names("agents"):
        c.execute("ALTER TABLE agents ADD COLUMN slack_user_id TEXT DEFAULT NULL")
        conn.commit()

    if "split_start_slot" not in _col_names("agent_work_hours"):
        c.execute("ALTER TABLE agent_work_hours ADD COLUMN split_start_slot TEXT DEFAULT NULL")
        conn.commit()

    if "split_end_slot" not in _col_names("agent_work_hours"):
        c.execute("ALTER TABLE agent_work_hours ADD COLUMN split_end_slot TEXT DEFAULT NULL")
        conn.commit()

    try:
        get_conn().execute("ALTER TABLE agent_coverage_rules ADD COLUMN lunch_overrides TEXT DEFAULT NULL")
    except Exception:
        pass

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

def get_user_by_id(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
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

def get_user_team_order(user_id):
    """Return saved team order list for a user, or [] if not set."""
    conn = get_conn()
    row = conn.execute("SELECT team_order FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    raw = row["team_order"] if row and row["team_order"] else ""
    return [t for t in raw.split(",") if t] if raw else []

def save_user_team_order(user_id, order_list):
    """Persist the team display order for a user."""
    conn = get_conn()
    conn.execute("UPDATE users SET team_order=? WHERE id=?", (",".join(order_list), user_id))
    conn.commit()
    conn.close()

def resolve_team_order(user, teams_with_agents):
    """
    Return an ordered list of team dicts for the given user.
    Priority: 1) saved DB order  2) user's own team first  3) DB insertion order.
    Syncs session_state and DB so reorders persist.
    """
    uid  = user["id"] if user else 0
    uname = user.get("display_name", "") if user else ""
    key  = f"team_order_{uid}"
    all_names = [t["name"] for t in teams_with_agents]

    if key not in st.session_state:
        saved = get_user_team_order(uid)
        # Sync: keep only teams that still exist, append new ones
        saved = [n for n in saved if n in all_names]
        new   = [n for n in all_names if n not in saved]
        order = saved + new
        # If no saved order, put user's own team first
        if not saved:
            agents = get_agents()
            my_team = next((a["team_name"] for a in agents if a["name"] == uname), None)
            if my_team and my_team in order:
                order = [my_team] + [n for n in order if n != my_team]
        st.session_state[key] = order
    else:
        # Sync new/removed teams into existing session order
        cur = set(all_names)
        saved = [n for n in st.session_state[key] if n in cur]
        new   = [n for n in all_names if n not in set(saved)]
        st.session_state[key] = saved + new

    lookup = {t["name"]: t for t in teams_with_agents}
    return [lookup[n] for n in st.session_state[key] if n in lookup], key

def delete_user_db(user_id):
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

def show_login():
    st.markdown("""
    <style>
    .login-wrap{background:white;border-radius:8px;padding:40px 36px;
                border:1px solid #D8D8D8;box-shadow:0 4px 24px rgba(29,32,25,0.08);
                margin-top:60px}
    .login-logo{font-family:'Cheltenham',Georgia,serif;font-size:26px;font-weight:bold;
                color:#1D2019;margin-bottom:4px;letter-spacing:-0.01em}
    .login-brand{font-family:'DM Sans',Helvetica,sans-serif;font-size:10px;
                 color:#89AC9E;letter-spacing:0.15em;text-transform:uppercase;margin-bottom:28px}
    </style>""", unsafe_allow_html=True)

    _, mid, _ = st.columns([1.5, 1, 1.5])
    with mid:
        st.markdown("""
        <div class="login-wrap">
            <div class="login-logo">CX Schedule</div>
            <div class="login-brand">Framebridge</div>
        </div>""", unsafe_allow_html=True)

        with st.form("login_form"):
            username  = st.text_input("Username")
            password  = st.text_input("Password", type="password")
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

def save_schedule_df(week_start, day_index, df, notify=True):
    conn = get_conn()
    c = conn.cursor()
    changed_agents = set()
    for slot in df.index:
        for agent in df.columns:
            act = str(df.at[slot, agent])
            # Check existing value to detect changes
            if notify:
                existing = c.execute(
                    "SELECT activity FROM schedule_cells WHERE week_start=? AND day_index=? AND time_slot=? AND agent_name=?",
                    (week_start, day_index, slot, agent)
                ).fetchone()
                if existing and existing["activity"] != act and act != ".":
                    changed_agents.add(agent)
            c.execute("""
                INSERT INTO schedule_cells (week_start,day_index,time_slot,agent_name,activity)
                VALUES (?,?,?,?,?)
                ON CONFLICT(week_start,day_index,time_slot,agent_name)
                DO UPDATE SET activity=excluded.activity
            """, (week_start, day_index, slot, agent, act))
    conn.commit()
    conn.close()
    # Stamp who saved and when so other users' watchers can detect the change
    _saver = current_user()
    _saver_name = _saver["display_name"] if _saver else "Someone"
    set_setting("schedule_last_modified",
                f"{datetime.datetime.now().isoformat()}|{_saver_name}")
    # Create notifications for agents whose schedule changed
    if notify:
        day_name = DAYS[day_index] if day_index < len(DAYS) else f"Day {day_index}"
        for agent in changed_agents:
            add_notification(agent,
                f"📅 Your {day_name} schedule (week of {week_start}) has been updated.")

        # Slack DM — only for today's schedule, only if DM notifications are enabled
        _today = datetime.date.today()
        _today_ws = str(_today - datetime.timedelta(days=_today.weekday()))
        if (week_start == _today_ws and day_index == _today.weekday()
                and get_setting("slack_dm_schedule_updates", "") == "yes"
                and changed_agents):
            _conn_dm = get_conn()
            for agent in changed_agents:
                _row = _conn_dm.execute(
                    "SELECT slack_user_id FROM agents WHERE name=?", (agent,)
                ).fetchone()
                _slack_id = _row["slack_user_id"] if _row else None
                if _slack_id:
                    _first = agent.split()[0]
                    send_slack_dm(
                        _slack_id,
                        f"📅 Hi {_first}! Your schedule for today ({_today.strftime('%-m/%-d')}) "
                        f"has been updated. Check the CX Schedule app for your current assignments."
                    )
            _conn_dm.close()

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
        st_time = req["start_time"] if req["start_time"] else ""
        en_time = req["end_time"]   if req["end_time"]   else ""
        # Determine which slots to fill: full day or bounded by start_time/end_time
        if st_time and en_time and st_time in TIME_SLOTS and en_time in TIME_SLOTS:
            si = TIME_SLOTS.index(st_time)
            ei = TIME_SLOTS.index(en_time)
            slots_to_fill = TIME_SLOTS[si:ei + 1]
        else:
            slots_to_fill = TIME_SLOTS
        for di, dd in enumerate([week_date + datetime.timedelta(days=i) for i in range(7)]):
            if s <= dd <= e:
                for slot in slots_to_fill:
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
    if status == "Approved":
        row = conn.execute("SELECT * FROM time_off_requests WHERE id=?", (req_id,)).fetchone()
        req = dict(row) if row else None
    else:
        req = None
    conn.close()
    if req:
        apply_single_timeoff(req)

def delete_time_off_request(req_id):
    conn = get_conn()
    conn.execute("DELETE FROM time_off_requests WHERE id=?", (req_id,))
    conn.commit()
    conn.close()

def apply_single_timeoff(req):
    """Write a single approved time-off request into schedule_cells immediately."""
    rtype = req.get("type", "")
    agent = req.get("agent_name", "")
    if not agent:
        return

    def _write_slots(date_obj, slots, activity):
        week_start = str(date_obj - datetime.timedelta(days=date_obj.weekday()))
        di = date_obj.weekday()
        conn = get_conn()
        for slot in slots:
            conn.execute("""
                INSERT INTO schedule_cells (week_start,day_index,time_slot,agent_name,activity)
                VALUES (?,?,?,?,?)
                ON CONFLICT(week_start,day_index,time_slot,agent_name)
                DO UPDATE SET activity=excluded.activity
            """, (week_start, di, slot, agent, activity))
        conn.commit()
        conn.close()

    def _slots_between(start_t, end_t):
        if start_t in TIME_SLOTS and end_t in TIME_SLOTS:
            si, ei = TIME_SLOTS.index(start_t), TIME_SLOTS.index(end_t)
            return TIME_SLOTS[si:ei + 1]
        return TIME_SLOTS

    if rtype == "Shift Swap":
        # Clear the "giving up" block
        from_date_str = req.get("swap_from_date", "")
        from_start    = req.get("swap_from_start", "")
        from_end      = req.get("swap_from_end", "")
        if from_date_str and from_start and from_end:
            from_date = datetime.date.fromisoformat(from_date_str)
            _write_slots(from_date, _slots_between(from_start, from_end), ".")

        # Fill the "taking on" block with agent's default activity
        to_date_str = req.get("start_date", "")
        to_start    = req.get("start_time", "")
        to_end      = req.get("end_time", "")
        if to_date_str and to_start and to_end:
            ag_data = next((a for a in get_agents() if a["name"] == agent), {})
            skill = ag_data.get("default_activity", "") or ag_data.get("team_name", "Support")
            to_date = datetime.date.fromisoformat(to_date_str)
            _write_slots(to_date, _slots_between(to_start, to_end), skill)
    else:
        activity = TIMEOFF_TO_ACTIVITY.get(rtype, rtype)
        start_t = req.get("start_time", "")
        end_t   = req.get("end_time", "")
        slots   = _slots_between(start_t, end_t) if (start_t and end_t) else TIME_SLOTS
        s = datetime.date.fromisoformat(req["start_date"])
        e = datetime.date.fromisoformat(req["end_date"])
        d = s
        while d <= e:
            _write_slots(d, slots, activity)
            d += datetime.timedelta(days=1)

# ─── APP SETTINGS ─────────────────────────────────────────────────────────────

def get_setting(key, default=""):
    conn = get_conn()
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT INTO app_settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))
    conn.commit()
    conn.close()

# ─── NOTIFICATIONS ────────────────────────────────────────────────────────────

def add_notification(agent_name, message):
    conn = get_conn()
    conn.execute("INSERT INTO notifications (agent_name,message,created_at) VALUES (?,?,?)",
                 (agent_name, message, str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))))
    conn.commit()
    conn.close()

def get_notifications(agent_name, unread_only=False):
    conn = get_conn()
    if unread_only:
        rows = conn.execute("SELECT * FROM notifications WHERE agent_name=? AND read=0 ORDER BY id DESC", (agent_name,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM notifications WHERE agent_name=? ORDER BY id DESC LIMIT 20", (agent_name,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def mark_notifications_read(agent_name):
    conn = get_conn()
    conn.execute("UPDATE notifications SET read=1 WHERE agent_name=?", (agent_name,))
    conn.commit()
    conn.close()

# ─── SLACK WEBHOOK ────────────────────────────────────────────────────────────

# ─── WORK HOURS & BASE SCHEDULE ───────────────────────────────────────────────

def get_agent_work_hours(agent_name):
    """Returns {day_index: {start_slot, end_slot, is_active, split_start_slot, split_end_slot}} for all 7 days."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT day_index, start_slot, end_slot, is_active, split_start_slot, split_end_slot FROM agent_work_hours WHERE agent_name=?",
        (agent_name,)
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        result[r["day_index"]] = {
            "start_slot":       r["start_slot"],
            "end_slot":         r["end_slot"],
            "is_active":        bool(r["is_active"]),
            "split_start_slot": r["split_start_slot"],
            "split_end_slot":   r["split_end_slot"],
        }
    return result

def save_agent_work_hours(agent_name, day_index, start_slot, end_slot, is_active,
                          split_start_slot=None, split_end_slot=None):
    conn = get_conn()
    conn.execute("""
        INSERT INTO agent_work_hours (agent_name, day_index, start_slot, end_slot, is_active,
                                      split_start_slot, split_end_slot)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(agent_name, day_index)
        DO UPDATE SET start_slot=excluded.start_slot,
                      end_slot=excluded.end_slot,
                      is_active=excluded.is_active,
                      split_start_slot=excluded.split_start_slot,
                      split_end_slot=excluded.split_end_slot
    """, (agent_name, day_index, start_slot, end_slot, int(is_active),
          split_start_slot, split_end_slot))
    conn.commit()
    conn.close()

def get_team_default_activity(team_name):
    conn = get_conn()
    row = conn.execute("SELECT default_activity FROM teams WHERE name=?", (team_name,)).fetchone()
    conn.close()
    return row["default_activity"] if row else ""

def set_team_default_activity(team_name, activity):
    conn = get_conn()
    conn.execute("UPDATE teams SET default_activity=? WHERE name=?", (activity, team_name))
    conn.commit()
    conn.close()

def get_agent_default_activity(agent_name):
    """Returns agent override if set, else falls back to team default."""
    conn = get_conn()
    row = conn.execute(
        "SELECT a.default_activity as agent_act, t.default_activity as team_act "
        "FROM agents a LEFT JOIN teams t ON a.team_name=t.name WHERE a.name=?",
        (agent_name,)
    ).fetchone()
    conn.close()
    if not row:
        return ""
    return row["agent_act"] if row["agent_act"] else row["team_act"] or ""

def apply_base_schedule(week_start, overwrite=False):
    """
    Fill schedule_cells for every agent based on their configured work hours
    and default activity (agent override → team default).
    Then overlay the agent's configured lunch slot with 'Break'.
    If overwrite=False, only fills slots currently set to '.'.
    Returns count of slots written.
    """
    agents = get_agents()
    agent_rules, _ = get_coverage_rules()
    count  = 0
    for ag in agents:
        wh      = get_agent_work_hours(ag["name"])
        default = get_agent_default_activity(ag["name"])
        if not default:
            continue  # Skip agents with no default activity configured
        lunch_rules   = agent_rules.get(ag["name"], {})
        for di in range(len(DAYS)):
            day_cfg = wh.get(di)
            if not day_cfg or not day_cfg["is_active"]:
                continue
            start = day_cfg["start_slot"]
            end   = day_cfg["end_slot"]
            if start not in TIME_SLOTS or end not in TIME_SLOTS:
                continue
            si = TIME_SLOTS.index(start)
            ei = TIME_SLOTS.index(end)
            if ei <= si:
                continue
            # Build list of slot ranges to fill (primary + optional split segment)
            _ranges = [TIME_SLOTS[si:ei]]
            _sp_start = day_cfg.get("split_start_slot")
            _sp_end   = day_cfg.get("split_end_slot")
            if _sp_start and _sp_end and _sp_start in TIME_SLOTS and _sp_end in TIME_SLOTS:
                _spi = TIME_SLOTS.index(_sp_start)
                _spei = TIME_SLOTS.index(_sp_end)
                if _spei > _spi:
                    _ranges.append(TIME_SLOTS[_spi:_spei])
            conn = get_conn()
            _day_abbr = DAYS[di][:3]
            _lunch_slot, _lunch_dur = _resolve_lunch(lunch_rules, _day_abbr)
            if _lunch_slot and _lunch_slot in TIME_SLOTS:
                _li = TIME_SLOTS.index(_lunch_slot)
                lunch_slots = set(TIME_SLOTS[_li:_li + _lunch_dur])
            else:
                lunch_slots = set()
            for _slot_range in _ranges:
                for slot in _slot_range:
                    activity = "Break" if slot in lunch_slots else default
                    if not overwrite:
                        existing = conn.execute(
                            "SELECT activity FROM schedule_cells WHERE week_start=? AND day_index=? AND time_slot=? AND agent_name=?",
                            (week_start, di, slot, ag["name"])
                        ).fetchone()
                        if existing and existing["activity"] != ".":
                            continue
                    conn.execute("""
                        INSERT INTO schedule_cells (week_start, day_index, time_slot, agent_name, activity)
                        VALUES (?,?,?,?,?)
                        ON CONFLICT(week_start,day_index,time_slot,agent_name)
                        DO UPDATE SET activity=excluded.activity
                    """, (week_start, di, slot, ag["name"], activity))
                    count += 1
            conn.commit()
            conn.close()
    return count


def send_slack_message(text):
    """Send a message to the configured Slack webhook. Silently fails if not configured."""
    import urllib.request, json as _json
    webhook = get_setting("slack_webhook_url", "")
    if not webhook.startswith("https://hooks.slack.com/"):
        return
    try:
        payload = _json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(webhook, data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # Never crash the app over a Slack failure

def send_slack_dm(slack_user_id, text):
    """Send a Slack DM to a specific user via Bot Token. Silently fails if not configured."""
    import urllib.request, json as _json
    token = get_setting("slack_bot_token", "")
    if not token or not slack_user_id:
        return
    try:
        _headers = {"Content-Type": "application/json",
                    "Authorization": f"Bearer {token}"}
        # Open DM channel
        req1 = urllib.request.Request(
            "https://slack.com/api/conversations.open",
            data=_json.dumps({"users": slack_user_id}).encode(),
            headers=_headers,
        )
        resp1 = _json.loads(urllib.request.urlopen(req1, timeout=5).read())
        if not resp1.get("ok"):
            return
        channel_id = resp1["channel"]["id"]
        # Send message
        req2 = urllib.request.Request(
            "https://slack.com/api/chat.postMessage",
            data=_json.dumps({"channel": channel_id, "text": text}).encode(),
            headers=_headers,
        )
        urllib.request.urlopen(req2, timeout=5)
    except Exception:
        pass

def import_timeoff_from_sheet(csv_url):
    """
    Fetch a public Google Sheet (CSV export URL) and import rows as time-off requests.
    Expected columns (case-insensitive): Agent, Team, Type of Request, Date and Time, Summary
    Returns (imported_count, skipped_count, error_message)
    """
    import urllib.request as _ur
    import ssl, csv, io, re
    try:
        import certifi as _certifi
        _ssl_ctx = ssl.create_default_context(cafile=_certifi.where())
    except ImportError:
        _ssl_ctx = ssl.create_default_context()

    # Convert edit/share URL → CSV export URL if needed
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", csv_url)
    gid_m = re.search(r"gid=(\d+)", csv_url)
    if m and "export?format=csv" not in csv_url:
        sheet_id = m.group(1)
        gid = gid_m.group(1) if gid_m else "0"
        csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"

    try:
        with _ur.urlopen(csv_url, timeout=10, context=_ssl_ctx) as resp:
            raw = resp.read().decode("utf-8-sig")
    except Exception as e:
        return 0, 0, f"Could not fetch sheet: {e}"

    reader = csv.DictReader(io.StringIO(raw))
    # Normalise header names
    def _norm(h): return h.strip().lower().replace(" ", "_").replace("/", "_")
    rows = [{_norm(k): (v or "").strip() for k, v in row.items()} for row in reader]

    def _parse_date(s):
        """Try several date formats, return datetime.date or None."""
        s = s.strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y",
                    "%m-%d-%Y", "%Y/%m/%d", "%d/%m/%Y"):
            try: return datetime.datetime.strptime(s, fmt).date()
            except ValueError: pass
        # Try extracting first date-like substring
        m = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", s)
        if m:
            for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"):
                try: return datetime.datetime.strptime(m.group(1), fmt).date()
                except ValueError: pass
        return None

    conn = get_conn()
    imported = skipped = 0
    agents_lower = {a["name"].lower(): a for a in get_agents()}
    teams_lower  = {t["name"].lower(): t for t in get_teams()}

    for row in rows:
        agent_raw = row.get("agent", "")
        if not agent_raw:
            skipped += 1; continue

        # Fuzzy-match agent name
        agent_match = agents_lower.get(agent_raw.lower())
        agent_name  = agent_match["name"] if agent_match else agent_raw

        team_raw   = row.get("team", "")
        team_match = teams_lower.get(team_raw.lower())
        team_name  = team_match["name"] if team_match else team_raw

        rtype   = row.get("type_of_request", row.get("type", "PTO")).strip() or "PTO"
        notes   = row.get("summary", row.get("notes", "")).strip()
        date_str = row.get("date_and_time", row.get("date", "")).strip()

        # Parse date range "MM/DD – MM/DD" or "MM/DD to MM/DD" or single date
        range_m = re.search(r"(\d[\d/\-.]+)\s*(?:to|–|-{1,2}|thru)\s*(\d[\d/\-.]+)", date_str, re.I)
        if range_m:
            start = _parse_date(range_m.group(1))
            end   = _parse_date(range_m.group(2))
        else:
            start = _parse_date(date_str)
            end   = start

        if not start or not end:
            skipped += 1; continue
        if end < start:
            end = start

        # Skip duplicates (same agent + same start date already exists)
        exists = conn.execute(
            "SELECT 1 FROM time_off_requests WHERE agent_name=? AND start_date=?",
            (agent_name, str(start))
        ).fetchone()
        if exists:
            skipped += 1; continue

        conn.execute(
            "INSERT INTO time_off_requests (submitted_date,agent_name,team_name,start_date,end_date,type,status,notes) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(datetime.date.today()), agent_name, team_name, str(start), str(end), rtype, "Pending", notes)
        )
        imported += 1

    conn.commit()
    conn.close()
    return imported, skipped, None


def add_time_off_request(agent, team, start, end, rtype, notes="", start_time="", end_time="",
                         swap_from_date="", swap_from_start="", swap_from_end=""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO time_off_requests "
        "(submitted_date,agent_name,team_name,start_date,end_date,type,status,notes,"
        "start_time,end_time,swap_from_date,swap_from_start,swap_from_end) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(datetime.date.today()), agent, team, str(start), str(end), rtype, "Pending", notes,
         start_time, end_time, swap_from_date, swap_from_start, swap_from_end)
    )
    conn.commit()
    conn.close()

# ─── TIMELINE HTML ────────────────────────────────────────────────────────────

def build_timeline_html(agents_info, schedule_data, act_colors=None, slot_label_map=None):
    """
    Transposed grid layout: times down the left, agent names across the top.
    agents_info: list of {"name": str, "team_name": str, "color": str}
    schedule_data: {agent_name: {time_slot: activity}}
    act_colors: {name: (bg_hex, fg_hex)} — if None, falls back to module-level ACT_COLORS
    slot_label_map: optional {original_slot: display_label} for timezone conversion
    Returns a scrollable HTML table.
    """
    _colors = act_colors if act_colors is not None else ACT_COLORS
    _INACTIVE_LOCAL    = {".", "Break", "Admin", "PTO", "VTO", "Sick",
                          "Holiday", "Bereavement", "FMLA", "Training", "Meeting"}
    _LIVE_CHAT_LOCAL   = {"Chat"}
    _LIVE_PHONES_LOCAL = {"Phones"}

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
        f'<th style="{sum_th_style};background:#0C4A6E;color:#BAE6FD">Chat</th>'
        f'<th style="{sum_th_style};background:#065F46;color:#A7F3D0">Phone</th>'
    )

    # ── Body: one <tr> per time slot ──────────────────────────────────────────
    rows_html = ""
    for slot in TIME_SLOTS:
        label   = slot_label_map.get(slot, _fmt_slot(slot)) if slot_label_map else _fmt_slot(slot)
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
        chat_count   = 0
        phone_count  = 0
        agent_tds    = ""
        for ag in agents_info:
            act = schedule_data.get(ag["name"], {}).get(slot, ".")
            c_bg, c_fg = _colors.get(act, ("#F8F8F6", "#AAAAAA"))
            if act not in _INACTIVE_LOCAL:
                active_count += 1
            if act in _LIVE_CHAT_LOCAL:
                chat_count += 1
            elif act in _LIVE_PHONES_LOCAL:
                phone_count += 1
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
        c_op  = round(0.12 + 0.55 * min(chat_count   / n, 1.0), 3) if chat_count   else 0.06
        p_op  = round(0.12 + 0.55 * min(phone_count  / n, 1.0), 3) if phone_count  else 0.06
        a_bg  = f"rgba(29,78,216,{a_op})"
        c_bg  = f"rgba(3,105,161,{c_op})"
        p_bg  = f"rgba(6,95,70,{p_op})"
        sum_td_base = (
            f'font-size:9px;font-weight:700;font-family:{FONT};'
            f'text-align:center;vertical-align:middle;height:{ROW_H}px;'
            f'border:1px solid rgba(0,0,0,0.18);{border_top}'
            f'box-sizing:border-box'
        )
        summary_tds = (
            f'<td style="background:{a_bg};color:#1E3A8A;{sum_td_base}">'
            f'{active_count if active_count else ""}</td>'
            f'<td style="background:{c_bg};color:#0C4A6E;{sum_td_base}">'
            f'{chat_count if chat_count else ""}</td>'
            f'<td style="background:{p_bg};color:#064E3B;{sum_td_base}">'
            f'{phone_count if phone_count else ""}</td>'
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
        overflow-x:auto;
        overflow-y:visible;
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
_LIVE_CHAT   = {"Chat"}
_LIVE_PHONES = {"Phones"}
_ON_QUEUE    = _LIVE_CHAT | _LIVE_PHONES   # kept for backward compat
_INACTIVE    = {".", "Break", "Admin", "PTO", "VTO", "Sick",
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

    # ── Live Chat total ────────────────────────────────────────────────────────
    chat_cells = ""
    max_chat = max(
        (sum(count(s, a) for a in _LIVE_CHAT) for s in TIME_SLOTS), default=0
    ) or 1
    for slot in TIME_SLOTS:
        cv = sum(count(slot, a) for a in _LIVE_CHAT)
        if cv == 0:
            cs = "background:#EFF6FF;color:#CBD5E1"; txt = ""
        else:
            intensity = 0.3 + 0.7 * min(cv / max(max_chat, 1), 1.0)
            cs = f"background:{_blend('#3B82F6', intensity)};color:#1E3A8A"
            txt = str(cv)
        chat_cells += (
            f'<div title="Live Chat @ {slot}: {cv}" '
            f'style="display:inline-block;width:{SLOT_W}px;height:24px;{cs};'
            f'font-size:9px;font-weight:700;line-height:24px;text-align:center;'
            f'box-sizing:border-box;border-right:1px solid rgba(0,0,0,0.22)">{txt}</div>'
        )
    rows_html += f"""
    <div style="display:flex;align-items:stretch;height:24px;border-bottom:1px solid #BFDBFE">
        <div style="width:{AGENT_COL_W}px;flex-shrink:0;display:flex;align-items:center;
                    padding:0 10px;border-right:1px solid #BFDBFE;background:#EFF6FF">
            <span style="font-size:10px;font-weight:700;color:#1D4ED8">💬 Live Chat</span>
        </div>
        <div>{chat_cells}</div>
    </div>"""

    # ── Live Phones total ──────────────────────────────────────────────────────
    phone_cells = ""
    max_phone = max(
        (sum(count(s, a) for a in _LIVE_PHONES) for s in TIME_SLOTS), default=0
    ) or 1
    for slot in TIME_SLOTS:
        pv = sum(count(slot, a) for a in _LIVE_PHONES)
        if pv == 0:
            cs = "background:#F0FDF9;color:#CBD5E1"; txt = ""
        else:
            intensity = 0.3 + 0.7 * min(pv / max(max_phone, 1), 1.0)
            cs = f"background:{_blend('#10B981', intensity)};color:#065F46"
            txt = str(pv)
        phone_cells += (
            f'<div title="Live Phones @ {slot}: {pv}" '
            f'style="display:inline-block;width:{SLOT_W}px;height:24px;{cs};'
            f'font-size:9px;font-weight:700;line-height:24px;text-align:center;'
            f'box-sizing:border-box;border-right:1px solid rgba(0,0,0,0.22)">{txt}</div>'
        )
    rows_html += f"""
    <div style="display:flex;align-items:stretch;height:24px;border-bottom:2px solid #A7F3D0">
        <div style="width:{AGENT_COL_W}px;flex-shrink:0;display:flex;align-items:center;
                    padding:0 10px;border-right:1px solid #A7F3D0;background:#F0FDF9">
            <span style="font-size:10px;font-weight:700;color:#065F46">📞 Live Phones</span>
        </div>
        <div>{phone_cells}</div>
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

# ─── COVERAGE RULES (DB) ─────────────────────────────────────────────────────

def get_coverage_rules():
    """
    Returns (agent_rules, global_rules):
      agent_rules  : dict[agent_name -> {"allowed_channels", "lunch_slot", "lunch_duration"}]
      global_rules : dict[key -> value_str]   e.g. {"no_back_to_back": "1"}
    """
    conn = get_conn()
    agent_rows  = conn.execute("SELECT * FROM agent_coverage_rules").fetchall()
    global_rows = conn.execute("SELECT * FROM coverage_global_rules").fetchall()
    conn.close()
    agent_rules  = {r["agent_name"]: dict(r) for r in agent_rows}
    global_rules = {r["key"]: r["value"] for r in global_rows}
    return agent_rules, global_rules


def save_coverage_rules(agent_df, global_rules_dict):
    """
    Persist coverage rules from the data-editor DataFrame + global dict.
    agent_df columns: Agent, Channels  (lunch fields now live on the agent profile)
    Existing lunch_slot / lunch_duration values are preserved.
    """
    conn = get_conn()
    c = conn.cursor()
    # Preserve existing lunch values before wiping
    existing_lunch = {
        row["agent_name"]: (row["lunch_slot"], row["lunch_duration"], row["lunch_overrides"])
        for row in c.execute("SELECT agent_name, lunch_slot, lunch_duration, lunch_overrides FROM agent_coverage_rules").fetchall()
    }
    c.execute("DELETE FROM agent_coverage_rules")
    for _, row in agent_df.iterrows():
        ls, ld, lo = existing_lunch.get(row["Agent"], (None, 1, None))
        ch = row["Channels"]
        # Guard against NaN (cleared selectbox) — fall back to "both"
        if not isinstance(ch, str) or ch not in ("both", "chat", "phones", "none"):
            ch = "both"
        c.execute(
            """INSERT INTO agent_coverage_rules
               (agent_name, allowed_channels, lunch_slot, lunch_duration, lunch_overrides)
               VALUES (?,?,?,?,?)""",
            (row["Agent"], ch, ls, int(ld) if ld is not None else 1, lo),
        )
    for key, val in global_rules_dict.items():
        # Booleans → "1"/"0"; strings (slot names, etc.) → stored as-is
        if isinstance(val, bool):
            stored = "1" if val else "0"
        elif val is None:
            stored = ""
        else:
            stored = str(val)
        c.execute(
            "INSERT OR REPLACE INTO coverage_global_rules (key, value) VALUES (?,?)",
            (key, stored),
        )
    conn.commit()
    conn.close()


def save_agent_lunch(agent_name, lunch_slot, lunch_duration):
    """Upsert just the lunch fields for one agent in agent_coverage_rules."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT * FROM agent_coverage_rules WHERE agent_name=?", (agent_name,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE agent_coverage_rules SET lunch_slot=?, lunch_duration=? WHERE agent_name=?",
            (lunch_slot or None, int(lunch_duration), agent_name),
        )
    else:
        conn.execute(
            "INSERT INTO agent_coverage_rules (agent_name, allowed_channels, lunch_slot, lunch_duration) VALUES (?,?,?,?)",
            (agent_name, "both", lunch_slot or None, int(lunch_duration)),
        )
    conn.commit()
    conn.close()


def save_agent_lunch_overrides(agent_name, overrides: dict):
    """Persist per-day lunch overrides for one agent.
    overrides = {day_abbr: {"slot": "12:00 PM", "duration": 2} or None}
    Missing keys mean 'use default'. None value means 'no lunch that day'.
    """
    import json as _json
    overrides_json = _json.dumps(overrides) if overrides else None
    conn = get_conn()
    existing = conn.execute(
        "SELECT agent_name FROM agent_coverage_rules WHERE agent_name=?", (agent_name,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE agent_coverage_rules SET lunch_overrides=? WHERE agent_name=?",
            (overrides_json, agent_name),
        )
    else:
        conn.execute(
            "INSERT INTO agent_coverage_rules (agent_name, allowed_channels, lunch_overrides) VALUES (?,?,?)",
            (agent_name, "both", overrides_json),
        )
    conn.commit()
    conn.close()


def _resolve_lunch(rules: dict, day_abbr: str):
    """Return (lunch_slot_or_None, lunch_duration) for a given day,
    applying any per-day override from rules['lunch_overrides'].
    day_abbr is the 3-letter abbreviation e.g. 'Mon', 'Tue', 'Sat'.
    """
    import json as _json
    raw = rules.get("lunch_overrides")
    if raw:
        try:
            overrides = _json.loads(raw)
            if day_abbr in overrides:
                day_val = overrides[day_abbr]
                if day_val is None:
                    return None, 1          # explicit "no lunch" this day
                slot = day_val.get("slot")
                dur  = int(day_val.get("duration", 1))
                return (slot or None), dur
        except Exception:
            pass
    slot = rules.get("lunch_slot") or None
    dur  = int(rules.get("lunch_duration", 1))
    return slot, dur


# ─── GLADLY IMPORT HELPERS ────────────────────────────────────────────────────

def parse_gladly_csv(file_bytes):
    """
    Parse a Gladly contact-level export CSV for volume signals.

    Key columns (per Gladly export format):
      Column D (index 3) = inbound / accepted timestamp
      Column I (index 8) = channel (PHONE_CALL, CHAT, SMS, EMAIL)

    Header names are detected first; if unrecognised the function falls back
    to column positions D and I.  EMAIL rows are ignored.

    Returns:
      {"_volume": {day_of_week: {slot_str: {"Chat": N, "Phones": N}}}}
    The single "_volume" key lets build_gladly_template aggregate via its
    existing `for ag_data in gladly_data.values()` loop unchanged.
    """
    import io, csv as _csv
    from collections import defaultdict

    content = file_bytes.decode("utf-8-sig", errors="replace")
    reader  = _csv.reader(io.StringIO(content))
    rows    = list(reader)
    if not rows:
        return {}

    header = [h.strip() for h in rows[0]]

    def _find(*names):
        """Return index of first matching header (case-insensitive), or None."""
        for name in names:
            for i, h in enumerate(header):
                if h.lower() == name.lower():
                    return i
        return None

    # Detect column positions; fall back to D=3 and I=8 as the user confirmed
    time_col = _find(
        "Inbound At", "Inbound Time", "Accepted At", "Created At",
        "Queued At", "Contact Created At", "Start Time", "Contacted At",
    )
    if time_col is None:
        time_col = 3   # column D

    chan_col = _find(
        "Channel", "Channel Type", "Contact Channel", "Channel Name",
    )
    if chan_col is None:
        chan_col = 8   # column I

    _TIME_FMTS = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%d %H:%M",
    )

    volume = defaultdict(lambda: defaultdict(lambda: {"Chat": 0, "Phones": 0}))
    need   = max(time_col, chan_col)

    for row in rows[1:]:
        if not row or len(row) <= need:
            continue

        channel = row[chan_col].strip().upper()
        if channel not in ("CHAT", "SMS", "PHONE_CALL"):
            continue               # EMAIL and anything else skipped

        ts = row[time_col].strip()
        if not ts:
            continue

        # Normalise and try multiple timestamp formats
        clean = ts.rstrip("Z").replace("T", " ").split(".")[0].strip()
        dt = None
        for fmt in _TIME_FMTS:
            try:
                dt = datetime.datetime.strptime(clean, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            continue

        slot_min = 0 if dt.minute < 30 else 30
        slot_str = datetime.time(dt.hour, slot_min).strftime("%I:%M %p").lstrip("0")
        if slot_str not in TIME_SLOTS:
            continue

        day     = dt.strftime("%A")
        ch_type = "Phones" if channel == "PHONE_CALL" else "Chat"
        volume[day][slot_str][ch_type] += 1

    if not volume:
        return {}

    return {"_volume": {day: dict(slots) for day, slots in volume.items()}}


def build_gladly_template(gladly_data, db_agent_names,
                          agent_rules=None, no_back_to_back=True,
                          default_activities=None,
                          agent_work_hours=None,
                          existing_schedule=None,
                          channel_windows=None,
                          min_per_channel=2,
                          max_consecutive_chat_slots=4):
    """
    Build per-day-of-week per-slot activity suggestions from Gladly history.

    Assignment is slot-centric: for every time slot the total team Chat and
    Phones volume is used to distribute ALL eligible agents proportionally.

      agent_rules                – dict[name -> {allowed_channels, lunch_slot, lunch_duration}]
      no_back_to_back            – insert a gap when an agent transitions Chat -> Phones directly
      default_activities         – dict[agent_name -> activity] used for gap/break fills
      agent_work_hours           – dict[name -> {day_index -> {start_slot, end_slot, is_active}}]
      existing_schedule          – dict[name -> {day_index -> {slot -> activity}}]
                                   Hard-protected slots (time off, Break, offline) are skipped.
      channel_windows            – per-channel per-day-type open/close windows:
                                   {
                                     "Chat":   {"weekday": ("10:00 AM", "4:30 PM"),
                                                "weekend": ("12:00 PM", "3:30 PM")},
                                     "Phones": {"weekday": ("10:00 AM", "4:30 PM"),
                                                "weekend": None},  # None = closed
                                   }
                                   Defaults to 10 AM-4:30 PM for both channels all days.
      min_per_channel            – minimum agents per active channel per slot (default 2)
      max_consecutive_chat_slots – max consecutive Chat slots before a break is inserted
                                   (default 4 = 2 hrs). Phones have NO consecutive limit.

    Returns: dict[agent_name, dict[day_of_week, dict[slot, activity_str]]]
    """
    if agent_rules        is None: agent_rules        = {}
    if default_activities is None: default_activities = {}
    if agent_work_hours   is None: agent_work_hours   = {}
    if existing_schedule  is None: existing_schedule  = {}

    _DEFAULT_WIN = ("10:00 AM", "4:30 PM")
    if channel_windows is None:
        channel_windows = {
            "Chat":   {"weekday": _DEFAULT_WIN, "weekend": _DEFAULT_WIN},
            "Phones": {"weekday": _DEFAULT_WIN, "weekend": _DEFAULT_WIN},
        }

    _LIVE     = {"Chat", "Phones"}
    _WEEKENDS = {"Saturday", "Sunday"}
    _si       = {s: i for i, s in enumerate(TIME_SLOTS)}

    def _win_range(channel, day_type):
        """Return (lo_idx, hi_idx) for this channel+day_type, or None if closed."""
        w = channel_windows.get(channel, {}).get(day_type)
        if not w:
            return None
        return (_si.get(w[0], 0), _si.get(w[1], len(TIME_SLOTS) - 1))

    # Step 1: aggregate total volume per day-of-week per slot
    from collections import defaultdict as _dd
    agg = _dd(lambda: _dd(lambda: {"Chat": 0, "Phones": 0}))
    for ag_data in gladly_data.values():
        for day, slots in ag_data.items():
            for slot, counts in slots.items():
                agg[day][slot]["Chat"]   += counts.get("Chat",   0)
                agg[day][slot]["Phones"] += counts.get("Phones", 0)

    if not agg:
        return {}

    _HARD_BLOCK = {
        ".", "",
        "Break", "PTO", "VTO", "Sick", "Holiday",
        "FMLA", "Bereavement", "Training", "Meeting",
    }

    # Step 2: per-agent eligibility helper (agent-level only, no channel window check)
    def _eligible(name, di, slot):
        """True if the agent is working and unblocked in this slot."""
        rules  = agent_rules.get(name, {})
        wh     = agent_work_hours.get(name, {})
        day_wh = wh.get(di, {})
        si     = _si.get(slot, -1)

        if wh:
            if not day_wh or not day_wh.get("is_active", False):
                return False
            sh_lo = _si.get(day_wh.get("start_slot", "10:00 AM"), 0)
            sh_hi = _si.get(day_wh.get("end_slot",   "10:00 PM"), len(TIME_SLOTS) - 1)
            sp_start = day_wh.get("split_start_slot")
            sp_end   = day_wh.get("split_end_slot")
            # Shift end is exclusive (consistent with base schedule slice)
            in_primary = sh_lo <= si < sh_hi
            if sp_start and sp_end:
                sp_lo = _si.get(sp_start, sh_hi + 1)
                sp_hi = _si.get(sp_end,   sh_hi + 1)
                in_split = sp_lo <= si < sp_hi
            else:
                in_split = False
            if not in_primary and not in_split:
                return False
            # Reserve the last 30-min slot of each work segment for Support
            if in_primary and sh_hi - sh_lo > 1 and si == sh_hi - 1:
                return False
            if in_split and sp_hi - sp_lo > 1 and si == sp_hi - 1:
                return False

        cur = existing_schedule.get(name, {}).get(di, {}).get(slot, ".")
        if cur in _HARD_BLOCK:
            return False

        _day_abbr_cov = DAYS[di][:3] if di < len(DAYS) else ""
        lunch_sl, lunch_d = _resolve_lunch(rules, _day_abbr_cov)
        if lunch_sl and lunch_sl in TIME_SLOTS:
            li = TIME_SLOTS.index(lunch_sl)
            if li <= si < li + lunch_d:
                return False

        return True

    # Step 3: slot-centric assignment
    # Each chat agent can handle CHAT_CONCURRENCY simultaneous chats,
    # so we cap chat assignments at ceil(chat_volume / CHAT_CONCURRENCY).
    CHAT_CONCURRENCY = 3

    template = {name: {} for name in db_agent_names}

    for day in DAYS:
        if day not in agg:
            continue
        di       = DAYS.index(day)
        day_type = "weekend" if day in _WEEKENDS else "weekday"

        chat_range  = _win_range("Chat",   day_type)
        phone_range = _win_range("Phones", day_type)

        for si, slot in enumerate(TIME_SLOTS):
            # End is exclusive: the close-time slot itself is NOT a live slot
            slot_in_chat  = chat_range  is not None and chat_range[0]  <= si < chat_range[1]
            slot_in_phone = phone_range is not None and phone_range[0] <= si < phone_range[1]

            if not slot_in_chat and not slot_in_phone:
                continue

            vol     = agg[day].get(slot) or {"Chat": 0, "Phones": 0}
            chat_n  = vol["Chat"]   if slot_in_chat  else 0
            phone_n = vol["Phones"] if slot_in_phone else 0
            has_vol = chat_n > 0 or phone_n > 0

            # How many agents does Chat actually need?
            # Agents handle CHAT_CONCURRENCY chats simultaneously, so cap accordingly.
            # Always ensure at least min_per_channel when the window is open.
            if slot_in_chat:
                chat_agents_cap = (max(min_per_channel, math.ceil(chat_n / CHAT_CONCURRENCY))
                                   if chat_n > 0 else min_per_channel)
            else:
                chat_agents_cap = 0

            chat_only, phone_only, both_agts = [], [], []
            for name in db_agent_names:
                if not _eligible(name, di, slot):
                    continue
                allowed = agent_rules.get(name, {}).get("allowed_channels", "both")
                if allowed == "none":
                    pass
                elif allowed == "chat":
                    if slot_in_chat:
                        chat_only.append(name)
                elif allowed == "phones":
                    if slot_in_phone:
                        phone_only.append(name)
                elif allowed == "both":
                    if slot_in_chat and slot_in_phone:
                        both_agts.append(name)
                    elif slot_in_chat:
                        chat_only.append(name)
                    elif slot_in_phone:
                        phone_only.append(name)

            if not chat_only and not phone_only and not both_agts:
                continue

            # Fixed-channel agents always get their channel
            for n in chat_only:
                template[n].setdefault(day, {})[slot] = "Chat"
            for n in phone_only:
                template[n].setdefault(day, {})[slot] = "Phones"

            chat_cnt  = len(chat_only)
            phone_cnt = len(phone_only)
            remaining = list(both_agts)

            if chat_n > 0 and phone_n > 0:
                total_vol  = chat_n + phone_n
                # Effective ratio using concurrency-adjusted chat demand
                eff_chat   = math.ceil(chat_n / CHAT_CONCURRENCY)
                eff_phone  = phone_n
                chat_ratio = eff_chat / (eff_chat + eff_phone)

                # Ensure min_per_channel for each, respecting chat cap
                if chat_ratio >= 0.5:
                    while chat_cnt  < min(min_per_channel, chat_agents_cap) and remaining:
                        n = remaining.pop(0); template[n].setdefault(day, {})[slot] = "Chat";   chat_cnt  += 1
                    while phone_cnt < min_per_channel and remaining:
                        n = remaining.pop(0); template[n].setdefault(day, {})[slot] = "Phones"; phone_cnt += 1
                else:
                    while phone_cnt < min_per_channel and remaining:
                        n = remaining.pop(0); template[n].setdefault(day, {})[slot] = "Phones"; phone_cnt += 1
                    while chat_cnt  < min(min_per_channel, chat_agents_cap) and remaining:
                        n = remaining.pop(0); template[n].setdefault(day, {})[slot] = "Chat";   chat_cnt  += 1

                # Distribute remaining "both" agents up to each channel's need
                for n in remaining:
                    chat_at_cap   = (chat_cnt  >= chat_agents_cap)
                    total_asgn    = chat_cnt + phone_cnt
                    if chat_at_cap:
                        # Chat is satisfied — remaining go to Phones
                        template[n].setdefault(day, {})[slot] = "Phones"; phone_cnt += 1
                    elif total_asgn == 0 or (chat_cnt / total_asgn) < chat_ratio:
                        template[n].setdefault(day, {})[slot] = "Chat";   chat_cnt  += 1
                    else:
                        template[n].setdefault(day, {})[slot] = "Phones"; phone_cnt += 1

            elif chat_n > 0 or (not has_vol and slot_in_chat):
                # Chat-only window or no history — assign up to chat cap
                for n in remaining:
                    if chat_cnt < chat_agents_cap:
                        template[n].setdefault(day, {})[slot] = "Chat"; chat_cnt += 1
                    # Beyond cap → leave unassigned (becomes Support in post-processing)
            elif phone_n > 0 or (not has_vol and slot_in_phone):
                # Phone-only window or no history
                for n in remaining:
                    template[n].setdefault(day, {})[slot] = "Phones"

    # Step 4: per-agent post-processing
    for name in db_agent_names:
        _gap = default_activities.get(name) or "Support"

        for day in list(template[name].keys()):
            day_tmpl = template[name][day]

            # Max consecutive CHAT limit only — Phones have no limit
            consec_chat  = 0
            last_chat_si = -2
            for i, slot in enumerate(TIME_SLOTS):
                if slot not in day_tmpl:
                    consec_chat = 0; last_chat_si = -2; continue
                act = day_tmpl[slot]
                if act == "Chat":
                    consec_chat = (consec_chat + 1) if i == last_chat_si + 1 else 1
                    if consec_chat > max_consecutive_chat_slots:
                        day_tmpl[slot] = _gap
                        consec_chat = 0; last_chat_si = -2
                    else:
                        last_chat_si = i
                else:
                    consec_chat = 0; last_chat_si = -2

            # No back-to-back channel switch
            if no_back_to_back:
                to_gap = set(); prev_idx = prev_act = None
                for i, slot in enumerate(TIME_SLOTS):
                    if slot not in day_tmpl:
                        prev_idx = prev_act = None; continue
                    act = day_tmpl[slot]
                    if (prev_act in _LIVE and act in _LIVE
                            and prev_act != act and prev_idx == i - 1):
                        to_gap.add(slot); prev_idx = prev_act = None; continue
                    prev_idx = i; prev_act = act
                for slot in to_gap:
                    day_tmpl[slot] = _gap

    # Step 5: strip empty days / agents
    return {
        name: {day: slots for day, slots in days.items() if slots}
        for name, days in template.items()
        if any(slots for slots in days.values())
    }


def apply_gladly_template(template, week_start, sel_date, default_activities=None):
    """
    Write the Gladly-derived activity suggestions to the schedule DB.

    For each agent+day that appears in the template:
      1. Sweep ALL time slots and reset any existing Chat/Phones back to the
         agent's default activity — this clears stale live assignments from
         previous applies that may now fall outside the channel window.
      2. Write the new template slots on top.

    Hard-protected slots (Break, PTO, etc.) are never touched in either step.
    Returns count of live-channel slots written.
    """
    if default_activities is None:
        default_activities = {}

    _live      = {"Chat", "Phones"}
    _protected = {
        "Break", "PTO", "VTO", "Sick", "Holiday",
        "FMLA", "Bereavement", "Training", "Meeting",
    }
    cells_written = 0

    for agent_name, days in template.items():
        _gap = default_activities.get(agent_name) or "Support"

        for day_name, slots in days.items():
            if day_name not in DAYS:
                continue
            di     = DAYS.index(day_name)
            df_tmp = get_schedule_df(week_start, di, [agent_name])
            changed = False

            # Step 1: clear all existing Chat/Phones (replace with default activity).
            # This removes stale live assignments outside the new channel window.
            for slot in TIME_SLOTS:
                if slot not in df_tmp.index:
                    continue
                cur = df_tmp.at[slot, agent_name]
                if cur in _live:
                    df_tmp.at[slot, agent_name] = _gap
                    changed = True

            # Step 2: write the new template slots.
            for slot, activity in slots.items():
                if slot not in df_tmp.index:
                    continue
                cur = df_tmp.at[slot, agent_name]
                if cur in _protected:
                    continue   # never overwrite time-off / breaks
                df_tmp.at[slot, agent_name] = activity
                cells_written += 1
                changed = True

            if changed:
                save_schedule_df(week_start, di, df_tmp, notify=False)

    return cells_written


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

    /* ── Sidebar — fixed width when open, collapsible ── */
    section[data-testid="stSidebar"] {{
        width: 18rem !important;
        min-width: 18rem !important;
    }}
    /* Keep collapse button hidden inside sidebar (we rely on native collapse arrow at edge) */
    [data-testid="stSidebarCollapseButton"] {{ display: none !important }}
    /* Show the expand control so mobile users can reopen the sidebar */
    [data-testid="collapsedControl"] {{ display: flex !important }}

    .stApp{{background:var(--fb-cream)!important}}
    div[data-testid="stMainBlockContainer"]{{padding:1.5rem 2rem}}

    /* ── Sidebar ── */
    [data-testid="stSidebar"]>div:first-child{{background:var(--fb-black)!important;padding-top:0}}
    [data-testid="stSidebar"] *{{color:#C8C5C0!important;font-family:var(--font-ui)!important}}
    /* Hide the radio group title label ("nav") — must be more specific than the option rule below */
    [data-testid="stSidebar"] [data-testid="stRadio"] > div > [data-testid="stWidgetLabel"]{{display:none!important}}
    [data-testid="stSidebar"] .stRadio div[role="radiogroup"]{{gap:0!important}}
    /* Style individual nav option labels only — scoped to radiogroup to avoid hitting the title label */
    [data-testid="stSidebar"] .stRadio div[role="radiogroup"] > label{{
        display:flex!important;align-items:center;
        padding:10px 16px!important;border-radius:4px!important;
        margin:1px 6px!important;cursor:pointer!important;
        transition:background 0.15s;font-size:13px!important;
        letter-spacing:0.03em!important;font-family:var(--font-ui)!important}}
    [data-testid="stSidebar"] .stRadio div[role="radiogroup"] > label:hover{{background:rgba(255,255,255,0.07)!important}}
    [data-testid="stSidebar"] .stRadio div[role="radiogroup"] > label[data-checked="true"]{{
        background:rgba(137,172,158,0.25)!important;
        border-left:2px solid var(--fb-sage)!important}}
    [data-testid="stSidebar"] .stRadio div[role="radiogroup"] > label[data-checked="true"] *{{
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

    /* ── Sidebar secondary buttons (hide control, mark-read, etc.) ── */
    [data-testid="stSidebar"] button[kind="secondary"] {{
        font-size:10px!important;font-weight:400!important;
        min-height:20px!important;height:20px!important;
        padding:0 8px!important;letter-spacing:0.05em!important;
        background:transparent!important;border:none!important;box-shadow:none!important;
        color:#475569!important;text-transform:none!important}}
    [data-testid="stSidebar"] button[kind="secondary"]:hover {{
        background:transparent!important;color:#94A3B8!important}}
    /* ── Sign Out (primary in sidebar) — keep as outlined white button ── */
    [data-testid="stSidebar"] button[kind="primary"] {{
        background:rgba(255,255,255,0.06)!important;
        color:#C8C5C0!important;
        border:1px solid rgba(255,255,255,0.18)!important}}

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

    /* ── Compact schedule editor rows ── */
    [data-testid="stDataEditor"] .ag-root-wrapper {{
        --ag-row-height: 22px;
        --ag-header-height: 28px;
        --ag-line-height: 22px;
        --ag-cell-vertical-padding: 0px;
        --ag-font-size: 10px;
    }}
    [data-testid="stDataEditor"] .ag-row {{
        height: 22px !important;
        min-height: 22px !important;
        max-height: 22px !important;
        line-height: 22px !important;
        overflow: hidden !important;
    }}
    [data-testid="stDataEditor"] .ag-cell,
    [data-testid="stDataEditor"] .ag-cell-wrapper {{
        height: 22px !important;
        line-height: 22px !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
        font-size: 10px !important;
        overflow: hidden !important;
    }}
    [data-testid="stDataEditor"] .ag-header-cell {{
        font-size: 10px !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
    }}
    /* Dropdown popup — full readable size regardless of cell height */
    .ag-popup .ag-list-item {{
        font-size: 12px !important;
        padding: 5px 12px !important;
        min-width: 160px !important;
        white-space: nowrap !important;
        line-height: 1.4 !important;
    }}

    /* ── Mobile layout ────────────────────────────────────────── */
    @media screen and (max-width: 768px) {{
        /* Style the sidebar hamburger as a clearly-visible floating button */
        [data-testid="collapsedControl"] {{
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            position: fixed !important;
            top: 6px !important;
            left: 6px !important;
            z-index: 99999 !important;
            min-width: 48px !important;
            min-height: 48px !important;
            background: var(--fb-black) !important;
            border-radius: 10px !important;
            box-shadow: 0 2px 10px rgba(0,0,0,0.3) !important;
        }}
        [data-testid="collapsedControl"] button {{
            min-width: 48px !important;
            min-height: 48px !important;
        }}
        [data-testid="collapsedControl"] svg {{
            width: 22px !important;
            height: 22px !important;
            color: #C8C5C0 !important;
            fill: #C8C5C0 !important;
        }}
        /* Push main content down so the fixed hamburger doesn't overlap page content */
        div[data-testid="stMainBlockContainer"] {{
            padding: 0.75rem !important;
            padding-top: 3.5rem !important;
        }}
        /* Ensure profile button column has enough width on narrow screens */
        div[data-testid="stHorizontalBlock"] > div:last-child {{
            min-width: 56px !important;
            flex: 0 0 56px !important;
        }}
        div[data-testid="stHorizontalBlock"] > div:first-child {{
            flex: 1 1 auto !important;
            min-width: 0 !important;
        }}
    }}
    </style>""", unsafe_allow_html=True)

def metric(label, val, sub=""):
    sub_html = f"<div class='metric-sub'>{sub}</div>" if sub else ""
    st.markdown(
        f'<div class="scard">'
        f'<div class="metric-lbl">{label}</div>'
        f'<div class="metric-num">{val}</div>'
        f'{sub_html}'
        f'</div>',
        unsafe_allow_html=True,
    )

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
                CX Schedule
            </div>
            <div style="font-family:'DM Sans',Helvetica,sans-serif;font-size:10px;
                        color:#89AC9E;margin-top:4px;letter-spacing:0.15em;text-transform:uppercase">
                Framebridge
            </div>
        </div>""", unsafe_allow_html=True)

        # Tiny hide-menu link, right-aligned under the header
        if st.button("‹ hide", key="hide_sidebar_btn", use_container_width=True):
            st.session_state["_cx_sidebar_hidden"] = True
            st.rerun()

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

        # Notifications bell for agents
        if user:
            unread = get_notifications(user["display_name"], unread_only=True)
            if unread:
                notif_html = "".join(
                    f'<div style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.06);'
                    f'font-size:11px;color:#E2E8F0;font-family:\'DM Sans\',sans-serif">{n["message"]}'
                    f'<div style="font-size:9px;color:#64748B;margin-top:2px">{n["created_at"]}</div></div>'
                    for n in unread
                )
                st.markdown(
                    f'<div style="margin:0 8px 10px;background:rgba(238,225,113,0.08);'
                    f'border:1px solid rgba(238,225,113,0.25);border-radius:6px;overflow:hidden">'
                    f'<div style="padding:6px 10px;font-size:10px;font-weight:700;color:#EEE171;'
                    f'text-transform:uppercase;letter-spacing:0.06em;font-family:\'DM Sans\',sans-serif">'
                    f'🔔 {len(unread)} new notification{"s" if len(unread) > 1 else ""}</div>'
                    f'{notif_html}</div>',
                    unsafe_allow_html=True
                )
                if st.button("Mark all read", key="mark_notif_read", use_container_width=False):
                    mark_notifications_read(user["display_name"])
                    st.rerun()

        pending = len(get_time_off_requests("Pending"))

        # Build nav based on role
        nav_labels = ["⬛  Schedule", "📥  Time Off"]
        if can_edit():
            nav_labels += ["🌐  Agent View"]
        if is_admin():
            nav_labels += ["👤  Roster", "🏷️  Teams", "📋  Templates", "📊  Reports", "👥  Users", "⚙️  Settings"]

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

        # Logout — pushed to the very bottom of the sidebar
        st.markdown('<div style="height:40px"></div>', unsafe_allow_html=True)
        st.markdown(
            '<style>'
            '[data-testid="stSidebar"] > div:first-child {'
            '  display:flex;flex-direction:column;'
            '}'
            '[data-testid="stSidebar"] [data-testid="stButton"]:last-child {'
            '  margin-top:auto;'
            '  padding-top:12px;'
            '  border-top:1px solid rgba(255,255,255,0.07);'
            '}'
            '</style>',
            unsafe_allow_html=True
        )
        if st.button("Sign out", use_container_width=True, key="signout_btn", type="primary"):
            st.session_state.pop("cx_user", None)
            st.rerun()

    page_key = page.split("  ")[1].split("  ")[0].strip()
    return page_key

# ─── PAGE: SCHEDULE ───────────────────────────────────────────────────────────

def _agent_hour_breakdown(agent_name, week_start):
    """Show a personal schedule + hour breakdown card for an agent."""
    _INACTIVE_SET = {".", "Break", "Admin", "PTO", "VTO", "Sick",
                     "Holiday", "Bereavement", "FMLA", "Training", "Meeting"}
    act_colors  = get_act_colors()
    _OFF_TYPES  = {"PTO", "VTO", "Sick", "Holiday", "Bereavement", "FMLA", "Vacation", "Personal"}
    sel         = datetime.date.fromisoformat(week_start)
    week_end    = sel + datetime.timedelta(days=6)
    FONT        = "'DM Sans','Apercu Pro',Helvetica,Arial,sans-serif"

    st.markdown(
        f'<div style="font-size:18px;font-weight:700;color:#1D2019;margin-bottom:12px;'
        f'font-family:{FONT}">Your schedule — week of {sel.strftime("%B %-d, %Y")}</div>',
        unsafe_allow_html=True
    )

    # ── Build per-day data ─────────────────────────────────────────────────────
    day_data   = {}   # {day: {act: hours, "_total": hours, "_off": [req, ...]}}
    total_hrs  = 0
    act_totals = {}   # {act: total_hours_across_week}

    # Time-off requests for this agent this week
    all_req = get_time_off_requests()
    my_req  = [r for r in all_req
               if r["agent_name"] == agent_name
               and datetime.date.fromisoformat(r["start_date"]) <= week_end
               and datetime.date.fromisoformat(r["end_date"])   >= sel]

    for di, day in enumerate(DAYS):
        date = sel + datetime.timedelta(days=di)
        df   = get_schedule_df(week_start, di, [agent_name])
        act_hrs = {}
        for slot in TIME_SLOTS:
            act = df.at[slot, agent_name]
            if act not in _INACTIVE_SET and act != ".":
                act_hrs[act] = act_hrs.get(act, 0) + 0.5
        total = sum(act_hrs.values())
        total_hrs += total
        for a, h in act_hrs.items():
            act_totals[a] = act_totals.get(a, 0) + h

        # Time-off requests that touch this date
        day_off = [r for r in my_req
                   if datetime.date.fromisoformat(r["start_date"]) <= date
                   <= datetime.date.fromisoformat(r["end_date"])]

        day_data[day] = {"acts": act_hrs, "total": total, "off": day_off, "date": date}

    # ── Weekly summary bar (totals by activity) ────────────────────────────────
    if act_totals:
        pills = ""
        for act, hrs in sorted(act_totals.items(), key=lambda x: -x[1]):
            bg, fg = act_colors.get(act, ("#F1F5F9", "#64748B"))
            pills += (
                f'<span style="background:{bg};color:{fg};padding:4px 10px;border-radius:99px;'
                f'font-size:11px;font-weight:600;display:inline-flex;align-items:center;gap:5px;'
                f'margin:2px 4px 2px 0;border:1px solid rgba(0,0,0,0.06);font-family:{FONT}">'
                f'{act} <span style="opacity:0.75">{hrs:.1f}h</span></span>'
            )
        st.markdown(
            f'<div style="margin-bottom:14px">'
            f'<div style="font-size:10px;font-weight:700;color:#979797;text-transform:uppercase;'
            f'letter-spacing:0.08em;margin-bottom:6px;font-family:{FONT}">Weekly totals</div>'
            f'{pills}'
            f'<span style="background:#1D2019;color:#FFF9F4;padding:4px 10px;border-radius:99px;'
            f'font-size:11px;font-weight:700;display:inline-flex;align-items:center;gap:5px;'
            f'margin:2px 0 2px 4px;font-family:{FONT}">'
            f'Total <span style="color:#89AC9E">{total_hrs:.1f}h</span></span>'
            f'</div>',
            unsafe_allow_html=True
        )

    # ── Day cards ──────────────────────────────────────────────────────────────
    cols = st.columns(len(DAYS))
    for i, day in enumerate(DAYS):
        d = day_data[day]
        with cols[i]:
            date_label = d["date"].strftime("%-m/%-d")
            has_off    = bool(d["off"])
            has_work   = d["total"] > 0

            # Day header
            hdr_bg = "#FEF9C3" if has_off else ("#F0F5F3" if has_work else "#F8F8F6")
            hdr_txt = "#1D2019" if has_work or has_off else "#AAAAAA"
            body = (
                f'<div style="background:{hdr_bg};border:1px solid #D8D8D8;border-radius:8px;'
                f'overflow:hidden;font-family:{FONT}">'
                # header strip
                f'<div style="padding:7px 8px 5px;border-bottom:1px solid #E8E8E8">'
                f'<div style="font-size:9px;color:#979797;text-transform:uppercase;'
                f'letter-spacing:0.08em">{day[:3]} {date_label}</div>'
                f'<div style="font-size:18px;font-weight:700;color:{hdr_txt};line-height:1.1">'
                f'{d["total"]:.1f}<span style="font-size:10px;font-weight:400;color:#979797"> hrs</span></div>'
                f'</div>'
            )
            # Activity breakdown rows
            if d["acts"]:
                body += '<div style="padding:5px 8px">'
                for act, hrs in sorted(d["acts"].items(), key=lambda x: -x[1]):
                    bg, fg = act_colors.get(act, ("#F1F5F9", "#64748B"))
                    bar_w  = int(min(hrs / max(d["total"], 0.5) * 100, 100))
                    body += (
                        f'<div style="margin-bottom:4px">'
                        f'<div style="display:flex;justify-content:space-between;'
                        f'font-size:9px;color:#484848;margin-bottom:1px">'
                        f'<span>{act}</span><span style="font-weight:600">{hrs:.1f}h</span></div>'
                        f'<div style="height:4px;background:#E8E8E8;border-radius:2px">'
                        f'<div style="height:4px;width:{bar_w}%;background:{bg};border-radius:2px"></div>'
                        f'</div></div>'
                    )
                body += '</div>'
            # Time-off badge
            if has_off:
                for r in d["off"]:
                    status_colors = {"Approved": "#16A34A", "Pending": "#D97706", "Denied": "#DC2626"}
                    sc = status_colors.get(r["status"], "#979797")
                    body += (
                        f'<div style="padding:4px 8px 6px">'
                        f'<div style="background:{sc}18;border:1px solid {sc}44;border-radius:4px;'
                        f'padding:4px 6px;font-size:9px;color:{sc};font-weight:600">'
                        f'{r["type"]} — {r["status"]}</div></div>'
                    )
            body += '</div>'
            st.markdown(body, unsafe_allow_html=True)

    st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

    # ── Day tabs with personal timeline ───────────────────────────────────────
    day_tabs = st.tabs([
        f"{d[:3]}  {(sel + datetime.timedelta(days=i)).strftime('%-m/%-d')}"
        for i, d in enumerate(DAYS)
    ])
    _default_to_today_tab(week_start)
    agents_all = get_agents()
    ag_info = next(({"name": a["name"], "team_name": a["team_name"],
                     "color": {t["name"]: t["color"] for t in get_teams()}.get(a["team_name"], "#89AC9E")}
                    for a in agents_all if a["name"] == agent_name), None)
    if not ag_info:
        st.info("Your agent profile hasn't been set up yet. Ask an admin to add you to the Roster.")
        return

    for di, tab in enumerate(day_tabs):
        with tab:
            df   = get_schedule_df(week_start, di, [agent_name])
            sched = {agent_name: df[agent_name].to_dict()}
            n_rows = len(TIME_SLOTS) * 26 + 120
            st_components.html(
                build_timeline_html([ag_info], sched, act_colors),
                height=n_rows, scrolling=False
            )


def _fmt_time_ago(ts: datetime.datetime) -> str:
    diff = int((datetime.datetime.now() - ts).total_seconds())
    if diff < 60:
        return "just now"
    if diff < 3600:
        m = diff // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    h = diff // 3600
    return f"{h} hour{'s' if h != 1 else ''} ago"


@st.fragment(run_every="30s")
def _schedule_update_watcher(baseline: str):
    """Polls the DB every 30 s. Shows a banner if another user saved the schedule."""
    current = get_setting("schedule_last_modified", "")
    if not current or current == baseline:
        return  # No change — render nothing
    try:
        ts_str, saver = current.split("|", 1)
        time_ago = _fmt_time_ago(datetime.datetime.fromisoformat(ts_str))
        msg = f"📅 Schedule updated by **{saver}** {time_ago} — you may be viewing outdated data."
    except Exception:
        msg = "📅 The schedule has been updated — you may be viewing outdated data."
    st.warning(msg)
    if st.button("🔄 Refresh now", key=f"sched_refresh_{abs(hash(baseline)) % 99999}"):
        # st.rerun() inside a fragment only reruns the fragment in Streamlit 1.35,
        # so we trigger a full browser reload via JS instead.
        st_components.html(
            "<script>window.parent.location.reload();</script>", height=0
        )


def page_schedule():
    st.markdown('<div class="page-title">Schedule</div>', unsafe_allow_html=True)

    # ── Schedule change watcher (all roles) ───────────────────────────────────
    if "_sched_baseline_ver" not in st.session_state:
        st.session_state["_sched_baseline_ver"] = get_setting("schedule_last_modified", "")
    _schedule_update_watcher(st.session_state["_sched_baseline_ver"])

    # Play chime when today's schedule is saved
    if st.session_state.pop("_play_schedule_sound", False):
        st_components.html("""
        <script>
        (function() {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            [[659.25, 0.00], [783.99, 0.12], [1046.50, 0.24]].forEach(([freq, t]) => {
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.connect(gain); gain.connect(ctx.destination);
                osc.type = 'sine'; osc.frequency.value = freq;
                gain.gain.setValueAtTime(0.2, ctx.currentTime + t);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + t + 0.25);
                osc.start(ctx.currentTime + t);
                osc.stop(ctx.currentTime + t + 0.25);
            });
        })();
        </script>
        """, height=0)

    # ── Viewer (agent) sees only their own schedule ────────────────────────────
    if not can_edit():
        user = current_user()
        if not user:
            st.warning("Please log in.")
            return
        today = datetime.date.today()
        default_mon = today - datetime.timedelta(days=today.weekday())
        if "agent_sched_week" not in st.session_state:
            st.session_state["agent_sched_week"] = default_mon
        c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
        with c2:
            if st.button("⬅ Prev", key="ag_prev", use_container_width=True):
                st.session_state["agent_sched_week"] -= datetime.timedelta(weeks=1)
                st.session_state["agent_sched_week_input"] = st.session_state["agent_sched_week"]
                st.rerun()
        with c3:
            if st.button("Next ➡", key="ag_next", use_container_width=True):
                st.session_state["agent_sched_week"] += datetime.timedelta(weeks=1)
                st.session_state["agent_sched_week_input"] = st.session_state["agent_sched_week"]
                st.rerun()
        with c1:
            sel = st.date_input("Week", value=st.session_state["agent_sched_week"],
                                label_visibility="collapsed", key="agent_sched_week_input")
            if sel.weekday() != 0:
                sel = sel - datetime.timedelta(days=sel.weekday())
            st.session_state["agent_sched_week"] = sel
        with c4:
            viewer_tz_label = st.selectbox(
                "🌐 Timezone",
                list(_TZ_OPTIONS.keys()),
                index=0,
                key="viewer_timezone",
            )
        viewer_tz_offset    = _TZ_OPTIONS[viewer_tz_label]
        viewer_slot_lbl_map = _make_tz_slot_label_map(viewer_tz_offset)
        if viewer_tz_label != _BASE_TZ_LABEL:
            sign = "+" if viewer_tz_offset >= 0 else ""
            st.markdown(
                f'<div style="font-size:11px;color:#64748B;margin-bottom:6px">'
                f'Showing times in <b style="color:#0F172A">{viewer_tz_label}</b> '
                f'({sign}{viewer_tz_offset}h from {_BASE_TZ_LABEL})</div>',
                unsafe_allow_html=True,
            )

        week_start = str(sel)
        my_tab, team_tab = st.tabs(["👤  My Schedule", "👥  Team View"])

        with my_tab:
            # Prefer linked_user_id match; fall back to display_name match
            _my_agents   = get_agents()
            _linked_agent = next((a for a in _my_agents if a.get("linked_user_id") == user["id"]), None)
            _name_agent   = next((a for a in _my_agents if a["name"] == user["display_name"]), None)
            _my_agent     = _linked_agent or _name_agent
            if _my_agent:
                _agent_hour_breakdown(_my_agent["name"], week_start)
            else:
                st.info("Your roster profile hasn't been set up yet, or hasn't been linked to this account. Ask an admin to add you to the Roster and link your login.")

        with team_tab:
            agents_all  = get_agents()
            teams       = get_teams()
            team_colors = {t["name"]: t["color"] for t in teams}
            act_colors  = get_act_colors()
            sched_data  = {}
            for ag in agents_all:
                for di in range(len(DAYS)):
                    df = get_schedule_df(week_start, di, [ag["name"]])
                    sched_data.setdefault(ag["name"], {}).update(df[ag["name"]].to_dict())

            agents_info = [
                {"name": a["name"], "team_name": a["team_name"],
                 "color": team_colors.get(a["team_name"], "#64748B")}
                for a in agents_all
            ]
            teams_with_agents = [t for t in teams
                                 if any(a["team_name"] == t["name"] for a in agents_info)]
            _ordered_teams, _tl_order_key = resolve_team_order(user, teams_with_agents)
            n_rows = len(TIME_SLOTS) * 26 + 120

            day_tabs = st.tabs([
                f"{d[:3]}  {(sel + datetime.timedelta(days=i)).strftime('%-m/%-d')}"
                for i, d in enumerate(DAYS)
            ])
            _default_to_today_tab(week_start)
            for di, dtab in enumerate(day_tabs):
                with dtab:
                    day_sched = {}
                    for ag in agents_all:
                        df = get_schedule_df(week_start, di, [ag["name"]])
                        day_sched[ag["name"]] = df[ag["name"]].to_dict()

                    for _i, team in enumerate(_ordered_teams):
                        team_agents = [a for a in agents_info if a["team_name"] == team["name"]]
                        if not team_agents:
                            continue
                        _hcol, _ucol, _dcol = st.columns([30, 1, 1])
                        with _hcol:
                            st.markdown(
                                f'<div style="display:flex;align-items:center;gap:8px;margin:10px 0 4px">'
                                f'<div style="width:10px;height:10px;border-radius:50%;background:{team["color"]}"></div>'
                                f'<span style="font-size:13px;font-weight:600;color:#1E293B">{team["name"]} Team</span>'
                                f'<span style="font-size:11px;color:#94A3B8">— {len(team_agents)} agents</span>'
                                f'</div>', unsafe_allow_html=True
                            )
                        with _ucol:
                            if st.button("↑", key=f"tv_up_{di}_{team['name']}",
                                         disabled=(_i == 0), help="Move up"):
                                _ord = st.session_state[_tl_order_key]
                                _idx = _ord.index(team["name"])
                                _ord[_idx], _ord[_idx-1] = _ord[_idx-1], _ord[_idx]
                                save_user_team_order(user["id"], _ord)
                                st.rerun()
                        with _dcol:
                            if st.button("↓", key=f"tv_dn_{di}_{team['name']}",
                                         disabled=(_i == len(_ordered_teams)-1), help="Move down"):
                                _ord = st.session_state[_tl_order_key]
                                _idx = _ord.index(team["name"])
                                _ord[_idx], _ord[_idx+1] = _ord[_idx+1], _ord[_idx]
                                save_user_team_order(user["id"], _ord)
                                st.rerun()
                        team_sched = {a["name"]: day_sched.get(a["name"], {}) for a in team_agents}
                        st_components.html(
                            build_timeline_html(team_agents, team_sched, act_colors,
                                                slot_label_map=viewer_slot_lbl_map),
                            height=n_rows, scrolling=False
                        )
        return

    today = datetime.date.today()
    default_mon = today - datetime.timedelta(days=today.weekday())

    # ── Week navigation via session state (fixes prev/next not sticking) ───────
    if "sched_week" not in st.session_state:
        st.session_state["sched_week"] = default_mon

    c1, c2, c3, c4 = st.columns([2, 1.2, 1.2, 1.2])
    with c2:
        if st.button("⬅ Prev week", use_container_width=True):
            st.session_state["sched_week"] -= datetime.timedelta(weeks=1)
            st.session_state["sched_week_input"] = st.session_state["sched_week"]
            st.rerun()
    with c3:
        if st.button("Next week ➡", use_container_width=True):
            st.session_state["sched_week"] += datetime.timedelta(weeks=1)
            st.session_state["sched_week_input"] = st.session_state["sched_week"]
            st.rerun()
    with c1:
        sel = st.date_input(
            "Week starting (Monday)",
            value=st.session_state["sched_week"],
            label_visibility="collapsed",
            key="sched_week_input",
        )
        if sel.weekday() != 0:
            sel = sel - datetime.timedelta(days=sel.weekday())
        # Keep session state in sync if user picks manually
        st.session_state["sched_week"] = sel
        week_start = str(sel)
        st.markdown(
            f'<div style="font-size:13px;color:#64748B;margin-top:2px">'
            f'Week of <b style="color:#0F172A">{sel.strftime("%B %-d, %Y")}</b></div>',
            unsafe_allow_html=True
        )
    with c4:
        if st.button("📋 Copy prev week", use_container_width=True):
            prev = str(sel - datetime.timedelta(weeks=1))
            ok, msg = copy_week(prev, week_start)
            st.toast(msg, icon="✅" if ok else "⚠️")
            st.rerun()

    # ── Base schedule generator ────────────────────────────────────────────────
    with st.expander("🗓  Generate base schedule", expanded=False):
        st.markdown(
            '<div style="font-size:12px;color:#484848;margin-bottom:10px">'
            'Fills each agent\'s shift hours with their team\'s default activity. '
            'Set shift times in <b>Roster → agent card → Shift hours</b> and '
            'default activities in <b>Teams → Edit team</b>.</div>',
            unsafe_allow_html=True
        )

        # Show readiness status for every agent
        all_agents   = get_agents()
        agents_ready = []
        agents_missing = []
        for ag in all_agents:
            default_act = get_agent_default_activity(ag["name"])
            wh          = get_agent_work_hours(ag["name"])
            active_days = [d for d, cfg in wh.items() if cfg["is_active"]]
            if default_act and active_days:
                agents_ready.append((ag["name"], default_act, active_days))
            else:
                reason = []
                if not default_act:   reason.append("no default activity")
                if not active_days:   reason.append("no shift hours saved")
                agents_missing.append((ag["name"], ", ".join(reason)))

        rc1, rc2 = st.columns(2)
        with rc1:
            if agents_ready:
                st.markdown(
                    f'<div style="font-size:11px;font-weight:700;color:#16A34A;margin-bottom:4px">'
                    f'✅ {len(agents_ready)} agent(s) configured</div>'
                    + "".join(
                        f'<div style="font-size:11px;color:#484848;margin-bottom:1px">'
                        f'• {name} <span style="color:#89AC9E">{act}</span> '
                        f'— {len(days)} day(s)</div>'
                        for name, act, days in agents_ready[:10]
                    )
                    + (f'<div style="font-size:10px;color:#979797">…and {len(agents_ready)-10} more</div>'
                       if len(agents_ready) > 10 else ""),
                    unsafe_allow_html=True
                )
            else:
                st.warning("No agents configured yet. Set shift hours in Roster and default activities in Teams.")

        with rc2:
            if agents_missing:
                st.markdown(
                    f'<div style="font-size:11px;font-weight:700;color:#D97706;margin-bottom:4px">'
                    f'⚠ {len(agents_missing)} agent(s) need setup</div>'
                    + "".join(
                        f'<div style="font-size:11px;color:#979797;margin-bottom:1px">'
                        f'• {name}: {reason}</div>'
                        for name, reason in agents_missing[:10]
                    ),
                    unsafe_allow_html=True
                )

        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
        gc1, gc2, gc3 = st.columns([2, 2, 1])
        with gc1:
            overwrite_mode = st.radio(
                "Existing slots:",
                ["Keep existing (only fill empty slots)", "Overwrite everything"],
                key=f"bs_overwrite_{week_start}",
            )
        with gc3:
            st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
            gen_clicked = st.button(
                "Generate",
                type="primary",
                use_container_width=True,
                key=f"gen_base_{week_start}",
                disabled=len(agents_ready) == 0,
            )
        if gen_clicked:
            overwrite = overwrite_mode.startswith("Overwrite")
            n = apply_base_schedule(week_start, overwrite=overwrite)
            if n > 0:
                st.success(f"✅ Base schedule generated — {n} slot(s) filled for week of {sel.strftime('%B %-d')}.")
            else:
                st.info("No slots were filled. If using 'Keep existing', all slots may already be set. Try 'Overwrite everything' to reset.")

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

    # ── Coverage rules ────────────────────────────────────────────────────────
    if can_edit():
        with st.expander("⚙️  Live coverage rules", expanded=False):
            st.markdown(
                '<div style="font-size:12px;color:#484848;margin-bottom:12px">'
                'Set per-agent channel capabilities and lunch times. These rules are applied '
                'automatically whenever you generate a Gladly Live coverage template.</div>',
                unsafe_allow_html=True,
            )

            _agent_rules_db, _global_rules_db = get_coverage_rules()
            _all_roster = get_agents()

            # Build DataFrame for data_editor
            _CHANNEL_OPTIONS = ["Both", "Chat only", "Phones only", "None"]

            _rows = []
            for ag in _all_roster:
                r = _agent_rules_db.get(ag["name"], {})
                ch_raw = r.get("allowed_channels", "both")
                ch_display = (
                    "Chat only"   if ch_raw == "chat"   else
                    "Phones only" if ch_raw == "phones" else
                    "None"        if ch_raw == "none"   else
                    "Both"
                )
                _rows.append({"Agent": ag["name"], "Channels": ch_display})

            import pandas as _pd
            _rules_df = _pd.DataFrame(_rows)

            _edited = st.data_editor(
                _rules_df,
                use_container_width=True,
                hide_index=True,
                key=f"coverage_rules_editor_{week_start}",
                column_config={
                    "Agent": st.column_config.TextColumn("Agent", disabled=True),
                    "Channels": st.column_config.SelectboxColumn(
                        "Channels",
                        options=_CHANNEL_OPTIONS,
                        help="Which live channels this agent can handle",
                    ),
                },
            )
            st.caption("Lunch slot and duration are now configured per agent in the Roster page.")

            _nbb_default = _global_rules_db.get("no_back_to_back", "1") == "1"
            _no_bb = st.checkbox(
                "🔄  No back-to-back channel switches — insert a gap whenever an agent "
                "would go directly from Chat → Phones or Phones → Chat",
                value=_nbb_default,
                key=f"no_back_to_back_{week_start}",
            )

            st.markdown("---")
            st.markdown("**📅 Channel Windows**")
            st.caption(
                "Define when each channel is open. The template only assigns live coverage "
                "within these windows. Weekend = Saturday & Sunday."
            )

            def _slot_idx(key, default):
                v = _global_rules_db.get(key, "")
                return TIME_SLOTS.index(v) if v in TIME_SLOTS else TIME_SLOTS.index(default)

            _cw_cols = st.columns([1, 1])
            with _cw_cols[0]:
                st.markdown("**Weekday (Mon–Fri)**")
                _cwdo = st.selectbox("Chat open",    TIME_SLOTS, index=_slot_idx("chat_wkday_open",   "10:00 AM"), key=f"cwdo_{week_start}")
                _cwdc = st.selectbox("Chat close",   TIME_SLOTS, index=_slot_idx("chat_wkday_close",  "4:30 PM"),  key=f"cwdc_{week_start}")
                _pwdo = st.selectbox("Phones open",  TIME_SLOTS, index=_slot_idx("phones_wkday_open", "10:00 AM"), key=f"pwdo_{week_start}")
                _pwdc = st.selectbox("Phones close", TIME_SLOTS, index=_slot_idx("phones_wkday_close","4:30 PM"),  key=f"pwdc_{week_start}")
            with _cw_cols[1]:
                st.markdown("**Weekend (Sat–Sun)**")
                _cweo = st.selectbox("Chat open",    TIME_SLOTS, index=_slot_idx("chat_wkend_open",   "12:00 PM"), key=f"cweo_{week_start}")
                _cwec = st.selectbox("Chat close",   TIME_SLOTS, index=_slot_idx("chat_wkend_close",  "3:30 PM"),  key=f"cwec_{week_start}")
                _phones_wkend_closed = st.checkbox(
                    "Phones closed on weekends",
                    value=_global_rules_db.get("phones_wkend_closed", "1") == "1",
                    key=f"pweclosed_{week_start}",
                )
                if not _phones_wkend_closed:
                    _pweo = st.selectbox("Phones open",  TIME_SLOTS, index=_slot_idx("phones_wkend_open",  "10:00 AM"), key=f"pweo_{week_start}")
                    _pwec = st.selectbox("Phones close", TIME_SLOTS, index=_slot_idx("phones_wkend_close", "4:30 PM"),  key=f"pwec_{week_start}")
                else:
                    _pweo = _pwec = ""

            if st.button("💾  Save rules", key=f"save_rules_{week_start}", type="primary"):
                # Normalise display → DB values
                _to_save = _edited.copy()
                _to_save["Channels"] = _to_save["Channels"].map({
                    "Both": "both", "Chat only": "chat",
                    "Phones only": "phones", "None": "none",
                })
                save_coverage_rules(_to_save, {
                    "no_back_to_back":      _no_bb,
                    "chat_wkday_open":      _cwdo,
                    "chat_wkday_close":     _cwdc,
                    "phones_wkday_open":    _pwdo,
                    "phones_wkday_close":   _pwdc,
                    "chat_wkend_open":      _cweo,
                    "chat_wkend_close":     _cwec,
                    "phones_wkend_closed":  _phones_wkend_closed,
                    "phones_wkend_open":    _pweo,
                    "phones_wkend_close":   _pwec,
                })
                # Bust the Gladly template cache so rules take effect immediately
                for _k in list(st.session_state.keys()):
                    if _k.startswith("gladly_tmpl_") or _k.startswith("gladly_raw_"):
                        del st.session_state[_k]
                st.toast("Coverage rules saved.", icon="✅")
                st.rerun()

    # ── Gladly volume import → coverage template ──────────────────────────────
    if can_edit():
        with st.expander("📊  Gladly import → Live coverage template", expanded=False):
            st.markdown(
                '<div style="font-size:12px;color:#484848;margin-bottom:12px">'
                'Upload a Gladly contact export (CSV). The template is built from '
                'aggregate Chat/Phone volume and applied only to the selected team. '
                'Channel windows and shift hours are applied per agent. '
                'Only blank and existing Chat/Phones slots are overwritten.</div>',
                unsafe_allow_html=True,
            )

            # ── Team selector (default: Support Team) ────────────────────
            _gl_teams     = get_teams()
            _gl_team_names = [t["name"] for t in _gl_teams]
            _gl_def_idx   = next(
                (i for i, n in enumerate(_gl_team_names) if "support" in n.lower()), 0
            )
            _gl_sel_team = st.selectbox(
                "Apply to team:",
                _gl_team_names,
                index=_gl_def_idx,
                key=f"gladly_team_{week_start}",
            )

            gl_file = st.file_uploader(
                "Gladly contact export CSV",
                type=["csv"],
                key=f"gladly_upload_{week_start}",
                label_visibility="collapsed",
            )

            if gl_file is not None:
                # CSV parse is team-independent; template is team-specific.
                # Include file size in keys so re-uploading the same filename
                # triggers a fresh parse rather than serving a stale cache.
                _gl_fsize  = gl_file.size
                _TMPL_VER  = "v7"   # bump when build_gladly_template logic changes
                raw_key    = f"gladly_raw_{week_start}_{gl_file.name}_{_gl_fsize}"
                cache_key  = f"gladly_tmpl_{_TMPL_VER}_{week_start}_{gl_file.name}_{_gl_fsize}_{_gl_sel_team}"

                if raw_key not in st.session_state:
                    with st.spinner("Parsing Gladly report…"):
                        st.session_state[raw_key] = parse_gladly_csv(gl_file.read())

                if cache_key not in st.session_state:
                    with st.spinner("Building live coverage template…"):
                        _all_agents  = get_agents()
                        db_agents    = [
                            a["name"] for a in _all_agents
                            if a.get("team_name") == _gl_sel_team
                        ]
                        _ag_rules, _gl_rules = get_coverage_rules()
                        _no_bb = _gl_rules.get("no_back_to_back", "1") == "1"
                        _def_acts = {
                            ag: get_agent_default_activity(ag) or "Support"
                            for ag in db_agents
                        }
                        # Load each agent's configured shift hours
                        _agent_wh = {
                            ag: get_agent_work_hours(ag) for ag in db_agents
                        }
                        # Load current schedule so the builder skips time off,
                        # lunch/break, and any other already-filled slots
                        _existing = {}
                        for _ag in db_agents:
                            _existing[_ag] = {}
                            for _di in range(len(DAYS)):
                                _df = get_schedule_df(week_start, _di, [_ag])
                                _existing[_ag][_di] = _df[_ag].to_dict()
                        # Load channel windows from saved rules
                        def _slot_or(key, default):
                            v = _gl_rules.get(key, "")
                            return v if v in TIME_SLOTS else default
                        _phones_wkend_closed = _gl_rules.get("phones_wkend_closed", "1") == "1"
                        _channel_windows = {
                            "Chat": {
                                "weekday": (_slot_or("chat_wkday_open",  "10:00 AM"),
                                            _slot_or("chat_wkday_close", "4:30 PM")),
                                "weekend": (_slot_or("chat_wkend_open",  "12:00 PM"),
                                            _slot_or("chat_wkend_close", "3:30 PM")),
                            },
                            "Phones": {
                                "weekday": (_slot_or("phones_wkday_open",  "10:00 AM"),
                                            _slot_or("phones_wkday_close", "4:30 PM")),
                                "weekend": None if _phones_wkend_closed else
                                           (_slot_or("phones_wkend_open",  "10:00 AM"),
                                            _slot_or("phones_wkend_close", "4:30 PM")),
                            },
                        }
                        tmpl = build_gladly_template(
                            st.session_state[raw_key],
                            db_agents,
                            agent_rules=_ag_rules,
                            no_back_to_back=_no_bb,
                            default_activities=_def_acts,
                            agent_work_hours=_agent_wh,
                            existing_schedule=_existing,
                            channel_windows=_channel_windows,
                        )
                        st.session_state[cache_key] = tmpl

                raw_data = st.session_state.get(raw_key, {})
                tmpl     = st.session_state.get(cache_key, {})

                # ── Volume summary (total contacts per day per channel) ──────
                from collections import defaultdict as _dd
                day_totals = _dd(lambda: {"Chat": 0, "Phones": 0})
                for ag_data in raw_data.values():
                    for day, slots in ag_data.items():
                        for counts in slots.values():
                            day_totals[day]["Chat"]   += counts.get("Chat", 0)
                            day_totals[day]["Phones"]  += counts.get("Phones", 0)

                if day_totals:
                    st.markdown(
                        '<div style="font-size:10px;font-weight:700;color:#689985;'
                        'letter-spacing:0.12em;text-transform:uppercase;margin:8px 0 4px">'
                        'Volume from uploaded report (answered contacts)</div>',
                        unsafe_allow_html=True,
                    )
                    vol_rows = ""
                    for day in DAYS:
                        if day not in day_totals:
                            continue
                        c = day_totals[day]["Chat"]
                        p = day_totals[day]["Phones"]
                        vol_rows += (
                            f'<tr><td style="padding:3px 10px;font-size:11px;color:#334155">{day[:3]}</td>'
                            f'<td style="padding:3px 10px;font-size:11px;color:#1D4ED8;text-align:right">💬 {c}</td>'
                            f'<td style="padding:3px 10px;font-size:11px;color:#065F46;text-align:right">📞 {p}</td></tr>'
                        )
                    st.markdown(
                        f'<table style="border-collapse:collapse;margin-bottom:10px">'
                        f'<thead><tr>'
                        f'<th style="font-size:10px;color:#94A3B8;padding:2px 10px;text-align:left">Day</th>'
                        f'<th style="font-size:10px;color:#94A3B8;padding:2px 10px;text-align:right">Chat / SMS</th>'
                        f'<th style="font-size:10px;color:#94A3B8;padding:2px 10px;text-align:right">Phone calls</th>'
                        f'</tr></thead><tbody>{vol_rows}</tbody></table>',
                        unsafe_allow_html=True,
                    )

                # ── Template preview ─────────────────────────────────────────
                if tmpl:
                    total_agents      = len(tmpl)
                    total_suggestions = sum(
                        len(slots) for days in tmpl.values() for slots in days.values()
                    )
                    st.markdown(
                        f'<div style="font-size:11px;color:#0F172A;margin-bottom:8px">'
                        f'<b>{_gl_sel_team}</b> · <b>{total_agents}</b> agent(s) · '
                        f'<b>{total_suggestions}</b> slot suggestions · '
                        f'live window 10 AM – 5 PM · shift hours applied</div>',
                        unsafe_allow_html=True,
                    )

                    # Show per-agent preview for the currently-selected week's days
                    preview_rows = ""
                    for ag_name in sorted(tmpl.keys()):
                        day_cells = ""
                        for day in DAYS:
                            slots = tmpl[ag_name].get(day, {})
                            if not slots:
                                day_cells += '<td style="padding:2px 6px;font-size:10px;color:#CBD5E1;text-align:center">—</td>'
                                continue
                            chat_n   = sum(1 for v in slots.values() if v == "Chat")
                            phones_n = sum(1 for v in slots.values() if v == "Phones")
                            parts = []
                            if chat_n:
                                parts.append(f'<span style="color:#1D4ED8">💬{chat_n}</span>')
                            if phones_n:
                                parts.append(f'<span style="color:#065F46">📞{phones_n}</span>')
                            day_cells += f'<td style="padding:2px 6px;font-size:10px;text-align:center">{"&nbsp;".join(parts)}</td>'
                        preview_rows += (
                            f'<tr><td style="padding:2px 10px;font-size:11px;color:#334155;'
                            f'white-space:nowrap">{ag_name}</td>{day_cells}</tr>'
                        )

                    day_headers = "".join(
                        f'<th style="font-size:10px;color:#94A3B8;padding:2px 6px;text-align:center">{d[:3]}</th>'
                        for d in DAYS
                    )
                    st.markdown(
                        f'<div style="overflow-x:auto;max-height:300px;overflow-y:auto;'
                        f'border:1px solid #E2E8F0;border-radius:6px">'
                        f'<table style="border-collapse:collapse;width:100%">'
                        f'<thead style="position:sticky;top:0;background:#F8FAFC;z-index:1"><tr>'
                        f'<th style="font-size:10px;color:#94A3B8;padding:4px 10px;text-align:left">Agent</th>'
                        f'{day_headers}</tr></thead>'
                        f'<tbody>{preview_rows}</tbody></table></div>',
                        unsafe_allow_html=True,
                    )

                    st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
                    if st.button(
                        f"✅  Apply Live template to week of {sel.strftime('%B %-d')}",
                        type="primary",
                        use_container_width=False,
                        key=f"gladly_apply_{week_start}",
                    ):
                        _apply_def_acts = {
                            ag: get_agent_default_activity(ag) or "Support"
                            for ag in tmpl.keys()
                        }
                        n = apply_gladly_template(tmpl, week_start, sel,
                                                  default_activities=_apply_def_acts)
                        st.toast(
                            f"Applied Gladly template — {n} slot(s) filled for week of {sel.strftime('%B %-d')}.",
                            icon="✅",
                        )
                        st.rerun()
                else:
                    st.info(
                        "No Chat, SMS, or Phone call contacts found in this report. "
                        "Make sure the CSV includes live-channel contacts with "
                        "Status = ANSWERED."
                    )

    st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)

    agents_all  = get_agents()
    teams       = get_teams()
    team_colors = {t["name"]: t["color"] for t in teams}
    act_names   = get_activity_names()   # dynamic from DB
    act_colors  = get_act_colors()       # dynamic from DB

    # ── Custom day selector (replaces st.tabs so only ONE day renders at a time,
    #    eliminating multi-iframe interference with the schedule editor component) ──
    _active_day_key = f"active_sched_day_{week_start}"
    if _active_day_key not in st.session_state:
        # Default to today when viewing the current week, else Monday
        _today = datetime.date.today()
        _cur_mon = str(_today - datetime.timedelta(days=_today.weekday()))
        _default_di = _today.weekday() if week_start == _cur_mon else 0
        st.session_state[_active_day_key] = _default_di

    _day_btn_cols = st.columns(len(DAYS))
    for _dbi, (_dbc, _dbn) in enumerate(zip(_day_btn_cols, DAYS)):
        _date_lbl = (sel + datetime.timedelta(days=_dbi)).strftime('%-m/%-d')
        with _dbc:
            if st.button(
                f"{_dbn[:3]}  {_date_lbl}",
                key=f"daybtn_{week_start}_{_dbi}",
                type="primary" if st.session_state[_active_day_key] == _dbi else "secondary",
                use_container_width=True,
            ):
                st.session_state[_active_day_key] = _dbi
                st.rerun()

    st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

    # Declare the schedule editor component ONCE, unconditionally.
    # path= tells Streamlit to serve the files itself — works for all users,
    # unlike url=localhost which only works on the developer's machine.
    _sched_editor = st_components.declare_component(
        "cx_schedule_editor",
        path=_COMPONENT_PATH,
    )

    # Render only the active day — a single pass, no loop
    di       = st.session_state[_active_day_key]
    day_name = DAYS[di]
    with st.container():
            # Load saved schedule data for all agents on this day
            sched_data = {}
            for ag in agents_all:
                df_tmp = get_schedule_df(week_start, di, [ag["name"]])
                sched_data[ag["name"]] = df_tmp[ag["name"]].to_dict()

            # ── View toggle (session-state so Edit persists after save) ──────
            # Single view key shared across all days in this week —
            # switching tabs preserves whichever view (timeline / edit) is active.
            _view_key = f"sched_view_{week_start}"
            if _view_key not in st.session_state:
                st.session_state[_view_key] = "timeline"

            _vt1, _vt2, _vtspc = st.columns([1, 1, 5])
            with _vt1:
                if st.button("👁  Timeline", key=f"vtl_{week_start}_{di}",
                             type="primary" if st.session_state[_view_key] == "timeline" else "secondary",
                             use_container_width=True):
                    st.session_state[_view_key] = "timeline"
                    st.rerun()
            if can_edit():
                with _vt2:
                    if st.button("✏️  Edit", key=f"ved_{week_start}_{di}",
                                 type="primary" if st.session_state[_view_key] == "edit" else "secondary",
                                 use_container_width=True):
                        st.session_state[_view_key] = "edit"
                        st.rerun()

            # shared team/agent info
            agents_info = [
                {"name": a["name"], "team_name": a["team_name"],
                 "color": team_colors.get(a["team_name"], "#64748B")}
                for a in agents_all
            ]
            teams_with_agents = [t for t in teams
                                  if any(a["team_name"] == t["name"] for a in agents_info)]
            _cu = current_user()
            _ordered_teams, _tl_order_key = resolve_team_order(_cu, teams_with_agents)

            # ── TIMELINE VIEW ─────────────────────────────────────────────────
            if st.session_state[_view_key] == "timeline":
                if not agents_all:
                    st.info("Add agents in the Roster page to see the schedule.")
                else:
                    n_rows = len(TIME_SLOTS) * 26 + 120
                    for _i, team in enumerate(_ordered_teams):
                        team_agents = [a for a in agents_info if a["team_name"] == team["name"]]
                        if not team_agents:
                            continue
                        _hcol, _ucol, _dcol = st.columns([30, 1, 1])
                        with _hcol:
                            st.markdown(
                                f'<div style="display:flex;align-items:center;gap:8px;margin:10px 0 4px">'
                                f'<div style="width:10px;height:10px;border-radius:50%;background:{team["color"]}"></div>'
                                f'<span style="font-size:13px;font-weight:600;color:#1E293B">{team["name"]} Team</span>'
                                f'<span style="font-size:11px;color:#94A3B8">— {len(team_agents)} agents</span>'
                                f'</div>', unsafe_allow_html=True
                            )
                        with _ucol:
                            if st.button("↑", key=f"tl_up_{di}_{team['name']}",
                                         disabled=(_i == 0), help="Move this team up"):
                                _order = st.session_state[_tl_order_key]
                                _idx   = _order.index(team["name"])
                                _order[_idx], _order[_idx - 1] = _order[_idx - 1], _order[_idx]
                                save_user_team_order(_cu["id"], _order)
                                st.rerun()
                        with _dcol:
                            if st.button("↓", key=f"tl_dn_{di}_{team['name']}",
                                         disabled=(_i == len(_ordered_teams) - 1),
                                         help="Move this team down"):
                                _order = st.session_state[_tl_order_key]
                                _idx   = _order.index(team["name"])
                                _order[_idx], _order[_idx + 1] = _order[_idx + 1], _order[_idx]
                                save_user_team_order(_cu["id"], _order)
                                st.rerun()
                        team_sched = {a["name"]: sched_data.get(a["name"], {}) for a in team_agents}
                        timeline_html = build_timeline_html(team_agents, team_sched, act_colors)
                        st_components.html(timeline_html, height=n_rows, scrolling=False)
                        st.markdown('<div style="height:4px"></div>', unsafe_allow_html=True)

                    if agents_all:
                        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
                        n_scheduled = sum(
                            1 for ag_s in sched_data.values()
                            if any(v not in (".", "") for v in ag_s.values())
                        )
                        chat_peak = max(
                            (sum(1 for ag_s in sched_data.values()
                                 if ag_s.get(s, ".") in _LIVE_CHAT) for s in TIME_SLOTS), default=0)
                        phone_peak = max(
                            (sum(1 for ag_s in sched_data.values()
                                 if ag_s.get(s, ".") in _LIVE_PHONES) for s in TIME_SLOTS), default=0)
                        st.markdown(
                            f'<div style="display:flex;gap:16px;margin-bottom:8px;flex-wrap:wrap">'
                            f'<span style="font-size:12px;color:#475569">👥 <b style="color:#0F172A">{len(agents_all)}</b> agents total</span>'
                            f'<span style="font-size:12px;color:#475569">📋 <b style="color:#0F172A">{n_scheduled}</b> have shifts entered</span>'
                            f'<span style="font-size:12px;color:#1D4ED8">💬 Peak Chat: <b>{chat_peak}</b></span>'
                            f'<span style="font-size:12px;color:#065F46">📞 Peak Phones: <b>{phone_peak}</b></span>'
                            f'</div>', unsafe_allow_html=True)
                        cov_html = build_coverage_bar_html(sched_data, act_colors)
                        if cov_html:
                            st.markdown(cov_html, unsafe_allow_html=True)
                        else:
                            st.caption("No schedule data yet — use Edit to build this day's schedule.")

            # ── EDIT VIEW ─────────────────────────────────────────────────────
            elif st.session_state[_view_key] == "edit" and can_edit():
                if not _ordered_teams:
                    st.info("Add agents in the Roster page first.")
                else:
                    # ── Team selector buttons ──────────────────────────────────
                    _team_sel_key  = f"edit_team_{week_start}_{di}"
                    _last_team_key = f"edit_last_team_{week_start}"   # persists across day tabs
                    _edit_team_names = [t["name"] for t in _ordered_teams]
                    if (_team_sel_key not in st.session_state or
                            st.session_state[_team_sel_key] not in _edit_team_names):
                        # Default to whichever team was last active on any tab this week
                        _last = st.session_state.get(_last_team_key, _edit_team_names[0])
                        st.session_state[_team_sel_key] = (
                            _last if _last in _edit_team_names else _edit_team_names[0]
                        )

                    _sel_team_name = st.session_state[_team_sel_key]
                    team_agents = [a["name"] for a in agents_all if a["team_name"] == _sel_team_name]

                    # ── Team selector ──────────────────────────────────────────
                    _tcols = st.columns(len(_edit_team_names))
                    for _tc, _tn in zip(_tcols, _edit_team_names):
                        with _tc:
                            if st.button(
                                _tn, key=f"tsel_{week_start}_{di}_{_tn}",
                                type="primary" if st.session_state[_team_sel_key] == _tn else "secondary",
                                use_container_width=True,
                            ):
                                st.session_state[_team_sel_key] = _tn
                                st.session_state[_last_team_key] = _tn  # remember across tabs
                                st.rerun()

                    # ── Stats bar (from saved data) ────────────────────────────
                    _ec_chat  = sum(1 for ag_s in sched_data.values() if any(v in _LIVE_CHAT   for v in ag_s.values()))
                    _ec_phone = sum(1 for ag_s in sched_data.values() if any(v in _LIVE_PHONES for v in ag_s.values()))
                    _chat_peak  = max((sum(1 for ag_s in sched_data.values() if ag_s.get(s, ".") in _LIVE_CHAT)   for s in TIME_SLOTS), default=0)
                    _phone_peak = max((sum(1 for ag_s in sched_data.values() if ag_s.get(s, ".") in _LIVE_PHONES) for s in TIME_SLOTS), default=0)
                    st.markdown(
                        f'<div style="display:flex;gap:16px;align-items:center;'
                        f'background:#F0F5F3;border:1px solid #C4D9D2;border-radius:6px;'
                        f'padding:8px 14px;margin-bottom:10px;flex-wrap:wrap">'
                        f'<span style="font-size:11px;color:#475569">👥 <b style="color:#0F172A">{len(agents_all)}</b> agents</span>'
                        f'<span style="font-size:11px;color:#1D4ED8">💬 Chat: <b>{_ec_chat}</b> &nbsp;·&nbsp; Peak: <b>{_chat_peak}</b></span>'
                        f'<span style="font-size:11px;color:#065F46">📞 Phones: <b>{_ec_phone}</b> &nbsp;·&nbsp; Peak: <b>{_phone_peak}</b></span>'
                        f'</div>', unsafe_allow_html=True)

                    # ── Color legend ───────────────────────────────────────────
                    _legend_chips = "".join(
                        f'<span style="background:{act_colors.get(_an,("#E2E8F0","#475569"))[0]};'
                        f'color:{act_colors.get(_an,("#E2E8F0","#475569"))[1]};'
                        f'padding:2px 7px;border-radius:3px;font-size:10px;'
                        f'font-weight:600;white-space:nowrap">{_an}</span>'
                        for _an in act_names if _an != "."
                    )
                    st.markdown(
                        f'<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px;'
                        f'padding:6px 10px;background:#FAFAFA;border:1px solid #E2E8F0;border-radius:6px">'
                        f'<span style="font-size:10px;color:#94A3B8;font-weight:700;'
                        f'letter-spacing:0.08em;text-transform:uppercase;align-self:center;'
                        f'margin-right:4px">KEY</span>{_legend_chips}</div>',
                        unsafe_allow_html=True)

                    # ── Dropdown color CSS ─────────────────────────────────────
                    _dropdown_css = "<style>"
                    for _an, (_abg, _afg) in act_colors.items():
                        _esc = _an.replace('"', '\\"')
                        _dropdown_css += (
                            f'.ag-popup .ag-list-item[aria-label="{_esc}"]'
                            f'{{background:{_abg}!important;color:{_afg}!important;font-weight:600!important}}'
                            f'.ag-popup .ag-list-item[aria-label="{_esc}"]:hover'
                            f'{{filter:brightness(0.93)!important}}'
                        )
                    _dropdown_css += "</style>"
                    st.markdown(_dropdown_css, unsafe_allow_html=True)

                    # ── Schedule editor (custom HTML component) ────────────────
                    df = get_schedule_df(week_start, di, team_agents)
                    _agent_cols = list(df.columns)

                    _color_map = {
                        k: {"bg": v[0], "fg": v[1]}
                        for k, v in act_colors.items()
                    }
                    # Neutral color for "." (empty slot)
                    _color_map.setdefault(".", {"bg": "#F8FAFC", "fg": "#CBD5E1"})

                    _result = _sched_editor(
                        schedule_data=df.values.tolist(),
                        agents=_agent_cols,
                        time_slots=TIME_SLOTS,
                        activities=act_names,
                        color_map=_color_map,
                        live_chat_acts=list(_LIVE_CHAT),
                        live_phone_acts=list(_LIVE_PHONES),
                        key=f"sched_ed_{week_start}_{di}_{_sel_team_name}",
                        default=None,
                    )

                    if _result and _result.get("saved"):
                        _new_df = pd.DataFrame(
                            _result["data"],
                            index=TIME_SLOTS,
                            columns=_agent_cols,
                        )
                        save_schedule_df(week_start, di, _new_df)
                        _edit_date = datetime.date.fromisoformat(week_start) + datetime.timedelta(days=di)
                        if _edit_date == datetime.date.today():
                            st.session_state["_play_schedule_sound"] = True
                        st.session_state[_view_key] = "edit"
                        st.session_state[_team_sel_key] = _sel_team_name
                        st.session_state[_last_team_key] = _sel_team_name  # persist across tabs
                        st.toast(f"Saved {_sel_team_name} for {day_name}.", icon="✅")
                        # Clear the component's stored value so _result resets to
                        # None on the next render — without this, Streamlit keeps
                        # returning {saved: True} from session state on every rerun,
                        # causing an infinite save/rerun loop.
                        _comp_ss_key = f"sched_ed_{week_start}_{di}_{_sel_team_name}"
                        st.session_state.pop(_comp_ss_key, None)
                        st.rerun()



# ─── PAGE: AGENT VIEW ────────────────────────────────────────────────────────

_TZ_OPTIONS = {
    "Eastern (ET)":   0,
    "Central (CT)":  -1,
    "Mountain (MT)": -2,
    "Pacific (PT)":  -3,
    "Alaska (AKT)":  -4,
    "Hawaii (HST)":  -5,
}
_BASE_TZ_LABEL = "Eastern (ET)"   # app's stored schedule timezone

def _make_tz_slot_label_map(offset_hours):
    """Return {original_slot: display_label} with times shifted by offset_hours."""
    if offset_hours == 0:
        return {}
    result = {}
    for slot in TIME_SLOTS:
        try:
            base = datetime.datetime.strptime(slot, "%I:%M %p")
            shifted = base + datetime.timedelta(hours=offset_hours)
            result[slot] = shifted.strftime("%I:%M %p").lstrip("0")
        except Exception:
            result[slot] = slot
    return result

def page_agent_view():
    st.markdown('<div class="page-title">Agent View</div>', unsafe_allow_html=True)

    # ── Schedule change watcher (all roles) ───────────────────────────────────
    if "_av_baseline_ver" not in st.session_state:
        st.session_state["_av_baseline_ver"] = get_setting("schedule_last_modified", "")
    _schedule_update_watcher(st.session_state["_av_baseline_ver"])

    user = current_user()
    if not user:
        st.warning("Please log in.")
        return

    today = datetime.date.today()
    default_mon = today - datetime.timedelta(days=today.weekday())

    if "av_sched_week" not in st.session_state:
        st.session_state["av_sched_week"] = default_mon

    # ── Controls row ──────────────────────────────────────────────────────────
    hc1, hc2, hc3, hc4 = st.columns([2, 1, 1, 2])
    with hc2:
        if st.button("⬅ Prev", key="av_prev", use_container_width=True):
            st.session_state["av_sched_week"] -= datetime.timedelta(weeks=1)
            st.session_state["av_week_input"] = st.session_state["av_sched_week"]
            st.rerun()
    with hc3:
        if st.button("Next ➡", key="av_next", use_container_width=True):
            st.session_state["av_sched_week"] += datetime.timedelta(weeks=1)
            st.session_state["av_week_input"] = st.session_state["av_sched_week"]
            st.rerun()
    with hc1:
        sel = st.date_input(
            "Week", value=st.session_state["av_sched_week"],
            label_visibility="collapsed", key="av_week_input",
        )
        if sel.weekday() != 0:
            sel = sel - datetime.timedelta(days=sel.weekday())
        st.session_state["av_sched_week"] = sel
    with hc4:
        tz_label = st.selectbox(
            "🌐 Timezone",
            list(_TZ_OPTIONS.keys()),
            index=0,
            key="av_timezone",
        )

    tz_offset    = _TZ_OPTIONS[tz_label]
    slot_lbl_map = _make_tz_slot_label_map(tz_offset)
    week_start   = str(sel)

    if tz_label != _BASE_TZ_LABEL:
        sign = "+" if tz_offset >= 0 else ""
        st.markdown(
            f'<div style="font-size:11px;color:#64748B;margin-bottom:6px">'
            f'Showing times in <b style="color:#0F172A">{tz_label}</b> '
            f'({sign}{tz_offset}h from {_BASE_TZ_LABEL})</div>',
            unsafe_allow_html=True,
        )

    agents_all  = get_agents()
    teams       = get_teams()
    team_colors = {t["name"]: t["color"] for t in teams}
    act_colors  = get_act_colors()
    agents_info = [
        {"name": a["name"], "team_name": a["team_name"],
         "color": team_colors.get(a["team_name"], "#64748B")}
        for a in agents_all
    ]

    if not agents_all:
        st.info("No agents on the roster yet.")
        return

    teams_with_agents = [t for t in teams
                         if any(a["team_name"] == t["name"] for a in agents_info)]
    _av_user = current_user()
    _ordered_teams, _tl_order_key = resolve_team_order(_av_user, teams_with_agents)
    n_rows = len(TIME_SLOTS) * 26 + 120

    # ── Top-level tabs: personal view + team view ─────────────────────────────
    my_tab, team_tab = st.tabs(["👤  My Schedule", "👥  Team View"])

    with my_tab:
        _linked_agent = next((a for a in agents_all if a.get("linked_user_id") == user["id"]), None)
        _name_agent   = next((a for a in agents_all if a["name"] == user["display_name"]), None)
        _my_agent     = _linked_agent or _name_agent
        if _my_agent:
            _agent_hour_breakdown(_my_agent["name"], week_start)
        else:
            st.info("Your roster profile hasn't been set up yet, or hasn't been linked to this account. Ask an admin to add you to the Roster and link your login.")

    with team_tab:
        st.markdown(
            f'<div style="font-size:13px;color:#64748B;margin-bottom:10px">'
            f'Week of <b style="color:#0F172A">{sel.strftime("%B %-d, %Y")}</b></div>',
            unsafe_allow_html=True,
        )
        # ── Day tabs ──────────────────────────────────────────────────────────
        day_tabs = st.tabs([
            f"{d[:3]}  {(sel + datetime.timedelta(days=i)).strftime('%-m/%-d')}"
            for i, d in enumerate(DAYS)
        ])
        _default_to_today_tab(week_start, key_prefix="av_day_tab__")
        for di, dtab in enumerate(day_tabs):
            with dtab:
                # Load schedule for the day
                day_sched = {}
                for ag in agents_all:
                    df = get_schedule_df(week_start, di, [ag["name"]])
                    day_sched[ag["name"]] = df[ag["name"]].to_dict()

                for _i, team in enumerate(_ordered_teams):
                    team_agents = [a for a in agents_info if a["team_name"] == team["name"]]
                    if not team_agents:
                        continue

                    _hcol, _ucol, _dcol = st.columns([30, 1, 1])
                    with _hcol:
                        st.markdown(
                            f'<div style="display:flex;align-items:center;gap:8px;margin:10px 0 4px">'
                            f'<div style="width:10px;height:10px;border-radius:50%;background:{team["color"]}"></div>'
                            f'<span style="font-size:13px;font-weight:600;color:#1E293B">{team["name"]} Team</span>'
                            f'<span style="font-size:11px;color:#94A3B8">— {len(team_agents)} agents</span>'
                            f'</div>', unsafe_allow_html=True
                        )
                    with _ucol:
                        if st.button("↑", key=f"av_up_{di}_{team['name']}",
                                     disabled=(_i == 0), help="Move up"):
                            _ord = st.session_state[_tl_order_key]
                            _idx = _ord.index(team["name"])
                            _ord[_idx], _ord[_idx-1] = _ord[_idx-1], _ord[_idx]
                            save_user_team_order(_av_user["id"], _ord)
                            st.rerun()
                    with _dcol:
                        if st.button("↓", key=f"av_dn_{di}_{team['name']}",
                                     disabled=(_i == len(_ordered_teams)-1), help="Move down"):
                            _ord = st.session_state[_tl_order_key]
                            _idx = _ord.index(team["name"])
                            _ord[_idx], _ord[_idx+1] = _ord[_idx+1], _ord[_idx]
                            save_user_team_order(_av_user["id"], _ord)
                            st.rerun()
                    team_sched = {a["name"]: day_sched.get(a["name"], {}) for a in team_agents}
                    st_components.html(
                        build_timeline_html(team_agents, team_sched, act_colors,
                                            slot_label_map=slot_lbl_map),
                        height=n_rows, scrolling=False,
                    )


# ─── PAGE: TIME OFF ───────────────────────────────────────────────────────────

def page_timeoff():
    st.markdown('<div class="page-title">Time Off</div>', unsafe_allow_html=True)

    # Play submission chime if flagged by previous rerun
    if st.session_state.pop("_play_timeoff_sound", False):
        st_components.html("""
        <script>
        (function() {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            [[523.25, 0.00], [659.25, 0.15], [783.99, 0.30]].forEach(([freq, t]) => {
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.connect(gain); gain.connect(ctx.destination);
                osc.type = 'sine'; osc.frequency.value = freq;
                gain.gain.setValueAtTime(0.25, ctx.currentTime + t);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + t + 0.35);
                osc.start(ctx.currentTime + t);
                osc.stop(ctx.currentTime + t + 0.35);
            });
        })();
        </script>
        """, height=0)

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
            st.markdown(f'<div style="font-size:13px;color:#475569;margin-bottom:8px">Submitting as <strong>{my_name}</strong></div>', unsafe_allow_html=True)
            _slot_opts = ["(select a time)"] + TIME_SLOTS
            _vtype = st.selectbox("Type", TIMEOFF_TYPES, key="viewer_rtype")

            if _vtype == "Shift Swap":
                st.markdown('<div style="font-size:12px;color:#64748B;margin:4px 0 8px">Fill in both the shift you\'re giving up and the shift you\'re taking on.</div>', unsafe_allow_html=True)
                st.markdown("**Shift to give up**")
                sf1, sf2, sf3 = st.columns(3)
                with sf1: _from_date = st.date_input("Date", value=datetime.date.today()+datetime.timedelta(7), key="v_from_date")
                with sf2: _from_start = st.selectbox("Start time", _slot_opts, key="v_from_start")
                with sf3: _from_end   = st.selectbox("End time",   _slot_opts, key="v_from_end")
                st.markdown("**Moving to**")
                st1, st2, st3 = st.columns(3)
                with st1: _to_date  = st.date_input("Date", value=datetime.date.today()+datetime.timedelta(8), key="v_to_date")
                with st2: _to_start = st.selectbox("Start time", _slot_opts, key="v_to_start")
                with st3: _to_end   = st.selectbox("End time",   _slot_opts, key="v_to_end")
                _vnotes = st.text_input("Notes *", key="v_swap_notes")
                if st.button("Submit request", type="primary", key="v_swap_submit"):
                    _errs = []
                    if not my_name: _errs.append("Could not identify your account.")
                    if not _vnotes.strip(): _errs.append("Notes are required.")
                    if _from_start == "(select a time)" or _from_end == "(select a time)": _errs.append("Select start and end times for the shift you're giving up.")
                    if _to_start == "(select a time)" or _to_end == "(select a time)": _errs.append("Select start and end times for the shift you're taking on.")
                    if _errs:
                        for _e in _errs: st.error(_e)
                    else:
                        ag_data = next((a for a in get_agents() if a["name"]==my_name), {})
                        add_time_off_request(my_name, ag_data.get("team_name",""),
                                             _to_date, _to_date, "Shift Swap", _vnotes,
                                             _to_start, _to_end,
                                             str(_from_date), _from_start, _from_end)
                        st.session_state["_play_timeoff_sound"] = True
                        st.toast("Shift swap request submitted — your manager will review it soon.", icon="✅")
                        st.rerun()
            else:
                _KRONOS_TYPES = {"PTO", "Sick", "Bereavement"}
                _needs_kronos = _vtype in _KRONOS_TYPES
                with st.form("submit_to_viewer"):
                    c1, c2 = st.columns(2)
                    with c1: start = st.date_input("Start date", value=datetime.date.today()+datetime.timedelta(7))
                    with c2: end   = st.date_input("End date",   value=datetime.date.today()+datetime.timedelta(7))
                    st.markdown('<div style="font-size:11px;color:#64748B;margin:2px 0 4px">Time range — leave blank to apply to the full day</div>', unsafe_allow_html=True)
                    _vt1, _vt2 = st.columns(2)
                    _v_slot_opts = ["(all day)"] + TIME_SLOTS
                    with _vt1: _v_st_sel = st.selectbox("Start time (ET)", _v_slot_opts, key="v_st_time")
                    with _vt2: _v_en_sel = st.selectbox("End time (ET)",   _v_slot_opts, key="v_en_time")
                    notes = st.text_input("Notes *")
                    _kronos_ok = True
                    if _needs_kronos:
                        st.markdown("""
                        <div style="background:#FEF3C7;border:1px solid #F59E0B;border-radius:6px;
                                    padding:10px 14px;margin:10px 0 4px">
                            <div style="font-size:12px;font-weight:700;color:#92400E">⚠️ Kronos submission required</div>
                            <div style="font-size:11px;color:#78350F;margin-top:3px">
                                This type of request must also be submitted in Kronos.
                                Please do that first, then confirm below.
                            </div>
                        </div>""", unsafe_allow_html=True)
                        _kronos_ok = st.checkbox("I have submitted this request in Kronos ✓")
                    if st.form_submit_button("Submit request", type="primary"):
                        if not my_name:
                            st.error("Could not identify your account. Please log out and back in.")
                        elif not notes.strip():
                            st.error("Notes are required.")
                        elif end < start:
                            st.error("End date must be on or after start date.")
                        elif _needs_kronos and not _kronos_ok:
                            st.error("Please confirm you have submitted this request in Kronos before continuing.")
                        else:
                            _v_st_time = "" if _v_st_sel == "(all day)" else _v_st_sel
                            _v_en_time = "" if _v_en_sel == "(all day)" else _v_en_sel
                            if _v_st_time and _v_en_time and TIME_SLOTS.index(_v_st_time) > TIME_SLOTS.index(_v_en_time):
                                st.error("Start time must be before end time.")
                            else:
                                ag_data = next((a for a in get_agents() if a["name"]==my_name), {})
                                add_time_off_request(my_name, ag_data.get("team_name",""), start, end, _vtype, notes, _v_st_time, _v_en_time)
                                if get_setting("slack_notify_submissions", "") == "yes":
                                    send_slack_message(
                                        f"📋 *New time-off request* — {my_name} ({ag_data.get('team_name','')}) "
                                        f"submitted a *{_vtype}* request from {start} to {end}."
                                        + (f"\n> {notes}" if notes else "")
                                    )
                                st.session_state["_play_timeoff_sound"] = True
                                st.toast("Request submitted — your manager will review it soon.", icon="✅")
                                st.rerun()
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
                    {f"&nbsp;·&nbsp; <b>{req['start_time']} – {req['end_time']}</b>" if req.get("start_time") and req.get("end_time") else "&nbsp;·&nbsp; all day"}
                    {"&nbsp;·&nbsp; <i>"+req['notes']+"</i>" if req["notes"] else ""}
                </div>
            </div>""", unsafe_allow_html=True)

            ca, cb, cc, _ = st.columns([1, 1, 1, 4])
            with ca:
                if st.button("✅ Approve", key=f"ap_{req['id']}", use_container_width=True, type="primary"):
                    u = current_user()
                    approver = u["display_name"] if u else "Admin"
                    update_request_status(req["id"], "Approved", approver)
                    add_notification(req["agent_name"],
                        f"✅ Your {req['type']} request ({req['start_date']} – {req['end_date']}) was approved by {approver}.")
                    send_slack_message(
                        f"✅ *Time-off approved* — {req['agent_name']}'s {req['type']} "
                        f"({req['start_date']} – {req['end_date']}) approved by {approver}."
                    )
                    st.toast(f"Approved {req['agent_name']}'s request.", icon="✅")
                    st.rerun()
            with cb:
                if st.button("✗ Deny", key=f"dn_{req['id']}", use_container_width=True):
                    u = current_user()
                    approver = u["display_name"] if u else "Admin"
                    update_request_status(req["id"], "Denied", approver)
                    add_notification(req["agent_name"],
                        f"🚫 Your {req['type']} request ({req['start_date']} – {req['end_date']}) was denied.")
                    send_slack_message(
                        f"🚫 *Time-off denied* — {req['agent_name']}'s {req['type']} "
                        f"({req['start_date']} – {req['end_date']}) denied by {approver}."
                    )
                    st.toast("Request denied.", icon="🚫")
                    st.rerun()
            with cc:
                if is_admin() and st.button("🗑 Delete", key=f"del_{req['id']}", use_container_width=True):
                    delete_time_off_request(req["id"])
                    st.toast("Request deleted.", icon="🗑️")
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
                _ar_l, _ar_r = st.columns([10, 1])
                with _ar_l:
                    st.markdown(f"""<div class="req-row" style="display:flex;align-items:center;gap:12px">
                        <div style="width:28px;height:28px;border-radius:50%;background:{tcolor}22;color:{tcolor};
                                    font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0">
                            {"".join(p[0] for p in req["agent_name"].split()[:2]).upper()}
                        </div>
                        <div style="flex:1">
                            <span style="font-size:13px;font-weight:600;color:#0F172A">{req["agent_name"]}</span>
                            <span style="font-size:12px;color:#94A3B8;margin-left:8px">{req["type"]} · {s.strftime("%-m/%-d")}–{e.strftime("%-m/%-d")} · {days}d{f" · {req['start_time']}–{req['end_time']}" if req.get("start_time") and req.get("end_time") else " · all day"}</span>
                        </div>
                        {status_pill(req["status"])}
                    </div>""", unsafe_allow_html=True)
                with _ar_r:
                    if is_admin() and st.button("🗑", key=f"alldel_{req['id']}", help="Delete request", use_container_width=True):
                        delete_time_off_request(req["id"])
                        st.toast("Request deleted.", icon="🗑️")
                        st.rerun()

    with tab_submit:
        agents_list = get_agent_names()
        if not agents_list:
            st.warning("Add agents to the Roster first.")
        else:
            _slot_opts = ["(all day)"] + TIME_SLOTS
            _swap_opts = ["(select a time)"] + TIME_SLOTS
            _admin_rtype = st.selectbox("Type", TIMEOFF_TYPES, key="admin_rtype")

            if _admin_rtype == "Shift Swap":
                agent = st.selectbox("Agent", agents_list, key="admin_swap_agent")
                st.markdown('<div style="font-size:12px;color:#64748B;margin:4px 0 8px">Fill in both the shift being given up and the shift being taken on.</div>', unsafe_allow_html=True)
                st.markdown("**Shift to give up**")
                af1, af2, af3 = st.columns(3)
                with af1: _a_from_date  = st.date_input("Date", value=datetime.date.today()+datetime.timedelta(7), key="a_from_date")
                with af2: _a_from_start = st.selectbox("Start time", _swap_opts, key="a_from_start")
                with af3: _a_from_end   = st.selectbox("End time",   _swap_opts, key="a_from_end")
                st.markdown("**Moving to**")
                at1, at2, at3 = st.columns(3)
                with at1: _a_to_date  = st.date_input("Date", value=datetime.date.today()+datetime.timedelta(8), key="a_to_date")
                with at2: _a_to_start = st.selectbox("Start time", _swap_opts, key="a_to_start")
                with at3: _a_to_end   = st.selectbox("End time",   _swap_opts, key="a_to_end")
                _a_notes = st.text_input("Notes *", key="a_swap_notes")
                if st.button("Submit shift swap", type="primary", key="a_swap_submit"):
                    _errs = []
                    if not _a_notes.strip(): _errs.append("Notes are required.")
                    if _a_from_start == "(select a time)" or _a_from_end == "(select a time)": _errs.append("Select times for the shift being given up.")
                    if _a_to_start == "(select a time)" or _a_to_end == "(select a time)": _errs.append("Select times for the shift being taken on.")
                    if _errs:
                        for _e in _errs: st.error(_e)
                    else:
                        ag_data = next((a for a in get_agents() if a["name"]==agent), {})
                        add_time_off_request(agent, ag_data.get("team_name",""),
                                             _a_to_date, _a_to_date, "Shift Swap", _a_notes,
                                             _a_to_start, _a_to_end,
                                             str(_a_from_date), _a_from_start, _a_from_end)
                        st.toast(f"Shift swap submitted for {agent}.", icon="✅")
                        st.rerun()
            else:
                _KRONOS_TYPES = {"PTO", "Sick", "Bereavement"}
                _needs_kronos = _admin_rtype in _KRONOS_TYPES
                with st.form("submit_to"):
                    agent = st.selectbox("Agent", agents_list)
                    c1, c2 = st.columns(2)
                    with c1: start = st.date_input("Start date", value=datetime.date.today()+datetime.timedelta(7))
                    with c2: end   = st.date_input("End date",   value=datetime.date.today()+datetime.timedelta(7))
                    st.markdown('<div style="font-size:11px;color:#64748B;margin:2px 0 4px">Time range — leave blank to apply to the full day</div>', unsafe_allow_html=True)
                    tc1, tc2 = st.columns(2)
                    with tc1: _st_sel = st.selectbox("Start time (ET)", _slot_opts, key="to_st_time")
                    with tc2: _en_sel = st.selectbox("End time (ET)",   _slot_opts, key="to_en_time")
                    notes = st.text_input("Notes *")
                    _kronos_ok = True
                    if _needs_kronos:
                        st.markdown("""
                        <div style="background:#FEF3C7;border:1px solid #F59E0B;border-radius:6px;
                                    padding:10px 14px;margin:10px 0 4px">
                            <div style="font-size:12px;font-weight:700;color:#92400E">⚠️ Kronos submission required</div>
                            <div style="font-size:11px;color:#78350F;margin-top:3px">
                                This type of request must also be submitted in Kronos.
                                Please confirm it has been submitted before proceeding.
                            </div>
                        </div>""", unsafe_allow_html=True)
                        _kronos_ok = st.checkbox("Submitted in Kronos ✓")
                    if st.form_submit_button("Submit request", type="primary"):
                        if not notes.strip():
                            st.error("Notes are required.")
                        elif end < start:
                            st.error("End date must be on or after start date.")
                        elif _needs_kronos and not _kronos_ok:
                            st.error("Please confirm the Kronos submission is complete before continuing.")
                        else:
                            _st_time = "" if _st_sel == "(all day)" else _st_sel
                            _en_time = "" if _en_sel == "(all day)" else _en_sel
                            if _st_time and _en_time and TIME_SLOTS.index(_st_time) > TIME_SLOTS.index(_en_time):
                                st.error("Start time must be before end time.")
                            else:
                                ag_data = next((a for a in get_agents() if a["name"]==agent), {})
                                add_time_off_request(agent, ag_data.get("team_name",""), start, end, _admin_rtype, notes, _st_time, _en_time)
                                if get_setting("slack_notify_submissions", "") == "yes":
                                    send_slack_message(
                                        f"📋 *New time-off request* — {agent} ({ag_data.get('team_name','')}) "
                                        f"submitted a *{_admin_rtype}* request from {start} to {end}."
                                        + (f"\n> {notes}" if notes else "")
                                    )
                                st.toast(f"Request submitted for {agent}.", icon="✅")
                                st.rerun()


# ─── PAGE: ROSTER ─────────────────────────────────────────────────────────────

def page_roster():
    st.markdown('<div class="page-title">Roster</div>', unsafe_allow_html=True)

    teams = get_teams()
    team_names = [t["name"] for t in teams]
    team_colors_map = {t["name"]: t["color"] for t in teams}

    add_tab, view_tab, import_tab = st.tabs(["All agents", "Add agent", "📥 Import"])

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
                            e_sel = ag["employment_type"]
                            h = int(ag["weekly_hours"])
                            wd = ag["work_days"]
                            # Per-agent default activity override
                            act_opts = ["(use team default)"] + get_activity_names()
                            ag_def = ag.get("default_activity", "") or "(use team default)"
                            ag_def_idx = act_opts.index(ag_def) if ag_def in act_opts else 0
                            ag_default_act = st.selectbox(
                                "Default activity (overrides team)",
                                act_opts, index=ag_def_idx,
                                help="Leave as 'use team default' unless this agent needs a different base activity."
                            )
                            nt = st.text_input("Notes", ag.get("notes",""))
                            slack_id_input = st.text_input(
                                "Slack Member ID",
                                value=ag.get("slack_user_id") or "",
                                placeholder="U0A1B2C3D",
                                help="In Slack: click the agent's profile → ⋯ → Copy member ID. Used to send them DMs when their today's schedule changes."
                            )
                            cs, cd = st.columns(2)
                            with cs:
                                if st.form_submit_button("Save", use_container_width=True):
                                    ok, msg = upsert_agent(n, t_sel, e_sel, h, wd, nt, ag["id"])
                                    if ok:
                                        # Save default activity override + Slack ID
                                        conn2 = get_conn()
                                        conn2.execute(
                                            "UPDATE agents SET default_activity=?, slack_user_id=? WHERE id=?",
                                            ("" if ag_default_act == "(use team default)" else ag_default_act,
                                             slack_id_input.strip() or None,
                                             ag["id"])
                                        )
                                        conn2.commit(); conn2.close()
                                    st.toast(msg, icon="✅" if ok else "❌")
                                    if ok: st.rerun()
                            with cd:
                                if st.form_submit_button("🗑 Remove", use_container_width=True):
                                    delete_agent(ag["id"])
                                    st.toast(f"Removed {ag['name']}.", icon="🗑️")
                                    st.rerun()

                        # ── Shift hours editor — one Save button for all days ──
                        st.markdown(
                            '<div style="font-size:10px;font-weight:700;color:#689985;'
                            'text-transform:uppercase;letter-spacing:0.1em;margin:10px 0 4px;'
                            'font-family:\'DM Sans\',sans-serif">Shift hours</div>',
                            unsafe_allow_html=True
                        )
                        wh = get_agent_work_hours(ag["name"])
                        # Render each day row — checkbox + start + end + split toggle
                        for di, day in enumerate(DAYS):
                            cfg = wh.get(di, {"start_slot": "9:00 AM", "end_slot": "5:00 PM",
                                              "is_active": False, "split_start_slot": None, "split_end_slot": None})
                            _day_active = st.session_state.get(f"wh_act_{ag['id']}_{di}", cfg["is_active"])
                            dc1, dc2, dc3, dc4 = st.columns([1.0, 2.0, 2.0, 1.5])
                            with dc1:
                                st.checkbox(day[:3], value=cfg["is_active"],
                                            key=f"wh_act_{ag['id']}_{di}")
                            with dc2:
                                si = TIME_SLOTS.index(cfg["start_slot"]) if cfg["start_slot"] in TIME_SLOTS else TIME_SLOTS.index("9:00 AM")
                                st.selectbox("Start", TIME_SLOTS, index=si,
                                             key=f"wh_start_{ag['id']}_{di}",
                                             label_visibility="collapsed",
                                             disabled=not _day_active)
                            with dc3:
                                ei = TIME_SLOTS.index(cfg["end_slot"]) if cfg["end_slot"] in TIME_SLOTS else TIME_SLOTS.index("5:00 PM")
                                st.selectbox("End", TIME_SLOTS, index=ei,
                                             key=f"wh_end_{ag['id']}_{di}",
                                             label_visibility="collapsed",
                                             disabled=not _day_active)
                            with dc4:
                                _has_split = cfg.get("split_start_slot") is not None
                                st.checkbox("Split", value=_has_split,
                                            key=f"wh_split_{ag['id']}_{di}",
                                            disabled=not _day_active,
                                            help="Agent works a second shift segment on this day")
                            # Split segment row (visible when split is checked and day is active)
                            _split_on = st.session_state.get(f"wh_split_{ag['id']}_{di}", _has_split)
                            if _split_on and _day_active:
                                _, ds1, ds2 = st.columns([1.0, 2.0, 2.0])
                                _saved_sp_start = cfg.get("split_start_slot") or "1:00 PM"
                                _saved_sp_end   = cfg.get("split_end_slot")   or "5:00 PM"
                                with ds1:
                                    _ssi = TIME_SLOTS.index(_saved_sp_start) if _saved_sp_start in TIME_SLOTS else TIME_SLOTS.index("1:00 PM")
                                    st.selectbox("Split start", TIME_SLOTS, index=_ssi,
                                                 key=f"wh_split_start_{ag['id']}_{di}",
                                                 label_visibility="collapsed")
                                with ds2:
                                    _sei = TIME_SLOTS.index(_saved_sp_end) if _saved_sp_end in TIME_SLOTS else TIME_SLOTS.index("5:00 PM")
                                    st.selectbox("Split end", TIME_SLOTS, index=_sei,
                                                 key=f"wh_split_end_{ag['id']}_{di}",
                                                 label_visibility="collapsed")
                        # Single save button for all days
                        if st.button("Save shift hours", key=f"wh_save_all_{ag['id']}",
                                     use_container_width=True):
                            for di in range(len(DAYS)):
                                active_val  = st.session_state.get(f"wh_act_{ag['id']}_{di}", False)
                                start_val   = st.session_state.get(f"wh_start_{ag['id']}_{di}", "9:00 AM")
                                end_val     = st.session_state.get(f"wh_end_{ag['id']}_{di}",   "5:00 PM")
                                split_on    = st.session_state.get(f"wh_split_{ag['id']}_{di}", False)
                                split_start = st.session_state.get(f"wh_split_start_{ag['id']}_{di}", None) if split_on else None
                                split_end   = st.session_state.get(f"wh_split_end_{ag['id']}_{di}",   None) if split_on else None
                                save_agent_work_hours(ag["name"], di, start_val, end_val, active_val,
                                                      split_start, split_end)
                            st.toast("Shift hours saved.", icon="✅")

                        # ── Lunch slot ─────────────────────────────────────────
                        st.markdown(
                            '<div style="font-size:10px;font-weight:700;color:#689985;'
                            'text-transform:uppercase;letter-spacing:0.1em;margin:10px 0 4px;'
                            'font-family:\'DM Sans\',sans-serif">Lunch</div>',
                            unsafe_allow_html=True
                        )
                        _ag_rules_all, _ = get_coverage_rules()
                        _ag_lunch = _ag_rules_all.get(ag["name"], {})
                        _lunch_opts = ["None"] + TIME_SLOTS
                        _cur_slot = _ag_lunch.get("lunch_slot") or "None"
                        if _cur_slot not in _lunch_opts:
                            _cur_slot = "None"
                        _cur_dur = int(_ag_lunch.get("lunch_duration", 1))
                        _lc1, _lc2 = st.columns([3, 2])
                        with _lc1:
                            _new_slot = st.selectbox(
                                "Lunch start", _lunch_opts,
                                index=_lunch_opts.index(_cur_slot),
                                key=f"lunch_slot_{ag['id']}",
                                label_visibility="collapsed",
                            )
                        with _lc2:
                            _DUR_OPTS = [1, 2, 3, 4]
                            _DUR_LABELS = {1: "30 min", 2: "1 hr", 3: "1.5 hrs", 4: "2 hrs"}
                            _new_dur = st.selectbox(
                                "Duration", _DUR_OPTS,
                                index=_DUR_OPTS.index(_cur_dur) if _cur_dur in _DUR_OPTS else 0,
                                key=f"lunch_dur_{ag['id']}",
                                label_visibility="collapsed",
                                format_func=lambda x: _DUR_LABELS[x],
                            )
                        if st.button("Save lunch", key=f"lunch_save_{ag['id']}",
                                     use_container_width=True):
                            save_agent_lunch(
                                ag["name"],
                                None if _new_slot == "None" else _new_slot,
                                _new_dur,
                            )
                            st.toast("Lunch settings saved.", icon="✅")

                        # ── Per-day lunch overrides ──────────────────────────
                        import json as _json
                        _ovr_raw = _ag_lunch.get("lunch_overrides")
                        _ovr_dict = {}
                        if _ovr_raw:
                            try:
                                _ovr_dict = _json.loads(_ovr_raw)
                            except Exception:
                                _ovr_dict = {}

                        _agent_days = [d[:3] for d in DAYS]  # always show all 7 days

                        with st.expander("Day overrides"):
                            _new_ovr = {}
                            for _wd in _agent_days:
                                _oc1, _oc2, _oc3 = st.columns([1, 3, 2])
                                with _oc1:
                                    st.markdown(f"<div style='padding-top:6px;font-size:12px;font-weight:600'>{_wd}</div>", unsafe_allow_html=True)
                                _day_ovr_val = _ovr_dict.get(_wd, "DEFAULT_SENTINEL")
                                if _day_ovr_val == "DEFAULT_SENTINEL":
                                    _cur_time_choice = "Default"
                                elif _day_ovr_val is None:
                                    _cur_time_choice = "No lunch"
                                else:
                                    _cur_time_choice = _day_ovr_val.get("slot", "Default") if isinstance(_day_ovr_val, dict) else "Default"

                                _time_opts = ["Default", "No lunch"] + TIME_SLOTS
                                _ti = _time_opts.index(_cur_time_choice) if _cur_time_choice in _time_opts else 0
                                with _oc2:
                                    _sel_t = st.selectbox(
                                        _wd, _time_opts, index=_ti,
                                        key=f"lo_t_{ag['id']}_{_wd}",
                                        label_visibility="collapsed",
                                    )
                                with _oc3:
                                    if _sel_t not in ("Default", "No lunch"):
                                        _cur_dur_ovr = _day_ovr_val.get("duration", _cur_dur) if isinstance(_day_ovr_val, dict) else _cur_dur
                                        _di_ovr = _DUR_OPTS.index(_cur_dur_ovr) if _cur_dur_ovr in _DUR_OPTS else 0
                                        _sel_d = st.selectbox(
                                            "Dur", _DUR_OPTS, index=_di_ovr,
                                            key=f"lo_d_{ag['id']}_{_wd}",
                                            label_visibility="collapsed",
                                            format_func=lambda x: _DUR_LABELS[x],
                                        )
                                        _new_ovr[_wd] = {"slot": _sel_t, "duration": _sel_d}
                                    elif _sel_t == "No lunch":
                                        _new_ovr[_wd] = None
                                        st.markdown("<div style='padding-top:6px;color:#94A3B8;font-size:12px'>—</div>", unsafe_allow_html=True)
                                    else:
                                        st.markdown("<div style='padding-top:6px;color:#94A3B8;font-size:12px'>—</div>", unsafe_allow_html=True)

                            if st.button("Save day overrides", key=f"lo_save_{ag['id']}", use_container_width=True):
                                save_agent_lunch_overrides(ag["name"], _new_ovr)
                                st.toast("Day overrides saved.", icon="✅")

                        # ── Linked login account ────────────────────────────────
                        st.markdown(
                            '<div style="font-size:10px;font-weight:700;color:#689985;'
                            'text-transform:uppercase;letter-spacing:0.1em;margin:10px 0 4px;'
                            'font-family:\'DM Sans\',sans-serif">Linked account</div>',
                            unsafe_allow_html=True
                        )
                        _all_users   = list_users()
                        _user_opts   = ["(none)"] + [f"{u['display_name']} ({u['username']})" for u in _all_users]
                        _user_ids    = [None] + [u["id"] for u in _all_users]
                        _cur_link    = ag.get("linked_user_id")
                        _link_idx    = _user_ids.index(_cur_link) if _cur_link in _user_ids else 0
                        _new_link_lbl = st.selectbox(
                            "Login account", _user_opts,
                            index=_link_idx,
                            key=f"linked_user_{ag['id']}",
                            label_visibility="collapsed",
                            help="Connect this roster profile to a login account so the agent sees their own schedule when they log in.",
                        )
                        _new_link_id = _user_ids[_user_opts.index(_new_link_lbl)]
                        if st.button("Save account link", key=f"link_save_{ag['id']}",
                                     use_container_width=True):
                            _lc = get_conn()
                            _lc.execute("UPDATE agents SET linked_user_id=? WHERE id=?",
                                        (_new_link_id, ag["id"]))
                            _lc.commit(); _lc.close()
                            st.toast("Account link saved.", icon="✅")

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

    with import_tab:
        st.subheader("Bulk import agents")

        # Template download
        _IMPORT_COLS = ["Name","Team","Employment Type","Weekly Hours","Work Days","Default Activity","Slack Member ID","Notes"]
        _template_df = pd.DataFrame([
            ["Jane Smith",  "Support", "FT", 40, "Mon,Tue,Wed,Thu,Fri", "Calls",  "",           ""],
            ["John Doe",    "CA",      "PT", 32, "Mon,Tue,Wed,Thu,Fri", "Chat",   "U0A1B2C3D4", "Bilingual"],
        ], columns=_IMPORT_COLS)
        _csv_bytes = _template_df.to_csv(index=False).encode()
        st.download_button("⬇ Download column template", _csv_bytes, "roster_import_template.csv", "text/csv", key="roster_dl_template")

        st.markdown("""
        **Required columns:** Name, Team  |  **Optional:** Employment Type (FT/PT), Weekly Hours, Work Days, Default Activity, Slack Member ID, Notes
        Column names are case-insensitive. Agents whose names already exist will be skipped.
        """)

        # ── Source selector ────────────────────────────────────────────────────
        _src = st.radio("Import from", ["Google Sheets", "File (CSV / Excel)"],
                        horizontal=True, key="roster_import_src",
                        label_visibility="collapsed")

        # imp_df is stored in session state so it survives the rerun triggered
        # by clicking the Import button (at that point _gs_load is False again).
        imp_df = st.session_state.get("_roster_import_df")

        if _src == "Google Sheets":
            st.markdown(
                "Paste the Google Sheets URL below. The sheet must be shared as **Anyone with the link can view**."
            )
            _gs_col, _gs_btn_col = st.columns([5, 1])
            with _gs_col:
                _gs_url = st.text_input("Google Sheets URL", placeholder="https://docs.google.com/spreadsheets/d/…",
                                        label_visibility="collapsed", key="roster_gs_url")
            with _gs_btn_col:
                _gs_load = st.button("Load", key="roster_gs_load", use_container_width=True)

            if _gs_load and _gs_url.strip():
                import re, urllib.request, io
                _match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", _gs_url)
                if not _match:
                    st.error("Couldn't find a Sheet ID in that URL — make sure you're pasting the full Google Sheets link.")
                else:
                    _sheet_id = _match.group(1)
                    _gid_match = re.search(r"[#&?]gid=(\d+)", _gs_url)
                    _gid_param = f"&gid={_gid_match.group(1)}" if _gid_match else ""
                    _export_url = f"https://docs.google.com/spreadsheets/d/{_sheet_id}/export?format=csv{_gid_param}"
                    try:
                        import ssl as _ssl
                        _ctx = _ssl.create_default_context()
                        _ctx.check_hostname = False
                        _ctx.verify_mode = _ssl.CERT_NONE
                        with urllib.request.urlopen(_export_url, timeout=10, context=_ctx) as _resp:
                            _csv_data = _resp.read().decode("utf-8")
                        imp_df = pd.read_csv(io.StringIO(_csv_data))
                        st.session_state["_roster_import_df"] = imp_df  # persist across reruns
                        st.success(f"Loaded {len(imp_df)} row(s) from Google Sheets.")
                    except urllib.error.HTTPError as _he:
                        if _he.code == 401:
                            st.error("Access denied — make sure the sheet is shared as 'Anyone with the link can view'.")
                        else:
                            st.error(f"Could not load sheet (HTTP {_he.code}). Check the URL and sharing settings.")
                    except Exception as _ge:
                        st.error(f"Could not load sheet: {_ge}")

        else:
            # Clear any cached Google Sheets data when switching to file mode
            st.session_state.pop("_roster_import_df", None)
            imp_df = None
            uploaded_file = st.file_uploader("Choose CSV or Excel file", type=["csv","xlsx"], key="roster_import_uploader")
            if uploaded_file:
                try:
                    if uploaded_file.name.lower().endswith(".csv"):
                        imp_df = pd.read_csv(uploaded_file)
                    else:
                        imp_df = pd.read_excel(uploaded_file)
                except Exception as _e:
                    st.error(f"Could not read file: {_e}")

        if imp_df is not None and not imp_df.empty:
            # ── Normalize column names ─────────────────────────────────────────
            _col_aliases = {
                "name": "name", "full name": "name",
                "team": "team_name", "team name": "team_name", "team_name": "team_name",
                "employment type": "employment_type", "type": "employment_type",
                "employment_type": "employment_type", "emp type": "employment_type",
                "weekly hours": "weekly_hours", "hours": "weekly_hours",
                "weekly_hours": "weekly_hours", "hrs": "weekly_hours",
                "work days": "work_days", "work_days": "work_days", "days": "work_days",
                "default activity": "default_activity", "default_activity": "default_activity",
                "activity": "default_activity",
                "slack member id": "slack_user_id", "slack id": "slack_user_id",
                "slack_user_id": "slack_user_id", "slack member": "slack_user_id",
                "notes": "notes", "note": "notes",
            }
            imp_df.columns = [_col_aliases.get(c.lower().strip(), c.lower().strip()) for c in imp_df.columns]

            # ── Check required columns ─────────────────────────────────────────
            if "name" not in imp_df.columns:
                st.error('File must include a "Name" column.')
            elif "team_name" not in imp_df.columns:
                st.error('File must include a "Team" column.')
            else:
                existing_agent_names = {a["name"].strip().lower() for a in get_agents()}
                valid_team_set = set(team_names)

                def _clean(val, default=""):
                    s = str(val).strip()
                    return default if s in ("", "nan", "NaN", "None") else s

                rows_display = []
                rows_to_import = []

                for _, row in imp_df.iterrows():
                    name_val    = _clean(row.get("name", ""))
                    team_val    = _clean(row.get("team_name", ""))
                    emp_val     = _clean(row.get("employment_type", ""), "FT").upper()
                    work_days_v = _clean(row.get("work_days", ""), "Mon,Tue,Wed,Thu,Fri")
                    def_act_v   = _clean(row.get("default_activity", ""))
                    slack_v     = _clean(row.get("slack_user_id", ""))
                    notes_v     = _clean(row.get("notes", ""))
                    try:
                        hrs_val = int(float(_clean(row.get("weekly_hours", "40"), "40")))
                    except (ValueError, TypeError):
                        hrs_val = 40

                    errors = []
                    warnings = []

                    if not name_val:
                        errors.append("Name is empty")
                    elif name_val.lower() in existing_agent_names:
                        warnings.append("Already exists — will skip")

                    if not team_val:
                        errors.append("Team is empty")
                    elif team_val not in valid_team_set:
                        errors.append(f'Team "{team_val}" not found')

                    if emp_val not in ("FT", "PT"):
                        warnings.append(f'Unknown type "{emp_val}" -> defaulting to FT')
                        emp_val = "FT"

                    hrs_val = max(1, min(hrs_val, 40))

                    is_dup   = bool(warnings and "Already exists" in warnings[0])
                    is_error = bool(errors)
                    status   = "error" if is_error else ("skip" if is_dup else "ok")
                    issue_txt = "; ".join(errors + warnings) if (errors or warnings) else "Ready to import"

                    rows_display.append({
                        "Name": name_val, "Team": team_val, "Type": emp_val,
                        "Hours": hrs_val, "Work Days": work_days_v,
                        "Status": status, "Issues": issue_txt,
                    })

                    if status == "ok":
                        rows_to_import.append({
                            "name": name_val, "team_name": team_val,
                            "employment_type": emp_val, "weekly_hours": hrs_val,
                            "work_days": work_days_v, "default_activity": def_act_v,
                            "slack_user_id": slack_v or None, "notes": notes_v,
                        })

                _cnt_ok   = sum(1 for r in rows_display if r["Status"] == "ok")
                _cnt_skip = sum(1 for r in rows_display if r["Status"] == "skip")
                _cnt_err  = sum(1 for r in rows_display if r["Status"] == "error")

                st.markdown(
                    f"**{len(rows_display)} row(s) found** — "
                    f"<span style='color:#15803D;font-weight:600'>{_cnt_ok} ready</span>, "
                    f"<span style='color:#92400E;font-weight:600'>{_cnt_skip} skipping (duplicate)</span>, "
                    f"<span style='color:#DC2626;font-weight:600'>{_cnt_err} error(s)</span>",
                    unsafe_allow_html=True
                )

                # ── Preview table ──────────────────────────────────────────────
                _rows_html = ""
                for r in rows_display:
                    if r["Status"] == "ok":
                        bg    = "#F0FDF4"
                        badge = '<span style="color:#15803D;font-weight:700">✓ Ready</span>'
                    elif r["Status"] == "skip":
                        bg    = "#FFFBEB"
                        badge = '<span style="color:#92400E;font-weight:700">⚠ Skip</span>'
                    else:
                        bg    = "#FEF2F2"
                        badge = '<span style="color:#DC2626;font-weight:700">✗ Error</span>'
                    _rows_html += (
                        f'<tr style="background:{bg}">'
                        f'<td style="padding:5px 8px">{r["Name"]}</td>'
                        f'<td style="padding:5px 8px">{r["Team"]}</td>'
                        f'<td style="padding:5px 8px">{r["Type"]}</td>'
                        f'<td style="padding:5px 8px;text-align:center">{r["Hours"]}</td>'
                        f'<td style="padding:5px 8px">{r["Work Days"]}</td>'
                        f'<td style="padding:5px 8px">{badge}</td>'
                        f'<td style="padding:5px 8px;color:#64748B;font-size:10px">{r["Issues"]}</td>'
                        f'</tr>'
                    )
                _th = lambda txt: f'<th style="padding:6px 8px;text-align:left;border-bottom:2px solid #E2E8F0;white-space:nowrap">{txt}</th>'
                st.markdown(
                    f'<div style="overflow-x:auto;border:1px solid #E2E8F0;border-radius:6px;margin:12px 0">'
                    f'<table style="width:100%;border-collapse:collapse;font-size:12px">'
                    f'<thead><tr style="background:#F8FAFC">'
                    f'{_th("Name")}{_th("Team")}{_th("Type")}{_th("Hrs")}{_th("Work Days")}{_th("Status")}{_th("Notes")}'
                    f'</tr></thead>'
                    f'<tbody>{_rows_html}</tbody>'
                    f'</table></div>',
                    unsafe_allow_html=True
                )

                # ── Confirm button ─────────────────────────────────────────────
                if _cnt_ok > 0:
                    if st.button(
                        f"✅ Import {_cnt_ok} agent{'s' if _cnt_ok != 1 else ''}",
                        type="primary",
                        key="roster_import_confirm",
                    ):
                        _imported = 0
                        _failed   = []
                        _conn_imp = get_conn()
                        for r in rows_to_import:
                            try:
                                _conn_imp.execute(
                                    """INSERT INTO agents
                                       (name,team_name,employment_type,weekly_hours,
                                        work_days,default_activity,slack_user_id,notes)
                                       VALUES (?,?,?,?,?,?,?,?)""",
                                    (r["name"], r["team_name"], r["employment_type"],
                                     r["weekly_hours"], r["work_days"],
                                     r["default_activity"], r["slack_user_id"], r["notes"]),
                                )
                                _imported += 1
                            except Exception:
                                _failed.append(r["name"])
                        _conn_imp.commit()
                        _conn_imp.close()
                        if _failed:
                            st.warning(f"Skipped (error saving): {', '.join(_failed)}")
                        if _imported:
                            st.session_state.pop("_roster_import_df", None)  # clear cached sheet
                            st.success(f"✅ Imported {_imported} agent{'s' if _imported != 1 else ''} — reloading roster…")
                            st_components.html(
                                "<script>setTimeout(()=>window.parent.location.reload(),1200);</script>",
                                height=0
                            )
                elif _cnt_err == 0 and _cnt_skip > 0:
                    st.info("All rows are duplicates — nothing new to import.")


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
                        # Default activity for base schedule generation
                        act_options = ["(none)"] + get_activity_names()
                        cur_default = team.get("default_activity", "") or "(none)"
                        def_idx = act_options.index(cur_default) if cur_default in act_options else 0
                        t_default_act = st.selectbox(
                            "Default activity (base schedule)",
                            act_options,
                            index=def_idx,
                            help="When generating a base schedule, agents on this team will be filled with this activity during their configured shift hours."
                        )
                        cs, cd = st.columns(2)
                        with cs:
                            if st.form_submit_button("Save", use_container_width=True):
                                ok, msg = upsert_team(tn, tc, td, team["id"])
                                if ok:
                                    set_team_default_activity(
                                        tn,
                                        "" if t_default_act == "(none)" else t_default_act
                                    )
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
                    teams_with_agents = [t for t in teams
                                         if any(a["team_name"] == t["name"] for a in agents_info)]
                    _tmpl_user = current_user()
                    _ordered_teams, _tl_order_key = resolve_team_order(_tmpl_user, teams_with_agents)
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
                    _tmpl_agent_cols = list(df.columns)
                    df[" "] = ""  # spacer so scrollbar doesn't overlap last agent
                    col_cfg = {
                        col: st.column_config.SelectboxColumn(
                            label=col.split()[0], options=act_names, default=".", width="small"
                        )
                        for col in _tmpl_agent_cols
                    }
                    col_cfg[" "] = st.column_config.TextColumn(" ", disabled=True, width="small")
                    def _cell_style_t(val):
                        if val == "." or not val or val not in act_colors:
                            return "color:#CBD5E1"
                        bg, _ = act_colors[val]
                        return f"color:{bg};font-weight:700"
                    _tmpl_ed_col, _ = st.columns([5, 1])
                    with _tmpl_ed_col:
                        edited = st.data_editor(
                            df.style.map(_cell_style_t),
                            column_config=col_cfg, use_container_width=True,
                            key=f"tmpl_edit_{template_id}_{di}_{team['name']}",
                            height=20 * len(TIME_SLOTS) + 40,
                        )
                    if st.button(f"💾 Save {team['name']}",
                                 key=f"tmpl_save_{template_id}_{di}_{team['name']}",
                                 use_container_width=True, type="primary"):
                        save_template_df(template_id, di, edited[_tmpl_agent_cols])
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

    # ── Slack integration ──────────────────────────────────────────────────────
    st.divider()
    st.markdown('<div style="font-size:13px;font-weight:600;color:#0F172A;margin-bottom:2px">Slack notifications</div>', unsafe_allow_html=True)
    st.caption("Configure Slack to send channel alerts and direct messages to agents.")

    _cur_webhook   = get_setting("slack_webhook_url",        "")
    _cur_token     = get_setting("slack_bot_token",           "")
    _cur_sub_notif = get_setting("slack_notify_submissions",  "yes" if _cur_webhook else "")
    _cur_dm_notif  = get_setting("slack_dm_schedule_updates", "")

    # ── Incoming Webhook (channel alerts) ────────────────────────────────────
    st.markdown('<div style="font-size:12px;font-weight:600;color:#475569;margin:10px 0 4px">Channel alerts (Incoming Webhook)</div>', unsafe_allow_html=True)
    st.caption("Posts to a Slack channel. No bot token needed. Create one at api.slack.com → Your App → Incoming Webhooks.")

    with st.form("slack_webhook_form"):
        webhook_input = st.text_input(
            "Webhook URL",
            value=_cur_webhook,
            placeholder="https://hooks.slack.com/services/...",
        )
        _sub_notify = st.checkbox(
            "Notify channel on new time-off submission",
            value=(_cur_sub_notif == "yes"),
            help="In addition to the existing approval/denial alerts."
        )
        sw1, sw2 = st.columns([2, 1])
        with sw1:
            if st.form_submit_button("Save", type="primary"):
                url = webhook_input.strip()
                if url and not url.startswith("https://hooks.slack.com/"):
                    st.error("Must start with https://hooks.slack.com/")
                else:
                    set_setting("slack_webhook_url", url)
                    set_setting("slack_notify_submissions", "yes" if _sub_notify else "no")
                    st.success("Saved." if url else "Webhook cleared.")
        with sw2:
            if st.form_submit_button("Send test"):
                set_setting("slack_webhook_url", webhook_input.strip())
                send_slack_message("👋 Test from CX Scheduler — channel alerts are working!")
                st.success("Sent!")

    if _cur_webhook:
        st.markdown('<div style="font-size:11px;color:#689985;margin-top:2px">✅ Webhook configured</div>', unsafe_allow_html=True)

    # ── Bot Token (DMs) ───────────────────────────────────────────────────────
    st.markdown('<div style="font-size:12px;font-weight:600;color:#475569;margin:14px 0 4px">Agent DMs (Bot Token)</div>', unsafe_allow_html=True)
    st.caption(
        "Sends a Slack DM to individual agents when their **today's** schedule is updated. "
        "Requires a Slack Bot Token with `chat:write` and `im:write` scopes. "
        "Each agent also needs their Slack Member ID set in their Roster profile."
    )

    with st.form("slack_dm_form"):
        token_input = st.text_input(
            "Bot Token",
            value=_cur_token,
            placeholder="xoxb-...",
            type="password",
            help="Create a Slack app at api.slack.com → OAuth & Permissions → Bot Token Scopes: chat:write, im:write, users:read"
        )
        _dm_sched = st.checkbox(
            "DM agents when their today's schedule is updated",
            value=(_cur_dm_notif == "yes"),
        )
        sd1, sd2 = st.columns([2, 1])
        with sd1:
            if st.form_submit_button("Save", type="primary"):
                set_setting("slack_bot_token", token_input.strip())
                set_setting("slack_dm_schedule_updates", "yes" if _dm_sched else "no")
                st.success("Saved.")
        with sd2:
            if st.form_submit_button("Send test DM"):
                _test_user = current_user()
                if not token_input.strip():
                    st.error("Enter a bot token first.")
                else:
                    set_setting("slack_bot_token", token_input.strip())
                    set_setting("slack_dm_schedule_updates", "yes" if _dm_sched else "no")
                    # Try to DM the current admin as a sanity check
                    _conn_t = get_conn()
                    _test_row = _conn_t.execute(
                        "SELECT slack_user_id FROM agents WHERE name=?",
                        (_test_user["display_name"] if _test_user else "",)
                    ).fetchone()
                    _conn_t.close()
                    _sid = _test_row["slack_user_id"] if _test_row else None
                    if _sid:
                        send_slack_dm(_sid, "👋 Test DM from CX Scheduler — agent DMs are working!")
                        st.success("Test DM sent to your Slack!")
                    else:
                        st.warning("Bot token saved. To test, add your own Slack Member ID in the Roster first.")

    if _cur_token:
        st.markdown('<div style="font-size:11px;color:#689985;margin-top:2px">✅ Bot token configured</div>', unsafe_allow_html=True)



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
    all_req     = get_time_off_requests()
    today       = datetime.date.today()
    monday      = today - datetime.timedelta(days=today.weekday())
    week_end    = monday + datetime.timedelta(days=6)

    # ── Top metrics ───────────────────────────────────────────────────────────
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
    with c4: metric("Pending approvals", len([r for r in all_req if r["status"] == "Pending"]))

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
            _df_req = pd.DataFrame(all_req)
            by_type = _df_req.groupby("type").size().reset_index(name="count").sort_values("count", ascending=False)
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

    # ── Schedule Coverage ─────────────────────────────────────────────────────
    st.markdown('<div style="height:24px"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div style="font-size:14px;font-weight:700;color:#0F172A;margin-bottom:10px">'
        'Schedule Coverage</div>', unsafe_allow_html=True)

    # Build 8-week options starting from this Monday
    _ws_list   = [str(monday + datetime.timedelta(weeks=i)) for i in range(8)]
    _team_opts = ["All Teams"] + [t["name"] for t in teams]

    def _ws_label(ws):
        d = datetime.date.fromisoformat(ws)
        end = d + datetime.timedelta(days=4)
        suffix = " · this week" if ws == str(monday) else ""
        return f"{d.strftime('%-m/%-d')} – {end.strftime('%-m/%-d')}{suffix}"

    _week_opts = ["All Upcoming"] + [_ws_label(ws) for ws in _ws_list]

    # Default week selector to "this week" on first load
    if "rpt_wk_sel" not in st.session_state:
        st.session_state["rpt_wk_sel"] = _week_opts[1]  # first real week = this week

    _f1, _f2, _fspc = st.columns([2.2, 2, 4])
    with _f1:
        _sel_wk = st.selectbox("Week", _week_opts, key="rpt_wk_sel")
    with _f2:
        _sel_tm = st.selectbox("Team", _team_opts, key="rpt_tm_sel")

    # Agents for the filter
    if _sel_tm == "All Teams":
        _rpt_agents = agents
    else:
        _rpt_agents = [a for a in agents if a["team_name"] == _sel_tm]
    _rpt_names = [a["name"] for a in _rpt_agents]

    _TIME_OFF_ACTS = {"PTO", "VTO", "Sick", "Holiday", "FMLA", "Bereavement"}
    # Each schedule slot = 30 minutes
    _SLOT_HRS = 0.5
    # Named activities shown as individual columns in coverage tables
    _NAMED_ACTS = ["Chat", "Phones", "Support", "CA-Remote", "CA-Studio", "GW", "Retail", "Design"]

    def _week_summary(ws, names):
        """Returns per-day hour totals summed across all agents for Mon–Fri.
        2 agents × 5 hrs Support = 10 hrs Support."""
        if not names:
            return {}
        conn = get_conn()
        ph = ','.join('?' * len(names))
        rows = conn.execute(
            f"SELECT day_index, agent_name, activity FROM schedule_cells "
            f"WHERE week_start=? AND day_index < 5 "
            f"AND agent_name IN ({ph}) AND activity != '.'",
            [ws] + names
        ).fetchall()
        conn.close()
        from collections import defaultdict as _dd
        day_slots = _dd(lambda: _dd(int))   # di -> activity -> slot count
        day_pto   = _dd(set)                # di -> agent names on time-off
        for r in rows:
            day_slots[r["day_index"]][r["activity"]] += 1
            if r["activity"] in _TIME_OFF_ACTS:
                day_pto[r["day_index"]].add(r["agent_name"])
        result = {}
        for di in range(5):
            acts = day_slots.get(di, {})
            def _h(key): return round(acts.get(key, 0) * _SLOT_HRS, 1)
            named_hrs  = {act: _h(act) for act in _NAMED_ACTS}
            pto_h      = round(sum(v for k, v in acts.items() if k in _TIME_OFF_ACTS) * _SLOT_HRS, 1)
            named_sum  = sum(named_hrs.values())
            other_h    = round(max(0, sum(acts.values()) * _SLOT_HRS - named_sum - pto_h), 1)
            total_h    = round(named_sum + other_h, 1)
            result[di] = {
                **{f"{act}_hrs": v for act, v in named_hrs.items()},
                "other_hrs":  other_h,
                "pto_agents": len(day_pto.get(di, set())),
                "total_hrs":  total_h,
            }
        return result

    def _fmt_h(h):
        return f"{h:.1f}h" if h else "—"

    # Resolve selected week string for the time-off section
    _resolved_ws = None
    if _sel_wk != "All Upcoming":
        _ws_idx      = _week_opts.index(_sel_wk) - 1
        _resolved_ws = _ws_list[_ws_idx]

    if not _rpt_names:
        st.caption("No agents in the selected team.")

    elif _sel_wk == "All Upcoming":
        # ── Multi-week hours table ────────────────────────────────────────────
        _rows = []
        for ws in _ws_list:
            summ = _week_summary(ws, _rpt_names)
            row = {"Week": _ws_label(ws)}
            for di, day in enumerate(DAYS[:5]):
                d = summ.get(di, {})
                row[day[:3]] = _fmt_h(d.get("total_hrs", 0))
            _rows.append(row)
        st.dataframe(pd.DataFrame(_rows).set_index("Week"), use_container_width=True)
        st.caption("Each cell = total scheduled hours across all matching agents for that day (excludes time-off slots).")

    else:
        # ── Specific week: hours by activity per day ──────────────────────────
        _ws      = _resolved_ws
        _ws_date = datetime.date.fromisoformat(_ws)
        summ     = _week_summary(_ws, _rpt_names)

        _cov_rows = []
        for di, day in enumerate(DAYS[:5]):
            _date_str = (_ws_date + datetime.timedelta(days=di)).strftime('%-m/%-d')
            d = summ.get(di, {})
            row = {"Day": f"{day} {_date_str}"}
            for _act in _NAMED_ACTS:
                row[_act] = _fmt_h(d.get(f"{_act}_hrs", 0))
            row["Other"]   = _fmt_h(d.get("other_hrs", 0))
            row["Total"]   = _fmt_h(d.get("total_hrs", 0))
            row["🌴 Out"]  = d.get("pto_agents", 0) or "—"
            _cov_rows.append(row)
        st.dataframe(pd.DataFrame(_cov_rows).set_index("Day"), use_container_width=True)

    # ── Upcoming Time Off ─────────────────────────────────────────────────────
    st.markdown('<div style="height:24px"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div style="font-size:14px;font-weight:700;color:#0F172A;margin-bottom:8px">'
        'Upcoming Time Off</div>', unsafe_allow_html=True)

    if _sel_wk == "All Upcoming":
        # All future approved/pending requests from today forward
        _pto_reqs = [
            r for r in all_req
            if r["status"] in ("Approved", "Pending")
            and datetime.date.fromisoformat(r["end_date"]) >= today
            and (_sel_tm == "All Teams" or r["team_name"] == _sel_tm)
        ]
    else:
        # Requests overlapping the selected week (Mon–Fri)
        _ws_date2 = datetime.date.fromisoformat(_resolved_ws)
        _ws_end2  = _ws_date2 + datetime.timedelta(days=4)
        _pto_reqs = [
            r for r in all_req
            if r["status"] in ("Approved", "Pending")
            and datetime.date.fromisoformat(r["start_date"]) <= _ws_end2
            and datetime.date.fromisoformat(r["end_date"])   >= _ws_date2
            and (_sel_tm == "All Teams" or r["team_name"] == _sel_tm)
        ]

    _pto_reqs = sorted(_pto_reqs, key=lambda r: r["start_date"])

    if _pto_reqs:
        _df_pto = pd.DataFrame([{
            "Agent":  r["agent_name"],
            "Team":   r["team_name"],
            "Type":   r["type"],
            "Start":  r["start_date"],
            "End":    r["end_date"],
            "Status": r["status"],
        } for r in _pto_reqs])
        st.dataframe(_df_pto, use_container_width=True, hide_index=True)
    else:
        st.caption("No upcoming time off for this selection.")


# ─── PAGE: PROFILE ────────────────────────────────────────────────────────────

def page_profile():
    user = current_user()
    if not user:
        st.error("Not logged in.")
        return

    FONT = "'DM Sans','Apercu Pro',Helvetica,Arial,sans-serif"
    role_colors = {"admin": "#EEE171", "editor": "#89AC9E", "viewer": "#979797"}
    rc = role_colors.get(user["role"], "#94A3B8")
    initials = "".join(p[0] for p in user["display_name"].split()[:2]).upper()

    st.markdown('<div class="page-title">My Profile</div>', unsafe_allow_html=True)

    # ── User info card ────────────────────────────────────────────────────────
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:16px;padding:20px 24px;
                background:white;border:1px solid #E2E8F0;border-radius:10px;
                margin-bottom:24px;font-family:{FONT}">
        <div style="width:52px;height:52px;border-radius:50%;background:{rc}22;
                    color:{rc};font-size:18px;font-weight:700;display:flex;
                    align-items:center;justify-content:center;flex-shrink:0">
            {initials}
        </div>
        <div>
            <div style="font-size:17px;font-weight:700;color:#0F172A;line-height:1.2">
                {user["display_name"]}
            </div>
            <div style="font-size:12px;color:{rc};font-weight:600;
                        text-transform:capitalize;margin-top:3px">
                {user["role"]}
            </div>
            <div style="font-size:12px;color:#94A3B8;margin-top:2px">
                @{user["username"]}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Change password form ──────────────────────────────────────────────────
    st.markdown(
        f'<div style="font-size:14px;font-weight:700;color:#0F172A;margin-bottom:10px;'
        f'font-family:{FONT}">Change Password</div>', unsafe_allow_html=True)

    with st.form("change_pw_form", clear_on_submit=True):
        current_pw = st.text_input("Current password", type="password")
        new_pw     = st.text_input("New password",     type="password")
        confirm_pw = st.text_input("Confirm new password", type="password")
        submitted  = st.form_submit_button("Update Password", type="primary")

    if submitted:
        if not current_pw or not new_pw or not confirm_pw:
            st.error("All three fields are required.")
        elif len(new_pw) < 6:
            st.error("New password must be at least 6 characters.")
        elif new_pw != confirm_pw:
            st.error("New passwords don't match.")
        else:
            # Fetch fresh hash from DB to verify current password
            _db_user = get_user_by_id(user["id"])
            if not _db_user or not _verify_pw(current_pw, _db_user["password_hash"]):
                st.error("Current password is incorrect.")
            else:
                reset_password(user["id"], new_pw.strip())
                st.success("Password updated successfully!")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    init_db()
    inject_css()

    # ── Login gate ─────────────────────────────────────────────────────────────
    if not current_user():
        show_login()
        return   # show_login calls st.stop() but return is here for clarity

    page = sidebar()
    user = current_user()

    # ── Sidebar hide/show ─────────────────────────────────────────────────────
    if st.session_state.get("_cx_sidebar_hidden"):
        st.markdown("""
        <style>
        section[data-testid="stSidebar"],
        [data-testid="collapsedControl"] {
            display: none !important;
            width: 0 !important; min-width: 0 !important;
        }
        </style>""", unsafe_allow_html=True)

    # Auto-close profile when the user clicks a different nav item
    _pnk = "_cx_prof_prev_nav"
    if st.session_state.get(_pnk) != page and st.session_state.get("_cx_profile_open"):
        st.session_state["_cx_profile_open"] = False
    st.session_state[_pnk] = page

    # ── Header: ☰ show-menu (when hidden) + profile icon ─────────────────────
    _hdr_l, _hdr_r = st.columns([11, 1])
    with _hdr_l:
        if st.session_state.get("_cx_sidebar_hidden"):
            if st.button("☰", key="show_sidebar_btn", help="Show navigation menu"):
                st.session_state["_cx_sidebar_hidden"] = False
                st.rerun()
    with _hdr_r:
        if user:
            _initials = "".join(p[0] for p in user["display_name"].split()[:2]).upper()
            _prof_open = st.session_state.get("_cx_profile_open", False)
            if st.button(
                _initials,
                key="profile_icon_btn",
                help="My Profile · Change Password",
                type="primary" if _prof_open else "secondary",
            ):
                st.session_state["_cx_profile_open"] = not _prof_open
                st.rerun()

    if st.session_state.get("_cx_profile_open"):
        page_profile()
        return

    page_map = {
        "Schedule":   page_schedule,
        "Time Off":   page_timeoff,
        "Agent View": page_agent_view,
        "Roster":     page_roster,
        "Teams":      page_teams,
        "Templates":  page_templates,
        "Users":      page_users,
        "Settings":   page_settings,
        "Reports":    page_reports,
    }
    fn = page_map.get(page)
    if fn:
        fn()

if __name__ == "__main__":
    main()
