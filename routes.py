from flask import render_template, redirect, request, url_for, session, flash, jsonify
from app import app

@app.route('/courts')
def courts():
    return render_template('courts.html')

@app.route('/lessons')
def lessons():
    return render_template('lessons.html')

@app.route('/membership')
def membership():
    return render_template('membership.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

