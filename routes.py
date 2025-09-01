from flask import render_template, request, redirect, url_for, flash, session, jsonify
from app import app, mongo, stripe, stripe_public_key, MEMBERSHIP_PLANS
import os

# Defensive initialization: some deployment/import orders leave `mongo` as None
# (or without a working .db). Try to ensure `mongo.db` is available by:
# 1. using the imported `mongo` if it already has `.db`;
# 2. attempting to create a `flask_pymongo.PyMongo(app)` instance;
# 3. falling back to a direct `pymongo.MongoClient` and exposing a lightweight
#    module-level `mongo` object with a `.db` attribute.
try:
    if mongo is None or getattr(mongo, 'db', None) is None:
        raise Exception('mongo unavailable')
except Exception:
    initialized = False
    # Try Flask-PyMongo first (preferred)
    try:
        from flask_pymongo import PyMongo
        print('routes.py: attempting to initialize PyMongo(app) as fallback')
        mongo = PyMongo(app)
        if getattr(mongo, 'db', None) is not None:
            initialized = True
            print('routes.py: initialized flask_pymongo successfully')
    except Exception as e:
        print(f'routes.py: flask_pymongo init failed: {e}')

    if not initialized:
        # Final fallback: use raw pymongo MongoClient and expose a `.db`
        try:
            from pymongo import MongoClient
            uri = app.config.get('MONGO_URI') or os.getenv('MONGO_URI') or 'mongodb://localhost:27017/primecourt'
            print(f'routes.py: falling back to pymongo MongoClient using URI: {uri}')
            client = MongoClient(uri)
            try:
                # Use default database from URI if present
                db = client.get_default_database()
                if db is None:
                    db = client['primecourt']
            except Exception:
                db = client['primecourt']

            class _SimpleMongo:
                pass
            mongo = _SimpleMongo()
            mongo.db = db
            print('routes.py: pymongo fallback initialized; mongo.db is available')
            initialized = True
        except Exception as e:
            print(f'routes.py: pymongo fallback failed: {e}')
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os
from bson import ObjectId
import datetime as dt  # Add this import for datetime operations
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from threading import Thread
from functools import wraps
from zoneinfo import ZoneInfo

LESSON_TYPES = {
    'group': {'name': 'Group Lesson', 'price_per_hour': 2500, 'capacity': 6},
    'private': {'name': 'Private Lesson', 'price_per_hour': 6000, 'capacity': 1},
}

app.secret_key = "f=qSj{PuL:,&^IP^zgDL=ez@dcSM"

