import os
import sqlite3
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'agri-secret-key-change-in-production')
DATABASE = 'tractor.db'

# ----------------- Database Helpers -----------------

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON;")
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        # Users table
        db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL UNIQUE,
                email TEXT,
                password_hash TEXT NOT NULL,
                role TEXT CHECK(role IN ('farmer', 'owner')) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        # Tractors table
        db.execute('''
            CREATE TABLE IF NOT EXISTS tractors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                hp INTEGER NOT NULL,
                price_per_hour REAL NOT NULL,
                price_per_acre REAL NOT NULL,
                location TEXT NOT NULL,
                contact_number TEXT NOT NULL,
                description TEXT,
                is_available INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE CASCADE
            );
        ''')
        # Bookings table
        db.execute('''
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tractor_id INTEGER NOT NULL,
                farmer_id INTEGER NOT NULL,
                booking_date DATE NOT NULL,
                duration_type TEXT CHECK(duration_type IN ('hours', 'acres')) NOT NULL,
                duration_value REAL NOT NULL,
                work_type TEXT NOT NULL,
                notes TEXT,
                total_price REAL NOT NULL,
                status TEXT CHECK(status IN ('Pending', 'Accepted', 'Rejected')) DEFAULT 'Pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tractor_id) REFERENCES tractors (id) ON DELETE CASCADE,
                FOREIGN KEY (farmer_id) REFERENCES users (id) ON DELETE CASCADE
            );
        ''')
        db.commit()

# ----------------- Decorators & Auth -----------------

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in first.', 'warning')
                return redirect(url_for('login'))
            if session.get('user_role') != role:
                flash('Unauthorized access for your user type.', 'danger')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ----------------- Authentication Routes -----------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name'].strip()
        phone = request.form['phone'].strip()
        email = request.form['email'].strip()
        password = request.form['password']
        role = request.form['role']

        if not name or not phone or not password or role not in ['farmer', 'owner']:
            flash('Please fill in all required fields accurately.', 'danger')
            return redirect(url_for('register'))

        db = get_db()
        user_exists = db.execute("SELECT id FROM users WHERE phone = ?", (phone,)).fetchone()
        if user_exists:
            flash('A user with this phone number already exists.', 'warning')
            return redirect(url_for('register'))

        hashed_pw = generate_password_hash(password)
        db.execute(
            "INSERT INTO users (name, phone, email, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            (name, phone, email, hashed_pw, role)
        )
        db.commit()
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form['phone'].strip()
        password = request.form['password']

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()

        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_role'] = user['role']
            session['user_phone'] = user['phone']
            flash(f"Welcome back, {user['name']}!", 'success')
            
            if user['role'] == 'owner':
                return redirect(url_for('owner_dashboard'))
            return redirect(url_for('index'))
        else:
            flash('Invalid phone number or password.', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))

# ----------------- Farmer / Public Routes -----------------

@app.route('/')
def index():
    location_query = request.args.get('location', '').strip()
    max_price = request.args.get('max_price', '').strip()

    db = get_db()
    query = """
        SELECT t.*, u.name as owner_name 
        FROM tractors t 
        JOIN users u ON t.owner_id = u.id 
        WHERE t.is_available = 1
    """
    params = []

    if location_query:
        query += " AND t.location LIKE ?"
        params.append(f"%{location_query}%")

    if max_price:
        try:
            val = float(max_price)
            query += " AND (t.price_per_hour <= ? OR t.price_per_acre <= ?)"
            params.extend([val, val])
        except ValueError:
            pass

    query += " ORDER BY t.created_at DESC"
    tractors = db.execute(query, params).fetchall()

    return render_template('index.html', tractors=tractors, location_query=location_query, max_price=max_price)

