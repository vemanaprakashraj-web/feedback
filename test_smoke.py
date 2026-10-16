"""End-to-end smoke test: seeds fake timetable data and exercises every route."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('SECRET_KEY', 'test')
import app as A  # noqa: E402

CSRF_RE = re.compile(r'name="?csrf"?\s+value="([0-9a-f]+)"')


def csrf_of(page_html):
    m = CSRF_RE.search(page_html)
    assert m, 'csrf token not found in page'
    return m.group(1)


A.init_db()

with A.app.app_context():
    data = []
    for dept, bcode, fac in (('CSE', 'CS', [('EMP01', 'Dr. Anitha Rao'), ('EMP02', 'Prof. Ravi Kumar')]),
                             ('ECE', 'EC', [('EMP03', 'Dr. Suresh Menon')])):
        for i in range(1, 6):
            roll = f"25B{'11' if bcode == 'CS' else '12'}{bcode}0{i:02d}"
            for j, (code, name) in enumerate(fac):
                data.append((roll, 'III', dept, f'SUBJ{j}10', f'Subject {j}', code, name))
    A.execute_many('INSERT INTO assignments(roll,semester,dept,course_code,course_name,emp_code,emp_name) '
                   'VALUES(?,?,?,?,?,?,?) ON CONFLICT DO NOTHING', data)
    A.sync_teachers()
    print('students:', A.one('SELECT COUNT(DISTINCT roll) n FROM assignments')['n'],
          'teachers:', A.one('SELECT COUNT(*) n FROM teachers')['n'])

admin_c = A.app.test_client()
stu_c = A.app.test_client()
tea_c = A.app.test_client()


def post(client, path, data, home='/'):
    token = csrf_of(client.get(home).data.decode())
    data['csrf'] = token
    return client.post(path, data=data, follow_redirects=True)


# ---------------------------------------------------------------- student flow
r = post(stu_c, '/', {'roll': '25B11CS001'})
assert b'My Feedback' in r.data, 'student login failed'
print('OK student login by roll number')

r = post(admin_c, '/admin/login', {'password': os.environ.get('ADMIN_PASSWORD', 'admin123')})
assert b'Admin Dashboard' in r.data, 'admin login failed'
print('OK admin login')

r = post(admin_c, '/admin/forms/new', {})
with A.app.app_context():
    fid = A.one('SELECT MAX(id) AS n FROM forms')['n']
answers = {'title': 'Sem III Feedback 2026', 'instructions': 'Rate honestly',
           'year': '3 Year', 'sem': 'III', 'branch': 'CSE', 'status': 'published'}
for i, (qt, apply) in enumerate([('Teaching quality', True), ('Course material', True),
                                 ('Lab support', False)], start=1):
    answers[f'qord_{i}'] = str(i)
    answers[f'qtext_{i}'] = qt
    if apply:
        answers[f'qall_{i}'] = 'on'
    for j, lab in enumerate(A.RATINGS, start=1):
        answers[f'qopt_{i}_{j}'] = lab
r = post(admin_c, f'/admin/forms/{fid}', answers)
assert b'Form saved' in r.data, 'form save failed'
with A.app.app_context():
    qs = A.questions_of(fid)
    assert len(qs) == 3 and len(qs[0]['options']) == 5, qs
    assert [q['apply_all'] for q in qs] == [1, 1, 0]
print('OK form builder: 3 questions x 5 options, apply-all toggles respected')

r = stu_c.get(f'/feedback/{fid}')
assert b'Sem III Feedback' in r.data, 'published form not visible to matching student'
assert b'Optional' in r.data
r = post(stu_c, f'/feedback/{fid}', {f'q_{qs[0]["id"]}': 'Excellent',
                                     f'q_{qs[1]["id"]}': 'Good',
                                     f'q_{qs[2]["id"]}': 'Bad',
                                     'statement': 'Please provide more PDFs'})
assert b'Thank you' in r.data, 'student submit failed'
with A.app.app_context():
    assert A.one('SELECT COUNT(*) n FROM statements WHERE form_id=?', (fid,))['n'] == 1
print('OK student submits ratings + ONE optional statement')

r = stu_c.get(f'/feedback/{fid}')
assert b'Please provide more PDFs' in r.data, 'statement not shown back to student'
print('OK saved answers are re-displayed')

# --------------------------------------------------------------- admin routes
for path, name in [('/admin', 'dashboard'), ('/admin/forms', 'forms'),
                   ('/admin/submissions', 'submissions'),
                   ('/admin/submissions?list=pending', 'not-submitted list'),
                   ('/admin/submissions?sem=III&branch=CSE', 'filtered'),
                   ('/admin/submissions?csv=1&list=pending', 'csv'),
                   ('/admin/publish', 'publish'), (f'/admin/publish/preview/{fid}', 'preview'),
                   ('/admin/settings', 'settings'), ('/admin/student/25B11CS001', 'roll detail'),
                   ('/admin/export/responses.csv', 'responses csv'),
                   ('/admin/export/statements.csv', 'statements csv')]:
    rr = admin_c.get(path)
    assert rr.status_code == 200, (path, rr.status_code)
    print(f'OK GET {path}')

sub = admin_c.get('/admin/submissions?sem=III&branch=CSE&list=submitted').data
assert b'25B11CS001' in sub
pend = admin_c.get('/admin/submissions?sem=III&branch=CSE&list=pending').data
if b'25B11CS002' not in pend:
    import re as _re
    print(_re.findall(r'25B\d+\w+', pend.decode()))
    print(_re.findall(r'[Nn]ot submitted[^\d]*(\d+)', pend.decode()))
assert b'25B11CS002' in pend and b'25B11CS001' not in pend
print('OK submitted / not-submitted roll lists render')

# ------------------------------------------------------------ publish toggles
post(admin_c, '/admin/publish', {'fid': str(fid), 'act': 'share', 'v': '1'})
with A.app.app_context():
    assert A.one('SELECT teacher_shared t FROM forms WHERE id=?', (fid,))['t'] == 1
post(admin_c, '/admin/publish', {'fid': str(fid), 'act': 'publish', 'v': '0'})
with A.app.app_context():
    assert A.one('SELECT status s FROM forms WHERE id=?', (fid,))['s'] == 'draft'
post(admin_c, '/admin/publish', {'fid': str(fid), 'act': 'publish', 'v': '1'})
print('OK publish-to-students / publish-box-to-teachers toggles')

# -------------------------------------------------------------- teacher flow
r = post(tea_c, '/teacher/login', {'emp_code': 'EMP01', 'password': 'aditya@123'})
assert b'Feedback Dashboard' in r.data, 'teacher login failed'
assert b'25B11CS001' in r.data, 'teacher cannot see student roll-number suggestions'
assert b'Excellent' in r.data
print('OK teacher login sees rating breakdown + suggestions with roll numbers')

assert tea_c.get('/teacher/suggestions/%d' % fid).status_code == 200
print('OK teacher suggestions page')

r = post(tea_c, '/teacher/login', {'emp_code': 'EMP01', 'password': 'nope'})
assert b'Invalid employee code' in r.data
print('OK teacher rejects wrong password')

# ------------------------------------------------------------- access control
anon = A.app.test_client()
for p in ('/admin', '/admin/forms', '/admin/settings'):
    r = anon.get(p, follow_redirects=True)
    assert b'Admin Login' in r.data, p
r = anon.get('/teacher', follow_redirects=True)
assert b'Teacher Login' in r.data
r = anon.get('/feedback', follow_redirects=True)
assert b'Student Login' in r.data
print('OK role guards redirect unauthenticated users')

print('\nALL TESTS PASSED')