# Default lesson pricing (in dollars)
DEFAULT_LESSON_PRICES = {
    'private': 50.00,
    'group': 25.00
}

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_admin'):
            flash('Access denied. Admin access required.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# Lightweight login_required defined early so decorators used above work at import time
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def _parse_range_minutes(range_str: str):
    parts = [p.strip() for p in str(range_str).split('-')]
    if len(parts) != 2:
        return None
    s = _parse_time_token(parts[0])
    e = _parse_time_token(parts[1])
    if s is None or e is None:
        return None
    return s, e

def _parse_time_token(token: str):
    token = token.strip()
    fmts = ["%I:%M %p", "%I %p", "%H:%M"]
    for fmt in fmts:
        try:
            t = datetime.strptime(token, fmt)
            return t.hour * 60 + t.minute
        except Exception:
            continue
    return None


@app.route('/_debug_db')
def _debug_db():
    """Temporary debug endpoint to check Mongo connectivity and sample coach data.
    Do not enable in production long-term. Returns a small JSON summary.
    """
    info = {'can_connect': False}
    try:
        uri = app.config.get('MONGO_URI') or os.getenv('MONGO_URI') or ''
        info['mongo_uri_masked'] = (uri[:12] + '...') if uri else None
    except Exception:
        info['mongo_uri_masked'] = None

    try:
        # Ping the server
        try:
            mongo.db.command('ping')
            info['can_connect'] = True
        except Exception as e:
            info['can_connect'] = False
            info['ping_error'] = str(e)

        # Counts and a single sample coach (mask ObjectId)
        try:
            info['users_count'] = mongo.db.users.count_documents({})
            info['coaches_count'] = mongo.db.users.count_documents({'role': 'coach'})
            sample = mongo.db.users.find_one({'role': 'coach'}, {'password': 0})
            if sample:
                sample['_id'] = str(sample.get('_id'))
            info['sample_coach'] = sample
        except Exception as e:
            info['counts_error'] = str(e)
    except Exception as e:
        info['error'] = str(e)

    return jsonify(info)

def get_lesson_prices():
    """Get lesson prices from database or return defaults if not set"""
    prices = mongo.db.settings.find_one({'type': 'pricing'})
    if prices and 'lessons' in prices:
        return {
            'private': int(prices['lessons'].get('private', DEFAULT_LESSON_PRICES['private'])),
            'group': int(prices['lessons'].get('group', DEFAULT_LESSON_PRICES['group']))
        }
    return DEFAULT_LESSON_PRICES.copy()

def update_lesson_prices(private_price, group_price):
    """Update lesson prices in the database"""
    try:
        mongo.db.settings.update_one(
            {'type': 'pricing'},
            {'$set': {
                'type': 'pricing',
                'lessons': {
                    'private': int(private_price * 100),  # Convert to cents
                    'group': int(group_price * 100)       # Convert to cents
                },
                'updated_at': datetime.utcnow()
            }},
            upsert=True
        )
        return True
    except Exception as e:
        print(f"Error updating lesson prices: {e}")
        return False

# Email configuration
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USER = os.getenv('EMAIL_USER', 'your-email@gmail.com')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', 'your-app-password')
EMAIL_FROM = os.getenv('EMAIL_FROM', 'noreply@primecourt.com')

def send_email_async(subject, recipient, html_content, text_content=None):
    """Send email asynchronously to avoid blocking the main thread"""
    def send_email():
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = EMAIL_FROM
            msg['To'] = recipient
            
            if text_content:
                msg.attach(MIMEText(text_content, 'plain'))
            msg.attach(MIMEText(html_content, 'html'))
            
            with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
                server.starttls()
                server.login(EMAIL_USER, EMAIL_PASSWORD)
                server.send_message(msg)
            
            print(f"Email sent successfully to {recipient}")
        except Exception as e:
            print(f"Error sending email to {recipient}: {str(e)}")
    
    # Start email sending in background thread
    Thread(target=send_email).start()

def send_booking_confirmation_email(booking_type, customer_name, customer_email, date, time, details):
    """Send booking confirmation email"""
    subject = f"PrimeCourt Arena - {booking_type} Booking Confirmation"
    
    html_content = f"""
    <html>
    <body>
        <h2>🎾 Booking Confirmation</h2>
        <p>Hi {customer_name},</p>
        <p>Your {booking_type.lower()} has been successfully booked!</p>
        
        <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
            <h3>Booking Details:</h3>
            <p><strong>Date:</strong> {date}</p>
            <p><strong>Time:</strong> {time}</p>
            {details}
        </div>
        
        <p>We look forward to seeing you at PrimeCourt Arena!</p>
        
        <p>Best regards,<br>
        PrimeCourt Arena Team</p>
    </body>
    </html>
    """
    
    text_content = f"""
    Booking Confirmation
    
    Hi {customer_name},
    
    Your {booking_type.lower()} has been successfully booked!
    
    Booking Details:
    Date: {date}
    Time: {time}
    {details}
    
    We look forward to seeing you at PrimeCourt Arena!
    
    Best regards,
    PrimeCourt Arena Team
    """
    
    send_email_async(subject, customer_email, html_content, text_content)

def send_reminder_email(booking_type, customer_name, customer_email, date, time, details):
    """Send reminder email 1 day before the booking"""
    subject = f"PrimeCourt Arena - Reminder: {booking_type} Tomorrow"
    
    html_content = f"""
    <html>
    <body>
        <h2>⏰ Booking Reminder</h2>
        <p>Hi {customer_name},</p>
        <p>This is a friendly reminder about your {booking_type.lower()} tomorrow!</p>
        
        <div style="background: #fff3cd; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #ffc107;">
            <h3>Tomorrow's Booking:</h3>
            <p><strong>Date:</strong> {date}</p>
            <p><strong>Time:</strong> {time}</p>
            {details}
        </div>
        
        <p>Please arrive 10 minutes before your scheduled time.</p>
        
        <p>See you tomorrow!<br>
        PrimeCourt Arena Team</p>
    </body>
    </html>
    """
    
    text_content = f"""
    Booking Reminder
    
    Hi {customer_name},
    
    This is a friendly reminder about your {booking_type.lower()} tomorrow!
    
    Tomorrow's Booking:
    Date: {date}
    Time: {time}
    {details}
    
    Please arrive 10 minutes before your scheduled time.
    
    See you tomorrow!
    PrimeCourt Arena Team
    """
    
    send_email_async(subject, customer_email, html_content, text_content)


def _assign_coach_for_date(date_str):
    """Return coach info dict {'coach_id': str, 'coach_name': name} if a coach is assigned/available for date_str."""
    try:
        # First check weekly availability collection (one coach per weekday)
        try:
            d = datetime.strptime(date_str, '%Y-%m-%d')
            weekday = d.weekday()  # Monday=0
            weekly = mongo.db.coach_weekly_availability.find_one({'weekdays': weekday})
            if weekly:
                return {'coach_id': str(weekly.get('coach_id') or ''), 'coach_name': weekly.get('coach_name', '')}
        except Exception:
            pass

        # Fallback: check per-date availability
        avail = mongo.db.coach_availability.find_one({'date': date_str})
        if not avail:
            return None
        coach_id = avail.get('coach_id')
        coach = None
        if coach_id:
            try:
                uid = coach_id
                if isinstance(uid, str):
                    uid = ObjectId(uid)
                coach = mongo.db.users.find_one({'_id': uid}, {'name': 1})
            except Exception:
                coach = None
        if coach:
            return {'coach_id': str(avail.get('coach_id')), 'coach_name': coach.get('name', '')}
        # Fallback to storing name on availability document
        if 'coach_name' in avail:
            return {'coach_id': str(avail.get('coach_id') or ''), 'coach_name': avail.get('coach_name', '')}
    except Exception as e:
        print(f"Error assigning coach for {date_str}: {e}")
    return None

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
        for h in range(9, 22):
            # Convert to 12-hour format
            start_hour = h if h <= 12 else h - 12
            end_hour = h + 1 if h + 1 <= 12 else (h + 1) - 12
            start_ampm = 'AM' if h < 12 else 'PM'
            end_ampm = 'AM' if h + 1 < 12 else 'PM'
            
            slot = {
                'time': f'{start_hour}:00 {start_ampm} - {end_hour}:00 {end_ampm}',
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
        name = (request.form['name'] or '').strip()
        # Enforce maximum name length to avoid layout break on small screens
        MAX_NAME_LEN = 32
        if len(name) == 0:
            flash('Please provide your name.')
            return redirect(url_for('signup'))
        if len(name) > MAX_NAME_LEN:
            flash(f'Name is too long. Please use {MAX_NAME_LEN} characters or fewer.', 'error')
            return redirect(url_for('signup'))
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
            
        # Check if this is the first user (make them admin by default)
        user_count = mongo.db.users.count_documents({})
        is_admin = user_count == 0
        
        # Only allow admins to create coach accounts
        is_coach = False
        if 'is_admin' in session and session['is_admin'] and request.form.get('is_coach') == 'yes':
            is_coach = True
            
        user_data = {
            'name': name, 
            'email': email, 
            'password': password,
            'role': 'admin' if is_admin else ('coach' if is_coach else 'member'),
            'is_admin': is_admin,
            'created_at': datetime.now(),
            'is_active': not is_coach,  # Coach accounts need admin approval
            'bio': 'Professional tennis coach' if is_coach else '',
            'specialties': ['Tennis', 'Coaching'] if is_coach else []
        }
        
        mongo.db.users.insert_one(user_data)
        flash('Signup successful! Please log in.')
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        # First, find the user by email only
        user = mongo.db.users.find_one({'email': email})
        
        if not user:
            flash('No account found with this email.')
            return redirect(url_for('login'))
            
        # Check if the account is active
        if not user.get('is_active', True):
            flash('This account is disabled or pending approval. Contact admin.', 'error')
            return redirect(url_for('login'))
            
        # Verify password
        if not check_password_hash(user['password'], password):
            flash('Incorrect password.')
            return redirect(url_for('login'))
        
        # Set session variables (limit displayed name length to avoid breaking mobile nav)
        session['user_id'] = str(user['_id'])
        display_name = user.get('name', '') or ''
        session['user_name'] = (display_name[:32] + '...') if len(display_name) > 32 else display_name
        session['user_email'] = user['email']
        session['is_admin'] = user.get('role') == 'admin'
        session['is_coach'] = user.get('role') == 'coach'

        # Redirect based on role
        flash('Logged in successfully!')
        if session['is_admin']:
            return redirect(url_for('admin_dashboard'))
        elif session['is_coach']:
            return redirect(url_for('coach_dashboard'))
        return redirect(url_for('lessons'))

    return render_template('login.html')

@app.route('/coach/dashboard', methods=['GET', 'POST'])
def coach_dashboard():
    if 'user_id' not in session or not session.get('is_coach'):
        flash('Access denied. Coach access required.', 'error')
        return redirect(url_for('login'))

    from bson import ObjectId

    user_id = session['user_id']

    # GET: show editable profile and coach-related data (upcoming lessons, availability)
    try:
        coach = mongo.db.users.find_one({'_id': ObjectId(user_id)}, {'password': 0})
    except Exception:
        coach = None

    if not coach:
        flash('Coach profile not found.', 'error')
        return redirect(url_for('login'))

    # Allow coach to select a weekday to view (0=Mon .. 6=Sun). Default to today
    selected_wd = request.args.get('weekday', type=int)
    today = datetime.now()
    if selected_wd is None:
        selected_wd = today.weekday()

    # Find bookings assigned to this coach for the next 30 days matching the selected weekday
    coach_id_str = str(session['user_id'])
    upcoming = []
    for b in mongo.db.bookings.find({'coach_id': coach_id_str}).sort('date', 1):
        bdate = b.get('date')
        if isinstance(bdate, datetime):
            ds = bdate.strftime('%Y-%m-%d')
        elif isinstance(bdate, str):
            ds = bdate.split('T')[0][:10]
        else:
            continue
        try:
            d = datetime.strptime(ds, '%Y-%m-%d')
        except Exception:
            continue
        # Skip cancelled or already-done bookings
        if b.get('status') in ('cancelled', 'done'):
            continue

        # If booking date matches selected weekday and is today or in future, consider it
        if d.weekday() == selected_wd and d >= today.replace(hour=0, minute=0, second=0, microsecond=0):
            # Determine if the lesson has already finished (end time passed). If so, mark it done automatically and skip.
            try:
                app_tz = os.getenv('APP_TIMEZONE', 'America/New_York')
                TZ = ZoneInfo(app_tz)
                time_range = b.get('time') or ''
                rng = _parse_range_minutes(time_range)
                if rng:
                    # rng is (start_min, end_min) in minutes since midnight
                    end_min = rng[1]
                    # Build end datetime in local TZ
                    end_hour = end_min // 60
                    end_minute = end_min % 60
                    end_dt = datetime(d.year, d.month, d.day, end_hour, end_minute).replace(tzinfo=TZ)
                    now_local = datetime.now(TZ)
                    if now_local >= end_dt:
                        # Mark booking as done and skip adding to upcoming
                        try:
                            mongo.db.bookings.update_one({'_id': b['_id']}, {'$set': {'status': 'done', 'done_at': datetime.utcnow()}})
                        except Exception as e:
                            print(f"Warning: could not mark booking {b.get('_id')} done: {e}")
                        continue
            except Exception:
                # If anything fails during time parsing, fall back to including the booking
                pass

            upcoming.append(b)

    # Get this coach's weekly availability to show selection state
    weekdays_docs = list(mongo.db.coach_weekly_availability.find({'coach_id': session['user_id']}))
    selected_weekdays = [doc.get('weekdays') for doc in weekdays_docs]
    # Flatten
    sel_wd_flat = []
    for s in selected_weekdays:
        if isinstance(s, list):
            sel_wd_flat.extend(s)

    return render_template('coach_dashboard.html', 
                         coach=coach,
                         coach_lessons=upcoming,
                         now=datetime.now(),
                         selected_weekday=selected_wd,
                         my_weekdays=sel_wd_flat)


@app.route('/coach/profile', methods=['GET', 'POST'])
def coach_profile_edit():
    """Separate 'My Profile' page for coaches. Accepts JSON POST for AJAX saves.
    GET: render profile edit form.
    POST (JSON): update fields and return JSON {success: True} or error.
    """
    if 'user_id' not in session or not session.get('is_coach'):
        flash('Access denied. Coach access required.', 'error')
        return redirect(url_for('login'))

    from bson import ObjectId

    user_id = session['user_id']

    try:
        coach = mongo.db.users.find_one({'_id': ObjectId(user_id)}, {'password': 0})
    except Exception:
        coach = None

    if not coach:
        flash('Coach profile not found.', 'error')
        return redirect(url_for('login'))

    if request.method == 'GET':
        return render_template('coach_my_profile.html', coach=coach)

    # POST: accept JSON or form data
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form.to_dict()

    bio = (data.get('bio') or '').strip()
    specialties_raw = (data.get('specialties') or '').strip()
    email = (data.get('email') or '').strip()
    specialties = [s.strip() for s in specialties_raw.split(',') if s.strip()]

    try:
        # Handle optional file upload (multipart/form-data)
        picture_path = None
        if 'picture' in request.files:
            pic = request.files.get('picture')
            if pic and pic.filename:
                from werkzeug.utils import secure_filename
                import time, uuid
                # sanitize filename and make it unique
                filename = secure_filename(pic.filename)
                filename = f"{int(time.time())}_{uuid.uuid4().hex}_{filename}"
                upload_folder = os.path.join(app.root_path, 'static', 'uploads', 'coach_photos')
                try:
                    os.makedirs(upload_folder, exist_ok=True)
                    save_path = os.path.join(upload_folder, filename)
                    pic.save(save_path)
                    # Store relative path for url_for('static', filename=...)
                    picture_path = os.path.join('uploads', 'coach_photos', filename).replace(os.path.sep, '/')
                except Exception as e:
                    print(f"Error saving uploaded picture: {e}")

        # Build update document
        update_doc = {'bio': bio, 'specialties': specialties, 'email': email}
        if picture_path:
            update_doc['picture'] = picture_path

        mongo.db.users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': update_doc}
        )
        # Update session email to reflect change
        session['user_email'] = email or session.get('user_email')
        # Return JSON for AJAX clients
        if request.is_json:
            return jsonify({'success': True})
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('coach_dashboard'))
    except Exception as e:
        print(f"Error updating coach profile: {e}")
        if request.is_json:
            return jsonify({'success': False, 'error': 'Server error'}), 500
        flash('Error updating profile. Check server logs.', 'error')
        return redirect(url_for('coach_profile_edit'))


@app.route('/coach/lessons')
def coach_lessons():
    """Dedicated page showing a coach's upcoming lessons for a selected weekday."""
    if 'user_id' not in session or not session.get('is_coach'):
        flash('Access denied. Coach access required.', 'error')
        return redirect(url_for('login'))

    from bson import ObjectId

    user_id = session['user_id']
    try:
        coach = mongo.db.users.find_one({'_id': ObjectId(user_id)}, {'password': 0})
    except Exception:
        coach = None

    if not coach:
        flash('Coach profile not found.', 'error')
        return redirect(url_for('login'))

    # Weekday selector (0=Mon..6=Sun). Default to today
    selected_wd = request.args.get('weekday', type=int)
    today = datetime.now()
    if selected_wd is None:
        selected_wd = today.weekday()

    coach_id_str = str(session['user_id'])
    upcoming = []
    for b in mongo.db.bookings.find({'coach_id': coach_id_str}).sort('date', 1):
        bdate = b.get('date')
        if isinstance(bdate, datetime):
            ds = bdate.strftime('%Y-%m-%d')
        elif isinstance(bdate, str):
            ds = bdate.split('T')[0][:10]
        else:
            continue
        try:
            d = datetime.strptime(ds, '%Y-%m-%d')
        except Exception:
            continue

        # Skip cancelled or already-done bookings
        if b.get('status') in ('cancelled', 'done'):
            continue

        if d.weekday() == selected_wd and d >= today.replace(hour=0, minute=0, second=0, microsecond=0):
            # Auto-mark done if slot end time has passed
            try:
                app_tz = os.getenv('APP_TIMEZONE', 'America/New_York')
                TZ = ZoneInfo(app_tz)
                time_range = b.get('time') or ''
                rng = _parse_range_minutes(time_range)
                if rng:
                    end_min = rng[1]
                    end_hour = end_min // 60
                    end_minute = end_min % 60
                    end_dt = datetime(d.year, d.month, d.day, end_hour, end_minute).replace(tzinfo=TZ)
                    now_local = datetime.now(TZ)
                    if now_local >= end_dt:
                        try:
                            mongo.db.bookings.update_one({'_id': b['_id']}, {'$set': {'status': 'done', 'done_at': datetime.utcnow()}})
                        except Exception as e:
                            print(f"Warning: could not mark booking {b.get('_id')} done: {e}")
                        continue
            except Exception:
                pass

            upcoming.append(b)

    # Get this coach's weekly availability for UI
    weekdays_docs = list(mongo.db.coach_weekly_availability.find({'coach_id': session['user_id']}))
    selected_weekdays = [doc.get('weekdays') for doc in weekdays_docs]
    sel_wd_flat = []
    for s in selected_weekdays:
        if isinstance(s, list):
            sel_wd_flat.extend(s)

    return render_template('coach_lessons.html',
                           coach=coach,
                           coach_lessons=upcoming,
                           selected_weekday=selected_wd,
                           my_weekdays=sel_wd_flat,
                           now=datetime.now())


