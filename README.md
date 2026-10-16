# Aditya University — Faculty Feedback System

A single Flask app with three roles: **Student**, **Teacher**, **Admin**.

![stack](https://img.shields.io/badge/python-3.10%2B-blue) ![db](https://img.shields.io/badge/db-SQLite%20%7C%20Supabase-green)

---

## 1. Run it

```bash
pip install -r requirements.txt
python app.py                     # http://localhost:5000
```

First run automatically imports every `*Student_timetable*.xlsx` file sitting next to `app.py`
and creates a teacher account for each employee code found in it.

| Role | Where | Login |
|------|-------|-------|
| Student | `/` | roll number (e.g. `25B11CS015`) |
| Teacher | `/teacher/login` | employee code + password |
| Admin | `/admin/login` | `ADMIN_PASSWORD` (default `admin123`) |

Set real secrets before going live — copy `.env.example` to `.env` and fill it in.

---

## 2. Try it with your real data

Your two timetable files are already in place, so a plain start imports everything:

```bash
python app.py
```

The first run prints e.g. `Imported 38341 rows ... (semester III)` and creates a teacher
account for every employee code it finds.

To get something on screen immediately, create one example published form:

```bash
python make_demo_form.py
```

Then log in:

| What | How |
|------|-----|
| Student | `/` with roll `25B11CS015` |
| Teacher | `/teacher/login` with emp code `1003`, password `aditya@123` |
| Admin | `/admin/login` with password `admin123` |

> Templates reload automatically while you edit them. If a page still looks stale,
> stop and start `python app.py` again.

## 3. What each role can do

### Student
1. Logs in with the roll number; year, semester and branch are detected from the timetable.
2. Sees only the feedback forms published for that year + semester + branch.
3. Rates every question (Excellent → Very Bad) and writes **one optional statement at the very end**.
4. Can reopen and revise the form until the admin closes feedback for that semester.

### Teacher
1. Logs in with employee code + password (set/reset by the admin in **Settings**).
2. **Feedback dashboard** per published form:
   - overall rating counts (Q1 … Qn, Excellent → Very Bad),
   - question-wise breakdown table,
   - **suggestions made by students, shown with their roll numbers** — only after the admin
     publishes the feedback box to teachers.
3. When the box is not published, ratings are visible but student statements stay hidden.

### Admin
1. **Dashboard** — KPIs, rating doughnut, daily response line chart, per-form summary.
2. **Build feedback form** — Year → Semester → Branch, title, instructions, and questions:
   - `+ Add question` button adds a new block at any time,
   - each question has 5 rating options (editable text),
   - **"Apply to all batches"** checkbox: ticked = the question is also used in every other
     branch's feedback form; unticked = that question only applies to the selected branch,
   - Save as draft or Publish now.
3. **Submission tracking** — filter by semester and branch (branch-wise), then see:
   - **Submitted** roll numbers, **Not submitted** roll numbers,
   - per-branch summary with completion %, a `View` button per student (their full answers),
   - CSV download of either list.
4. **Publish to teachers** — per form: *Preview* (what teachers will see), *Publish to students*,
   and *Publish box to teachers* (reveals student statements + roll numbers).
5. **Settings** — open/close feedback per semester, upload a new timetable `.xlsx`,
   reset teacher passwords, delete all feedback.
6. **Exports** — responses CSV, statements CSV.

---

## 4. Campus photo (optional)

The header hero shows a photo. Drop your campus image at:

```
static/img/campus.jpg
```

Without it the hero falls back to a clean blue gradient — nothing breaks.

The logo itself is `static/img/logo.svg` (vector, recolours with the theme) and is shown
**above the photo** on the login pages and in the top-left corner on every page.

---

## 5. Deploy

### Render / Railway / any host
Build: `pip install -r requirements.txt` · Start: `gunicorn app:app`
Env: `ADMIN_PASSWORD`, `SECRET_KEY`, `TEACHER_DEFAULT_PASSWORD` (optional).

### Vercel (needs Supabase, SQLite cannot persist there)
1. Create a Supabase project, copy the **Transaction pooler** URI into `.env` as `DATABASE_URL`.
2. Run `python app.py` once locally to create the tables and import your Excel data.
3. Deploy, then set `DATABASE_URL`, `ADMIN_PASSWORD`, `SECRET_KEY` in the Vercel env vars.

The same code path works for both SQLite and Postgres, so you can start local and move to
Supabase without changing anything.

---

## 6. Tests

```bash
python test_smoke.py
```

Seeds fake timetable data and exercises the whole flow: student login → admin form builder →
student submission → admin dashboards/exports → publish toggles → teacher login → access control.

> The test writes sample students into `feedback.db`. Delete that file before going live:
> `Remove-Item feedback.db` (or `rm feedback.db`), then start the app so only your real
> timetable Excel file is imported.

---

## 7. Project layout

```
app.py                     routes, database layer, Excel import, auth
templates/                 base layout + one template per screen
static/css/style.css       theme (light/dark), cards, tables, charts
static/img/logo.svg        Aditya University logo
static/img/campus.jpg      optional hero photo
test_smoke.py              end-to-end test
```

Database tables: `assignments` (timetable), `forms`, `questions`, `qoptions`, `responses`,
`statements`, `teachers`, `settings`.