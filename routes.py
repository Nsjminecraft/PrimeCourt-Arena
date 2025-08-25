from flask import render_template, request, redirect, url_for
from app import app, mongo
from datetime import datetime, timedelta

def generate_month_slots():
    slots = []
    today = datetime.now()
    first_day = today.replace(day=1)
    # Get last day of month
    if first_day.month == 12:
        next_month = first_day.replace(year=first_day.year+1, month=1, day=1)
    else:
        next_month = first_day.replace(month=first_day.month+1, day=1)
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

lesson_days = generate_month_slots()

@app.route('/lessons', methods=['GET', 'POST'])
def lessons():
    message = None
    selected_day_idx = request.args.get('day')
    selected_day = None
    if selected_day_idx is not None:
        selected_day_idx = int(selected_day_idx)
        selected_day = lesson_days[selected_day_idx]

    # Load bookings from MongoDB
    bookings = list(mongo.db.bookings.find({}))
    for day_idx, day in enumerate(lesson_days):
        for slot_idx, slot in enumerate(day['slots']):
            for booking in bookings:
                if booking['date'] == day['date'] and booking['time'] == slot['time']:
                    if booking['lesson_type'] == 'private':
                        slot['private'] = {'name': booking['name'], 'email': booking['email']}
                    elif booking['lesson_type'] == 'group':
                        slot['group'].append({'name': booking['name'], 'email': booking['email']})

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
    first_day_str = lesson_days[0]['date']
    first_day_dt = datetime.strptime(first_day_str, '%Y-%m-%d')
    month_name = first_day_dt.strftime('%B')
    year = first_day_dt.year

    return render_template(
        'lessons.html',
        lesson_days=lesson_days,
        selected_day=selected_day,
        selected_day_idx=selected_day_idx,
        message=message,
        weekday=first_day_dt.weekday(),
        month_name=month_name,
        year=year,
        now=datetime.now()
    )

@app.route('/courts')
def courts():
    return render_template('courts.html')

@app.route('/membership')
def membership():
    return render_template('membership.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