@app.route('/coach/booking/<booking_id>/mark_done', methods=['POST'])
def coach_mark_booking_done(booking_id):
    """Mark a booking as done. Returns JSON."""
    if 'user_id' not in session or not session.get('is_coach'):
        return jsonify({'success': False, 'error': 'Access denied'}), 403

    from bson import ObjectId
    try:
        b = mongo.db.bookings.find_one({'_id': ObjectId(booking_id)})
    except Exception:
        b = None

    if not b:
        return jsonify({'success': False, 'error': 'Booking not found'}), 404

    # Ensure this coach owns the booking
    if str(b.get('coach_id')) != str(session['user_id']):
        return jsonify({'success': False, 'error': 'Not authorized for this booking'}), 403

    try:
        mongo.db.bookings.update_one({'_id': ObjectId(booking_id)}, {'$set': {'status': 'done', 'done_at': datetime.utcnow()}})
        return jsonify({'success': True, 'status': 'done'})
    except Exception as e:
        print(f"Error marking booking done: {e}")
        return jsonify({'success': False, 'error': 'Server error'}), 500


@app.route('/coach/booking/<booking_id>/message', methods=['POST'])
def coach_message_student(booking_id):
    """Send a message from coach to student via email. Expects JSON {message: '...'}"""
    if 'user_id' not in session or not session.get('is_coach'):
        return jsonify({'success': False, 'error': 'Access denied'}), 403

    if request.is_json:
        payload = request.get_json()
    else:
        payload = request.form.to_dict()

    message_text = (payload.get('message') or '').strip()
    if not message_text:
        return jsonify({'success': False, 'error': 'Message is empty'}), 400

    from bson import ObjectId
    try:
        b = mongo.db.bookings.find_one({'_id': ObjectId(booking_id)})
    except Exception:
        b = None

    if not b:
        return jsonify({'success': False, 'error': 'Booking not found'}), 404

    if str(b.get('coach_id')) != str(session['user_id']):
        return jsonify({'success': False, 'error': 'Not authorized for this booking'}), 403

    student_email = b.get('email')
    student_name = b.get('name', '')
    coach_name = session.get('user_name', 'Your coach')

    try:
        subject = f"Message from your coach {coach_name}"
        html_content = f"<p>Hi {student_name},</p><p>{message_text}</p><p>— {coach_name}</p>"
        text_content = f"Hi {student_name},\n\n{message_text}\n\n— {coach_name}"
        send_email_async(subject, student_email, html_content, text_content)
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error sending message email: {e}")
        return jsonify({'success': False, 'error': 'Failed to send message'}), 500


@app.route('/coach/availability', methods=['GET', 'POST'])
@login_required
def coach_availability():
    if 'is_coach' not in session or not session.get('is_coach'):
        flash('Access denied. Coaches only.', 'error')
        return redirect(url_for('login'))

    coach_id = session['user_id']

    if request.method == 'POST':
        # Expect weekdays[] form values (0=Mon..6=Sun)
        weekdays = request.form.getlist('weekdays') or request.form.getlist('weekdays[]')
        # Convert to ints and dedupe
        try:
            wd_ints = sorted(set(int(w) for w in weekdays))
        except Exception:
            flash('Invalid weekdays selected.', 'error')
            return redirect(url_for('coach_availability'))

        # Ensure no other coach has these weekdays (one coach per weekday)
        for wd in wd_ints:
            conflict = mongo.db.coach_weekly_availability.find_one({'weekdays': wd, 'coach_id': {'$ne': coach_id}})
            if conflict:
                flash(f'Weekday {wd} is already taken by another coach.', 'error')
                return redirect(url_for('coach_availability'))

        # Remove existing entries for this coach and insert new document
        mongo.db.coach_weekly_availability.delete_many({'coach_id': coach_id})
        if wd_ints:
            mongo.db.coach_weekly_availability.insert_one({
                'coach_id': coach_id,
                'coach_name': session.get('user_name', ''),
                'weekdays': wd_ints,
                'updated_at': datetime.utcnow()
            })
        flash('Availability updated.', 'success')
        return redirect(url_for('coach_dashboard'))

    # GET: show current availability
    docs = mongo.db.coach_weekly_availability.find({'coach_id': coach_id})
    my_weekdays = []
    for d in docs:
        my_weekdays.extend(d.get('weekdays', []))

    return render_template('coach_availability.html', my_weekdays=my_weekdays)

