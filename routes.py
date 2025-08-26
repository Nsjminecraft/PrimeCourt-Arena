from flask import render_template, request, redirect, url_for, session, flash
from app import app, mongo
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from threading import Thread

app.secret_key = "f=qSj{PuL:,&^IP^zgDL=ez@dcSM"

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
        is_admin = user_count == 0  # First user becomes admin
        
        mongo.db.users.insert_one({
            'name': name, 
            'email': email, 
            'password': password,
            'is_admin': is_admin,
            'created_at': datetime.now()
        })
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
            session['is_admin'] = bool(user.get('is_admin', False))
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

    # Load schedule settings and bookings
    schedule_settings = {s['date']: s for s in mongo.db.schedule_settings.find()}
    bookings = list(mongo.db.bookings.find({}))
    
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
        now=datetime.now()
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
        court_id = request.form.get('court_id')
        time_slot = request.form.get('time_slot')
        date_str = request.form.get('date') or selected_date_str

        if not court_id or not time_slot:
            flash('Please select a court and time slot', 'error')
            return redirect(url_for('courts', date=date_str))

        # Prevent double booking
        existing = mongo.db.court_bookings.find_one({
            'date': date_str,
            'court_id': court_id,
            'time': time_slot
        })
        if existing:
            flash('That slot is already booked. Please choose another.', 'error')
            return redirect(url_for('courts', date=date_str))

        court_booking = {
            'date': date_str,
            'court_id': court_id,
            'time': time_slot,
            'user_id': session.get('user_id'),
            'user_name': session.get('user_name'),
            'user_email': session.get('user_email'),
            'created_at': datetime.now()
        }
        mongo.db.court_bookings.insert_one(court_booking)
        
        # Send confirmation email
        court_name = next((court['name'] for court in courts_list if court['id'] == court_id), court_id)
        details = f"<p><strong>Court:</strong> {court_name}</p>"
        send_booking_confirmation_email("Court", session.get('user_name'), session.get('user_email'), date_str, time_slot, details)
        
        flash('Court booked successfully!', 'success')
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
    # Get user data from MongoDB
    user = mongo.db.users.find_one({'_id': session['user_id']})
    
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
        
        if expires_at > now:
            is_active = True
            days_remaining = (expires_at - now).days
    
    return render_template('profile.html', 
                         user=user, 
                         membership=membership, 
                         is_active=is_active, 
                         days_remaining=days_remaining)

