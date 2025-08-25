from flask import render_template, request, redirect, url_for, session, flash
from app import app, mongo
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta

app.secret_key = "f=qSj{PuL:,&^IP^zgDL=ez@dcSM"  # Set a strong secret key!

def generate_month_slots(year, month):
    slots = []
    first_day = datetime(year, month, 1)
    # Get last day of month
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    days_in_month = (next_month - first_day).days

    for d in range(days_in_month):
        date = first_day + timedelta(days=d)
        day_slots = []
        for h in range(9, 18):
            slot = {
                'time': f'{h}:00 - {h+1}:00',
                'private': None,
                'group': []
            }
            day_slots.append(slot)
        slots.append({
            'date': date.strftime('%Y-%m-%d'),
            'weekday': date.weekday(),  # Monday=0
            'slots': day_slots
        })
    return slots

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        if password != confirm_password:
            flash('Passwords do not match.')
            return redirect(url_for('signup'))
        password = generate_password_hash(password, method='pbkdf2:sha256')
        if mongo.db.users.find_one({'email': email}):
            flash('Email already registered.')
            return redirect(url_for('signup'))
        mongo.db.users.insert_one({'name': name, 'email': email, 'password': password})
        flash('Signup successful! Please log in.')
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = mongo.db.users.find_one({'email': email})
        if user and check_password_hash(user['password'], password):
            session['user_id'] = str(user['_id'])
            session['user_name'] = user['name']
            session['user_email'] = user['email']
            flash('Logged in successfully!')
            return redirect(url_for('lessons'))
        else:
            flash('Invalid email or password.')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.')
    return redirect(url_for('login'))

# Protect lessons and membership routes
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/lessons', methods=['GET', 'POST'])
@login_required
def lessons():
    message = None
    # Get month/year from query params, default to current
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    today = datetime.now()
    if not year:
        year = today.year
    if not month:
        month = today.month

    lesson_days = generate_month_slots(year, month)

    selected_day_idx = request.args.get('day')
    selected_day = None
    if selected_day_idx is not None:
        selected_day_idx = int(selected_day_idx)
        selected_day = lesson_days[selected_day_idx]

    # Load bookings from MongoDB
    bookings = list(mongo.db.bookings.find({}))
    for day_idx, day in enumerate(lesson_days):
        for slot_idx, slot in enumerate(day['slots']):
            slot['group'] = []  # Reset group list
            slot['private'] = None  # Reset private slot
            for booking in bookings:
                if booking['date'] == day['date'] and booking['time'] == slot['time']:
                    if booking['lesson_type'] == 'group':
                        slot['group'].append({'name': booking['name'], 'email': booking['email']})
                    elif booking['lesson_type'] == 'private':
                        slot['private'] = {'name': booking['name'], 'email': booking['email']}

    if request.method == 'POST':
        day_idx = int(request.form['day_idx'])
        slot_idx = int(request.form['slot_idx'])
        lesson_type = request.form['lesson_type']
        name = request.form['name']
        email = request.form['email']
        slot = lesson_days[day_idx]['slots'][slot_idx]

        # Check booking status
        if lesson_type == 'private':
            if slot['private'] is None:
                mongo.db.bookings.insert_one({
                    'date': lesson_days[day_idx]['date'],
                    'time': slot['time'],
                    'lesson_type': 'private',
                    'name': name,
                    'email': email
                })
                message = 'Private lesson booked!'
            else:
                message = 'This private slot is already booked.'
        elif lesson_type == 'group':
            if len(slot['group']) < 5:
                mongo.db.bookings.insert_one({
                    'date': lesson_days[day_idx]['date'],
                    'time': slot['time'],
                    'lesson_type': 'group',
                    'name': name,
                    'email': email
                })
                message = f'Group lesson booked! ({len(slot["group"]) + 1}/5)'
            else:
                message = 'This group slot is full.'
        selected_day = lesson_days[day_idx]
        selected_day_idx = day_idx

    # Calculate weekday index for the first day
    first_day_dt = datetime(year, month, 1)
    weekday = first_day_dt.weekday()  # Monday=0
    # For Sunday=0 alignment:
    weekday = (weekday + 1) % 7

    month_name = first_day_dt.strftime('%B')

    return render_template(
        'lessons.html',
        lesson_days=lesson_days,
        selected_day=selected_day,
        selected_day_idx=selected_day_idx,
        message=message,
        weekday=weekday,
        month_name=month_name,
        year=year,
        month=month,
        now=datetime.now()
    )

@app.route('/courts')
def courts():
    return render_template('courts.html')

@app.route('/membership', methods=['GET', 'POST'])
@login_required
def membership():
    return render_template('membership.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

