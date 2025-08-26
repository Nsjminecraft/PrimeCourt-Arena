from waitress import serve
from app import app  # or whatever your Flask app file is called

serve(app, host='0.0.0.0', port=25565)