@app.route('/coach/cancel-days', methods=['GET', 'POST'])
def coach_cancel_days():
    if 'is_coach' not in session or not session.get('is_coach'):
        flash('Access denied. Coaches only.', 'error')
        return redirect(url_for('login'))

    coach_id = str(session['user_id'])

    # prepare next 30 days list
    today = datetime.utcnow().date()
    upcoming_dates = [(today + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(0, 30)]

    if request.method == 'POST':
        # selected dates may come as comma-separated or list depending on form encoding
        selected = request.form.getlist('dates') or request.form.get('dates')
        if isinstance(selected, str):
            selected = [s.strip() for s in selected.split(',') if s.strip()]

        if not selected:
            flash('No dates selected for cancellation.', 'error')
            return redirect(url_for('coach_cancel_days'))

        cancelled_count = 0
        refund_errors = []

        for date_str in selected:
            # find bookings for this coach on that date
            bookings = list(mongo.db.bookings.find({'date': date_str, 'coach_id': coach_id}))
            for b in bookings:
                try:
                    # Update booking status to cancelled
                    mongo.db.bookings.update_one({'_id': b['_id']}, {'$set': {'status': 'cancelled', 'cancelled_by': 'coach', 'cancelled_at': datetime.utcnow()}})

                    # Send cancellation email to student
                    details = f"<p>Your lesson on {date_str} at {b.get('time')} has been cancelled by the coach. You will receive a refund for this lesson shortly.</p>"
                    send_booking_confirmation_email('Lesson Cancelled', b.get('name', ''), b.get('email', ''), date_str, b.get('time', ''), details)

                    # Attempt Stripe refund if we have a session id or payment info
                    # Prefer charge/refund by using stored stripe_session_id or stripe_subscription_id
                    stripe_refund_ok = False
                    try:
                        # If booking has stripe_session_id, try to retrieve the Checkout Session and related payment_intent/charge
                        sess_id = b.get('stripe_session_id')
                        if sess_id:
                            try:
                                sess = stripe.checkout.Session.retrieve(sess_id)
                            except Exception:
                                sess = None
                            payment_intent = None
                            if sess:
                                payment_intent = sess.get('payment_intent') or getattr(sess, 'payment_intent', None)
                            if payment_intent:
                                # Create a refund for the PaymentIntent's charge
                                # Retrieve PaymentIntent to get the latest charge
                                try:
                                    pi = stripe.PaymentIntent.retrieve(payment_intent)
                                    charges = pi.get('charges', {}).get('data', []) if isinstance(pi, dict) else getattr(pi, 'charges', {}).get('data', [])
                                    if charges:
                                        charge_id = charges[0].get('id') if isinstance(charges[0], dict) else getattr(charges[0], 'id', None)
                                        if charge_id:
                                            stripe.Refund.create(charge=charge_id)
                                            stripe_refund_ok = True
                                except Exception as e:
                                    refund_errors.append(str(e))

                        # If not refunded yet and we have a stored stripe_subscription_id, try to refund last invoice/charge
                        if not stripe_refund_ok and b.get('stripe_subscription_id'):
                            sub_id = b.get('stripe_subscription_id')
                            try:
                                # get invoices for subscription and refund most recent paid invoice's charge
                                invs = stripe.Invoice.list(subscription=sub_id, limit=3)
                                for inv in (invs.get('data') if isinstance(invs, dict) else getattr(invs, 'data', [])):
                                    if inv.get('paid') or getattr(inv, 'paid', False):
                                        ch = inv.get('charge') or getattr(inv, 'charge', None)
                                        if ch:
                                            stripe.Refund.create(charge=ch)
                                            stripe_refund_ok = True
                                            break
                            except Exception as e:
                                refund_errors.append(str(e))

                    except Exception as e:
                        refund_errors.append(str(e))

                    cancelled_count += 1
                except Exception as e:
                    print(f"Error cancelling booking {b.get('_id')}: {e}")
                    refund_errors.append(str(e))

        flash(f'Cancelled {cancelled_count} bookings. Refunds attempted.', 'success' if cancelled_count > 0 else 'error')
        if refund_errors:
            print('Refund errors:', refund_errors)
            flash('Some refunds failed or require manual review. Check server logs.', 'warning')

        return redirect(url_for('coach_dashboard'))

    return render_template('coach_cancel_days.html', upcoming_dates=upcoming_dates)
@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.')
    return redirect(url_for('login'))


# Temporary route to make the current logged-in user an admin.
# Protection: requires environment variable TEMP_ADMIN_TOKEN to be set and
# the same token provided as ?token=... when calling this route.
# Remove this route after use.
@app.route('/make-me-admin', methods=['GET', 'POST'])
def make_me_admin():
    import os
    token = request.args.get('token') or request.form.get('token')
    expected = os.getenv('TEMP_ADMIN_TOKEN')

    if not expected:
        return "TEMP_ADMIN_TOKEN is not configured on the server. Set the env var to enable this temporary route.", 403

    if token != expected:
        return "Invalid token.", 403

    # Must be logged in
    if 'user_id' not in session:
        flash('Please log in first.', 'error')
        return redirect(url_for('login'))

    try:
        # Update the user's role in the database
        mongo.db.users.update_one({'_id': ObjectId(session['user_id'])}, {'$set': {'role': 'admin'}})
        # Update session flag so the current session recognizes admin rights immediately
        session['is_admin'] = True
        flash('You have been promoted to admin (temporary). Remove the TEMP route after use.', 'success')
        return redirect(url_for('admin_dashboard'))
    except Exception as e:
        print(f"Error making user admin: {e}")
        flash('There was an error promoting your account. Check server logs.', 'error')
        return redirect(url_for('profile'))

# Protect lessons and membership routes
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/create-lesson-booking', methods=['POST'])
def create_lesson_booking():
    # Accept JSON (AJAX) or form-encoded POSTs
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form.to_dict()

    lesson_type = data.get('lesson_type')
    slot_idx = data.get('slot_idx')
    day_idx = data.get('day_idx')
    name = data.get('name') or data.get('student_name') or session.get('user_name')
    contact = data.get('contact') or data.get('student_email') or session.get('user_email')
    try:
        recurring_weeks = int(data.get('recurring_weeks') or 1)
    except Exception:
        recurring_weeks = 1

    # Clamp recurring weeks (1..12)
    if recurring_weeks < 1:
        recurring_weeks = 1
    if recurring_weeks > 12:
        recurring_weeks = 12

    # Log incoming payload for easier debugging
    try:
        print('[create_lesson_booking] payload:', data)
    except Exception:
        pass

    # Basic validation with helpful errors
    if lesson_type not in ('group', 'private'):
        return jsonify({'error': 'Invalid or missing lesson_type. Must be "group" or "private".'}), 400

    # Require either explicit date+time or day_idx+slot_idx to be present
    has_date_time = bool(data.get('date') and (data.get('time') or (slot_idx is not None and day_idx is not None)))
    has_slot_indexes = slot_idx is not None and day_idx is not None
    if not has_date_time and not has_slot_indexes:
        return jsonify({'error': 'Missing date/time information. Provide "date" and "time" or both "day_idx" and "slot_idx".'}), 400

    # Normalize numeric slot/day indexes when provided
    try:
        if slot_idx is not None:
            slot_idx = int(slot_idx)
        if day_idx is not None:
            day_idx = int(day_idx)
    except Exception:
        return jsonify({'error': 'slot_idx and day_idx must be integers'}), 400

    # Map lesson_type to price and description
    if lesson_type == 'group':
        # price_amount is in CAD (dollars). Stripe expects the smallest currency unit (cents) in unit_amount.
        price_amount = 25.00  # CAD $25.00 for a group lesson
        description = 'Group Lesson'
    elif lesson_type == 'private':
        price_amount = 60.00  # CAD $60.00 for a private lesson
        description = 'Private Lesson'
    else:
        return jsonify({'error': 'Invalid lesson type'}), 400

    # Ensure Stripe API key is configured
    if not getattr(stripe, 'api_key', None):
        err = 'Stripe API key not configured on server. Set STRIPE_SECRET_KEY.'
        print(err)
        return jsonify({'error': err}), 500

    try:
        # Create Checkout Session
        # Try to include concrete date/time in metadata when provided so success handler can create bookings
        metadata = {
            'name': name or '',
            'lesson_type': lesson_type,
            'slot_idx': str(slot_idx or ''),
            'day_idx': str(day_idx or ''),
            'recurring_weeks': str(recurring_weeks)
        }

        # If user is logged in, attach user id
        if 'user_id' in session:
            metadata['user_id'] = str(session['user_id'])

        # If date/time provided in POST, attach them
        if data.get('date'):
            metadata['date'] = data.get('date')
        if data.get('time'):
            metadata['time'] = data.get('time')

        # If user requested multiple recurring weeks, create a subscription Checkout Session
        if recurring_weeks and int(recurring_weeks) > 1:
            session_obj = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'cad',
                        'product_data': {'name': f'PrimeCourt - {description}'},
                        'unit_amount': int(price_amount * 100),
                        'recurring': {'interval': 'week', 'interval_count': 1}
                    },
                    'quantity': 1,
                }],
                mode='subscription',
                subscription_data={'metadata': metadata},
                success_url=url_for('lesson_booking_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
                cancel_url=url_for('lessons', _external=True) + '?status=cancel',
                customer_email=contact,
                metadata=metadata
            )
        else:
            session_obj = stripe.checkout.Session.create(
                submit_type='book',
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'cad',
                        'product_data': {'name': f'PrimeCourt - {description}'},
                        'unit_amount': int(price_amount * 100),
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=url_for('lesson_booking_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
                cancel_url=url_for('lessons', _external=True) + '?status=cancel',
                customer_email=contact,
                metadata=metadata
            )

        # If AJAX request, return session id and url for redirect by Stripe.js
        if request.is_json:
            return jsonify({'sessionId': session_obj.id, 'url': session_obj.url})

        # For non-AJAX (form) requests, redirect user to Stripe Checkout page
        return redirect(session_obj.url, code=303)

    except Exception as e:
        print('Error creating Stripe session:', str(e))
        return jsonify({'error': str(e)}), 500


@app.route('/create-court-booking-session', methods=['POST'])
@login_required
def create_court_booking_session():
    # Accept JSON from the courts page
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form.to_dict()

    court_id = data.get('court_id')
    time_slot = data.get('time_slot')
    date = data.get('date')
    user_id = session.get('user_id')
    user_email = session.get('user_email')

    if not (court_id and time_slot and date):
        return jsonify({'error': 'Missing booking information'}), 400

    # Price for courts (CAD $15)
    price_amount = 15.00

    # Ensure Stripe API key is configured
    if not getattr(stripe, 'api_key', None):
        err = 'Stripe API key not configured on server. Set STRIPE_SECRET_KEY.'
        print(err)
        return jsonify({'error': err}), 500

    try:
        metadata = {
            'court_id': court_id,
            'time_slot': time_slot,
            'date': date,
            'user_id': str(user_id) if user_id else '',
            'user_name': session.get('user_name', ''),
            'user_email': session.get('user_email', '')
        }

        session_obj = stripe.checkout.Session.create(
            submit_type='book',
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'cad',
                    'product_data': {'name': f'PrimeCourt - Court Booking ({court_id})'},
                    'unit_amount': int(price_amount * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('court_booking_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('courts', _external=True) + '?status=cancel',
            customer_email=user_email,
            metadata=metadata
        )

        return jsonify({'sessionId': session_obj.id, 'url': session_obj.url})
    except Exception as e:
        print('Error creating court Stripe session:', str(e))
        return jsonify({'error': str(e)}), 500

@app.route('/lesson-booking-success')
def lesson_booking_success():
    session_id = request.args.get('session_id')
    if not session_id:
        flash('Invalid session', 'error')
        return redirect(url_for('lessons'))

    try:
        # Verify the session with Stripe
        checkout_session = stripe.checkout.Session.retrieve(session_id)

        # Determine whether this Checkout Session created a subscription or a one-time payment.
        if isinstance(checkout_session, dict):
            subscription_id = checkout_session.get('subscription')
            payment_status = checkout_session.get('payment_status')
            metadata = checkout_session.get('metadata', {})
        else:
            subscription_id = getattr(checkout_session, 'subscription', None)
            payment_status = getattr(checkout_session, 'payment_status', None)
            metadata = getattr(checkout_session, 'metadata', {}) or {}

        if not subscription_id and payment_status != 'paid':
            flash('Payment was not successful. Please try again.', 'error')
            return redirect(url_for('lessons'))

        # Get metadata from the checkout session
        user_id = metadata.get('user_id')
        day_idx = int(metadata.get('day_idx', 0))
        slot_idx = int(metadata.get('slot_idx', 0))
        lesson_type = metadata.get('lesson_type')
        recurring_weeks = int(metadata.get('recurring_weeks', 1))
        date_str = metadata.get('date')
        time_str = metadata.get('time')

        # Get user info
        user = mongo.db.users.find_one({'_id': ObjectId(user_id)})
        if not user:
            flash('User not found', 'error')
            return redirect(url_for('lessons'))

        # Generate month slots to get the selected day
        selected_date = datetime.strptime(date_str, '%Y-%m-%d')
        lesson_days = generate_month_slots(selected_date.year, selected_date.month)

        if day_idx < 0 or day_idx >= len(lesson_days):
            flash('Invalid day selected', 'error')
            return redirect(url_for('lessons'))

        selected_day = lesson_days[day_idx]
        if slot_idx < 0 or slot_idx >= len(selected_day['slots']):
            flash('Invalid time slot selected', 'error')
            return redirect(url_for('lessons'))

        slot = selected_day['slots'][slot_idx]

        # Process the booking for each week
        success_count = 0
        # If this session created a subscription, store it and schedule cancellation after recurring_weeks
        stripe_subscription_id = None
        if subscription_id:
            stripe_subscription_id = subscription_id
            # Attempt to set subscription cancel_at to end of the requested recurring series
            try:
                # Compute cancel timestamp at end of last period (UTC)
                last_date = selected_date + timedelta(weeks=(recurring_weeks - 1))
                # Cancel after the last period ends: set cancel_at to last_date + 7 days (end of that week)
                cancel_at_dt = datetime.combine(last_date, datetime.min.time()) + timedelta(days=7)
                cancel_at_ts = int(cancel_at_dt.replace(tzinfo=None).timestamp())
                stripe.Subscription.modify(stripe_subscription_id, cancel_at=cancel_at_ts)
            except Exception as e:
                print(f"Warning: could not set subscription cancel_at: {e}")

        booked_entries = []
        for week in range(recurring_weeks):
            lesson_date = selected_date + timedelta(weeks=week)
            week_date_str = lesson_date.strftime('%Y-%m-%d')

            # Check if this date has no classes
            schedule_settings = mongo.db.schedule_settings.find_one({'date': week_date_str}) or {}
            if schedule_settings.get('no_classes', False):
                continue

            # Check if slot is available for this date
            if schedule_settings.get('custom_time_slots'):
                if time_str not in schedule_settings['custom_time_slots']:
                    continue

            # Check if slot is already booked (private vs group rules)
            existing_booking = mongo.db.bookings.find_one({
                'date': week_date_str,
                'time': time_str,
                'lesson_type': 'private'
            })

            if existing_booking:
                if lesson_type == 'private':
                    continue
                elif lesson_type == 'group':
                    group_count = mongo.db.bookings.count_documents({
                        'date': week_date_str,
                        'time': time_str,
                        'lesson_type': 'group'
                    })
                    if group_count >= 5:
                        continue

            # Determine assigned coach for this date (if any)
            coach_info = _assign_coach_for_date(week_date_str)

            # Create booking in database
            booking_data = {
                'user_id': user_id,
                'name': user.get('name', ''),
                'email': user.get('email', ''),
                'date': week_date_str,
                'time': time_str,
                'lesson_type': lesson_type,
                'created_at': datetime.utcnow(),
                'payment_status': 'paid',
                'stripe_session_id': session_id,
                'recurring_week': f"{week + 1}/{recurring_weeks}" if recurring_weeks > 1 else None
            }
            if stripe_subscription_id:
                booking_data['stripe_subscription_id'] = stripe_subscription_id
            if coach_info:
                booking_data['coach_id'] = coach_info.get('coach_id')
                booking_data['coach_name'] = coach_info.get('coach_name')

            # Check if this is a group or private lesson
            if lesson_type == 'group':
                group_count = mongo.db.bookings.count_documents({
                    'date': week_date_str,
                    'time': time_str,
                    'lesson_type': 'group'
                })
                if group_count >= 5:  # Max 5 people in a group
                    continue
                booking_data['group_size'] = group_count + 1

            # Insert booking
            mongo.db.bookings.insert_one(booking_data)
            success_count += 1

            # Collect booked entry for single aggregated email
            entry = {
                'date': week_date_str,
                'time': time_str,
                'type': lesson_type,
                'week_number': week + 1,
                'total_weeks': recurring_weeks,
            }
            if coach_info:
                entry['coach_name'] = coach_info.get('coach_name')
            booked_entries.append(entry)

        if success_count > 0:
            # Send a single aggregated confirmation email for the series (or single booking)
            try:
                details = f"<p><strong>Type:</strong> {lesson_type.title()} Lesson</p>"
                if len(booked_entries) == 1:
                    details += f"<p><strong>Date:</strong> {booked_entries[0]['date']}</p>"
                    details += f"<p><strong>Time:</strong> {booked_entries[0]['time']}</p>"
                else:
                    details += "<h3>Scheduled Dates:</h3><ul>"
                    for be in booked_entries:
                        details += f"<li>Week {be['week_number']}/{be['total_weeks']}: {be['date']} — {be['time']}</li>"
                    details += "</ul>"

                # If coach assigned, include first coach name (assumes same coach across series)
                coach_name = None
                for be in booked_entries:
                    if be.get('coach_name'):
                        coach_name = be.get('coach_name')
                        break
                if coach_name:
                    details += f"<p><strong>Coach:</strong> {coach_name}</p>"

                if len(booked_entries) > 1:
                    details += f"<p><strong>Series:</strong> {len(booked_entries)} of {recurring_weeks} weeks</p>"

                send_booking_confirmation_email(
                    "Lesson",
                    user.get('name', ''),
                    user.get('email', ''),
                    booked_entries[0]['date'] if booked_entries else selected_date.strftime('%Y-%m-%d'),
                    booked_entries[0]['time'] if booked_entries else time_str,
                    details
                )
            except Exception as e:
                print(f"Error sending aggregated confirmation email: {e}")

            if recurring_weeks == 1:
                flash(f'Successfully booked {lesson_type} lesson!', 'success')
            else:
                flash(f'Successfully booked {success_count} {lesson_type} lessons!', 'success')
        else:
            flash('No available slots were booked. Please try again.', 'error')

        return redirect(url_for('lessons', day=day_idx, slot=slot_idx))

    except Exception as e:
        print(f"Error processing lesson booking success: {str(e)}")
        flash('Your payment was successful, but there was an error completing your booking. Please contact support.', 'warning')
        return redirect(url_for('lessons', show_flash=1))

@app.route('/lessons', methods=['GET', 'POST'])
@login_required
def lessons():
    message = None
    # Use configured timezone for all time comparisons
    app_tz = os.getenv('APP_TIMEZONE', 'America/New_York')
    TZ = ZoneInfo(app_tz)
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
        if 0 <= selected_day_idx < len(lesson_days):
            selected_day = lesson_days[selected_day_idx]

    # Get current user from session
    user = None
    if 'user_id' in session:
        user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})

    # Load schedule settings and bookings
    schedule_settings = {s['date']: s for s in mongo.db.schedule_settings.find()}
    # Only show paid bookings or bookings by the current user
    bookings = list(mongo.db.bookings.find({
        '$or': [
            {'payment_status': 'paid'},
            {'user_id': str(session['user_id'])}
        ]
    }))
    
    for day_idx, day in enumerate(lesson_days):
        # Check if this day has no classes
        day['no_classes'] = schedule_settings.get(day['date'], {}).get('no_classes', False)
        day['no_classes_reason'] = schedule_settings.get(day['date'], {}).get('reason', '')
        
        # Get custom time slots for this day (if any)
        custom_slots = schedule_settings.get(day['date'], {}).get('custom_time_slots', [])
        # Pre-parse to minutes for robust comparison across formats
        custom_ranges = set()
        for s in custom_slots:
            rng = _parse_range_minutes(s)
            if rng:
                custom_ranges.add(rng)
        
        for slot_idx, slot in enumerate(day['slots']):
            slot['group'] = []  # Reset group list
            slot['private'] = None  # Reset private slot
            
            # Check if this time slot is available based on custom settings
            if custom_ranges:
                slot_rng = _parse_range_minutes(slot['time'])
                slot['is_available'] = slot_rng in custom_ranges if slot_rng else False
            else:
                slot['is_available'] = True
            
            # Check if this time slot has passed for today (use minutes-accurate comparison in local TZ)
            now_local = datetime.now(TZ)
            if day['date'] == now_local.strftime('%Y-%m-%d'):
                try:
                    # Parse end time (e.g., "5:00 PM - 6:30 PM") and build a datetime on the slot's date
                    end_time_str = slot['time'].split(' - ')[1].strip()
                    end_dt = datetime.strptime(f"{day['date']} {end_time_str}", "%Y-%m-%d %I:%M %p").replace(tzinfo=TZ)
                    now_dt = now_local
                    # Mark as past if the slot has ended
                    slot['is_past'] = now_dt >= end_dt
                except Exception:
                    slot['is_past'] = False
            else:
                slot['is_past'] = False
            
            for booking in bookings:
                if booking['date'] == day['date'] and booking['time'] == slot['time']:
                    if booking['lesson_type'] == 'group':
                        slot['group'].append({'name': booking['name'], 'email': booking['email']})
                    elif booking['lesson_type'] == 'private':
                        slot['private'] = {'name': booking['name'], 'email': booking['email']}

    if request.method == 'POST':
        try:
            day_idx = int(request.form['day_idx'])
            slot_idx = int(request.form['slot_idx'])
            lesson_type = request.form['lesson_type']
            name = request.form.get('student_name', session.get('user_name', ''))
            email = request.form.get('student_email', session.get('user_email', ''))
            recurring_weeks = int(request.form.get('recurring_weeks', 1))
            
            # Validate recurring weeks (1-12 weeks max)
            if recurring_weeks < 1 or recurring_weeks > 12:
                recurring_weeks = 1
            
            slot = lesson_days[day_idx]['slots'][slot_idx]
            selected_date = datetime.strptime(lesson_days[day_idx]['date'], '%Y-%m-%d')
            
            # Book recurring lessons
            success_count = 0
            for week in range(recurring_weeks):
                # Calculate date for this week
                lesson_date = selected_date + timedelta(weeks=week)
                date_str = lesson_date.strftime('%Y-%m-%d')
                
                # Check if this date has no classes
                if schedule_settings.get(date_str, {}).get('no_classes', False):
                    continue  # Skip this date
                
                # Check if slot is available for this date
                slot_available = True
                if schedule_settings.get(date_str, {}).get('custom_time_slots'):
                    custom_slots = schedule_settings[date_str]['custom_time_slots']
                    custom_ranges = set()
                    for s in custom_slots:
                        rng = _parse_range_minutes(s)
                        if rng:
                            custom_ranges.add(rng)
                    if custom_ranges:
                        slot_rng = _parse_range_minutes(slot['time'])
                        slot_available = slot_rng in custom_ranges if slot_rng else False
                    else:
                        slot_available = True  # No valid ranges parsed; treat as unrestricted
                
                if not slot_available:
                    continue  # Skip this date
                
                # Check if slot is past for this date (use minutes-accurate comparison in local TZ)
                now_local = datetime.now(TZ)
                if date_str == now_local.strftime('%Y-%m-%d'):
                    try:
                        end_time_str = slot['time'].split(' - ')[1].strip()
                        end_dt = datetime.strptime(f"{date_str} {end_time_str}", "%Y-%m-%d %I:%M %p").replace(tzinfo=TZ)
                        now_dt = now_local
                        if now_dt >= end_dt:
                            continue  # Skip past slots
                    except Exception:
                        pass
                
                # Check if slot is already booked for this date
                existing_booking = mongo.db.bookings.find_one({
                    'date': date_str,
                    'time': slot['time']
                })
                
                if existing_booking:
                    if lesson_type == 'private':
                        continue  # Skip if private slot is booked
                    elif lesson_type == 'group' and len(existing_booking.get('group', [])) >= 5:
                        continue  # Skip if group is full
                
                # Book the lesson
                if lesson_type == 'private':
                    booking_data = {
                        'date': date_str,
                        'time': slot['time'],
                        'lesson_type': 'private',
                        'name': name,
                        'email': email,
                        'recurring_id': f"{name}_{email}_{slot['time']}_{lesson_type}",
                        'week_number': week + 1,
                        'total_weeks': recurring_weeks
                    }
                    # Check if this is the first week of a recurring booking
                    if week == 0:
                        # Create the booking
                        mongo.db.bookings.insert_one(booking_data)
                        success_count += 1
                
                    # Send confirmation email
                    details = f"<p><strong>Type:</strong> Private Lesson</p>"
                    if recurring_weeks > 1:
                        details += f"<p><strong>Series:</strong> Week {week + 1} of {recurring_weeks}</p>"
                    send_booking_confirmation_email("Lesson", name, email, date_str, slot['time'], details)
                
                elif lesson_type == 'group':
                    # Check current group size for this date
                    current_bookings = list(mongo.db.bookings.find({
                        'date': date_str,
                        'time': slot['time'],
                        'lesson_type': 'group'
                    }))
                    
                    if len(current_bookings) < 5:
                        booking_data = {
                            'date': date_str,
                            'time': slot['time'],
                            'lesson_type': 'group',
                            'name': name,
                            'email': email,
                            'recurring_id': f"{name}_{email}_{slot['time']}_{lesson_type}",
                            'week_number': week + 1,
                            'total_weeks': recurring_weeks
                        }
                        mongo.db.bookings.insert_one(booking_data)
                        success_count += 1
                        
                        # Send confirmation email
                        details = f"<p><strong>Type:</strong> Group Lesson</p>"
                        details += f"<p><strong>Group Size:</strong> {len(current_bookings) + 1}/5</p>"
                        if recurring_weeks > 1:
                            details += f"<p><strong>Series:</strong> Week {week + 1} of {recurring_weeks}</p>"
                        send_booking_confirmation_email("Lesson", name, email, date_str, slot['time'], details)
            
            if success_count > 0:
                if recurring_weeks == 1:
                    message = f'{lesson_type.title()} lesson booked!'
                else:
                    message = f'Booked {success_count} out of {recurring_weeks} recurring {lesson_type} lessons!'
            else:
                message = 'No lessons could be booked. All slots may be unavailable or booked.'
            
            selected_day = lesson_days[day_idx]
            selected_day_idx = day_idx
            
        except Exception as e:
            print(f"Error processing lesson booking: {str(e)}")
            message = 'An error occurred while processing your booking.'
            
            # Set default values for rendering the page
            if 'day_idx' in request.form:
                day_idx = int(request.form['day_idx'])
                selected_day = lesson_days[day_idx]
                selected_day_idx = day_idx

    # Calculate weekday index for the first day
    first_day_dt = datetime(year, month, 1)
    weekday = first_day_dt.weekday()  # Monday=0
    # For Sunday=0 alignment:
    weekday = (weekday + 1) % 7

    month_name = first_day_dt.strftime('%B')

    # Optional debug: log why slots are marked unavailable
    if request.args.get('debug') == '1' and selected_day:
        day_cfg = schedule_settings.get(selected_day['date'], {})
        print(f"[LESSONS DEBUG] Date: {selected_day['date']} no_classes={day_cfg.get('no_classes')} custom_time_slots={day_cfg.get('custom_time_slots')}")
        for s in selected_day['slots']:
            print(f"[LESSONS DEBUG] time={s['time']} is_available={s.get('is_available')} is_past={s.get('is_past')} has_private={bool(s.get('private'))} group_size={len(s.get('group', []))}")

    return render_template(
        'lessons.html',
        lesson_days=lesson_days,
        month=month,
        year=year,
        month_name=month_name,
        weekday=weekday,
        selected_day=selected_day,
        selected_day_idx=selected_day_idx,
        lesson_types=LESSON_TYPES,
        stripe_public_key=stripe_public_key,
        message=message,
        now=datetime.now(TZ), # Pass timezone-aware now
        current_user=user
    )


@app.route('/courts', methods=['GET', 'POST'])
def courts():
    from datetime import datetime
    # Selected date (defaults to today)
    selected_date_str = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')

    # Define available courts (could be moved to DB later)
    courts_list = [
        {'id': 'court-1', 'name': 'Court 1', 'surface': 'Hard'},
        {'id': 'court-2', 'name': 'Court 2', 'surface': 'Clay'},
        {'id': 'court-3', 'name': 'Court 3', 'surface': 'Grass'}
    ]

    # Handle booking submission
    if request.method == 'POST':
        if 'user_id' not in session:
            flash('Please log in to book a court', 'error')
            return redirect(url_for('login'))
            
        # Handle AJAX request for payment session creation
        if request.is_json:
            data = request.get_json()
            court_id = data.get('court_id')
            time_slot = data.get('time_slot')
            date_str = data.get('date', selected_date_str)
            
            if not all([court_id, time_slot]):
                return jsonify({'error': 'Missing required fields'}), 400
                
            # Check if slot is already booked
            existing = mongo.db.court_bookings.find_one({
                'date': date_str,
                'court_id': court_id,
                'time': time_slot
            })
            
            if existing:
                return jsonify({'error': 'That slot is already booked. Please choose another.'}), 400
                
            # All checks passed, return success to trigger Stripe checkout
            return jsonify({
                'success': True,
                'court_id': court_id,
                'time_slot': time_slot,
                'date': date_str,
                'price': 15.00  # $15 per hour
            })
        
        # Handle regular form submission (shouldn't happen with new JS flow)
        court_id = request.form.get('court_id')
        time_slot = request.form.get('time_slot')
        date_str = request.form.get('date') or selected_date_str

        if not court_id or not time_slot:
            flash('Please select a court and time slot', 'error')
            return redirect(url_for('courts', date=date_str))

        flash('Please use the new booking system', 'error')
        return redirect(url_for('courts', date=date_str))

    # Build availability for the selected date (09:00–21:00)
    hours = list(range(9, 21))
    time_slots = [f"{h:02d}:00 - {h+1:02d}:00" for h in hours]

    # Load bookings for the selected date
    bookings = list(mongo.db.court_bookings.find({'date': selected_date_str}))
    booked_map = {}
    for b in bookings:
        booked_map[(b['court_id'], b['time'])] = b

    return render_template(
        'courts.html',
        courts=courts_list,
        date_str=selected_date_str,
        time_slots=time_slots,
    booked_map=booked_map,
    stripe_public_key=stripe_public_key
    )

@app.route('/membership', methods=['GET', 'POST'])
@login_required
def membership():
    from app import MEMBERSHIP_PLANS
    return render_template('membership.html', membership_plans=MEMBERSHIP_PLANS)


@app.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    from app import MEMBERSHIP_PLANS
    plan_id = request.form.get('plan_id')
    plan = MEMBERSHIP_PLANS.get(plan_id)

    if not plan:
        flash('Invalid membership plan selected.', 'error')
        return redirect(url_for('membership'))

    try:
        # Create a subscription Checkout Session so the customer is charged monthly
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': plan['name'],
                    },
                    'unit_amount': plan['price'],
                    'recurring': {'interval': 'month', 'interval_count': 1}
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url=request.url_root + 'membership-success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('membership_cancel', _external=True),
            metadata={
                'user_id': session['user_id'],
                'plan_id': plan_id
            }
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        flash(f'Error creating checkout session: {e}', 'error')
        return redirect(url_for('membership'))


