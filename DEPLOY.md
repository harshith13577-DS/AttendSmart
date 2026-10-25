# AttendSmart — PythonAnywhere Free Deploy Guide

You're already logged in to PythonAnywhere as **holoverse** in Chrome. Follow these steps in your browser's PythonAnywhere dashboard.

## 1. Push code to GitHub
From this folder (`AttendSmart/`), initialize a git repo and push:

```bash
git init
git add .
git commit -m "Initial AttendSmart release"
git branch -M main
git remote add origin https://github.com/<your-username>/AttendSmart.git
git push -u origin main
```

Add your partner as a collaborator on the repo.

## 2. PythonAnywhere dashboard
1. Log in at **pythonanywhere.com** (you're already logged in as **holoverse**).
2. Go to the **Dashboard** → **Files** tab.
3. Click **New file** and create an empty file named `requirements.txt` (or upload the one from this repo).
4. Copy the whole `AttendSmart/` folder into your PythonAnywhere files area (e.g. `/home/holoverse/mysite/AttendSmart` or just `/home/holoverse/AttendSmart`).

   Easiest: use the **Bash console** from the dashboard and clone your repo there:
   ```bash
   cd ~
   git clone https://github.com/<your-username>/AttendSmart.git
   ```

## 3. Set up the web app
1. Dashboard → **Web** tab → **Add a new web app**.
2. Choose **Manual configuration** (not the quick installer).
3. Python version: **Python 3.10** (or latest available).
4. Click **Next** / **Finish**.

## 4. Virtualenv + install requirements
Open a **Bash console** from the dashboard:

```bash
cd ~
mkvirtualenv --python=/usr/bin/python3.10 attendsmart   # or python3
workon attendsmart
pip install --upgrade pip
pip install -r /home/holoverse/AttendSmart/requirements.txt
```

*(Adjust the path to where you cloned the repo.)*

## 5. Point the WSGI file to our app
In the **Web** tab, click the **WSGI configuration file** link. Replace the entire contents with:

```python
import sys
import os

# Path to your AttendSmart project folder
path = '/home/holoverse/AttendSmart'
if path not in sys.path:
    sys.path.insert(0, path)

from app import app as application
```

Save the file, then go back to the **Web** tab and click the **Reload** button (green reload icon) next to your domain (`holoverse.pythonanywhere.com`).

## 6. Check the live site
Open: **https://holoverse.pythonanywhere.com**

- Login with any demo account (password `admin123`).
- Lecturer/CR: edit timetable → mark a period Cancelled → save a class note.
- Student: the timetable updates live (polls every 10 seconds) and attendance numbers reflect the change.

## 7. Test checklist on phones
- [ ] Login with roll number (student / lecturer / CR)
- [ ] Role permissions (student cannot access lecturer timetable editor)
- [ ] Timetable add / edit / delete periods
- [ ] Mark period Attending / Cancelled
- [ ] Save class note at end of class
- [ ] Student page reflects changes within 10 seconds (live badge blinks)
- [ ] Day-wise and subject-wise attendance percentages update
- [ ] Safe-skip / "attend next X" messages show correctly

## 8. Submit
- Record a 2-minute demo video of the live link.
- Write a short README (problem, solution, features, tech stack) — already included as `README.md`.
- Submit the live PythonAnywhere link.

---

**Troubleshooting**
- 500 error? Check the **error log** in the Web tab — usually a wrong WSGI path or missing package.
- DB not updating? Make sure `attend.db` is writable; PythonAnywhere creates it under your project folder automatically.
- Changes not showing? Click **Reload** in the Web tab after editing `app.py` or the WSGI file.