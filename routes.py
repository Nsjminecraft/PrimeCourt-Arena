from flask import render_template, request, redirect, url_for, session, flash, jsonify
from app import app, mongo
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os
from bson import ObjectId
import datetime as dt  # Add this import for datetime operations
import smtplib
import stripe
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from threading import Thread
from functools import wraps

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_admin'):
            flash('Access denied. Admin access required.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

app.secret_key = "f=qSj{PuL:,&^IP^zgDL=ez@dcSM"

# Initialize Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY')

# Default lesson pricing (in cents)
DEFAULT_LESSON_PRICES = {
    'private': 5000,  # $50.00
    'group': 2500,    # $25.00
}

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

# Protect lessons and membership routes
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/create-lesson-booking-session', methods=['POST'])
@login_required
def create_lesson_booking_session():
    if 'user_id' not in session:
        return jsonify({'error': 'Please log in to book a lesson'}), 401
    
    try:
        data = request.get_json()
        day_idx = int(data.get('day_idx'))
        slot_idx = int(data.get('slot_idx'))
        lesson_type = data.get('lesson_type')
        recurring_weeks = int(data.get('recurring_weeks', 1))
        
        # Get current pricing
        prices = get_lesson_prices()
        
        # Validate lesson type
        if lesson_type not in ['private', 'group']:
            return jsonify({'error': 'Invalid lesson type'}), 400
        
        # Get user info
        user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Generate month slots to get the selected day
        today = datetime.now()
        lesson_days = generate_month_slots(today.year, today.month)
        
        if day_idx < 0 or day_idx >= len(lesson_days):
            return jsonify({'error': 'Invalid day selected'}), 400
            
        selected_day = lesson_days[day_idx]
        if slot_idx < 0 or slot_idx >= len(selected_day['slots']):
            return jsonify({'error': 'Invalid time slot selected'}), 400
            
        slot = selected_day['slots'][slot_idx]
        
        # Calculate total price using current pricing
        price_cents = prices.get(lesson_type, 0) * recurring_weeks
        
        # Create Stripe Checkout session
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'{lesson_type.title()} Lesson',
                        'description': f'Badminton {lesson_type} lesson on {selected_day["date"]} at {slot["time"]}'
                    },
                    'unit_amount': prices.get(lesson_type, 0),
                },
                'quantity': recurring_weeks,
            }],
            mode='payment',
            success_url=url_for('lesson_booking_success', session_id='{CHECKOUT_SESSION_ID}', _external=True),
            cancel_url=url_for('lessons', _external=True),
            metadata={
                'day_idx': str(day_idx),
                'slot_idx': str(slot_idx),
                'lesson_type': lesson_type,
                'recurring_weeks': str(recurring_weeks),
                'user_id': str(session['user_id']),
                'user_name': user.get('name', ''),
                'user_email': user.get('email', '')
            }
        )
        
        return jsonify({'sessionId': checkout_session.id})
        
    except Exception as e:
        print(f"Error creating lesson booking session: {str(e)}")
        return jsonify({'error': 'An error occurred while processing your request'}), 500

@app.route('/lesson-booking-success')
def lesson_booking_success():
    session_id = request.args.get('session_id')
    if not session_id:
        flash('Invalid session', 'error')
        return redirect(url_for('lessons'))
    
    try:
        # Verify the session with Stripe
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        
        if checkout_session.payment_status != 'paid':
            flash('Payment not completed', 'error')
            return redirect(url_for('lessons'))
        
        # Process the booking
        metadata = checkout_session.metadata
        day_idx = int(metadata.get('day_idx'))
        slot_idx = int(metadata.get('slot_idx'))
        lesson_type = metadata.get('lesson_type')
        recurring_weeks = int(metadata.get('recurring_weeks', 1))
        
        # Get user info from session
        user_id = metadata.get('user_id')
        user_name = metadata.get('user_name')
        user_email = metadata.get('user_email')
        
        # Generate month slots to get the selected day
        today = datetime.now()
        lesson_days = generate_month_slots(today.year, today.month)
        
        if day_idx < 0 or day_idx >= len(lesson_days):
            flash('Invalid day selected', 'error')
            return redirect(url_for('lessons'))
            
        selected_day = lesson_days[day_idx]
        if slot_idx < 0 or slot_idx >= len(selected_day['slots']):
            flash('Invalid time slot selected', 'error')
            return redirect(url_for('lessons'))
            
        slot = selected_day['slots'][slot_idx]
        
        # Process the booking (similar to the original POST handler in lessons route)
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
            
            # Create the booking
            booking_data = {
                'date': date_str,
                'time': slot['time'],
                'lesson_type': lesson_type,
                'name': user_name,
                'email': user_email,
                'user_id': user_id,
                'recurring_id': f"{user_id}_{slot['time']}_{lesson_type}",
                'week_number': week + 1,
                'total_weeks': recurring_weeks,
                'payment_status': 'paid',
                'payment_id': checkout_session.payment_intent,
                'amount_paid': prices[lesson_type] if lesson_type in prices else 0,
                'created_at': datetime.now()
            }
            
            mongo.db.bookings.insert_one(booking_data)
            success_count += 1
            
            # Send confirmation email
            details = f"<p><strong>Type:</strong> {lesson_type.title()} Lesson</p>"
            if lesson_type == 'group':
                group_count = mongo.db.bookings.count_documents({
                    'date': date_str,
                    'time': slot['time'],
                    'lesson_type': 'group'
                })
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
        
        return redirect(url_for('lessons'))
        
    except Exception as e:
        print(f"Error processing lesson booking success: {str(e)}")
        flash('Your payment was successful, but there was an error completing your booking. Please contact support.', 'warning')
        return redirect(url_for('lessons'))