@app.route('/membership-success')
@login_required
def membership_success():
    from bson.objectid import ObjectId
    session_id = request.args.get('session_id')
    if not session_id:
        flash('No session ID provided.', 'error')
        return redirect(url_for('membership'))

    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)

        # For subscription sessions, Stripe returns a `subscription` id on the session.
        subscription_id = None
        if isinstance(checkout_session, dict):
            subscription_id = checkout_session.get('subscription')
            metadata = checkout_session.get('metadata', {})
            payment_status = checkout_session.get('payment_status')
        else:
            subscription_id = getattr(checkout_session, 'subscription', None)
            metadata = getattr(checkout_session, 'metadata', {}) or {}
            payment_status = getattr(checkout_session, 'payment_status', None)

        user_id = metadata.get('user_id')
        plan_id = metadata.get('plan_id')

        # If we got a subscription id, consider the membership active and save subscription info.
        if subscription_id or payment_status == 'paid':
            # Mark membership active and store subscription id when available
            update = {
                'membership': {
                    'plan_id': plan_id,
                    'status': 'active',
                    'purchased_at': datetime.now(),
                    'expires_at': datetime.now() + timedelta(days=30)
                }
            }
            if subscription_id:
                update['membership']['stripe_subscription_id'] = subscription_id

            mongo.db.users.update_one({'_id': ObjectId(user_id)}, {'$set': update})

            flash('Membership purchased successfully!', 'success')
            return redirect(url_for('profile'))
        else:
            flash('Payment was not successful.', 'error')
            return redirect(url_for('membership'))
    except stripe.error.StripeError as e:
        flash(f'An error occurred: {e}', 'error')
        return redirect(url_for('membership'))


