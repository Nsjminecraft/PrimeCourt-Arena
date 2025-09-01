PrimeCourt Arena
=================

A small Flask-based booking app for lessons and court reservations with coach profiles, Stripe payments, and basic admin features.

Quick overview
--------------
- Flask backend, Jinja2 templates, static assets in `static/` and templates in `templates/`.
- MongoDB used as the primary data store (via `pymongo` / Flask-PyMongo).
- Stripe Checkout integration for payments and subscriptions.
- Coach profiles (with photo upload), lesson and court booking flows, and simple admin endpoints.

Prerequisites
-------------
- Python 3.10+ (project contains files that used Python 3.13 in the venv but 3.10+ is recommended)
- MongoDB (local or remote URI)
- Stripe account (for API keys if you want to test payments)
- Git (optional)

Installation (local)
--------------------
1. Create and activate a virtual environment:

```bash
python -m venv env
source env/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy environment example and set secrets:

```bash
cp env_example.txt .env
# edit .env to set MONGO_URI, STRIPE_SECRET_KEY, STRIPE_PUBLIC_KEY, EMAIL_*, SECRET_KEY, etc.
```

See the Environment configuration section below for keys the app expects.

4. Run the app (development):

```bash
# If the repo includes an executable run script use that
./run
# Or run the Flask app directly (ensure FLASK_APP is set if required)
python serve.py
# Or
FLASK_APP=app.py flask run --host=0.0.0.0 --port=5000
```

Environment configuration
-------------------------
The app reads configuration from environment variables. Common variables used in the codebase include:

- `MONGO_URI` — MongoDB connection URI (e.g. mongodb://localhost:27017/primecourt)
- `STRIPE_SECRET_KEY` — Stripe secret key
- `STRIPE_PUBLIC_KEY` — Stripe publishable key (used in templates)
- `SECRET_KEY` — Flask session secret (or the app uses a hard-coded fallback)
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USER`, `EMAIL_PASSWORD`, `EMAIL_FROM` — SMTP settings used for booking emails
- `APP_TIMEZONE` — Optional timezone for schedule comparisons (default in code: America/New_York)
- `TEMP_ADMIN_TOKEN` — Token used by the temporary admin promotion route (optional)

The repository includes `env_example.txt` showing example values — copy it to `.env` or export variables directly in your shell when running.

Database
--------
- The app stores users, coach availability, bookings and schedule settings in MongoDB collections such as `users`, `bookings`, `court_bookings`, `coach_weekly_availability`, and `schedule_settings`.
- Ensure the configured `MONGO_URI` points to a writable DB. If you run locally, start a local MongoDB instance.

Uploads and static files
------------------------
- Uploaded coach photos are stored under `static/uploads/coach_photos/` in the project. Ensure the `static/uploads` folder is writable by the process when running locally or in production.
- When testing changes to `static/style.css` or templates, hard-refresh your browser to avoid cached assets.

Key features & implementation notes
----------------------------------
- Coach profile image upload and display are handled in the coach profile routes and templates.
- Mobile hamburger navigation added to `templates/base.html`; CSS is in `static/style.css` (ensure browser cache cleared if you don't see updates).
- Name length is limited to 32 characters client- and server-side to avoid mobile layout breakage.
- Stripe Checkout is used (see `create-lesson-booking` and `create-court-booking-session` endpoints). Set `STRIPE_SECRET_KEY` to test payments; you can use Stripe test keys.

Debugging & common fixes
------------------------
- If the navbar toggle doesn't show on mobile: clear browser cache, verify `static/style.css` changes loaded, and confirm you are testing at width <= 900px.
- If uploads fail: check filesystem permissions of `static/uploads/*` and ensure the web process user can write there.
- If Stripe sessions fail: confirm `STRIPE_SECRET_KEY` is set and reachable on the server.
- If the app fails to import `mongo`: confirm `MONGO_URI` is reachable. The app contains defensive fallback code but a working Mongo instance is expected for full functionality.

Testing
-------
- There are no automated tests included in the repository. Manual checks:
  - Sign up as a user and as a coach, test profile edits and photo upload.
  - Book a lesson and follow the Stripe Checkout flow (use Stripe test keys).

Deployment
----------
- Use a production WSGI server (e.g., `gunicorn`) and a process manager (systemd, Supervisor) in front of a real MongoDB instance.
- Ensure `STATIC` files are served by the web server or CDN in production, and `static/uploads` is a persistent storage location.

Contributing
------------
- Open an issue describing the problem or enhancement.
- For fixes, create a feature branch and submit a PR with tests where appropriate.

Questions / Help
----------------
If you want, I can:
- wrap the navbar username in a `<span>` so truncation always applies; or
- enforce a shorter "display_name" at signup and persist it to the DB.

To request either change or provide screenshots of a problem, share them and I will update code and tests as needed.

License
-------
This project is released under the MIT License. See the `LICENSE` file in the repository root for the full text and copyright details.
