"""Create one example published feedback form (safe to re-run; skips if one exists)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('SECRET_KEY', 'demo')
import app as A  # noqa: E402

DEMO = [
    ('The faculty member explains the subject clearly.', 1),
    ('Course material and lecture notes are provided in time.', 1),
    ('Doubt-solving sessions are conducted regularly.', 1),
    ('Internal assessment and evaluation are fair.', 1),
    ('Laboratory / project work is well guided.', 0),
]

with A.app.app_context():
    if A.one("SELECT id FROM forms WHERE title LIKE 'Semester Feedback%' LIMIT 1"):
        print('demo form already exists')
        raise SystemExit
    fid = A.run('INSERT INTO forms(title,instructions,year,sem,branch,status,teacher_shared)'
                " VALUES(?,?,?,?,?,'published',1)",
                ('Semester Feedback - III Semester (CSE)',
                 'Rate each statement honestly. Your roll number is only shown to the faculty and the '
                 'administration. Use the optional box at the end for anything extra you want to share.',
                 '3', 'III', 'CSE'))
    for i, (text, apply_all) in enumerate(DEMO, start=1):
        qid = A.run('INSERT INTO questions(form_id,text,apply_all,ord) VALUES(?,?,?,?)',
                    (fid, text, apply_all, i))
        A.execute_many('INSERT INTO qoptions(question_id,label,ord) VALUES(?,?,?)',
                       [(qid, r, n) for n, r in enumerate(A.RATINGS)])
    print('created demo form id', fid, 'with', len(DEMO), 'questions')

for who, roll in (('student', '25B11CS015'),):
    pass
print('done')