@app.route('/membership-cancel')
@login_required
def membership_cancel():
    flash('Membership purchase was cancelled.', 'info')
    return redirect(url_for('membership'))

@app.route('/profile')
@login_required
def profile():
    from bson.objectid import ObjectId
    
    # Get user data from MongoDB - convert string ID to ObjectId
    user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
    
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('index'))
    
    from app import MEMBERSHIP_PLANS

    # Get membership details
    membership = user.get('membership', {})
    plan_name = "N/A"

    # Check if membership is active and not expired
    is_active = False
    days_remaining = 0
    
    if membership.get('status') == 'active' and membership.get('expires_at'):
        expires_at = membership['expires_at']
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        
        now = datetime.now()
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=now.tzinfo)

        if expires_at > now:
            is_active = True
            days_remaining = (expires_at - now).days
            # Get the plan display name
            plan_id = membership.get('plan_id')
            if plan_id and plan_id in MEMBERSHIP_PLANS:
                plan_name = MEMBERSHIP_PLANS[plan_id].get('name', 'Unknown Plan')

    return render_template(
        'profile.html',
        user=user,
        member_since=(lambda u: (
            (u.strftime('%B %d, %Y') if isinstance(u, datetime) else
             (datetime.fromisoformat(u.replace('Z', '+00:00')).strftime('%B %d, %Y') if isinstance(u, str) else 'N/A'))
        ))(user.get('created_at')),
        membership=membership,
        is_active=is_active,
        days_remaining=days_remaining,
        plan_name=plan_name
    )


@app.route('/coaches')
def coaches():
    """Public page listing all active coaches."""
    # Find users with role 'coach'. Accept documents where `is_active` is True
    # or the field is missing (common when importing from another DB).
    query = {'role': 'coach', '$or': [{'is_active': True}, {'is_active': {'$exists': False}}]}
    coaches = list(mongo.db.users.find(query, {'password': 0}))
    return render_template('coaches.html', coaches=coaches)


