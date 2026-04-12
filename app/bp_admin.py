from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from sqlalchemy import text

from .core import get_db_connection, admin_required, permission_required

admin_bp = Blueprint('admin', __name__)

@admin_bp.route('/users', methods=['GET'])
@login_required
@permission_required('user_management')
def users_page():
    engine = get_db_connection()
    with engine.connect() as conn:
        users = conn.execute(text("SELECT id, username, role, department FROM users")).fetchall()
        user_list = [{"id": u[0], "username": u[1], "role": u[2], "department": u[3]} for u in users]
        
        perms = conn.execute(text("SELECT role_name, menu_key, can_access FROM role_permissions")).fetchall()
        perm_matrix = {'admin': {}, 'viewer': {}}
        for p in perms:
            if p[0] not in perm_matrix: perm_matrix[p[0]] = {}
            perm_matrix[p[0]][p[1]] = bool(p[2])
    return render_template('users.html', users=user_list, permissions=perm_matrix)

@admin_bp.route('/api/users', methods=['POST'])
@login_required
@admin_required
def api_add_user():
    data = request.json
    engine = get_db_connection()
    with engine.begin() as conn:
        if conn.execute(text("SELECT id FROM users WHERE username = :u"), {"u": data['username']}).fetchone():
            return jsonify({"error": "Username already exists"}), 400
        
        conn.execute(text("INSERT INTO users (username, password_hash, role, department) VALUES (:u, :p, :r, :d)"), 
                     {"u": data['username'], "p": generate_password_hash(data['password']), "r": data['role'], "d": data.get('department')})
    return jsonify({"status": "success"})

@admin_bp.route('/api/users/<int:user_id>', methods=['PUT', 'DELETE'])
@login_required
@admin_required
def api_manage_user(user_id):
    engine = get_db_connection()
    if request.method == 'DELETE':
        if str(user_id) == str(current_user.id): return jsonify({"error": "Cannot delete yourself"}), 400
        with engine.begin() as conn: conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        return jsonify({"status": "deleted"})
        
    if request.method == 'PUT':
        data = request.json
        with engine.begin() as conn:
            if data.get('password'):
                conn.execute(text("UPDATE users SET username=:u, role=:r, department=:d, password_hash=:p WHERE id=:id"), 
                             {"u": data['username'], "r": data['role'], "d": data.get('department'), "p": generate_password_hash(data['password']), "id": user_id})
            else:
                conn.execute(text("UPDATE users SET username=:u, role=:r, department=:d WHERE id=:id"), 
                             {"u": data['username'], "r": data['role'], "d": data.get('department'), "id": user_id})
        return jsonify({"status": "updated"})

@admin_bp.route('/api/permissions', methods=['POST'])
@login_required
@admin_required
def api_update_permissions():
    data = request.json
    engine = get_db_connection()
    with engine.begin() as conn:
        for role, menus in data.items():
            for menu, has_access in menus.items():
                conn.execute(text("""
                    INSERT INTO role_permissions (role_name, menu_key, can_access) 
                    VALUES (:r, :m, :a) 
                    ON DUPLICATE KEY UPDATE can_access = :a
                """), {"r": role, "m": menu, "a": 1 if has_access else 0})
    return jsonify({"status": "Permissions updated"})