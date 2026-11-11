# Deploying the study on PythonAnywhere (free plan)

Replace `<you>` with your PythonAnywhere username everywhere below. The two
study links you put in the class spreadsheet will be:

- **Without AI:** `https://<you>.pythonanywhere.com/baseline`
- **With AI:** `https://<you>.pythonanywhere.com/ai`

(`https://<you>.pythonanywhere.com/` itself just shows both links.)

> Labels in PythonAnywhere's dashboard change now and then. If a button name
> below doesn't match exactly, look for the closest equivalent on the same tab.

---

## 0. What goes where

| Item | In git? | How it gets to the server |
|---|---|---|
| App code, `model_out/`, `relevance_model.py` | yes | `git clone` / `git pull` |
| `study_dataset_submission.csv` (the 298-headline study set) | yes | comes with `git clone` (or upload it through the **Files** tab) |
| `instance/secrets.json` | **no** | create on the server (step 4) |
| `instance/study.db` (all participant data) | **no** | created on the server (step 5) |

---

## 1. Get the code onto PythonAnywhere

Open a **Bash console** (Consoles tab → Bash):

```bash
cd ~
git clone https://github.com/khs14/HAI-Assignment-3.git hai-relevance-study
cd hai-relevance-study
```

The last word of the `git clone` line names the folder. Keep it as
`hai-relevance-study`, because every path in this guide (and in the WSGI file)
uses that name.

## 2. Create the virtualenv with the pinned versions

The model was trained with Python 3.11, so use 3.11:

```bash
/usr/local/bin/python3.11 -m venv ~/.virtualenvs/study-env
source ~/.virtualenvs/study-env/bin/activate
pip install --no-cache-dir -r ~/hai-relevance-study/requirements.txt
```

Use `/usr/local/bin/python3.11`. On some PythonAnywhere system images
`mkvirtualenv --python=/usr/bin/python3.11` creates a virtualenv whose `pip`
fails with `No module named '_posixsubprocess'`. Run
`which -a python3.11` to see the interpreters available.

`--no-cache-dir` matters: the free plan has 512 MB of disk, and pip's download
cache would otherwise take a large share of it. The virtualenv takes about
230 MB. Check usage with `du -sh ~/.virtualenvs ~/.cache 2>/dev/null`. If
`~/.cache/pip` exists and is large, it is safe to delete.

Later, to get back into the virtualenv in a new console:
`source ~/.virtualenvs/study-env/bin/activate && cd ~/hai-relevance-study`.

## 3. Check the study CSV is there

`study_dataset_submission.csv` is part of the repository, so `git clone` already
put it in `~/hai-relevance-study/`. (If you installed from a zip without it:
**Files** tab → `/home/<you>/hai-relevance-study/` → **Upload a file**.)

## 4. Create the secrets file

Still in the Bash console (with the virtualenv active):

```bash
cd ~/hai-relevance-study
mkdir -p instance
python - <<'EOF'
import json, secrets
s = {"SECRET_KEY": secrets.token_hex(32),
     "UNIQNAME_KEY": secrets.token_hex(32),
     "ADMIN_PASSWORD": secrets.token_urlsafe(12)}
open("instance/secrets.json", "w").write(json.dumps(s, indent=2))
print("Admin password:", s["ADMIN_PASSWORD"])
EOF
chmod 600 instance/secrets.json
```

Write the admin password down. You can edit `instance/secrets.json` later to
change it (then reload the web app).

**Never change `UNIQNAME_KEY` during the study.** The one-attempt-per-uniqname
rule depends on it; a new key would let everyone take the study again.

(Alternative: instead of the file, set `STUDY_SECRET_KEY`, `STUDY_ADMIN_PASSWORD`
and `STUDY_UNIQNAME_KEY` with `os.environ[...] = ...` lines at the top of the
WSGI file in step 6. Environment variables win over the file.)

## 5. Check the model and build the database

```bash
cd ~/hai-relevance-study
python scripts/check_model.py      # must end with: RESULT: OK - live model matches the CSV
python scripts/prepare_trials.py   # loads 298 headlines into instance/study.db
python scripts/init_db.py          # confirms every table exists (safe to re-run)
```

If `check_model.py` reports a mismatch, **stop**: the installed library
versions don't reproduce the model's scores. Re-run step 2 and make sure the
virtualenv is active.

## 6. Create the web app

**Web** tab → **Add a new web app** → Next → **Manual configuration** (not the
"Flask" quick-start, which would make its own project) → **Python 3.11** → Next.

Then, on the web app's page:

1. **Code** section:
   - Source code: `/home/<you>/hai-relevance-study`
   - Working directory: `/home/<you>/hai-relevance-study`