@app.route('/coaches/<coach_id>')
def coach_profile(coach_id):
    """Show a coach's profile, availability and upcoming bookings."""
    try:
        # Try ObjectId lookup first (typical in MongoDB), then fall back to string _id
        coach = None
        try:
            coach = mongo.db.users.find_one({'_id': ObjectId(coach_id)}, {'password': 0})
        except Exception:
            # Not an ObjectId or lookup failed; try string _id
            try:
                coach = mongo.db.users.find_one({'_id': coach_id}, {'password': 0})
            except Exception:
                coach = None

        if not coach:
            flash('Coach not found.', 'error')
            return redirect(url_for('coaches'))

        # Weekly availability - accept coach_id stored either as string or ObjectId
        coach_id_val = coach.get('_id')
        coach_id_str = str(coach_id_val)
        coach_id_obj = None
        try:
            coach_id_obj = ObjectId(coach_id_val)
        except Exception:
            try:
                coach_id_obj = ObjectId(coach_id_str)
            except Exception:
                coach_id_obj = None

        q_or = [{'coach_id': coach_id_str}]
        if coach_id_obj:
            q_or.append({'coach_id': coach_id_obj})

        weekly = mongo.db.coach_weekly_availability.find_one({'$or': q_or})
        weekdays = weekly.get('weekdays') if weekly else []

        # Upcoming bookings for this coach (next 30 days) — query both id forms
        today = datetime.utcnow().date()
        end_date = today + timedelta(days=30)
        upcoming = []
        for b in mongo.db.bookings.find({'$or': q_or}).sort('date', 1):
            bdate = b.get('date')
            if isinstance(bdate, datetime):
                ds = bdate.strftime('%Y-%m-%d')
            elif isinstance(bdate, str):
                ds = bdate.split('T')[0][:10]
            else:
                continue
            try:
                d = datetime.strptime(ds, '%Y-%m-%d').date()
            except Exception:
                continue
            if today <= d <= end_date and b.get('status') != 'cancelled':
                # normalize date string
                b['date'] = ds
                upcoming.append(b)

        return render_template('coach_profile.html', coach=coach, weekdays=weekdays, upcoming=upcoming)
    except Exception as e:
        print(f"Error loading coach profile: {e}")
        flash('Error loading coach profile.', 'error')
        return redirect(url_for('coaches'))

@app.route('/admin/pricing', methods=['GET', 'POST'])
@admin_required
def admin_pricing():
    if request.method == 'POST':
        private_price = float(request.form.get('private_price', 50))
        group_price = float(request.form.get('group_price', 25))
        
        if update_lesson_prices(private_price, group_price):
            flash('Pricing updated successfully!', 'success')
        else:
            flash('Failed to update pricing. Please try again.', 'error')
        
        return redirect(url_for('admin_pricing'))
    
    # Get current prices
    prices = get_lesson_prices()
    
    # Convert cents to dollars for display
    prices_dollars = {
        'private': prices['private'] / 100,
        'group': prices['group'] / 100
    }
    
    return render_template('admin_pricing.html', prices=prices_dollars)

@app.route('/admin/users')
@admin_required
def admin_users():
    """Admin view for managing users"""
    # Allow filtering by role via query param: ?role=admin|coach|member|all
    selected_role = request.args.get('role', 'all')
    query = {}
    if selected_role and selected_role != 'all':
        # UI uses 'user' label, but DB stores 'member' role for regular users.
        # Also include legacy user documents that may not have a `role` field set.
        if selected_role == 'user':
            query = {'$or': [
                {'role': 'member'},
                {'role': {'$exists': False}},
                {'role': None}
            ]}
        else:
            query = {'role': selected_role}

    users = list(mongo.db.users.find(query, {'password': 0}))  # Exclude passwords
    return render_template('admin_users.html', users=users, selected_role=selected_role)

@app.route('/admin')
@admin_required
def admin_dashboard():
    from datetime import datetime, timedelta
    
    # Get today's date for filtering
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_str = today.strftime('%Y-%m-%d')
    start_dt = today - timedelta(days=7)
    start_str = start_dt.strftime('%Y-%m-%d')

    # Fetch all lesson bookings and filter in Python to handle mixed date types (str or datetime)
    all_lesson_bookings = list(mongo.db.bookings.find())
    lesson_bookings = []
    for b in all_lesson_bookings:
        bdate = b.get('date')
        if isinstance(bdate, datetime):
            ds = bdate.strftime('%Y-%m-%d')
        elif isinstance(bdate, str):
            ds = bdate.split('T')[0][:10]
        else:
            continue

        if ds >= start_str:
            # normalize date to string for templates
            b['date'] = ds
            lesson_bookings.append(b)
    
    # Process lesson bookings to include user names and format dates
    for booking in lesson_bookings:
        # Add user name if not already present
        if 'user_name' not in booking and 'user_id' in booking:
            user = mongo.db.users.find_one({'_id': booking['user_id']}, {'name': 1})
            if user:
                booking['user_name'] = user.get('name', 'Unknown User')
        
        # Ensure date is in string format for the template
        if 'date' in booking and isinstance(booking['date'], datetime):
            booking['date'] = booking['date'].strftime('%Y-%m-%d')
    
    # Fetch all court bookings and filter similarly
    all_court_bookings = list(mongo.db.court_bookings.find())
    court_bookings = []
    for b in all_court_bookings:
        bdate = b.get('date')
        if isinstance(bdate, datetime):
            ds = bdate.strftime('%Y-%m-%d')
        elif isinstance(bdate, str):
            ds = bdate.split('T')[0][:10]
        else:
            continue

        if ds >= start_str:
            b['date'] = ds
            # Add user name if not already present
            if 'user_name' not in b and 'user_id' in b:
                try:
                    uid = b['user_id']
                    if isinstance(uid, str):
                        uid = ObjectId(uid)
                    user = mongo.db.users.find_one({'_id': uid}, {'name': 1})
                    if user:
                        b['user_name'] = user.get('name', 'Unknown User')
                except Exception:
                    b['user_name'] = 'Unknown User'
            court_bookings.append(b)
    
    # Get users count and basic info
    users = list(mongo.db.users.find(
        {},
        {'name': 1, 'email': 1, 'is_admin': 1, 'role': 1, 'created_at': 1}
    ).sort('created_at', -1))
    
    # Get counts for the stats cards (use string-based comparison to match stored formats)
    total_users = mongo.db.users.count_documents({})
    # Count lessons and courts where date >= today_str
    total_lessons = 0
    for b in all_lesson_bookings:
        bdate = b.get('date')
        if isinstance(bdate, datetime):
            ds = bdate.strftime('%Y-%m-%d')
        elif isinstance(bdate, str):
            ds = bdate.split('T')[0][:10]
        else:
            continue
        if ds >= today_str:
            total_lessons += 1

    total_courts = 0
    for b in all_court_bookings:
        bdate = b.get('date')
        if isinstance(bdate, datetime):
            ds = bdate.strftime('%Y-%m-%d')
        elif isinstance(bdate, str):
            ds = bdate.split('T')[0][:10]
        else:
            continue
        if ds >= today_str:
            total_courts += 1
    
    # Get current prices for the dashboard
    prices = get_lesson_prices()
    
    return render_template(
        'admin.html',
        lesson_bookings=lesson_bookings[:10],  # Limit to 10 most recent
        court_bookings=court_bookings[:10],    # Limit to 10 most recent
        users=users[:5],                       # Show 5 most recent users
        total_users=total_users,
        total_lessons=total_lessons,
        total_courts=total_courts,
        today=today.strftime('%Y-%m-%d'),
        prices=prices
    )


@app.route('/admin/courts')
@admin_required
def admin_courts():
    from datetime import datetime
    # Get all court bookings ordered by date,time
    bookings = list(mongo.db.court_bookings.find().sort([('date', 1), ('time', 1)]))
    # Ensure user names are present
    for b in bookings:
        if 'user_name' not in b and 'user_id' in b:
            try:
                uid = b['user_id']
                if isinstance(uid, str):
                    uid = ObjectId(uid)
                user = mongo.db.users.find_one({'_id': uid}, {'name': 1, 'email': 1})
                if user:
                    b['user_name'] = user.get('name', '')
                    b['user_email'] = user.get('email', '')
            except Exception:
                b['user_name'] = ''
                b['user_email'] = ''

    return render_template('admin_courts.html', bookings=bookings)


@app.route('/admin/lessons-today')
@admin_required
def admin_lessons_today():
    from datetime import datetime
    today_str = datetime.now().strftime('%Y-%m-%d')

    todays = []
    for b in mongo.db.bookings.find().sort('time', 1):
        bdate = b.get('date')
        if isinstance(bdate, datetime):
            ds = bdate.strftime('%Y-%m-%d')
        elif isinstance(bdate, str):
            ds = bdate.split('T')[0][:10]
        else:
            continue

        if ds == today_str:
            # Ensure user_name present
            if 'user_name' not in b and 'user_id' in b:
                try:
                    uid = b['user_id']
                    if isinstance(uid, str):
                        uid = ObjectId(uid)
                    user = mongo.db.users.find_one({'_id': uid}, {'name': 1, 'email': 1})
                    if user:
                        b['user_name'] = user.get('name', '')
                        b['user_email'] = user.get('email', '')
                except Exception:
                    b['user_name'] = b.get('name', '')
                    b['user_email'] = b.get('email', '')

            todays.append(b)

    return render_template('admin_lessons_today.html', bookings=todays, today=today_str)

# ... (rest of the code remains the same)
    # Demote an admin to regular user
    from bson import ObjectId
    try:
        # Prevent self-demotion
        if str(user_id) == session['user_id']:
            flash('You cannot demote yourself.', 'error')
            return redirect(request.referrer or url_for('admin_dashboard'))
        
        mongo.db.users.update_one(
            {'_id': ObjectId(user_id)}, 
            {'$set': {'is_admin': False, 'role': 'member', 'demoted_at': datetime.now()}}, 
            upsert=False
        )
        flash('User demoted from admin successfully.', 'success')
        return redirect(request.referrer or url_for('admin_users'))
    except Exception as e:
        flash('Error demoting user. Please try again.', 'error')
        return redirect(request.referrer or url_for('admin_users'))

@app.route('/admin/promote-to-admin/<user_id>')
@admin_required
def promote_to_admin(user_id):
    # Promote a user to admin status
    from bson import ObjectId
    try:
        mongo.db.users.update_one(
            {'_id': ObjectId(user_id)}, 
            {'$set': {
                'is_admin': True, 
                'role': 'admin',
                'promoted_at': datetime.now()
            }}, 
            upsert=False
        )
        flash('User promoted to admin successfully.', 'success')
    except Exception as e:
        flash('Error promoting user. Please try again.', 'error')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/promote-to-coach/<user_id>')
@admin_required
def promote_to_coach(user_id):
    # Promote a user to coach status
    from bson import ObjectId
    try:
        mongo.db.users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {
                'role': 'coach',
                'is_active': True,
                'bio': 'Professional tennis coach',
                'specialties': ['Tennis', 'Coaching'],
                'promoted_at': datetime.now()
            }},
            upsert=False
        )
        flash('User promoted to coach successfully.', 'success')
    except Exception as e:
        flash('Error promoting user to coach. Please try again.', 'error')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/disable-user/<user_id>')
