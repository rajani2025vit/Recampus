# ReCampus – Lost, Found, Borrow & Lend (Flask + SQLite)

College Operating Systems project for VIT Chennai. Plain HTML/CSS/JavaScript frontend,
Python Flask backend, SQLite database. No other frameworks.

## Folder structure

```
ReCampus/
├── app.py               ← Flask backend: database, login, all APIs, OS scheduler logic
├── make_admin.py        ← run once to make an account an administrator
├── test_app.py          ← automated tests (python -m unittest -v test_app)
├── requirements.txt     ← just Flask
├── README.md
├── templates/           ← your HTML pages: index, lost, found, borrow, lend,
│                          signin, signup, scheduler (new), account (new)
├── static/
│   ├── style.css        ← your stylesheet + new sections at the bottom
│   ├── script.js        ← your script, now talking to the server
│   ├── assets/vit-chennai.jpg
│   └── uploads/         ← uploaded images are stored here
├── recampus.db          ← created automatically on first run (your data lives here)
└── secret_key.txt       ← created automatically on first run (signs login cookies; keep private)
```

## Run it on Windows with VS Code

1. Install Python 3.10+ from python.org (tick **"Add python.exe to PATH"**).
2. Unzip the project. In VS Code: **File → Open Folder…** → choose the `ReCampus` folder.
3. Open a terminal (**Terminal → New Terminal**) and run:
   ```
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```
   If PowerShell blocks `activate`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use **Command Prompt**.
4. Open **http://127.0.0.1:5000** in your browser. Stop the server with `Ctrl+C`.
5. Always open the site through that address. Double-clicking an `.html` file will not work, because the pages need the Flask server.

### Make yourself an administrator (needed for the OS Scheduler buttons)
1. Sign up on the website with your `@vitstudent.ac.in` email.
2. Open a **second** terminal (same folder, `venv\Scripts\activate`) and run:
   `python make_admin.py your.email@vitstudent.ac.in`
3. Refresh the site. The navbar now shows "(admin)".

## What each role can do

| Action | Visitor | Student | Admin |
|---|---|---|---|
| Browse Lost/Found/Lend/Borrow, watch the dashboard | yes | yes | yes |
| See contact details on Lost/Found cards | no | yes | yes |
| Submit reports/listings, request items | no | yes | yes |
| Complete **own** Found report; complete **own** Processing Lost report | – | yes | yes |
| Approve / reject / mark returned on **own** lend items | – | yes | yes |
| Same on **anyone's** records, and **Process Next Report** | – | no | yes |

Privacy: a lender's contact is shown only to the lender, admins, and the borrower whose request was approved. A borrower's hand-over contact is shown only to the lender and admins. Passwords are stored only as salted PBKDF2-SHA256 hashes.

## OS concepts (application-level simulation)

This is **not** real CPU scheduling or kernel resource management. It models the policies on top of a web app.

| Concept | How ReCampus does it |
|---|---|
| Non-preemptive Priority + FCFS | Each Lost report is a process (PID = report id). `ORDER BY High first, then id` picks the next one. A running report is never interrupted. |
| Process states | Ready = Pending, Running = Processing, Terminated = Completed. Every change is saved in `lost_state_log`. |
| Mutual exclusion | "Process Next" runs in one SQLite `BEGIN IMMEDIATE` transaction (takes the database write lock, checks nothing is Processing, then updates). A partial `UNIQUE` index makes the database itself reject a second Processing row. A Python lock alone would only protect one Python process, not the database. |
| Resource allocation | A lend item is an exclusive resource. Approve = allocate (Available → Borrowed); Return = release. Both are atomic transactions, and a partial `UNIQUE` index allows only one Approved request per item. |

The **OS Scheduler** page shows the live queue, the running report, the state diagram, the transition log and these explanations.

## Testing

- `python -m unittest -v test_app` runs 21 automated tests on a temporary database (your real data is untouched), including 12 admins pressing "Process Next" at the same instant (exactly one wins).
- Viva demo: on the Lost page submit Low, Low, High → on the OS Scheduler page the High report is first → Process Next → submit another High (it does **not** interrupt) → press Process Next again (refused: already Processing) → Mark Completed → Process Next.

## Notes

- Debug mode is on in `app.py` (auto-reload while you edit). The server only listens on your own computer (127.0.0.1).
- Back up your data by copying `recampus.db` and `static/uploads/` while the server is stopped.
- Older databases are migrated on start: missing tables/columns are added and existing rows are kept.
