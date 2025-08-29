from flask import render_template, request, redirect, url_for, flash, session, jsonify
from app import app, mongo, stripe, stripe_public_key, MEMBERSHIP_PLANS
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
            flash('This account is pending admin approval. Please wait for activation.')
            return redirect(url_for('login'))
            
        # Verify password
        if not check_password_hash(user['password'], password):
            flash('Incorrect password.')
            return redirect(url_for('login'))
        
        # Set session variables
        session['user_id'] = str(user['_id'])
        session['user_name'] = user['name']
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

@app.route('/coach/dashboard')
def coach_dashboard():
    if 'user_id' not in session or not session.get('is_coach'):
        flash('Access denied. Coach access required.')
        return redirect(url_for('login'))
        
    # Get coach's upcoming lessons
    coach_lessons = list(mongo.db.bookings.find({
        'coach_id': session['user_id'],
        'type': 'lesson',
        'start_time': {'$gte': datetime.now()}
    }).sort('start_time', 1))
    
    return render_template('coach_dashboard.html', 
                         coach_lessons=coach_lessons,
                         now=datetime.now())

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
    recurring_weeks = int(data.get('recurring_weeks') or 1)

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
        
        # Check if payment was successful
        if checkout_session.payment_status != 'paid':
            flash('Payment was not successful. Please try again.', 'error')
            return redirect(url_for('lessons'))
        
        # Get metadata from the checkout session
        metadata = checkout_session.metadata
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
            
            # Check if slot is already booked
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
            
            # Send confirmation email
            details = f"<p><strong>Type:</strong> {lesson_type.title()} Lesson</p>"
            if recurring_weeks > 1:
                details += f"<p><strong>Series:</strong> Week {week + 1} of {recurring_weeks}</p>"
            
            send_booking_confirmation_email(
                "Lesson", 
                user.get('name', ''), 
                user.get('email', ''), 
                week_date_str, 
                time_str, 
                details
            )
        
        if success_count > 0:
            if recurring_weeks == 1:
                flash(f'Successfully booked {lesson_type} lesson!', 'success')
            else:
                flash(f'Successfully booked {success_count} {lesson_type} lessons!', 'success')
        else:
            flash('No available slots were booked. Please try again.', 'error')
        
        return redirect(url_for('lessons', day=day_idx, slot=slot_idx))
        
        # Process the booking
        success_count = 0
        for week in range(recurring_weeks):
            lesson_date = datetime.strptime(selected_day['date'], '%Y-%m-%d') + timedelta(weeks=week)
            date_str = lesson_date.strftime('%Y-%m-%d')
            
            # Check if this date has no classes
            schedule_settings = mongo.db.schedule_settings.find_one({'date': date_str}) or {}
            if schedule_settings.get('no_classes', False):
                continue
                
            # Check if slot is available for this date
            if schedule_settings.get('custom_time_slots'):
                if slot['time'] not in schedule_settings['custom_time_slots']:
                    continue
            
            # Check if slot is already booked
            existing_booking = mongo.db.bookings.find_one({
                'date': date_str,
                'time': slot['time'],
                'lesson_type': 'private'
            })
            
            if existing_booking:
                if lesson_type == 'private':
                    continue
                elif lesson_type == 'group':
                    group_count = mongo.db.bookings.count_documents({
                        'date': date_str,
                        'time': slot['time'],
                        'lesson_type': 'group'
                    })
                    if group_count >= 5:
                        continue
            
            # Create booking in database
            booking_data = {
                'user_id': str(session['user_id']),
                'name': user.get('name', ''),
                'email': user.get('email', ''),
                'date': date_str,
                'time': slot['time'],
                'lesson_type': lesson_type,
                'created_at': datetime.utcnow(),
                'payment_status': 'paid',  # No payment required now
                'recurring_week': f"{week + 1}/{recurring_weeks}" if recurring_weeks > 1 else None
            }
            
            # Check if this is a group or private lesson
            if lesson_type == 'group':
                if len(slot['group']) >= 5:  # Max 5 people in a group
                    continue
                booking_data['group_size'] = len(slot['group']) + 1
                details += f"<p><strong>Group Size:</strong> {group_count}/5</p>"
            
            if recurring_weeks > 1:
                details += f"<p><strong>Series:</strong> Week {week + 1} of {recurring_weeks}</p>"
                
            send_booking_confirmation_email(
                "Lesson", 
                user_name, 
                user_email, 
                date_str, 
                slot['time'], 
                details
            )
        
        if success_count > 0:
            if recurring_weeks == 1:
                flash(f'Successfully booked {lesson_type} lesson!', 'success')
            else:
                flash(f'Successfully booked {success_count} out of {recurring_weeks} recurring {lesson_type} lessons!', 'success')
        else:
            flash('No lessons could be booked. All slots may be unavailable or booked.', 'warning')
        
        # Redirect with flag to allow flashes to render once on lessons page
        return redirect(url_for('lessons', show_flash=1))
        
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
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': plan['name'],
                    },
                    'unit_amount': plan['price'],
                },
                'quantity': 1,
            }],
            mode='payment',
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
        if checkout_session.payment_status == 'paid':
            metadata = checkout_session.metadata
            user_id = metadata['user_id']
            plan_id = metadata['plan_id']
            
            # Update user's membership status in the database
            expires_at = datetime.now() + timedelta(days=30) # Assuming monthly membership
            mongo.db.users.update_one(
                {'_id': ObjectId(user_id)},
                {'$set': {
                    'membership': {
                        'plan_id': plan_id,
                        'status': 'active',
                        'purchased_at': datetime.now(),
                        'expires_at': expires_at
                    }
                }}
            )
            
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
    users = list(mongo.db.users.find({}, {'password': 0}))  # Exclude passwords
    return render_template('admin_users.html', users=users)

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