@app.route('/change-membership', methods=['GET', 'POST'])
@login_required
def change_membership():
    # Get user data and current membership
    user = mongo.db.users.find_one({'_id': session['user_id']})
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('profile'))
    
    current_membership = user.get('membership', {})
    
    if request.method == 'POST':
        new_plan_id = request.form.get('plan_id')
        if not new_plan_id:
            flash('Please select a plan', 'error')
            return redirect(url_for('change_membership'))
        
        # Import MEMBERSHIP_PLANS from app
        from app import MEMBERSHIP_PLANS
        
        if new_plan_id not in MEMBERSHIP_PLANS:
            flash('Invalid plan selected', 'error')
            return redirect(url_for('change_membership'))
        
        new_plan = MEMBERSHIP_PLANS[new_plan_id]
        
        # Check if user is selecting a different plan
        current_plan_price = 0
        if current_membership.get('plan') in MEMBERSHIP_PLANS:
            current_plan_price = MEMBERSHIP_PLANS[current_membership['plan']]['price']
        
        if new_plan['price'] == current_plan_price:
            flash('You are already on this plan', 'error')
            return redirect(url_for('change-membership'))
        
        # Determine if this is an upgrade or downgrade
        is_upgrade = new_plan['price'] > current_plan_price
        action_type = "Upgrade" if is_upgrade else "Downgrade"
        
        # Create plan change checkout session
        try:
            import stripe
            stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
            
            # Create product and price for plan change
            product_name = f"PrimeCourt {new_plan['name']} ({action_type})"
            
            # Check if product exists, create if not
            products = stripe.Product.list(limit=100)
            product = None
            for p in products.data:
                if p.name == product_name:
                    product = p
                    break
            
            if not product:
                product = stripe.Product.create(
                    name=product_name,
                    description=f"{action_type} to {new_plan['name']} - Monthly subscription"
                )
            
            # Check if price exists, create if not
            prices = stripe.Price.list(product=product.id, limit=100)
            price = None
            for p in prices.data:
                if p.unit_amount == new_plan['price'] and p.recurring.interval == new_plan['interval']:
                    price = p
                    break
            
            if not price:
                price = stripe.Price.create(
                    product=product.id,
                    unit_amount=new_plan['price'],
                    currency='usd',
                    recurring={'interval': new_plan['interval']}
                )
            
            # Create checkout session for plan change
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price': price.id,
                    'quantity': 1,
                }],
                mode='subscription',
                success_url=url_for('plan_change_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
                cancel_url=url_for('change_membership', _external=True),
                customer_email=session.get('user_email'),
                client_reference_id=session.get('user_id'),
                metadata={
                    'plan_id': new_plan_id,
                    'user_id': str(session.get('user_id')),
                    'action_type': action_type.lower()
                }
            )
            return redirect(checkout_session.url)
            
        except Exception as e:
            print(f"Error creating plan change checkout session: {str(e)}")
            flash('An error occurred while processing your request. Please try again.', 'error')
            return redirect(url_for('change_membership'))
    
    # GET request - show all available plans
    from app import MEMBERSHIP_PLANS
    
    # Get current plan details
    current_plan_price = 0
    current_plan_name = "No Plan"
    if current_membership.get('plan') in MEMBERSHIP_PLANS:
        current_plan_price = MEMBERSHIP_PLANS[current_membership['plan']]['price']
        current_plan_name = MEMBERSHIP_PLANS[current_membership['plan']]['name']
    
    # Show all plans with upgrade/downgrade indicators
    all_plans = {}
    for plan_id, plan in MEMBERSHIP_PLANS.items():
        if plan_id != current_membership.get('plan'):  # Don't show current plan
            plan['is_upgrade'] = plan['price'] > current_plan_price
            plan['price_difference'] = plan['price'] - current_plan_price
            all_plans[plan_id] = plan
    
    return render_template('change_membership.html', 
                         current_membership=current_membership,
                         current_plan_price=current_plan_price,
                         current_plan_name=current_plan_name,
                         all_plans=all_plans)

@app.route('/plan-change-success')
@login_required
def plan_change_success():
    session_id = request.args.get('session_id')
    if not session_id:
        flash('Invalid session', 'error')
        return redirect(url_for('profile'))
    
    try:
        import stripe
        stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
        
        # Verify the session with Stripe
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        
        # Update user's membership in the database
        user_id = checkout_session.metadata.get('user_id')
        plan_id = checkout_session.metadata.get('plan_id')
        action_type = checkout_session.metadata.get('action_type', 'changed')
        
        if user_id and plan_id:
            from app import MEMBERSHIP_PLANS
            if plan_id in MEMBERSHIP_PLANS:
                plan = MEMBERSHIP_PLANS[plan_id]
                expires_at = datetime.now() + timedelta(days=30)  # 1 month from now
                
                mongo.db.users.update_one(
                    {'_id': user_id},
                    {'$set': {
                        'membership.plan': plan_id,
                        'membership.status': 'active',
                        'membership.expires_at': expires_at,
                        'membership.stripe_customer_id': checkout_session.customer,
                        'membership.stripe_subscription_id': checkout_session.subscription,
                        'membership.changed_at': datetime.now(),
                        'membership.last_action': action_type
                    }},
                    upsert=True
                )
                
                action_text = "upgraded to" if action_type == "upgrade" else "downgraded to"
                flash(f'Successfully {action_text} {plan["name"]}!', 'success')
            else:
                flash('Membership changed successfully!', 'success')
        else:
            flash('Plan change successful, but there was an error updating your membership. Please contact support.', 'warning')
            
    except Exception as e:
        print(f"Error processing plan change success: {str(e)}")
        flash('Your plan change was successful, but there was an error updating your membership. Please contact support.', 'warning')
    
    return redirect(url_for('profile'))

