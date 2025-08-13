from flask import Flask, render_template, redirect, request, url_for, session, flash, jsonify

app = Flask(__name__)

import routes 

@app.route('/')
def index():
    return render_template('main.html')

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')