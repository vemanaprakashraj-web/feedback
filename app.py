"""Aditya University - Faculty Feedback System.

Roles
-----
Student : logs in with roll number, answers the feedback form published for their
          year / semester / branch, and can add ONE optional statement at the end.
Teacher : logs in with employee code + password, sees rating breakdown per
          question and the suggestions given by students (with roll numbers once
          the admin has published the box to teachers).
Admin   : builds feedback forms (year / sem / branch, title, instructions,
          questions + options), publishes them, tracks which roll numbers have
          or have not submitted, and manages settings.

Database: SQLite by default, Supabase/Postgres when DATABASE_URL is set.
"""
import os
import re
import io
import csv
import glob
import json
import secrets
import hmac
import hashlib
import sqlite3
from collections import Counter, defaultdict
from functools import wraps
from datetime import datetime

from flask import (Flask, request, session, redirect, url_for, render_template,
                   Response, flash, g, abort)
import openpyxl

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()   # Supabase Postgres when set
PG = bool(DATABASE_URL)
if os.environ.get('VERCEL') and not PG:
    raise RuntimeError('Set DATABASE_URL (Supabase) in Vercel env vars. SQLite cannot persist data on Vercel.')
if PG:
    import psycopg2
    import psycopg2.extras

DB = os.path.join(BASE, 'feedback.db')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
DEFAULT_TEACHER_PASSWORD = os.environ.get('TEACHER_DEFAULT_PASSWORD', 'aditya@123')

RATINGS = ['Excellent', 'Very Good', 'Good', 'Bad', 'Very Bad']
LABEL = {5: 'Excellent', 4: 'Very Good', 3: 'Good', 2: 'Bad', 1: 'Very Bad'}
WEIGHTS = {'Excellent': 5, 'Very Good': 4, 'Good': 3, 'Bad': 2, 'Very Bad': 1}
ROMAN = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7, 'VIII': 8}
YEARS = ['1', '2', '3', '4']
YEAR_LABEL = {1: '1st Year', 2: '2nd Year', 3: '3rd Year', 4: '4th Year'}

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-me')
app.config.update(SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=20 * 1024 * 1024)
# pick up template/CSS edits without restarting (turn off with TEMPLATES_AUTO_RELOAD=0)
app.config['TEMPLATES_AUTO_RELOAD'] = os.environ.get('TEMPLATES_AUTO_RELOAD', '1') == '1'


# --------------------------------------------------------------------------- db
def raw():
    return psycopg2.connect(DATABASE_URL) if PG else sqlite3.connect(DB)


def conn():
    if 'c' not in g:
        g.c = raw()
    return g.c


def cur_of(c):
    if PG:
        return c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.row_factory = sqlite3.Row
    return c.cursor()


def fix(sql):
    return sql.replace('?', '%s') if PG else sql


def rows(sql, args=()):
    c = conn()
    cur = cur_of(c)
    try:
        cur.execute(fix(sql), args)
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        c.rollback()
        raise


