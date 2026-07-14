import hashlib
import os
import sqlite3
from datetime import datetime

from flask import Flask, flash, g, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.config["SECRET_KEY"] = "trekking-management-secret"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "trekking.db")


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS treks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            duration_days INTEGER NOT NULL,
            available_slots INTEGER NOT NULL,
            assigned_staff_id INTEGER,
            status TEXT NOT NULL DEFAULT 'Pending',
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            description TEXT,
            FOREIGN KEY (assigned_staff_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS bookings  (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            trek_id INTEGER NOT NULL,
            booking_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Booked',
            payment_status TEXT NOT NULL DEFAULT 'Pending',
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (trek_id) REFERENCES treks(id)
        );

        CREATE TABLE IF NOT EXISTS staff_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            experience_years INTEGER DEFAULT 0,
            specialties TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )

    admin = conn.execute("SELECT id FROM users WHERE username = ?", ("admin",)).fetchone()
    if not admin:
        conn.execute(
            """
            INSERT INTO users (username, full_name, email, phone, password_hash, role, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "admin",
                "System Administrator",
                "admin@trekking.local",
                "0000000000",
                hash_password("admin123"),
                "admin",
                "active",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

    conn.commit()
    conn.close()


def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


@app.before_request
def load_logged_in_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        conn = get_db()
        g.user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()


@app.route("/")
def index():
    if g.user is not None:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        full_name = request.form["full_name"].strip()
        email = request.form["email"].strip()
        phone = request.form.get("phone", "").strip()
        password = request.form["password"]
        role = request.form.get("role", "user")

        if role not in {"user", "staff"}:
            flash("Invalid role selected. Admin registration is not permitted.", "danger")
            return render_template("register.html")

        if not username or not full_name or not email or not password:
            flash("Please fill in all required fields.", "danger")
            return render_template("register.html")

        conn = get_db()
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ? OR email = ?",
            (username, email),
        ).fetchone()
        if existing:
            conn.close()
            flash("Username or email already exists.", "danger")
            return render_template("register.html")

        status = "pending" if role == "staff" else "active"
        cursor = conn.execute(
            """
            INSERT INTO users (username, full_name, email, phone, password_hash, role, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                full_name,
                email,
                phone,
                hash_password(password),
                role,
                status,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        if role == "staff":
            user_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO staff_profiles (user_id, experience_years, specialties) VALUES (?, ?, ?)",
                (user_id, 0, ""),
            )
        conn.commit()
        conn.close()

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        conn.close()

        if user and user["password_hash"] == hash_password(password):
            if user["status"] == "blacklisted":
                flash("Your account has been blacklisted.", "danger")
                return render_template("login.html")
            if user["role"] == "staff" and user["status"] != "approved":
                flash("Your staff account is still pending approval.", "warning")
                return render_template("login.html")

            session.clear()
            session["user_id"] = user["id"]
            flash("Welcome back!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have logged out.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    if g.user is None:
        return redirect(url_for("login"))

    conn = get_db()
    if g.user["role"] == "admin":
        search = request.args.get("search", "").strip()

        # Stats
        total_treks = conn.execute("SELECT COUNT(*) AS count FROM treks").fetchone()["count"]
        total_users = conn.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'user'").fetchone()["count"]
        total_staff = conn.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'staff'").fetchone()["count"]
        total_bookings = conn.execute("SELECT COUNT(*) AS count FROM bookings").fetchone()["count"]

        # Treks query with search option
        if search:
            treks = conn.execute(
                """
                SELECT t.*, u.full_name AS staff_name 
                FROM treks t 
                LEFT JOIN users u ON t.assigned_staff_id = u.id 
                WHERE t.name LIKE ? OR t.location LIKE ? OR CAST(t.id AS TEXT) = ? 
                ORDER BY t.id DESC
                """,
                (f"%{search}%", f"%{search}%", search),
            ).fetchall()
        else:
            treks = conn.execute(
                "SELECT t.*, u.full_name AS staff_name FROM treks t LEFT JOIN users u ON t.assigned_staff_id = u.id ORDER BY t.id DESC"
            ).fetchall()

        # Users and staff query with search option
        if search:
            users = conn.execute(
                """
                SELECT * FROM users 
                WHERE role != 'admin' AND (full_name LIKE ? OR username LIKE ? OR CAST(id AS TEXT) = ?) 
                ORDER BY id DESC
                """,
                (f"%{search}%", f"%{search}%", search),
            ).fetchall()
        else:
            users = conn.execute("SELECT * FROM users WHERE role != 'admin' ORDER BY id DESC").fetchall()

        # Pending staff
        pending_staff = conn.execute(
            "SELECT * FROM users WHERE role = 'staff' AND status = 'pending' ORDER BY id DESC"
        ).fetchall()

        # Bookings history
        bookings = conn.execute(
            """
            SELECT b.*, u.full_name AS user_name, t.name AS trek_name 
            FROM bookings b 
            JOIN users u ON b.user_id = u.id 
            JOIN treks t ON b.trek_id = t.id 
            ORDER BY b.id DESC
            """
        ).fetchall()

        conn.close()
        return render_template(
            "dashboard.html",
            role="admin",
            stats={
                "treks": total_treks,
                "users": total_users,
                "staff": total_staff,
                "bookings": total_bookings,
            },
            treks=treks,
            pending_staff=pending_staff,
            users=users,
            bookings=bookings,
            search=search,
        )

    if g.user["role"] == "staff":
        # Treks with participant counts
        treks = conn.execute(
            """
            SELECT t.*, COUNT(b.id) AS participant_count 
            FROM treks t 
            LEFT JOIN bookings b ON t.id = b.trek_id AND b.status = 'Booked'
            WHERE t.assigned_staff_id = ? 
            GROUP BY t.id
            ORDER BY t.id DESC
            """,
            (g.user["id"],),
        ).fetchall()

        # Participants list for assigned treks (with trek name)
        participants = conn.execute(
            """
            SELECT b.*, u.full_name, t.name AS trek_name 
            FROM bookings b 
            JOIN users u ON b.user_id = u.id 
            JOIN treks t ON b.trek_id = t.id 
            WHERE t.assigned_staff_id = ?
            ORDER BY b.id DESC
            """,
            (g.user["id"],),
        ).fetchall()

        conn.close()
        return render_template("dashboard.html", role="staff", treks=treks, participants=participants)

    # User Role
    search = request.args.get("search", "").strip()
    difficulty = request.args.get("difficulty", "").strip()

    query = "SELECT * FROM treks WHERE status IN ('Approved', 'Open')"
    params = []
    if search:
        query += " AND (name LIKE ? OR location LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    if difficulty:
        query += " AND difficulty = ?"
        params.append(difficulty)
    query += " ORDER BY id DESC"

    treks = conn.execute(query, params).fetchall()

    bookings = conn.execute(
        "SELECT b.*, t.name AS trek_name FROM bookings b JOIN treks t ON b.trek_id = t.id WHERE b.user_id = ? ORDER BY b.id DESC",
        (g.user["id"],),
    ).fetchall()
    conn.close()
    return render_template(
        "dashboard.html",
        role="user",
        treks=treks,
        bookings=bookings,
        search=search,
        difficulty=difficulty,
    )


@app.route("/treks/new", methods=["GET", "POST"])
def new_trek():
    if g.user is None or g.user["role"] != "admin":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db()
    staff = conn.execute("SELECT * FROM users WHERE role = 'staff' AND status = 'approved'").fetchall()

    if request.method == "POST":
        name = request.form["name"].strip()
        location = request.form["location"].strip()
        difficulty = request.form["difficulty"]
        duration_days = int(request.form["duration_days"])
        available_slots = int(request.form["available_slots"])
        assigned_staff_id = request.form.get("assigned_staff_id") or None
        status = request.form.get("status", "Pending")
        start_date = request.form["start_date"]
        end_date = request.form["end_date"]
        description = request.form.get("description", "").strip()

        conn.execute(
            """
            INSERT INTO treks (name, location, difficulty, duration_days, available_slots, assigned_staff_id, status, start_date, end_date, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, location, difficulty, duration_days, available_slots, assigned_staff_id, status, start_date, end_date, description),
        )
        conn.commit()
        conn.close()
        flash("Trek created successfully.", "success")
        return redirect(url_for("dashboard"))

    conn.close()
    return render_template("manage_trek.html", staff=staff, trek=None)


@app.route("/treks/<int:trek_id>/edit", methods=["GET", "POST"])
def edit_trek(trek_id):
    if g.user is None or g.user["role"] != "admin":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db()
    trek = conn.execute("SELECT * FROM treks WHERE id = ?", (trek_id,)).fetchone()
    staff = conn.execute("SELECT * FROM users WHERE role = 'staff' AND status = 'approved'").fetchall()
    if request.method == "POST":
        conn.execute(
            """
            UPDATE treks
            SET name = ?, location = ?, difficulty = ?, duration_days = ?, available_slots = ?, assigned_staff_id = ?, status = ?, start_date = ?, end_date = ?, description = ?
            WHERE id = ?
            """,
            (
                request.form["name"].strip(),
                request.form["location"].strip(),
                request.form["difficulty"],
                int(request.form["duration_days"]),
                int(request.form["available_slots"]),
                request.form.get("assigned_staff_id") or None,
                request.form.get("status", "Pending"),
                request.form["start_date"],
                request.form["end_date"],
                request.form.get("description", "").strip(),
                trek_id,
            ),
        )
        conn.commit()
        conn.close()
        flash("Trek updated successfully.", "success")
        return redirect(url_for("dashboard"))

    conn.close()
    return render_template("manage_trek.html", staff=staff, trek=trek)


@app.route("/treks/<int:trek_id>/delete", methods=["POST"])
def delete_trek(trek_id):
    if g.user is None or g.user["role"] != "admin":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db()
    conn.execute("DELETE FROM bookings WHERE trek_id = ?", (trek_id,))
    conn.execute("DELETE FROM treks WHERE id = ?", (trek_id,))
    conn.commit()
    conn.close()
    flash("Trek deleted successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/approve-staff/<int:user_id>", methods=["POST"])
def approve_staff(user_id):
    if g.user is None or g.user["role"] != "admin":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db()
    conn.execute("UPDATE users SET status = 'approved' WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("Staff account approved.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/blacklist-user/<int:user_id>", methods=["POST"])
def blacklist_user(user_id):
    if g.user is None or g.user["role"] != "admin":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db()
    conn.execute("UPDATE users SET status = 'blacklisted' WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("User account blacklisted.", "warning")
    return redirect(url_for("dashboard"))


@app.route("/admin/restore-user/<int:user_id>", methods=["POST"])
def restore_user(user_id):
    if g.user is None or g.user["role"] != "admin":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db()
    conn.execute("UPDATE users SET status = 'approved' WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("User account restored and approved.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/delete-user/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    if g.user is None or g.user["role"] != "admin":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db()
    conn.execute("DELETE FROM bookings WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM staff_profiles WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("User account deleted successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/staff/treks/<int:trek_id>/update", methods=["POST"])
def update_trek(trek_id):
    if g.user is None or g.user["role"] != "staff":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    slots = int(request.form["available_slots"])
    status = request.form["status"]
    conn = get_db()
    conn.execute(
        "UPDATE treks SET available_slots = ?, status = ? WHERE id = ? AND assigned_staff_id = ?",
        (slots, status, trek_id, g.user["id"]),
    )
    conn.commit()
    conn.close()
    flash("Trek updated successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/treks/<int:trek_id>/book", methods=["POST"])
def book_trek(trek_id):
    if g.user is None or g.user["role"] != "user":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db()
    trek = conn.execute("SELECT * FROM treks WHERE id = ?", (trek_id,)).fetchone()
    if not trek:
        conn.close()
        flash("Trek not found.", "danger")
        return redirect(url_for("dashboard"))

    if trek["status"] != "Open":
        conn.close()
        flash("This trek is not open for booking yet.", "warning")
        return redirect(url_for("dashboard"))

    if trek["available_slots"] <= 0:
        conn.close()
        flash("No slots available.", "warning")
        return redirect(url_for("dashboard"))

    existing = conn.execute(
        "SELECT id FROM bookings WHERE user_id = ? AND trek_id = ?",
        (g.user["id"], trek_id),
    ).fetchone()
    if existing:
        conn.close()
        flash("You already booked this trek.", "warning")
        return redirect(url_for("dashboard"))

    conn.execute(
        "INSERT INTO bookings (user_id, trek_id, booking_date, status) VALUES (?, ?, ?, ?)",
        (g.user["id"], trek_id, datetime.now().strftime("%Y-%m-%d"), "Booked"),
    )
    conn.execute(
        "UPDATE treks SET available_slots = available_slots - 1 WHERE id = ?",
        (trek_id,),
    )
    conn.commit()
    conn.close()
    flash("Booking successful.", "success")
    return redirect(url_for("dashboard"))


@app.route("/bookings/<int:booking_id>/cancel", methods=["POST"])
def cancel_booking(booking_id):
    if g.user is None:
        return redirect(url_for("login"))

    conn = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if not booking:
        conn.close()
        flash("Booking not found.", "danger")
        return redirect(url_for("dashboard"))

    if g.user["role"] != "admin" and booking["user_id"] != g.user["id"]:
        conn.close()
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    # If it was already cancelled, don't allow cancelling again
    if booking["status"] == "Cancelled":
        conn.close()
        flash("Booking is already cancelled.", "warning")
        return redirect(url_for("dashboard"))

    conn.execute("UPDATE bookings SET status = 'Cancelled' WHERE id = ?", (booking_id,))
    conn.execute("UPDATE treks SET available_slots = available_slots + 1 WHERE id = ?", (booking["trek_id"],))
    conn.commit()
    conn.close()
    flash("Booking cancelled successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if g.user is None:
        return redirect(url_for("login"))

    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        email = request.form["email"].strip()
        phone = request.form.get("phone", "").strip()
        conn = get_db()
        conn.execute(
            "UPDATE users SET full_name = ?, email = ?, phone = ? WHERE id = ?",
            (full_name, email, phone, g.user["id"]),
        )
        conn.commit()
        conn.close()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))

    return render_template("profile.html")


init_db()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)