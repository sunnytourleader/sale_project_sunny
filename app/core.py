import os
import io
import re
import socket
import logging
import threading
import traceback
import pandas as pd
import numpy as np
from PIL import Image
from functools import wraps
from flask import request, jsonify, flash, redirect, url_for, current_app
from flask_login import LoginManager, UserMixin, current_user, logout_user
from werkzeug.security import generate_password_hash
from sqlalchemy import create_engine, Text, Float, inspect, text

# ==========================================
# 🗄️ DATABASE CONFIGURATION
# ==========================================
DB_USER = 'root'
DB_PASSWORD = ''
DB_HOST = '127.0.0.1'
DB_NAME = 'sale_dash_db'

_db_engine = None
excel_lock = threading.Lock()

def get_db_connection():
    global _db_engine
    if _db_engine is None:
        _db_engine = create_engine(
            f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}?charset=utf8mb4",
            pool_pre_ping=True, pool_recycle=1800, pool_size=10, max_overflow=20
        )
    return _db_engine

# ==========================================
# 🔐 AUTHENTICATION & PERMISSIONS
# ==========================================
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = "danger"

class User(UserMixin):
    def __init__(self, id, username, role, department=None):
        self.id = str(id)
        self.username = username
        self.role = role
        self.department = department

@login_manager.user_loader
def load_user(user_id):
    try:
        engine = get_db_connection()
        with engine.connect() as conn:
            res = conn.execute(text("SELECT id, username, role, department FROM users WHERE id = :id"), {"id": user_id}).fetchone()
            if res: return User(id=res[0], username=res[1], role=res[2], department=res[3])
    except Exception as e: print(f"Auth Load Error: {e}")
    return None

def init_db_tables():
    try:
        engine = get_db_connection()
        with engine.connect() as conn:
            conn.execute(text("""CREATE TABLE IF NOT EXISTS users (id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(50) UNIQUE NOT NULL, password_hash VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL DEFAULT 'viewer', department VARCHAR(50) DEFAULT NULL)"""))
            conn.execute(text("""CREATE TABLE IF NOT EXISTS role_permissions (id INT AUTO_INCREMENT PRIMARY KEY, role_name VARCHAR(20) NOT NULL, menu_key VARCHAR(50) NOT NULL, can_access TINYINT(1) DEFAULT 0, UNIQUE KEY unique_role_menu (role_name, menu_key))"""))
            if not conn.execute(text("SELECT * FROM users WHERE username = 'Admin'")).fetchone():
                conn.execute(text("INSERT INTO users (username, password_hash, role, department) VALUES ('Admin', :hash, 'admin', 'sale')"), {"hash": generate_password_hash('admin123')})
            if conn.execute(text("SELECT COUNT(*) FROM role_permissions")).scalar() == 0:
                defaults = [('admin', 'sales_dashboard', 1), ('admin', 'revenue_dashboard', 1), ('admin', 'compare_tools', 1), ('admin', 'meeting_report', 1), ('admin', 'ocr_tool', 1), ('admin', 'user_management', 1), ('viewer', 'sales_dashboard', 1), ('viewer', 'revenue_dashboard', 1), ('viewer', 'compare_tools', 1), ('viewer', 'meeting_report', 1), ('viewer', 'ocr_tool', 1), ('viewer', 'user_management', 0)]
                for r, m, a in defaults: conn.execute(text("INSERT IGNORE INTO role_permissions (role_name, menu_key, can_access) VALUES (:r, :m, :a)"), {"r": r, "m": m, "a": a})
            conn.commit()
    except Exception as e: print(f"DB Init Error: {e}")

