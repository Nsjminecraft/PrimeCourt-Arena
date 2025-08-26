import os
from dotenv import load_dotenv
from flask import Flask, render_template, redirect, request, url_for, session, flash, jsonify, abort
from flask_pymongo import PyMongo
import stripe
from datetime import datetime, timedelta

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
    return render_template('main.html')

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    if 'user_id' not in session:
        flash('Please log in to purchase a membership', 'error')
        return redirect(url_for('login'))
    
    plan_id = request.form.get('plan_id')
    if not plan_id or plan_id not in MEMBERSHIP_PLANS:
        flash('Invalid membership plan selected', 'error')
        return redirect(url_for('membership'))
    
    plan = MEMBERSHIP_PLANS[plan_id]
    
    try:
        # Create product and price dynamically if they don't exist
        product_name = f"PrimeCourt {plan['name']}"
        
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
                description=f"Monthly subscription for {plan['name']}"
            )
        
        # Check if price exists, create if not
        prices = stripe.Price.list(product=product.id, limit=100)
        price = None
        for p in prices.data:
            if p.unit_amount == plan['price'] and p.recurring.interval == plan['interval']:
                price = p
                break
        
        if not price:
            price = stripe.Price.create(
                product=product.id,
                unit_amount=plan['price'],
                currency='usd',
                recurring={'interval': plan['interval']}
            )
        
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': price.id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=url_for('payment_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('membership', _external=True),
            customer_email=session.get('user_email'),
            client_reference_id=session.get('user_id'),
            metadata={
                'plan_id': plan_id,
                'user_id': str(session.get('user_id'))
            }
        )
        return redirect(checkout_session.url)
    except Exception as e:
        print(f"Error creating checkout session: {str(e)}")
        flash('An error occurred while processing your request. Please try again.', 'error')
        return redirect(url_for('membership'))

@app.route('/payment-success')
def payment_success():
    session_id = request.args.get('session_id')
    if not session_id:
        flash('Invalid session', 'error')
        return redirect(url_for('membership'))
    
    try:
        # Verify the session with Stripe
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        
        # Update user's membership in the database
        user_id = checkout_session.metadata.get('user_id')
        plan_id = checkout_session.metadata.get('plan_id')
        
        if user_id and plan_id and plan_id in MEMBERSHIP_PLANS:
            plan = MEMBERSHIP_PLANS[plan_id]
            expires_at = datetime.now() + timedelta(days=30)  # 1 month from now
            
            mongo.db.users.update_one(
                {'_id': user_id},
                {'$set': {
                    'membership.plan': plan_id,
                    'membership.status': 'active',
                    'membership.expires_at': expires_at,
                    'membership.stripe_customer_id': checkout_session.customer,
                    'membership.stripe_subscription_id': checkout_session.subscription
                }},
                upsert=True
            )
            
            flash(f'Successfully subscribed to {plan["name"]}!', 'success')
        else:
            flash('Membership activated successfully!', 'success')
            
    except Exception as e:
        print(f"Error processing payment success: {str(e)}")
        flash('Your payment was successful, but there was an error updating your membership. Please contact support.', 'warning')
    
    return redirect(url_for('profile'))



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

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')