from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.security import check_password_hash
from sqlalchemy import text
from .core import get_db_connection, User

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('dashboard.index'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        engine = get_db_connection()
        with engine.connect() as conn:
            user_data = conn.execute(text("SELECT id, username, password_hash, role, department FROM users WHERE username = :uname"), {"uname": username}).fetchone()
            if user_data and check_password_hash(user_data[2], password):
                login_user(User(id=user_data[0], username=user_data[1], role=user_data[3], department=user_data[4]), remember=True)
                next_page = request.args.get('next')
                return redirect(next_page if next_page else url_for('dashboard.index'))
            else:
                flash('Invalid username or password', 'danger')
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))