@app.route('/contact')
def contact():
    return render_template('contact.html')

# Admin protection

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            flash('Admin access required', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin')
@admin_required
def admin_dashboard():
    # Recent lesson bookings
    lesson_bookings = list(mongo.db.bookings.find().sort('date', 1))
    # Recent court bookings
    court_bookings = list(mongo.db.court_bookings.find().sort('date', 1))
    # Users overview (limited fields)
    users = list(mongo.db.users.find({}, {'name': 1, 'email': 1, 'is_admin': 1}))
    return render_template('admin.html',
                           lesson_bookings=lesson_bookings,
                           court_bookings=court_bookings,
                           users=users)

@app.route('/admin/promote-me')
@login_required
def promote_me_admin():
    # Promote the currently logged in user to admin
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    
    # Convert string ID to ObjectId for MongoDB
    from bson import ObjectId
    try:
        mongo.db.users.update_one(
            {'_id': ObjectId(user_id)}, 
            {'$set': {'is_admin': True, 'promoted_at': datetime.now()}}, 
            upsert=False
        )
        session['is_admin'] = True
        flash('You are now an admin.', 'success')
        return redirect(url_for('admin_dashboard'))
    except Exception as e:
        flash('Error promoting to admin. Please try again.', 'error')
        return redirect(url_for('profile'))

@app.route('/admin/demote-user/<user_id>')
@admin_required
def demote_user(user_id):
    # Demote a user from admin status
    from bson import ObjectId
    try:
        # Don't allow demoting yourself
        if user_id == session.get('user_id'):
            flash('You cannot demote yourself.', 'error')
            return redirect(url_for('admin_dashboard'))
        
        mongo.db.users.update_one(
            {'_id': ObjectId(user_id)}, 
            {'$set': {'is_admin': False, 'demoted_at': datetime.now()}}, 
            upsert=False
        )
        flash('User demoted from admin successfully.', 'success')
        return redirect(url_for('admin_dashboard'))
    except Exception as e:
        flash('Error demoting user. Please try again.', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/promote-user/<user_id>')
@admin_required
def promote_user(user_id):
    # Promote a user to admin status
    from bson import ObjectId
    try:
        mongo.db.users.update_one(
            {'_id': ObjectId(user_id)}, 
            {'$set': {'is_admin': True, 'promoted_at': datetime.now()}}, 
            upsert=False
        )
        flash('User promoted to admin successfully.', 'success')
        return redirect(url_for('admin_dashboard'))
    except Exception as e:
        flash('Error promoting user. Please try again.', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/schedule', methods=['GET', 'POST'])
@admin_required
def admin_schedule():
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
                    'updated_at': datetime.now()
                }},
                upsert=True
            )
            flash(f'Set {date} as no classes: {reason}', 'success')
            
        elif action == 'remove_no_classes':
            date = request.form.get('date')
            
            # Remove no classes setting for this date
            mongo.db.schedule_settings.update_one(
                {'date': date},
                {'$set': {
                    'no_classes': False,
                    'updated_at': datetime.now()
                }},
                upsert=True
            )
            flash(f'Removed no classes setting for {date}', 'success')
            
        elif action == 'set_time_slots':
            date = request.form.get('date')
            available_slots = request.form.getlist('available_slots')
            
            # Update time slot availability for this date
            mongo.db.schedule_settings.update_one(
                {'date': date},
                {'$set': {
                    'custom_time_slots': available_slots,
                    'updated_at': datetime.now()
                }},
                upsert=True
            )
            flash(f'Updated time slots for {date}', 'success')
            
        elif action == 'reset_time_slots':
            date = request.form.get('date')
            
            # Reset to default time slots for this date
            mongo.db.schedule_settings.update_one(
                {'date': date},
                {'$unset': {'custom_time_slots': ""}},
                upsert=True
            )
            flash(f'Reset time slots to default for {date}', 'success')
    
    # Get current schedule settings
    schedule_settings = list(mongo.db.schedule_settings.find().sort('date', 1))
    
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
    
    return render_template('admin_schedule.html',
                         schedule_settings=schedule_settings,
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