@admin_required
def admin_disable_user(user_id):
    """Disable a user account: set is_active=False and cancel membership subscription if present."""
    from bson import ObjectId
    try:
        # Prevent disabling self
        if str(user_id) == session.get('user_id'):
            flash('You cannot disable your own admin account.', 'error')
            return redirect(request.referrer or url_for('admin_users'))

        user = mongo.db.users.find_one({'_id': ObjectId(user_id)})
        if not user:
            flash('User not found.', 'error')
            return redirect(request.referrer or url_for('admin_users'))

        # Attempt to cancel Stripe subscription if present
        stripe_sub = user.get('membership', {}).get('stripe_subscription_id')
        if stripe_sub:
            try:
                stripe.Subscription.delete(stripe_sub)
            except Exception as e:
                print(f"Warning: could not cancel subscription {stripe_sub}: {e}")

        mongo.db.users.update_one({'_id': ObjectId(user_id)}, {'$set': {'is_active': False, 'disabled_at': datetime.utcnow()}})
        flash('User disabled and membership cancelled where possible.', 'success')

        # Send notification email to the user about the disable action
        try:
            recipient = user.get('email')
            if recipient:
                subject = 'Your PrimeCourt account has been disabled'
                html_content = f"""
                <html><body>
                <p>Hi {user.get('name','')},</p>
                <p>Your account at PrimeCourt Arena has been disabled by an administrator. While your account is disabled you will not be able to sign in or access member features.</p>
                <p>We attempted to cancel any active membership subscription on your behalf. If you believe you were incorrectly charged or need help reactivating your account, please contact support.</p>
                <p>Best regards,<br/>PrimeCourt Arena Team</p>
                </body></html>
                """
                text_content = f"Hi {user.get('name','')},\n\nYour PrimeCourt account has been disabled by an administrator. While disabled you will not be able to sign in or access member features. We attempted to cancel any active membership subscription. If you need help, contact support.\n\nBest regards,\nPrimeCourt Arena Team"
                send_email_async(subject, recipient, html_content, text_content)
        except Exception as e:
            print(f"Error sending disable notification email: {e}")
    except Exception as e:
        print(f"Error disabling user: {e}")
        flash('Error disabling user. Check logs.', 'error')
    return redirect(request.referrer or url_for('admin_users'))


@app.route('/admin/enable-user/<user_id>')
@admin_required
def admin_enable_user(user_id):
    from bson import ObjectId
    try:
        mongo.db.users.update_one({'_id': ObjectId(user_id)}, {'$set': {'is_active': True}, '$unset': {'disabled_at': ''}})
        flash('User account enabled.', 'success')
        # Send notification email to the user about the enable action
        try:
            user = mongo.db.users.find_one({'_id': ObjectId(user_id)})
            recipient = user.get('email') if user else None
            if recipient:
                subject = 'Your PrimeCourt account has been re-enabled'
                html_content = f"""
                <html><body>
                <p>Hi {user.get('name','')},</p>
                <p>Your account at PrimeCourt Arena has been re-enabled by an administrator. You can now sign in and access member features again.</p>
                <p>If you need help restoring a previous membership or have questions, please contact support.</p>
                <p>Best regards,<br/>PrimeCourt Arena Team</p>
                </body></html>
                """
                text_content = f"Hi {user.get('name','')},\n\nYour PrimeCourt account has been re-enabled by an administrator. You can now sign in and access member features again. If you need help restoring a membership, contact support.\n\nBest regards,\nPrimeCourt Arena Team"
                send_email_async(subject, recipient, html_content, text_content)
        except Exception as e:
            print(f"Error sending enable notification email: {e}")
    except Exception as e:
        print(f"Error enabling user: {e}")
        flash('Error enabling user. Check logs.', 'error')
    return redirect(request.referrer or url_for('admin_users'))


@app.route('/admin/demote/<user_id>')
@admin_required
def demote_user(user_id):
    """Demote a user (admin only). Prevent self-demotion."""
    from bson import ObjectId
    try:
        # Prevent self-demotion
        if str(user_id) == session.get('user_id'):
            flash('You cannot demote yourself.', 'error')
            return redirect(request.referrer or url_for('admin_dashboard'))

        mongo.db.users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {'is_admin': False, 'role': 'member', 'demoted_at': datetime.now()}},
            upsert=False
        )
        flash('User demoted from admin successfully.', 'success')
    except Exception as e:
        print(f"Error demoting user: {e}")
        flash('Error demoting user. Please try again.', 'error')
    return redirect(request.referrer or url_for('admin_users'))

# Custom Jinja2 filter for date formatting
def format_datetime(value, format='%Y-%m-%d %H:%M'):
    if value is None:
        return ''
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            return value
    return value.strftime(format)

# Register the filter
app.jinja_env.filters['format_datetime'] = format_datetime

@app.route('/admin/schedule', methods=['GET', 'POST'])
@admin_required
def admin_schedule():
    # Ensure the schedule_settings collection exists
    if 'schedule_settings' not in mongo.db.list_collection_names():
        mongo.db.create_collection('schedule_settings')

    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'set_no_classes':
            date = request.form.get('date')
            reason = request.form.get('reason', 'Holiday')
            
            # Set this date as no classes
            mongo.db.schedule_settings.update_one(
                {'date': date},
                {'$set': {
                    'no_classes': True,
                    'reason': reason,
                    'updated_at': dt.datetime.now(),
                    'date': date  # Ensure date is set in case this is a new document
                }},
                upsert=True
            )
            flash(f'Set {date} as no classes: {reason}', 'success')
            return redirect(url_for('admin_schedule'))
            
        elif action == 'remove_no_classes':
            date = request.form.get('date')
            
            # Remove no classes setting for this date
            mongo.db.schedule_settings.update_one(
                {'date': date},
                {'$set': {
                    'no_classes': False,
                    'updated_at': dt.datetime.now(),
                    'date': date  # Ensure date is set in case this is a new document
                }},
                upsert=True
            )
            flash(f'Removed no classes setting for {date}', 'success')
            return redirect(url_for('admin_schedule'))
            
        elif action == 'set_time_slots':
            date = request.form.get('date')
            available_slots = request.form.getlist('available_slots')
            
            # Update time slot availability for this date
            mongo.db.schedule_settings.update_one(
                {'date': date},
                {'$set': {
                    'custom_time_slots': available_slots,
                    'updated_at': dt.datetime.now(),
                    'date': date,  # Ensure date is set in case this is a new document
                    'no_classes': False  # Reset no_classes when setting time slots
                }},
                upsert=True
            )
            flash(f'Updated time slots for {date}', 'success')
            return redirect(url_for('admin_schedule'))
            
        elif action == 'reset_time_slots':
            date = request.form.get('date')
            
            # Reset to default time slots for this date
            mongo.db.schedule_settings.update_one(
                {'date': date},
                {
                    '$unset': {'custom_time_slots': ""},
                    '$set': {
                        'updated_at': dt.datetime.now(),
                        'date': date  # Ensure date is set in case this is a new document
                    }
                },
                upsert=True
            )
            flash(f'Reset time slots to default for {date}', 'success')
            return redirect(url_for('admin_schedule'))
    
    # Get current schedule settings and prepare for template
    schedule_settings = []
    for setting in mongo.db.schedule_settings.find().sort('date', 1):
        # Convert ObjectId to string and ensure all required fields exist
        setting['_id'] = str(setting.get('_id', ''))
        setting['date'] = setting.get('date', '')
        setting['no_classes'] = setting.get('no_classes', False)
        setting['reason'] = setting.get('reason', '')
        setting['custom_time_slots'] = setting.get('custom_time_slots', [])
        
        # Convert updated_at to string if it exists
        if 'updated_at' in setting and isinstance(setting['updated_at'], dt.datetime):
            setting['updated_at'] = setting['updated_at'].strftime('%Y-%m-%d %H:%M')
            
        schedule_settings.append(setting)
    
    # Get next 30 days for easy selection
    from datetime import datetime, timedelta
    today = datetime.now()
    future_dates = []
    for i in range(30):
        date = today + timedelta(days=i)
        future_dates.append({
            'date': date.strftime('%Y-%m-%d'),
            'day_name': date.strftime('%A'),
            'formatted': date.strftime('%B %d, %Y')
        })
    
    # Process schedule settings to ensure all have required fields
    processed_settings = []
    for setting in schedule_settings:
        if '_id' in setting and not isinstance(setting['_id'], str):
            setting['_id'] = str(setting['_id'])
        if 'no_classes' not in setting:
            setting['no_classes'] = False
        if 'custom_time_slots' not in setting:
            setting['custom_time_slots'] = []
        processed_settings.append(setting)
    
    return render_template('admin_schedule.html',
                         schedule_settings=processed_settings,
                         future_dates=future_dates)

@app.route('/send-reminders')
def send_reminders():
    """Send reminder emails for bookings tomorrow (can be called by cron job)"""
    if not os.getenv('REMINDER_SECRET') or request.args.get('secret') != os.getenv('REMINDER_SECRET'):
        return "Unauthorized", 401
    
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    reminder_count = 0
    
    # Send lesson reminders
    lesson_bookings = mongo.db.bookings.find({'date': tomorrow})
    for booking in lesson_bookings:
        details = f"<p><strong>Type:</strong> {booking['lesson_type'].title()} Lesson</p>"
        if booking.get('recurring_id'):
            details += f"<p><strong>Series:</strong> Week {booking.get('week_number', 1)} of {booking.get('total_weeks', 1)}</p>"
        
        send_reminder_email("Lesson", booking['name'], booking['email'], 
                           tomorrow, booking['time'], details)
        reminder_count += 1
    
    # Send court reminders
    court_bookings = mongo.db.court_bookings.find({'date': tomorrow})
    for booking in court_bookings:
        details = f"<p><strong>Court:</strong> {booking['court_id']}</p>"
        send_reminder_email("Court", booking['user_name'], booking['user_email'], 
                           tomorrow, booking['time'], details)
        reminder_count += 1
    
    return f"Sent {reminder_count} reminder emails for {tomorrow}"

@app.route('/admin/promote-me')
@login_required
def promote_me():
    """Promote the current logged-in user to admin."""
    try:
        user_id = session.get('user_id')
        if not user_id:
            flash('You must be logged in.', 'error')
            return redirect(url_for('login'))
        mongo.db.users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {
                'is_admin': True,
                'role': 'admin',
                'promoted_at': datetime.now()
            }},
            upsert=False
        )
        session['is_admin'] = True
        flash('You have been promoted to admin!', 'success')
    except Exception as e:
        flash('Error promoting to admin. Please try again.', 'error')
    return redirect(url_for('admin_dashboard'))