@app.route('/book/<int:tractor_id>', methods=['GET', 'POST'])
@login_required
@role_required('farmer')
def book_tractor(tractor_id):
    db = get_db()
    tractor = db.execute("SELECT * FROM tractors WHERE id = ?", (tractor_id,)).fetchone()
    if not tractor:
        flash('Tractor listing not found.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        booking_date = request.form['booking_date']
        duration_type = request.form['duration_type']
        duration_value = float(request.form['duration_value'])
        work_type = request.form['work_type']
        notes = request.form.get('notes', '')

        unit_price = tractor['price_per_hour'] if duration_type == 'hours' else tractor['price_per_acre']
        total_price = round(unit_price * duration_value, 2)

        db.execute('''
            INSERT INTO bookings (tractor_id, farmer_id, booking_date, duration_type, duration_value, work_type, notes, total_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (tractor_id, session['user_id'], booking_date, duration_type, duration_value, work_type, notes, total_price))
        db.commit()

        flash('Booking request submitted! Waiting for owner acceptance.', 'success')
        return redirect(url_for('farmer_bookings'))

    return render_template('book_tractor.html', tractor=tractor)

@app.route('/my-bookings')
@login_required
@role_required('farmer')
def farmer_bookings():
    db = get_db()
    bookings = db.execute('''
        SELECT b.*, t.model, t.location, t.contact_number, u.name as owner_name 
        FROM bookings b
        JOIN tractors t ON b.tractor_id = t.id
        JOIN users u ON t.owner_id = u.id
        WHERE b.farmer_id = ?
        ORDER BY b.created_at DESC
    ''', (session['user_id'],)).fetchall()
    return render_template('farmer_bookings.html', bookings=bookings)

# ----------------- Owner Routes -----------------

@app.route('/owner/dashboard')
@login_required
@role_required('owner')
def owner_dashboard():
    db = get_db()
    my_tractors = db.execute(
        "SELECT * FROM tractors WHERE owner_id = ? ORDER BY created_at DESC", 
        (session['user_id'],)
    ).fetchall()

    incoming_bookings = db.execute('''
        SELECT b.*, t.model, u.name as farmer_name, u.phone as farmer_phone
        FROM bookings b
        JOIN tractors t ON b.tractor_id = t.id
        JOIN users u ON b.farmer_id = u.id
        WHERE t.owner_id = ?
        ORDER BY b.created_at DESC
    ''', (session['user_id'],)).fetchall()

    return render_template('owner_dashboard.html', tractors=my_tractors, bookings=incoming_bookings)

@app.route('/owner/tractor/new', methods=['GET', 'POST'])
@login_required
@role_required('owner')
def add_tractor():
    if request.method == 'POST':
        model = request.form['model'].strip()
        hp = int(request.form['hp'])
        price_per_hour = float(request.form['price_per_hour'])
        price_per_acre = float(request.form['price_per_acre'])
        location = request.form['location'].strip()
        contact_number = request.form['contact_number'].strip()
        description = request.form.get('description', '').strip()

        db = get_db()
        db.execute('''
            INSERT INTO tractors (owner_id, model, hp, price_per_hour, price_per_acre, location, contact_number, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (session['user_id'], model, hp, price_per_hour, price_per_acre, location, contact_number, description))
        db.commit()

        flash('Tractor listing published successfully!', 'success')
        return redirect(url_for('owner_dashboard'))

    return render_template('tractor_form.html', action='Add', tractor=None)

@app.route('/owner/tractor/edit/<int:tractor_id>', methods=['GET', 'POST'])
@login_required
@role_required('owner')
def edit_tractor(tractor_id):
    db = get_db()
    tractor = db.execute("SELECT * FROM tractors WHERE id = ? AND owner_id = ?", (tractor_id, session['user_id'])).fetchone()
    if not tractor:
        flash('Listing not found or access denied.', 'danger')
        return redirect(url_for('owner_dashboard'))

    if request.method == 'POST':
        model = request.form['model'].strip()
        hp = int(request.form['hp'])
        price_per_hour = float(request.form['price_per_hour'])
        price_per_acre = float(request.form['price_per_acre'])
        location = request.form['location'].strip()
        contact_number = request.form['contact_number'].strip()
        description = request.form.get('description', '').strip()

        db.execute('''
            UPDATE tractors 
            SET model=?, hp=?, price_per_hour=?, price_per_acre=?, location=?, contact_number=?, description=?
            WHERE id = ? AND owner_id = ?
        ''', (model, hp, price_per_hour, price_per_acre, location, contact_number, description, tractor_id, session['user_id']))
        db.commit()

        flash('Listing updated successfully.', 'success')
        return redirect(url_for('owner_dashboard'))

    return render_template('tractor_form.html', action='Edit', tractor=tractor)

@app.route('/owner/tractor/delete/<int:tractor_id>', methods=['POST'])
@login_required
@role_required('owner')
def delete_tractor(tractor_id):
    db = get_db()
    db.execute("DELETE FROM tractors WHERE id = ? AND owner_id = ?", (tractor_id, session['user_id']))
    db.commit()
    flash('Tractor listing removed.', 'info')
    return redirect(url_for('owner_dashboard'))

@app.route('/owner/booking/<int:booking_id>/<action>', methods=['POST'])
@login_required
@role_required('owner')
def update_booking_status(booking_id, action):
    if action not in ['accept', 'reject']:
        flash('Invalid action.', 'danger')
        return redirect(url_for('owner_dashboard'))

    status = 'Accepted' if action == 'accept' else 'Rejected'
    db = get_db()

    # Ensure this booking belongs to a tractor owned by the logged-in user
    booking = db.execute('''
        SELECT b.id FROM bookings b 
        JOIN tractors t ON b.tractor_id = t.id 
        WHERE b.id = ? AND t.owner_id = ?
    ''', (booking_id, session['user_id'])).fetchone()

    if not booking:
        flash('Booking request not found or unauthorized.', 'danger')
        return redirect(url_for('owner_dashboard'))

    db.execute("UPDATE bookings SET status = ? WHERE id = ?", (status, booking_id))
    db.commit()

    flash(f'Booking request {status.lower()} successfully!', 'success')
    return redirect(url_for('owner_dashboard'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)