def one(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


def run(sql, args=()):
    c = conn()
    cur = cur_of(c)
    try:
        cur.execute(fix(sql), args)
        c.commit()
        return cur.lastrowid
    except Exception:
        c.rollback()
        raise


def execute_many(sql, seq):
    c = conn()
    cur = c.cursor()
    try:
        if PG:
            psycopg2.extras.execute_values(cur, sql, seq)
        else:
            cur.executemany(sql, seq)
        c.commit()
    except Exception:
        c.rollback()
        raise


@app.teardown_appcontext
def close_db(_):
    c = g.pop('c', None)
    if c:
        c.close()


def setting(k, d=None):
    r = one('SELECT v FROM settings WHERE k=?', (k,))
    return r['v'] if r else d


def set_setting(k, v):
    run('INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v', (k, v))


def hash_pw(pw, salt='aditya'):
    return hashlib.sha256((salt + ':' + pw).encode()).hexdigest()


# ------------------------------------------------------------------------- schema
def init_db():
    pk = 'BIGSERIAL PRIMARY KEY' if PG else 'INTEGER PRIMARY KEY'
    c = raw()
    cur = c.cursor()
    for s in [
        f"CREATE TABLE IF NOT EXISTS assignments(id {pk}, roll TEXT NOT NULL, semester TEXT, dept TEXT,"
        f" course_code TEXT, course_name TEXT, emp_code TEXT, emp_name TEXT,"
        f" UNIQUE(roll,course_code,emp_code))",
        f"CREATE TABLE IF NOT EXISTS forms(id {pk}, title TEXT, instructions TEXT, year TEXT, sem TEXT,"
        f" branch TEXT, status TEXT DEFAULT 'draft', teacher_shared INTEGER DEFAULT 0,"
        f" created TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        f"CREATE TABLE IF NOT EXISTS questions(id {pk}, form_id INTEGER NOT NULL, text TEXT,"
        f" apply_all INTEGER DEFAULT 1, ord INTEGER DEFAULT 0)",
        f"CREATE TABLE IF NOT EXISTS qoptions(id {pk}, question_id INTEGER NOT NULL, label TEXT, ord INTEGER DEFAULT 0)",
        f"CREATE TABLE IF NOT EXISTS responses(id {pk}, form_id INTEGER NOT NULL, question_id INTEGER NOT NULL,"
        f" roll TEXT NOT NULL, option TEXT, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        f"CREATE TABLE IF NOT EXISTS statements(id {pk}, form_id INTEGER NOT NULL, roll TEXT NOT NULL,"
        f" text TEXT, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS teachers(emp_code TEXT PRIMARY KEY, emp_name TEXT, passhash TEXT)",
        "CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)",
        "CREATE INDEX IF NOT EXISTS ix_a_roll ON assignments(roll)",
        "CREATE INDEX IF NOT EXISTS ix_a_sem ON assignments(semester, dept)",
        "CREATE INDEX IF NOT EXISTS ix_a_emp ON assignments(emp_code)",
        "CREATE INDEX IF NOT EXISTS ix_r_form ON responses(form_id)",
    ]:
        cur.execute(s)
    if PG:
        for t in ('assignments', 'forms', 'questions', 'qoptions', 'responses', 'statements', 'teachers', 'settings'):
            cur.execute(f'ALTER TABLE {t} ENABLE ROW LEVEL SECURITY')
    c.commit()
    c.close()

    if os.environ.get('VERCEL'):
        return
    with app.app_context():
        for f in sorted(timetable_files()):
            key = 'imported:' + os.path.basename(f)
            if not setting(key):
                n, sems = import_file(f)
                set_setting(key, str(n))
                print(f'Imported {n} rows from {os.path.basename(f)} (semester {", ".join(sems)})')
        sync_teachers()


def timetable_files():
    """Every '*student*timetable*.xlsx' next to app.py (spaces or underscores, any case)."""
    out = []
    for f in glob.glob(os.path.join(BASE, '*.xlsx')):
        name = os.path.basename(f).lower().replace('_', ' ').replace('-', ' ')
        if 'timetable' in name and 'student' in name:
            out.append(f)
    return out


def import_file(src):
    """Import a 'Student timetable detailed report' workbook (semester from cell A3)."""
    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    data, sems = [], set()
    for ws in wb:
        allr = list(ws.iter_rows(values_only=True))
        m = re.search(r'B\.Tech\s+([IVX]+)\s+Semester', str(allr[2][0]) if len(allr) > 2 and allr[2] else '')
        sem = m.group(1) if m else '?'
        sems.add(sem)
        for r in allr[4:]:
            if r and len(r) > 6 and r[6] and r[4] is not None:
                data.append((str(r[6]).strip().upper(), sem, ws.title, str(r[2]).strip(),
                             str(r[3]).strip(), str(r[4]).strip(), str(r[5]).strip()))
    if data:
        sql = 'INSERT INTO assignments(roll,semester,dept,course_code,course_name,emp_code,emp_name) VALUES %s ON CONFLICT DO NOTHING'
        if PG:
            c = raw()
            psycopg2.extras.execute_values(c.cursor(), sql, data, page_size=2000)
            c.commit()
            c.close()
        else:
            execute_many(sql.replace('VALUES %s', 'VALUES(?,?,?,?,?,?,?)'), data)
    return len(data), sems


def sync_teachers():
    """Every employee code found in the timetable gets a teacher account."""
    fac = rows('SELECT DISTINCT emp_code, emp_name FROM assignments WHERE emp_code IS NOT NULL AND emp_code<>""')
    for f in fac:
        run('INSERT INTO teachers(emp_code,emp_name,passhash) VALUES(?,?,?) ON CONFLICT(emp_code) DO NOTHING',
            (f['emp_code'], f['emp_name'], hash_pw(DEFAULT_TEACHER_PASSWORD, f['emp_code'])))
    return len(fac)


# ------------------------------------------------------------------------ helpers
def semesters():
    return sorted({r['semester'] for r in rows('SELECT DISTINCT semester FROM assignments')},
                  key=lambda s: ROMAN.get(s, 99))


def departments():
    return [r['dept'] for r in rows('SELECT DISTINCT dept FROM assignments ORDER BY dept')]


def year_of(sem):
    return ROMAN.get((sem or '').strip().upper(), 0)


def is_open(sem):
    return setting(f'open_{sem}', '1') != '0'


def csv_response(name, header, data):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    w.writerows(data)
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename={name}'})


# ------------------------------------------------------------------------ security
@app.before_request
def csrf_protect():
    if 'tok' not in session:
        session['tok'] = secrets.token_hex(16)
    if request.method == 'POST' and request.form.get('csrf') != session['tok']:
        abort(400)


@app.context_processor
def inject():
    return {'csrf': session.get('tok', ''), 'is_admin': bool(session.get('admin')),
            'is_teacher': bool(session.get('teacher')), 'year': datetime.now().year,
            'year_label': YEAR_LABEL}


def admin_only(f):
    @wraps(f)
    def w(*a, **k):
        return f(*a, **k) if session.get('admin') else redirect(url_for('admin_login'))
    return w


def teacher_only(f):
    @wraps(f)
    def w(*a, **k):
        return f(*a, **k) if session.get('teacher') else redirect(url_for('teacher_login'))
    return w


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# ----------------------------------------------------------------------- student
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        roll = request.form.get('roll', '').strip().upper()
        if one('SELECT 1 AS x FROM assignments WHERE roll=?', (roll,)):
            session.clear()
            session['roll'] = roll
            return redirect(url_for('student_forms'))
        flash('Roll number not found in the timetable data.')
    return render_template('student_login.html')


def student_info():
    r = one('SELECT roll, semester, dept FROM assignments WHERE roll=? LIMIT 1', (session['roll'],))
    if r:
        r['year'] = year_of(r['semester'])
        r['year_label'] = YEAR_LABEL.get(r['year'], r['semester'])
    return r


def year_num(v):
    """Accept '3', '3 Year', '3rd Year', 3 -> 3 (0 when blank = all years)."""
    m = re.search(r'[1-4]', str(v or ''))
    return int(m.group()) if m else 0


def forms_for_student(info, published_only=True):
    """Published forms for this student's year and branch (blank year/branch = everyone)."""
    if not info:
        return []
    all_forms = rows("SELECT * FROM forms" + (" WHERE status='published'" if published_only else '')
                     + ' ORDER BY id DESC')
    return [f for f in all_forms
            if year_num(f['year']) in (0, info['year'])
            and (not f['branch'] or f['branch'] == info['dept'])]


def questions_of(form_id, apply_all_only=False):
    qs = rows('SELECT * FROM questions WHERE form_id=? ORDER BY ord, id', (form_id,))
    for q in qs:
        q['options'] = rows('SELECT * FROM qoptions WHERE question_id=? ORDER BY ord, id', (q['id'],))
    if apply_all_only:
        return [q for q in qs if q['apply_all']]
    return qs


@app.route('/feedback')
def student_forms():
    roll = session.get('roll')
    if not roll:
        return redirect(url_for('index'))
    info = student_info()
    forms = forms_for_student(info)
    cards = []
    for f in forms:
        qs = questions_of(f['id'])
        done = one('SELECT COUNT(DISTINCT question_id) AS n FROM responses WHERE form_id=? AND roll=?',
                   (f['id'], roll))['n']
        st = one('SELECT 1 AS x FROM statements WHERE form_id=? AND roll=? AND text<>""', (f['id'], roll))
        cards.append({'form': f, 'nq': len(qs), 'done': done or 0, 'stated': bool(st)})
    return render_template('student_forms.html', info=info, cards=cards,
                           years=[y for y in range(1, 5)], open_=is_open(info['semester']) if info else True)


@app.route('/feedback/<int:fid>', methods=['GET', 'POST'])
def feedback_form(fid):
    roll = session.get('roll')
    if not roll:
        return redirect(url_for('index'))
    info = student_info()
    f = one('SELECT * FROM forms WHERE id=?', (fid,))
    if not f:
        abort(404)
    if f['status'] != 'published':
        flash('This feedback form has not been published yet.')
        return redirect(url_for('student_forms'))
    if not is_open(info['semester']):
        flash('Feedback is currently closed by the administrator.')
        return redirect(url_for('student_forms'))
    qs = questions_of(fid)
    if request.method == 'POST':
        run('DELETE FROM responses WHERE form_id=? AND roll=?', (fid, roll))
        saved = 0
        for q in qs:
            val = request.form.get(f'q_{q["id"]}', '').strip()
            if val and any(val == o['label'] for o in q['options']):
                run('INSERT INTO responses(form_id,question_id,roll,option) VALUES(?,?,?,?)',
                    (fid, q['id'], roll, val))
                saved += 1
        run('DELETE FROM statements WHERE form_id=? AND roll=?', (fid, roll))
        note = request.form.get('statement', '').strip()[:1000]
        if note:
            run('INSERT INTO statements(form_id,roll,text) VALUES(?,?,?)', (fid, roll, note))
        flash(f'Thank you! Your feedback was submitted ({saved} response(s)).')
        return redirect(url_for('student_forms'))
    done = {r['question_id']: r['option'] for r in
            rows('SELECT question_id, option FROM responses WHERE form_id=? AND roll=?', (fid, roll))}
    prev = one('SELECT text FROM statements WHERE form_id=? AND roll=?', (fid, roll))
    return render_template('feedback_form.html', form=f, questions=qs, ratings=RATINGS,
                           done=done, roll=roll, info=info, statement_text=(prev or {}).get('text', ''))


# ----------------------------------------------------------------------- teacher
@app.route('/teacher/login', methods=['GET', 'POST'])
def teacher_login():
    if request.method == 'POST':
        code = request.form.get('emp_code', '').strip().upper()
        pw = request.form.get('password', '')
        t = one('SELECT * FROM teachers WHERE UPPER(emp_code)=?', (code,))
        if t and hmac.compare_digest(t['passhash'] or '', hash_pw(pw, t['emp_code'])):
            session.clear()
            session['teacher'] = t['emp_code']
            session['teacher_name'] = t['emp_name']
            return redirect(url_for('teacher_dashboard'))
        flash('Invalid employee code or password.')
    return render_template('teacher_login.html')


@app.route('/teacher')
@teacher_only
def teacher_dashboard():
    code = session['teacher']
    name = one('SELECT emp_name FROM teachers WHERE emp_code=?', (code,))['emp_name'] \
        or session.get('teacher_name', 'Faculty')
    forms = rows("SELECT * FROM forms WHERE status='published' ORDER BY id DESC")
    data = []
    for f in forms:
        # questions marked "apply to all batches" are the ones this teacher is asked about
        qs = [q for q in questions_of(f['id']) if q['apply_all']]
        dist = {lab: 0 for lab in RATINGS}
        per_q = []
        for i, q in enumerate(qs, 1):
            c = Counter(r['option'] for r in
                        rows('SELECT option FROM responses WHERE form_id=? AND question_id=?', (f['id'], q['id'])))
            for lab in RATINGS:
                dist[lab] += c.get(lab, 0)
            per_q.append({'no': i, 'text': q['text'], 'counts': c, 'total': sum(c.values())})
        notes = rows('SELECT roll, text, created FROM statements WHERE form_id=? AND text<>"" ORDER BY id DESC',
                     (f['id'],))
        avg = None
        vals = [WEIGHTS[r['option']] for r in rows('SELECT option FROM responses WHERE form_id=?', (f['id'],))]
        if vals:
            avg = round(sum(vals) / len(vals), 2)
        data.append({'form': f, 'dist': dist, 'total': sum(dist.values()), 'avg': avg,
                     'per_q': per_q, 'notes': notes if f['teacher_shared'] else [],
                     'shared': bool(f['teacher_shared']), 'nq': len(qs)})
    return render_template('teacher_dashboard.html', name=name, emp=code, data=data,
                           ratings=RATINGS, labels=[f'Q{i}' for i in range(1, 20)])


@app.route('/teacher/suggestions/<int:fid>')
@teacher_only
def teacher_suggestions(fid):
    f = one('SELECT * FROM forms WHERE id=?', (fid,))
    if not f:
        abort(404)
    if not f['teacher_shared']:
        flash('The admin has not published this feedback box to teachers yet.')
        return redirect(url_for('teacher_dashboard'))
    return render_template('teacher_suggestions.html', form=f,
                           notes=rows('SELECT roll, text, created FROM statements WHERE form_id=? AND text<>"" ORDER BY id DESC', (fid,)))


# ------------------------------------------------------------------------- admin
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if hmac.compare_digest(request.form.get('password', '').encode(), ADMIN_PASSWORD.encode()):
            session.clear()
            session['admin'] = True
            return redirect(url_for('admin'))
        flash('Wrong password.')
    return render_template('admin_login.html')


@app.route('/admin')
@admin_only
def admin():
    kpi = one("""SELECT COUNT(*) AS responses,
        AVG(CASE option WHEN 'Excellent' THEN 5 WHEN 'Very Good' THEN 4 WHEN 'Good' THEN 3
            WHEN 'Bad' THEN 2 WHEN 'Very Bad' THEN 1 END) AS m FROM responses""")
    dist = Counter(r['option'] for r in rows('SELECT option FROM responses'))
    byform = rows("""SELECT f.id, f.title, f.year, f.sem, f.branch, f.status, f.teacher_shared,
        (SELECT COUNT(*) FROM questions q WHERE q.form_id=f.id) AS nq,
        (SELECT COUNT(*) FROM responses r WHERE r.form_id=f.id) AS responses,
        (SELECT COUNT(DISTINCT roll) FROM responses r WHERE r.form_id=f.id) AS rolls,
        (SELECT COUNT(*) FROM statements s WHERE s.form_id=f.id AND s.text<>'') AS statements
        FROM forms f ORDER BY f.id DESC""")
    days = Counter(str(r['d'])[:10] for r in rows('SELECT created AS d FROM responses'))
    students = one('SELECT COUNT(DISTINCT roll) AS n FROM assignments')['n']
    forms = rows('SELECT * FROM forms')
    roster = Counter()
    for r in rows('SELECT semester, dept, COUNT(DISTINCT roll) AS n FROM assignments'
                  ' GROUP BY semester, dept'):
        roster[(year_of(r['semester']), r['dept'])] += r['n']
    all_students = sum(roster.values())
    total_expected = 0
    for f in forms:
        y, b = year_num(f['year']), (f['branch'] or '')
        n = sum(c for (ry, rb), c in roster.items()
                if (y == 0 or ry == y) and (not b or rb == b))
        total_expected += n * len(questions_of(f['id']))
    submitted = kpi['responses']
    pct = round(submitted * 100 / total_expected) if total_expected else 0
    return render_template('admin.html', kpi=kpi, avg=round(float(kpi['m']), 2) if kpi['m'] else None,
                           students=students, submitted=submitted, pct=pct, expected=total_expected,
                           dist_labels=RATINGS, dist_vals=[dist.get(l, 0) for l in RATINGS],
                           byform=byform, day_labels=sorted(days), day_vals=[days[d] for d in sorted(days)],
                           sems=semesters(), depts=departments())


@app.route('/admin/forms')
@admin_only
def admin_forms():
    forms = rows('SELECT * FROM forms ORDER BY id DESC')
    for f in forms:
        f['nq'] = one('SELECT COUNT(*) AS n FROM questions WHERE form_id=?', (f['id'],))['n']
        f['resp'] = one('SELECT COUNT(*) AS n FROM responses WHERE form_id=?', (f['id'],))['n']
        f['rolls'] = one('SELECT COUNT(DISTINCT roll) AS n FROM responses WHERE form_id=?', (f['id'],))['n']
    return render_template('admin_forms.html', forms=forms, sems=semesters(), depts=departments(),
                           years=YEARS, f=request.args)


@app.route('/admin/forms/new', methods=['GET', 'POST'])
@admin_only
def admin_form_new():
    if request.method == 'POST':
        fid = save_form(None)
        flash('Feedback form created. Add questions, then publish it.')
        return redirect(url_for('admin_form_edit', fid=fid))
    return redirect(url_for('admin_forms'))


def save_form(fid):
    title = request.form.get('title', '').strip() or 'Untitled feedback form'
    data = (title, request.form.get('instructions', '').strip(), request.form.get('year', ''),
            request.form.get('sem', ''), request.form.get('branch', ''),
            request.form.get('status', 'draft'))
    if fid is None:
        fid = run('INSERT INTO forms(title,instructions,year,sem,branch,status) VALUES(?,?,?,?,?,?)', data)
    else:
        run('UPDATE forms SET title=?,instructions=?,year=?,sem=?,branch=?,status=? WHERE id=?', data + (fid,))
        run('DELETE FROM qoptions WHERE question_id IN (SELECT id FROM questions WHERE form_id=?)', (fid,))
        run('DELETE FROM questions WHERE form_id=?', (fid,))
    idxs = sorted({int(k.split('_')[1]) for k in request.form if k.startswith('qtext_')},
                  key=lambda i: request.form.get(f'qord_{i}', '0'))
    for i in idxs:
        text = request.form.get(f'qtext_{i}', '').strip()
        if not text:
            continue
        qid = run('INSERT INTO questions(form_id,text,apply_all,ord) VALUES(?,?,?,?)',
                  (fid, text, 1 if request.form.get(f'qall_{i}') else 0,
                   int(request.form.get(f'qord_{i}', i) or i)))
        opts = [request.form.get(f'qopt_{i}_{j}', '').strip() for j in range(1, 6)]
        opts = [o or RATINGS[j - 1] for j, o in enumerate(opts) if o or j <= 5]
        execute_many('INSERT INTO qoptions(question_id,label,ord) VALUES(?,?,?)',
                     [(qid, o, n) for n, o in enumerate(opts) if o])
    return fid


@app.route('/admin/forms/<int:fid>', methods=['GET', 'POST'])
@admin_only
def admin_form_edit(fid):
    f = one('SELECT * FROM forms WHERE id=?', (fid,))
    if not f:
        abort(404)
    if request.method == 'POST':
        save_form(fid)
        flash('Form saved.')
        return redirect(url_for('admin_form_edit', fid=fid))
    qs = questions_of(fid)
    blanks = [{'id': None, 'text': '', 'apply_all': 1, 'options': [{'label': r} for r in RATINGS]}]
    return render_template('admin_form_edit.html', form=f, questions=qs, blanks=blanks,
                           ratings=RATINGS, sems=semesters(), depts=departments(), years=YEARS)


@app.route('/admin/forms/<int:fid>/delete', methods=['POST'])
@admin_only
def admin_form_delete(fid):
    for q in rows('SELECT id FROM questions WHERE form_id=?', (fid,)):
        run('DELETE FROM qoptions WHERE question_id=?', (q['id'],))
    run('DELETE FROM responses WHERE form_id=?', (fid,))
    run('DELETE FROM statements WHERE form_id=?', (fid,))
    run('DELETE FROM questions WHERE form_id=?', (fid,))
    run('DELETE FROM forms WHERE id=?', (fid,))
    flash('Form deleted.')
    return redirect(url_for('admin_forms'))


# -------------------------------------------------------- submissions tracking
@app.route('/admin/submissions')
@admin_only
def admin_submissions():
    sem = request.args.get('sem', '')
    branch = request.args.get('branch', '')
    which = request.args.get('list', 'submitted')
    where, args = [], []
    if sem:
        where.append('semester=?')
        args.append(sem)
    if branch:
        where.append('dept=?')
        args.append(branch)
    w = ('WHERE ' + ' AND '.join(where)) if where else ''
    roster = rows(f'SELECT roll, semester, dept, COUNT(*) AS courses FROM assignments {w} GROUP BY roll, semester, dept ORDER BY roll', tuple(args))
    submitted, pending = [], []
    for s in roster:
        r = one('SELECT COUNT(*) AS n FROM responses WHERE roll=?', (s['roll'],))
        s['responses'] = r['n']
        (submitted if r['n'] else pending).append(s)
    if request.args.get('csv'):
        src = submitted if which == 'submitted' else pending
        return csv_response(f'{which}_{sem or "all"}_{branch or "all"}.csv',
                            ['Roll', 'Semester', 'Branch', 'Courses', 'Responses'],
                            [[s['roll'], s['semester'], s['dept'], s['courses'], s['responses']] for s in src])
    agg = defaultdict(lambda: [0, 0])
    for s in roster:
        agg[s['dept']][0] += 1
        agg[s['dept']][1] += bool(s['responses'])
    branch_rows = sorted(agg.items())
    return render_template('admin_submissions.html', sem=sem, branch=branch, which=which,
                           submitted=submitted, pending=pending, n_sub=len(submitted), n_pend=len(pending),
                           branch_rows=branch_rows, sems=semesters(), depts=departments(), f=request.args)


@app.route('/admin/student/<roll>')
@admin_only
def admin_student(roll):
    roll = roll.upper()
    info = one('SELECT semester, dept FROM assignments WHERE roll=? LIMIT 1', (roll,))
    forms = rows("""SELECT DISTINCT f.id, f.title FROM responses r JOIN forms f ON f.id=r.form_id
        WHERE r.roll=? ORDER BY f.id DESC""", (roll,))
    out = []
    for f in forms:
        qs = questions_of(f['id'])
        ans = {r['question_id']: r['option'] for r in
               rows('SELECT question_id, option FROM responses WHERE form_id=? AND roll=?', (f['id'], roll))}
        st = one('SELECT text FROM statements WHERE form_id=? AND roll=?', (f['id'], roll))
        out.append({'form': f, 'rows': [(q['text'], ans.get(q['id'], '-')) for q in qs], 'statement': st})
    return render_template('admin_student.html', roll=roll, info=info, out=out)


# ------------------------------------------------------ publish box to teacher
@app.route('/admin/publish', methods=['GET', 'POST'])
@admin_only
def admin_publish():
    if request.method == 'POST':
        fid = int(request.form['fid'])
        act = request.form.get('act')
        if act == 'publish':
            run('UPDATE forms SET status=? WHERE id=?', ('published' if request.form.get('v') == '1' else 'draft', fid))
            flash('Form publication status updated.')
        elif act == 'share':
            run('UPDATE forms SET teacher_shared=? WHERE id=?', (1 if request.form.get('v') == '1' else 0, fid))
            flash('Feedback box published to teachers.' if request.form.get('v') == '1'
                  else 'Feedback box hidden from teachers.')
        return redirect(url_for('admin_publish'))
    forms = rows('SELECT * FROM forms ORDER BY id DESC')
    for f in forms:
        f['resp'] = one('SELECT COUNT(*) AS n FROM responses WHERE form_id=?', (f['id'],))['n']
        f['notes'] = one('SELECT COUNT(*) AS n FROM statements WHERE form_id=? AND text<>""', (f['id'],))['n']
    return render_template('admin_publish.html', forms=forms)


@app.route('/admin/publish/preview/<int:fid>')
@admin_only
def admin_preview(fid):
    f = one('SELECT * FROM forms WHERE id=?', (fid,))
    if not f:
        abort(404)
    qs = [q for q in questions_of(fid) if q['apply_all']]
    dist = {lab: 0 for lab in RATINGS}
    per_q = []
    for i, q in enumerate(qs, 1):
        c = Counter(r['option'] for r in rows('SELECT option FROM responses WHERE form_id=? AND question_id=?', (fid, q['id'])))
        for lab in RATINGS:
            dist[lab] += c.get(lab, 0)
        per_q.append({'no': i, 'text': q['text'], 'counts': c, 'total': sum(c.values())})
    notes = rows('SELECT roll, text, created FROM statements WHERE form_id=? AND text<>"" ORDER BY id DESC', (fid,))
    vals = [WEIGHTS[r['option']] for r in rows('SELECT option FROM responses WHERE form_id=?', (fid,))]
    return render_template('admin_preview.html', form=f, per_q=per_q, dist=dist,
                           total=sum(dist.values()), notes=notes, ratings=RATINGS,
                           avg=round(sum(vals) / len(vals), 2) if vals else None)


# --------------------------------------------------------------------- settings
@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_only
def admin_settings():
    if request.method == 'POST':
        act = request.form.get('action')
        if act == 'toggle':
            s = request.form['sem']
            set_setting(f'open_{s}', '0' if is_open(s) else '1')
            flash(f'Semester {s} feedback {"closed" if is_open(s) else "opened"}.')
        elif act == 'upload' and request.files.get('file'):
            try:
                n, sems = import_file(io.BytesIO(request.files['file'].read()))
                sync_teachers()
                flash(f'Imported {n} rows (semester {", ".join(sems)}).')
            except Exception as e:
                flash(f'Import failed: {e}')
        elif act == 'tpw':
            code = request.form.get('emp_code', '').strip().upper()
            pw = request.form.get('pw', '').strip()
            if code and pw:
                name = (one('SELECT emp_name FROM teachers WHERE emp_code=?', (code,)) or {}).get('emp_name', code)
                run('INSERT INTO teachers(emp_code,emp_name,passhash) VALUES(?,?,?) ON CONFLICT(emp_code) DO UPDATE SET passhash=excluded.passhash',
                    (code, name, hash_pw(pw, code)))
                flash(f'Password updated for {code}.')
            else:
                flash('Employee code and new password are required.')
        elif act == 'reset':
            if request.form.get('confirm') == 'RESET':
                run('DELETE FROM responses')
                run('DELETE FROM statements')
                flash('All feedback deleted.')
            else:
                flash('Type RESET to confirm.')
        return redirect(url_for('admin_settings'))
    return render_template('admin_settings.html', sems=[(s, is_open(s)) for s in semesters()],
                           teachers=rows('SELECT * FROM teachers ORDER BY emp_name'),
                           depts=departments())


# ---------------------------------------------------------------------- exports
@app.route('/admin/export/responses.csv')
@admin_only
def export_responses():
    R = rows("""SELECT f.title, f.year, f.sem, f.branch, q.ord, q.text AS question, r.roll, r.option, r.created
        FROM responses r JOIN forms f ON f.id=r.form_id JOIN questions q ON q.id=r.question_id ORDER BY f.id, q.ord""")
    return csv_response('feedback_responses.csv',
                        ['Form', 'Year', 'Sem', 'Branch', 'Q#', 'Question', 'Roll', 'Rating', 'Submitted'],
                        [[r['title'], r['year'], r['sem'], r['branch'], r['ord'], r['question'],
                          r['roll'], r['option'], r['created']] for r in R])


@app.route('/admin/export/statements.csv')
@admin_only
def export_statements():
    R = rows("""SELECT f.title, s.roll, s.text, s.created FROM statements s JOIN forms f ON f.id=s.form_id
        WHERE s.text<>'' ORDER BY f.id, s.id""")
    return csv_response('feedback_statements.csv', ['Form', 'Roll', 'Statement', 'Submitted'],
                        [[r['title'], r['roll'], r['text'], r['created']] for r in R])


app.jinja_env.globals['year_text'] = (
    lambda v: YEAR_LABEL.get(year_num(v), 'All years') if year_num(v) else 'All years')

init_db()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))