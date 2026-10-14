"""
Turn an existing ReCampus account into an administrator (or back to a student).

Usage (in the VS Code terminal, inside the ReCampus folder):
    python make_admin.py your.name2025@vitstudent.ac.in
    python make_admin.py your.name2025@vitstudent.ac.in --student     (remove admin rights)

The account must already exist: sign up on the website first, then run this once.
Administrators can run the OS scheduler (Process Next Report) and manage any record.
"""
import sys

from app import connect, init_db


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)
    role = "student" if "--student" in sys.argv else "admin"
    email = args[0].strip().lower()

    init_db()
    db = connect()
    row = db.execute("SELECT id, name FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        print(f"No account found for {email}. Sign up on the website first, then run this again.")
        sys.exit(1)
    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, row["id"]))
    db.close()
    print(f"Done: {row['name']} ({email}) is now a(n) {role}.")


if __name__ == "__main__":
    main()