2. **Virtualenv** section: `/home/<you>/.virtualenvs/study-env`
3. **WSGI configuration file**: click the link (it is
   `/var/www/<you>_pythonanywhere_com_wsgi.py`), delete everything in it and
   paste:

   ```python
   import sys

   project = "/home/<you>/hai-relevance-study"
   if project not in sys.path:
       sys.path.insert(0, project)

   # Optional: secrets as environment variables instead of instance/secrets.json
   # import os
   # os.environ["STUDY_ADMIN_PASSWORD"] = "..."

   from app import create_app
   application = create_app()   # loads the model once, when the worker starts
   ```

4. **Static files** section: URL `/static/` → Directory
   `/home/<you>/hai-relevance-study/static/`
5. **Security** section: turn on **Force HTTPS**.
6. Click the green **Reload** button at the top.

Open `https://<you>.pythonanywhere.com/baseline` and `.../ai`. You should see
each version's welcome page, with "Version: Without AI" / "Version: With AI" in
the top bar.
Then open `https://<you>.pythonanywhere.com/admin`, log in, and check that
"Model failed to load" does **not** appear.

If something breaks, the **Error log** link on the Web tab shows the Python
traceback.

## 7. Test run, then reset

1. Go through both versions yourself, once on your phone and once on a laptop.
2. Optionally run the simulator **from your own computer** (PythonAnywhere free
   consoles can't reach arbitrary sites, so run it locally):

   ```bash
   python scripts/simulate_participants.py --base-url https://<you>.pythonanywhere.com --n 20 --admin-password <pw>
   ```

3. Download `analysis.csv` and `sessions.csv` from `/admin` and check they look right.
4. **Clear all test data before the real study** (in a PythonAnywhere console):

   ```bash
   source ~/.virtualenvs/study-env/bin/activate && cd ~/hai-relevance-study
   python scripts/reset_db.py --yes
   ```

   This makes a backup copy in `instance/backups/` first, then empties every
   participant table. The 298 headlines are kept. Reload the web app afterwards.

## 8. Pulling updates from GitHub

```bash
source ~/.virtualenvs/study-env/bin/activate && cd ~/hai-relevance-study
git pull
pip install --no-cache-dir -r requirements.txt     # only if requirements.txt changed
python scripts/init_db.py                           # harmless; adds any new tables
```

Then **Reload** on the Web tab. (If you change the threshold or band cut points
in `config.py`, also run `python scripts/prepare_trials.py` and
`python scripts/check_model.py`.)

Avoid pushing changes while participants are mid-study. A reload restarts the
worker. Participants' progress is saved, but anyone who clicks during the
restart sees the "couldn't reach the server, try again" banner.

## 9. On the day of the class (Nov 12)

- Before class, open both links and `/admin` to confirm the app is up, and
  check that "AI fallback calls" is 0.
- Don't `git pull` or reload during class (see step 8).
- Afterwards, check on `/admin` that at least 5 people completed **each**
  version, and take a backup (step 11).

## 10. Keep the web app alive

**Free web apps are switched off if they are not extended.** On the Web tab,
click **"Run until 1 month from today"** (or the equivalent extend button) at
least once a month. Put a recurring reminder in your calendar for the whole
study period. PythonAnywhere also emails a warning before expiry.

## 11. Downloading and backing up the data

- **`/admin`** → `analysis.csv` (one row per real trial), `sessions.csv` (one
  row per session, with `person_key` linking each person's two versions, ready
  for paired tests), plus one CSV per table (`participants`, `responses`, `questionnaire`,
  `ai_calls`, `events`, `assignments`, `trials`).
- **Full backup** (do this after class and every day or two while you collect more data):

  ```bash
  source ~/.virtualenvs/study-env/bin/activate && cd ~/hai-relevance-study
  python scripts/backup_db.py
  ```

  Then download the new file from `instance/backups/` through the **Files** tab
  and store it outside PythonAnywhere. Old backups count against the 512 MB
  quota, so delete them on the server once downloaded.

The downloaded data contains ground truth and, for participants who did not
choose to stay anonymous, their uniqnames. Keep it out of git and out of
public folders.

## 12. Quick reference

| Task | Command / place |
|---|---|
| Check model on server | `python scripts/check_model.py` |
| Load headlines | `python scripts/prepare_trials.py` |
| Create tables | `python scripts/init_db.py` |
| Back up | `python scripts/backup_db.py` |
| Wipe test data | `python scripts/reset_db.py --yes` |
| Look up a uniqname | `/admin` → "Look up a uniqname" |
| Errors | Web tab → Error log |
| Restart app | Web tab → Reload |
