import os
from dotenv import load_dotenv
from flask import Flask, render_template, redirect, request, url_for, session, flash, jsonify, abort
from flask_pymongo import PyMongo
import stripe
from datetime import datetime, timedelta
from bson import ObjectId

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configuration
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'RYxsY:K#IPqI+dMr&MojdYLU7H]vV7')
app.config['MONGO_URI'] = os.getenv('MONGO_URI', 'mongodb://localhost:27017/primecourt')

# Initialize extensions
mongo = PyMongo(app)

# Configure Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
stripe_public_key = os.getenv('STRIPE_PUBLIC_KEY')

# Membership plans configuration (in cents)
MEMBERSHIP_PLANS = {
    'basic': {
        'name': 'Basic Membership',
        'price': 3000,  # $30.00
        'interval': 'month',
        'features': [
            'Access to courts during open hours',
            '10% discount on equipment rental',
            'Member-only events'
        ]
    },
    'premium': {
        'name': 'Premium Membership',
        'price': 6000,  # $60.00
        'interval': 'month',
        'features': [
            'All Basic benefits',
            'Unlimited group lessons',
            '20% discount on private coaching',
            'Priority court booking'
        ]
    },
    'family': {
        'name': 'Family Membership',
        'price': 10000,  # $100.00
        'interval': 'month',
        'features': [
            'Up to 4 family members',
            'All Premium benefits',
            'Family events and tournaments',
            '25% discount on additional coaching'
        ]
    }
}

# Import routes after app is created to avoid circular imports
import routes

@app.route('/')
def index():
    # Load active coaches to show on the homepage
    try:
        # Match coaches where role == 'coach' and is_active is True or missing (imported docs may lack the flag)
        query = {'role': 'coach', '$or': [{'is_active': True}, {'is_active': {'$exists': False}}]}
        coaches = list(mongo.db.users.find(query, {'password': 0}))
    except Exception:
        coaches = []
    return render_template('main.html', coaches=coaches)

@app.route('/contact')
def contact():
    return render_template('contact.html')


@app.route('/court-booking-success')
def court_booking_success():
    session_id = request.args.get('session_id')
    if not session_id:
        flash('Invalid session', 'error')
        return redirect(url_for('courts'))
    
    try:
        # Verify the session with Stripe
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        
        if checkout_session.payment_status != 'paid':
            flash('Payment not completed', 'error')
            return redirect(url_for('courts'))
        
        # Extract booking details from metadata
        metadata = checkout_session.metadata
        court_id = metadata.get('court_id')
        date = metadata.get('date')
        time_slot = metadata.get('time_slot')
        user_id = metadata.get('user_id')
        user_name = metadata.get('user_name')
        user_email = metadata.get('user_email')
        
        # Create the court booking
        court_booking = {
            'date': date,
            'court_id': court_id,
            'time': time_slot,
            'user_id': user_id,
            'user_name': user_name,
            'user_email': user_email,
            'payment_intent': checkout_session.payment_intent,
            'amount_paid': checkout_session.amount_total / 100,  # Convert back to dollars
            'created_at': datetime.now(),
            'status': 'confirmed'
        }
        
    # Save to database
    mongo.db.court_bookings.insert_one(court_booking)

    # Send confirmation email
    details = f"<p><strong>Court:</strong> {court_id}</p>"
    routes.send_booking_confirmation_email("Court Booking", user_name, user_email, date, time_slot, details)

    flash('Court booked successfully!', 'success')
    return redirect(url_for('courts', date=date))
        
    except Exception as e:
        print(f"Error processing court booking success: {str(e)}")
        flash('Your payment was successful, but there was an error completing your booking. Please contact support.', 'warning')
        return redirect(url_for('courts'))


@app.route('/test-email')
def test_email():
    """Test email sending functionality"""
    from routes import send_email_async, EMAIL_USER
    try:
        # Send a test email to the admin email
        test_recipient = EMAIL_USER  # Sending to yourself for testing
        send_email_async(
            "PrimeCourt Arena - Test Email",
            test_recipient,
            "<h2>🎾 Test Email from PrimeCourt Arena</h2><p>If you're seeing this, email is working correctly!</p>",
            "Test Email from PrimeCourt Arena\n\nIf you're seeing this, email is working correctly!"
        )
        return "Test email sent! Check your inbox (and spam folder)."
    except Exception as e:
        return f"Error sending test email: {str(e)}"


@app.route('/_test_smtp')
def _test_smtp():
    """Synchronous SMTP connectivity/login test that returns JSON with the result.
    Use this on Render to see the exact exception when connecting to your SMTP provider.
    """
    try:
        # Import email settings from routes to keep a single source of truth
        from routes import EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASSWORD
        import smtplib

        # Attempt a connection and login (timeout short so it fails fast)
        server = smtplib.SMTP(EMAIL_HOST, int(EMAIL_PORT), timeout=10)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.quit()
        return jsonify({'ok': True, 'message': 'SMTP connection and login succeeded', 'host': EMAIL_HOST, 'port': EMAIL_PORT, 'user': EMAIL_USER})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')