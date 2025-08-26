from flask import render_template

@app.route('/contact')
def contact():
    return render_template('contact.html')