@app.route('/lessons', methods=['GET', 'POST'])
@login_required
def lessons():
    message = None
    # Consume any pending flash messages so none render on this page
    try:
        from flask import get_flashed_messages
        get_flashed_messages(with_categories=True)
    except Exception:
        pass
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
        
        for slot_idx, slot in enumerate(day['slots']):
            slot['group'] = []  # Reset group list
            slot['private'] = None  # Reset private slot
            
            # Check if this time slot is available based on custom settings
            if custom_slots:
                slot['is_available'] = slot['time'] in custom_slots
            else:
                slot['is_available'] = True
            
            # Check if this time slot has passed for today
            if day['date'] == datetime.now().strftime('%Y-%m-%d'):
                # Extract end hour from time slot (e.g., "5:00 PM - 6:00 PM" -> 6)
                try:
                    end_hour_str = slot['time'].split(' - ')[1].split(':')[0]
                    end_hour = int(end_hour_str)
                    current_hour = datetime.now().hour
                    
                    # Convert 12-hour format to 24-hour for comparison
                    if 'PM' in slot['time'] and end_hour != 12:
                        end_hour += 12
                    elif 'AM' in slot['time'] and end_hour == 12:
                        end_hour = 0
                    
                    # Slot is past if it has already ended
                    slot['is_past'] = end_hour <= current_hour
                except:
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
        day_idx = int(request.form['day_idx'])
        slot_idx = int(request.form['slot_idx'])
        lesson_type = request.form['lesson_type']
        name = request.form['name']
        email = request.form['email']
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
                slot_available = slot['time'] in custom_slots
            
            if not slot_available:
                continue  # Skip this date
            
            # Check if slot is past for this date
            if date_str == datetime.now().strftime('%Y-%m-%d'):
                try:
                    end_hour_str = slot['time'].split(' - ')[1].split(':')[0]
                    end_hour = int(end_hour_str)
                    current_hour = datetime.now().hour
                    
                    if 'PM' in slot['time'] and end_hour != 12:
                        end_hour += 12
                    elif 'AM' in slot['time'] and end_hour == 12:
                        end_hour = 0
                    
                    if end_hour <= current_hour:
                        continue  # Skip past slots
                except:
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
        now=datetime.now(),
        stripe_public_key=STRIPE_PUBLIC_KEY
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
        booked_map=booked_map
    )

@app.route('/membership', methods=['GET', 'POST'])
@login_required
def membership():
    from app import MEMBERSHIP_PLANS
    return render_template('membership.html', membership_plans=MEMBERSHIP_PLANS)

@app.route('/profile')
@login_required
def profile():
    from bson.objectid import ObjectId
    
    # Get user data from MongoDB - convert string ID to ObjectId
    user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
    
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('index'))
    
    # Get membership details
    membership = user.get('membership', {})
    
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
    
    # Get recent lesson bookings (upcoming and recent past)
    lesson_bookings = list(mongo.db.bookings.find({
        'date': {'$gte': today - timedelta(days=7)}
    }).sort('date', 1))
    
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
    
    # Get recent court bookings
    court_bookings = list(mongo.db.court_bookings.find({
        'date': {'$gte': today - timedelta(days=7)}
    }).sort('date', 1))
    
    # Process court bookings to include user names and format dates
    for booking in court_bookings:
        # Add user name if not already present
        if 'user_name' not in booking and 'user_id' in booking:
            user = mongo.db.users.find_one({'_id': booking['user_id']}, {'name': 1})
            if user:
                booking['user_name'] = user.get('name', 'Unknown User')
        
        # Ensure date is in string format for the template
        if 'date' in booking and isinstance(booking['date'], datetime):
            booking['date'] = booking['date'].strftime('%Y-%m-%d')
    
    # Get users count and basic info
    users = list(mongo.db.users.find(
        {},
        {'name': 1, 'email': 1, 'is_admin': 1, 'role': 1, 'created_at': 1}
    ).sort('created_at', -1))
    
    # Get counts for the stats cards
    total_users = mongo.db.users.count_documents({})
    total_lessons = mongo.db.bookings.count_documents({
        'date': {'$gte': today}
    })
    total_courts = mongo.db.court_bookings.count_documents({
        'date': {'$gte': today}
    })
    
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