def inject_permissions():
    perms = {}
    if current_user.is_authenticated:
        try:
            engine = get_db_connection()
            with engine.connect() as conn:
                for row in conn.execute(text("SELECT menu_key, can_access FROM role_permissions WHERE role_name = :role"), {"role": current_user.role}).fetchall():
                    perms[row[0]] = bool(row[1])
        except Exception: pass
    return dict(user_permissions=perms)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            if request.is_json: return jsonify({'error': 'Unauthorized: Admin access required'}), 403
            flash('Admin privileges required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

def permission_required(menu_key):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated: return redirect(url_for('auth.login'))
            has_perm = False
            try:
                engine = get_db_connection()
                with engine.connect() as conn:
                    res = conn.execute(text("SELECT can_access FROM role_permissions WHERE role_name = :role AND menu_key = :mkey"), {"role": current_user.role, "mkey": menu_key}).scalar()
                    if res is not None: has_perm = bool(res)
            except Exception: pass
            if not has_perm:
                try:
                    engine = get_db_connection()
                    with engine.connect() as conn:
                        avail = conn.execute(text("SELECT menu_key FROM role_permissions WHERE role_name = :role AND can_access = 1"), {"role": current_user.role}).fetchall()
                        if avail:
                            route_map = {'sales_dashboard': 'dashboard.index', 'revenue_dashboard': 'dashboard.revenue_page', 'compare_tools': 'tools.compare_tools', 'meeting_report': 'tools.meeting_report', 'ocr_tool': 'tools.ocr_tool_page', 'user_management': 'admin.users_page'}
                            for am in avail:
                                if am[0] in route_map and am[0] != menu_key:
                                    if menu_key != 'sales_dashboard': flash(f"Access Denied: Redirected.", "warning")
                                    return redirect(url_for(route_map[am[0]]))
                except Exception: pass
                logout_user(); flash("Access Denied.", "danger"); return redirect(url_for('auth.login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ==========================================
# 🤖 OCR ENGINE & FORMATTERS
# ==========================================
ocr_reader = None
ocr_lock = threading.Lock()

def get_ocr_reader():
    global ocr_reader
    if ocr_reader is None:
        with ocr_lock:
            if ocr_reader is None:
                current_app.logger.info("Loading EasyOCR models...")
                import easyocr
                ocr_reader = easyocr.Reader(['en'], gpu=False) 
    return ocr_reader

def format_phone_number(phone_raw):
    if not phone_raw or phone_raw == "N/A": return phone_raw
    digits = re.sub(r'\D', '', phone_raw)
    if len(digits) == 9: return f"{digits[:3]} {digits[3:6]} {digits[6:]}"
    elif len(digits) == 10: return f"{digits[:3]} {digits[3:6]} {digits[6:]}"
    return phone_raw

def extract_data_from_image(image_bytes):
    try:
        reader = get_ocr_reader()
        img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        img_np = np.array(img)
        img_width = img.width 
        img_height = img.height
        is_landscape = img_width > img_height * 1.1
        
        ocr_results = reader.readtext(img_np)
        ocr_results.sort(key=lambda x: x[0][0][1]) 
        
        extracted_name = "N/A"
        found_phones = []
        ignore_words = [
            'search', 'back', 'edit', 'online', 'last seen', 'cellcard', 'smart', 'metfone', 
            'lte', 'wi-fi', 'wifi', 'unread', 'messages', 'mute', 'today', 'yesterday', 
            'calls', 'chats', 'settings', 'contacts', 'photo', 'video', 'voice', 'missed', 
            'am', 'pm', 'telegram', 'whatsapp', 'messenger', 'active', 'ago', 'cancel',
            'forward', 'reply', 'copy', 'pin', 'delete', 'message', 'type', 'intake', 'automated', 'response'
        ]
        
        for bbox, text, prob in ocr_results:
            text_clean = text.strip()
            if not text_clean: continue
            center_x = (bbox[0][0] + bbox[1][0]) / 2
            center_y = (bbox[0][1] + bbox[2][1]) / 2
            
            num_text = text_clean.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1').replace('S', '5').replace('s', '5').replace('B', '8')
            compact_num = re.sub(r'[\s\-\.\,]', '', num_text)
            phones = re.findall(r'(?:\+?855\d{8,9}|0\d{8,9})', compact_num)
            
            is_left_panel = is_landscape and center_x < (img_width * 0.33)
            is_top_right_battery = (not is_landscape) and center_x > (img_width * 0.85) and center_y < (img_height * 0.08)

            if phones and not is_left_panel and not is_top_right_battery:
                p_digits = phones[0].replace('+', '')
                if p_digits.startswith('855'): p_digits = '0' + p_digits[3:]
                skip_patterns = ['4949', '6868', '7618']
                if len(p_digits) in [9, 10] and not any(skip in p_digits for skip in skip_patterns):
                    found_phones.append((p_digits, center_x, center_y))

            text_for_name = text_clean
            if phones: text_for_name = re.sub(r'\d', '', text_for_name).strip()

            is_time = bool(re.match(r'^\d{1,2}:\d{2}', text_for_name) or re.search(r'\d{1,2}:\d{2}\s*(AM|PM|am|pm)', text_for_name))
            has_letters = bool(re.search(r'[a-zA-Z\u1780-\u17FF]', text_for_name))
            words_in_text = text_for_name.lower().split()
            is_ignored = any(word == text_for_name.lower() or word in words_in_text for word in ignore_words)
            
            if extracted_name == "N/A" and has_letters and not is_time and not is_ignored and not is_left_panel:
                if 2 < len(text_for_name) < 35:
                    letter_count = sum(c.isalpha() for c in text_for_name)
                    if letter_count > (len(text_for_name) * 0.4): extracted_name = text_for_name
                    
        extracted_phone = "N/A"
        if found_phones:
            right_pane_phones = [p for p in found_phones if p[1] > (img_width * 0.65)]
            if right_pane_phones:
                extracted_phone = right_pane_phones[0][0]
            elif is_landscape:
                found_phones.sort(key=lambda x: x[1], reverse=True)
                extracted_phone = found_phones[0][0]
            else:
                extracted_phone = found_phones[0][0]

        if extracted_name == "N/A" and extracted_phone != "N/A":
            extracted_name = extracted_phone
        elif extracted_phone == "N/A" and extracted_name != "N/A":
            extracted_phone = extracted_name

        return {"name": extracted_name, "phone_number": extracted_phone}, None
    except Exception as e:
        current_app.logger.error(f"Chat OCR Extraction Error: {str(e)}", exc_info=True)
        return None, str(e)

def get_fun_quotes():
    return ["Scanning pixels...", "Extracting chat bubbles...", "Crunching the numbers...", "Beep boop! Extracting text..."]

# ==========================================
# 📊 UTILITIES & DATA SYNC (SAFE SMART MERGE)
# ==========================================
def get_machine_info(ip):
    if ip in ['127.0.0.1', '::1']: return socket.gethostname() 
    try: return socket.gethostbyaddr(ip)[0]
    except: return request.headers.get('User-Agent', 'Unknown Device')[:60] 

def log_page_views():
    if request.endpoint and 'static' not in request.endpoint and request.endpoint != 'main.log_activity':
        logging.getLogger('activity_tracker').info(f"IP: {request.remote_addr:<15} | Machine: {get_machine_info(request.remote_addr):<30} | Action: PAGE_VIEW | Details: Visited: {request.path}")

def sync_to_mysql(df, target_table=None):
    if df.empty: return False
    try:
        engine = get_db_connection()
        target_table = target_table or 'client_contacts_leads'
        
        # 1. Clean the new data columns (Converts / and () into underscores to prevent SQL Schema errors like GIT/FIT!)
        df_new = df.copy()
        df_new.columns = [str(c).strip().replace(' ', '_').replace('.', '').replace('/', '_').replace('-', '_').replace('(', '').replace(')', '').replace('%', 'PCT') for c in df_new.columns]
        df_new = df_new.dropna(how='all')
        
        # 2. Smart Merge with Existing Data (In-Memory Upsert via Keep=Last)
        if inspect(engine).has_table(target_table):
            try:
                df_existing = pd.read_sql_table(target_table, con=engine)
                if not df_existing.empty:
                    combined_df = pd.concat([df_existing, df_new], ignore_index=True)
                    
                    subset_keys = []
                    if target_table == 'client_contacts_leads':
                        tc_col = next((c for c in combined_df.columns if 'dest' in c.lower()), None)
                        date_col = next((c for c in combined_df.columns if 'date' in c.lower()), None)
                        n_col = next((c for c in combined_df.columns if 'name' in c.lower()), None)
                        p_col = next((c for c in combined_df.columns if 'number' in c.lower() or 'phone' in c.lower()), None)
                        subset_keys = [c for c in [tc_col, date_col, n_col, p_col] if c]
                            
                    elif target_table == 'financial_sales_ledger':
                        so_col = next((c for c in combined_df.columns if 'so_no' in c.lower() or 's_o' in c.lower() or 'invoice' in c.lower()), None)
                        n_col = next((c for c in combined_df.columns if 'name' in c.lower()), None)
                        d_col = next((c for c in combined_df.columns if 'dest' in c.lower() or 'tour' in c.lower()), None)
                        if so_col and n_col and d_col: subset_keys = [so_col, n_col, d_col]
                    
                    elif target_table == 'group_tours_report':
                        tc_col = next((c for c in combined_df.columns if 'tour_code' in c.lower() or 'code' in c.lower()), None)
                        date_col = next((c for c in combined_df.columns if 'departure' in c.lower()), None)
                        subset_keys = [c for c in [tc_col, date_col] if c]
                    
                    if subset_keys and all(k in combined_df.columns for k in subset_keys):
                        # Protect NaN values so they aren't marked as duplicates
                        for k in subset_keys:
                            combined_df[k] = combined_df[k].fillna("TEMP_BLANK_" + combined_df.index.astype(str))
                            
                        # CRITICAL: KEEP LAST logic updates old statuses with new ones!
                        df_new = combined_df.drop_duplicates(subset=subset_keys, keep='last')
                        
                        for k in subset_keys:
                            df_new.loc[df_new[k].astype(str).str.startswith('TEMP_BLANK_'), k] = None
                    else:
                        df_new = combined_df.drop_duplicates(keep='last')
                        
            except Exception as read_e:
                current_app.logger.warning(f"Could not merge with existing table: {read_e}")

        # 3. Format Data Types for Safe Saving
        dtype_dict = {}
        finance_cols = ['amount', 'deposit', 'balance', 'total', 'tour_fare', 'fare', 'qty', 'pax', 'revenue', 'price']
        for col in df_new.columns:
            if any(f in col.lower() for f in finance_cols):
                df_new[col] = pd.to_numeric(df_new[col].astype(str).replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0.0)
                dtype_dict[col] = Float()
            else:
                # 📌 Automatically handles formatting for all text columns including the 'Department' column
                df_new[col] = df_new[col].astype(str).replace(['nan', 'NaN', 'None', 'NaT', ''], None)
                dtype_dict[col] = Text()
                
        # 4. Save back to SQL using REPLACE
        # Because we merged the old + new data into df_new in memory, 'replace' safely rebuilds the table
        # to include any newly added columns (like GIT_FIT or Department) without losing a single old record!
        if not df_new.empty:
            df_new.to_sql(name=target_table, con=engine, if_exists='replace', index=False, chunksize=500, dtype=dtype_dict)
            
        return True
    except Exception as e: 
        current_app.logger.error(f"MySQL Sync Error: {e}", exc_info=True)
        return False

def is_clean_val(val):
    # Added 'department' to ignored filler texts
    return str(val).lower().strip() not in ['', 'nan', 'none', 'not specified', 'destination', 'sale rep', 'status', 'customer name', 'zone', 'department']

def get_session_context(df):
    if df.empty: return {'destinations': [], 'reps': [], 'zones': [], 'statuses': [], 'departments': [], 'min_date': None, 'max_date': None}
    
    min_date, max_date = None, None
    date_col = next((c for c in df.columns if 'date' in c.lower()), None)
    
    if date_col:
        valid_dates = pd.to_datetime(df[date_col], errors='coerce').dropna()
        if not valid_dates.empty: min_date = valid_dates.min().strftime('%Y-%m-%d'); max_date = valid_dates.max().strftime('%Y-%m-%d')

    departments = []
    dept_col = next((c for c in df.columns if 'department' in c.lower()), None)
    if dept_col:
        departments = sorted(list(set([str(d).strip().title() for d in df[dept_col].dropna() if is_clean_val(d)])))

    return {
        'destinations': sorted(list(set([str(d).strip().title() for d in df.get(next((c for c in df.columns if 'dest' in c.lower()), 'Destination'), []) if is_clean_val(d)]))),
        'reps': sorted(list(set([str(r).strip() for r in df.get(next((c for c in df.columns if 'rep' in c.lower()), 'Sale Rep'), []) if is_clean_val(r)]))),
        'zones': sorted(list(set([str(z).strip() for z in df.get(next((c for c in df.columns if 'zone' in c.lower()), 'Zone'), []) if is_clean_val(z)]))),
        'statuses': sorted(list(set([str(s).strip() for s in df.get(next((c for c in df.columns if 'status' in c.lower()), 'Status'), []) if is_clean_val(s)]))),
        'departments': departments,
        'min_date': min_date, 'max_date': max_date
    }