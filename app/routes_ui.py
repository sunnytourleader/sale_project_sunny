import os
import io
import json
import base64
import time
import tempfile
import traceback
import pandas as pd
from PIL import Image
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import inspect, Float, Text, text

from app.core import get_db_connection, admin_required, permission_required, extract_data_from_image, format_phone_number, get_fun_quotes, excel_lock, sync_to_mysql, get_session_context
from app.services import DataService

tools_bp = Blueprint('tools', __name__)

@tools_bp.route('/api/upload', methods=['POST'])
@login_required
@admin_required
def upload_file():
    file = request.files.get('file')
    if not file or file.filename == '': return jsonify({'error': 'No file'}), 400
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    file.save(filepath)
    current_app.current_file_path = filepath
    return jsonify({'sheets': ["COMBINE_ALL"] + list(pd.ExcelFile(filepath).sheet_names), 'status': "Excel uploaded."})

@tools_bp.route('/api/select_sheet', methods=['POST'])
@login_required
@admin_required
def select_sheet():
    sheet_name = request.get_json().get('sheet')
    filepath = getattr(current_app, 'current_file_path', None)
    try:
        target_table = 'financial_sales_ledger' if 'REVENUE' in (request.referrer or '').upper() else 'group_tours_report' if 'MEETING' in (request.referrer or '').upper() else 'client_contacts_leads'
        
        if target_table == 'group_tours_report':
            engine = get_db_connection()
            xls = pd.ExcelFile(filepath)
            if sheet_name == "COMBINE_ALL": sheet_name = xls.sheet_names[0]
            
            temp_df = pd.read_excel(filepath, sheet_name=sheet_name, header=None, nrows=20)
            h_idx = 0
            for i, row in temp_df.iterrows():
                vals = [str(val).strip().lower() for val in row.values]
                if any(k in v for v in vals for k in ['tour code', 'departure', 'zone']):
                    h_idx = i; break
            
            df_new = pd.read_excel(filepath, sheet_name=sheet_name, header=h_idx)
            df_new.columns = [str(c).strip().replace(' ', '_').replace('.', '').replace('/', '_').replace('-', '_').replace('(', '').replace(')', '').replace('%', 'PCT').replace('\n', '') for c in df_new.columns]
            
            for col in df_new.columns:
                if 'REMAIN' in col.upper(): df_new.rename(columns={col: 'Remain'}, inplace=True); break
                
            if 'Tour_Code' in df_new.columns: 
                df_new = df_new.dropna(subset=['Tour_Code'])
                df_new = df_new[df_new['Tour_Code'].astype(str).str.strip() != '']
                df_new = df_new[df_new['Tour_Code'].astype(str).str.lower() != 'nan']

            if inspect(engine).has_table('group_tours_report'):
                try:
                    df_existing = pd.read_sql_table('group_tours_report', con=engine)
                    combined_df = pd.concat([df_existing, df_new], ignore_index=True)
                    subset = ['Tour_Code']
                    if 'Departure' in combined_df.columns: subset.append('Departure')
                    
                    for k in subset:
                        combined_df[k] = combined_df[k].fillna("TEMP_BLANK_" + combined_df.index.astype(str))
                        
                    df_new = combined_df.drop_duplicates(subset=subset, keep='last')
                    
                    for k in subset:
                        df_new.loc[df_new[k].astype(str).str.startswith('TEMP_BLANK_'), k] = None
                except Exception as e:
                    current_app.logger.warning(f"Group Tours merge failed: {e}")
            
            numeric_cols = ['Booked', 'Sold', 'Holding', 'Remain', 'Price_10apx', 'Price_10up', 'DAY']
            dtype_dict = {}
            for col in df_new.columns:
                if any(n.upper() in col.upper() for n in numeric_cols): 
                    df_new[col] = pd.to_numeric(df_new[col].astype(str).replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0.0)
                    dtype_dict[col] = Float()
                else: 
                    df_new[col] = df_new[col].astype(str).replace(['nan', 'NaN', 'None', 'NaT', '<NA>'], None)
                    dtype_dict[col] = Text()
            
            if not df_new.empty:
                df_new.to_sql(name='group_tours_report', con=engine, if_exists='replace', index=False, dtype=dtype_dict)
            return jsonify({'status': "Group Tours Synced ✅", 'filters': {}})

        if sheet_name == "COMBINE_ALL":
            all_dfs = [DataService.process_dataframe(pd.read_excel(filepath, sheet_name=s, skiprows=DataService.find_header_row(pd.read_excel(filepath, sheet_name=s, nrows=50, header=None)))) for s in pd.ExcelFile(filepath).sheet_names if 'META DATA' not in s and 'Summary' not in s]
            current_app.master_df = pd.concat([df for df in all_dfs if not df.empty], ignore_index=True) if all_dfs else pd.DataFrame()
        else:
            current_app.master_df = DataService.process_dataframe(pd.read_excel(filepath, sheet_name=sheet_name, skiprows=DataService.find_header_row(pd.read_excel(filepath, sheet_name=sheet_name, nrows=50, header=None))))
        
        sync_to_mysql(current_app.master_df, target_table)
        return jsonify({'status': "Synced to MySQL ✅", 'filters': get_session_context(current_app.master_df)})
    except Exception as e: 
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@tools_bp.route('/meeting-report', methods=['GET', 'POST'])
@login_required
@permission_required('meeting_report')
def meeting_report():
    engine = get_db_connection()
    table_name = 'group_tours_report'
    
    if request.method == 'POST':
        if current_user.role != 'admin': 
            flash('Admin privileges required.', 'danger')
            return redirect(url_for('tools.meeting_report'))
            
        file = request.files.get('file')
        if file and file.filename:
            try:
                filepath = os.path.join(tempfile.gettempdir(), f"meeting_{int(time.time())}_{secure_filename(file.filename)}")
                file.save(filepath)
                xls = pd.ExcelFile(filepath)
                sheet_name = next((s for s in xls.sheet_names if 'master' in s.lower() or 'data' in s.lower()), xls.sheet_names[0])
                
                temp_df = pd.read_excel(filepath, sheet_name=sheet_name, header=None, nrows=20)
                h_idx = 0
                for i, row in temp_df.iterrows():
                    vals = [str(val).strip().lower() for val in row.values]
                    if any(k in v for v in vals for k in ['tour code', 'departure', 'zone']):
                        h_idx = i
                        break
                
                df_new = pd.read_excel(filepath, sheet_name=sheet_name, header=h_idx)
                df_new.columns = [str(c).strip().replace(' ', '_').replace('.', '').replace('/', '_').replace('-', '_').replace('(', '').replace(')', '').replace('%', 'PCT').replace('\n', '') for c in df_new.columns]
                
                for col in df_new.columns:
                    if 'REMAIN' in col.upper(): 
                        df_new.rename(columns={col: 'Remain'}, inplace=True)
                        break
                        
                if 'Tour_Code' in df_new.columns: 
                    df_new = df_new.dropna(subset=['Tour_Code'])
                    df_new = df_new[df_new['Tour_Code'].astype(str).str.strip() != '']
                    df_new = df_new[df_new['Tour_Code'].astype(str).str.lower() != 'nan']

                numeric_cols = ['Booked', 'Sold', 'Holding', 'Remain', 'Price_10apx', 'Price_10up', 'DAY']
                dtype_dict = {}
                for col in df_new.columns:
                    if any(n.upper() in col.upper() for n in numeric_cols): 
                        df_new[col] = pd.to_numeric(df_new[col].astype(str).replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0.0)
                        dtype_dict[col] = Float()
                    else:
                        df_new[col] = df_new[col].astype(str).replace(['nan', 'NaN', 'None', 'NaT', ''], None)
                        dtype_dict[col] = Text()

                if inspect(engine).has_table(table_name):
                    try:
                        df_existing = pd.read_sql_table(table_name, con=engine)
                        combined_df = pd.concat([df_existing, df_new], ignore_index=True)
                        subset = ['Tour_Code']
                        if 'Departure' in combined_df.columns: subset.append('Departure')
                        
                        for k in subset:
                            combined_df[k] = combined_df[k].fillna("TEMP_BLANK_" + combined_df.index.astype(str))
                            
                        df_new = combined_df.drop_duplicates(subset=subset, keep='last')
                        
                        for k in subset:
                            df_new.loc[df_new[k].astype(str).str.startswith('TEMP_BLANK_'), k] = None

                    except Exception as e:
                        current_app.logger.warning(f"Group Tours filter failed: {e}")
                
                if not df_new.empty:
                    df_new.to_sql(name=table_name, con=engine, if_exists='replace', index=False, dtype=dtype_dict)
                    flash(f"Successfully processed {len(df_new)} groups! Live status data updated.", "success")

                try: os.remove(filepath)
                except: pass
                
                return redirect(url_for('tools.meeting_report'))
            except Exception as e: 
                traceback.print_exc()
                flash(f"Process Error: {str(e)}", "danger")

    data = []; summary = {'total_groups': 0, 'total_sold': 0, 'total_remain': 0}
    if inspect(engine).has_table(table_name):
        df_db = pd.read_sql_table(table_name, con=engine)
        if not df_db.empty:
            records = df_db.fillna('').to_dict(orient='records')
            for r in records:
                for c in ['Departure', 'Arrival', 'Close_Date']:
                    if r.get(c): r[c] = str(r[c]).replace(' 00:00:00', '')
            data = records
            summary = {
                'total_groups': len(data), 
                'total_sold': int(sum(float(r.get('Sold', 0) or 0) for r in records)), 
                'total_remain': int(sum(float(r.get('Remain', 0) or 0) for r in records))
            }

    # Meta data for Group Tours (Kept intact)
    meta_data = {'destinations': [], 'zones': [], 'statuses': []}
    if inspect(engine).has_table('meta_data'):
        try:
            df_meta = pd.read_sql_table('meta_data', con=engine).fillna('')
            if 'destinations' in df_meta.columns: 
                meta_data['destinations'] = sorted(list(set(str(d).strip() for d in df_meta['destinations'] if str(d).strip() and str(d).strip().upper() != 'ALL DESTINATIONS')))
            if 'zone' in df_meta.columns: 
                meta_data['zones'] = sorted(list(set(str(z).strip() for z in df_meta['zone'] if str(z).strip() and str(z).strip().upper() != 'ALL ZONES')))
            if 'status' in df_meta.columns: 
                meta_data['statuses'] = sorted(list(set(str(s).strip() for s in df_meta['status'] if str(s).strip())))
        except Exception as e:
            current_app.logger.error(f"Meta data load error: {e}")

    # Fallbacks if meta_data is empty
    if not meta_data['destinations'] and data:
        meta_data['destinations'] = sorted(list(set(str(r.get('Destination', r.get('Destinations', ''))).strip().upper() for r in data if str(r.get('Destination', r.get('Destinations', ''))).strip() not in ['', 'NAN'])))
    if not meta_data['zones'] and data:
        meta_data['zones'] = sorted(list(set(str(r.get('Zone', '')).strip() for r in data if str(r.get('Zone', '')).strip() not in ['', 'NAN'])))
    if not meta_data['statuses'] and data:
        meta_data['statuses'] = sorted(list(set(str(r.get('Status', '')).strip() for r in data if str(r.get('Status', '')).strip() not in ['', 'NAN'])))

    return render_template('meeting_report.html', data=data, summary=summary, meta_data=meta_data)


@tools_bp.route('/api/save_tour', methods=['POST'])
@login_required
@admin_required
def save_tour():
    data = request.json
    engine = get_db_connection()
    table_name = 'group_tours_report'
    
    inspector = inspect(engine)
    if not inspector.has_table(table_name):
        return jsonify({"error": "Database table not found. Please sync a master file first."}), 400
        
    is_edit = str(data.get('is_edit')) == '1'
    orig_tc = str(data.get('orig_tour_code')).strip()
    orig_dep = str(data.get('orig_dep')).strip()
    
    provided_tc = data.get('tour_code')
    provided_dest = data.get('destination')
    primary_id = provided_tc if provided_tc else provided_dest
    
    cols_mapping = {
        'Tour_Code': primary_id,
        'Destination': provided_dest if provided_dest else provided_tc,
        'Destinations': provided_dest if provided_dest else provided_tc,
        'Month': data.get('month'),
        'Zone': data.get('zone'),
        'DAY': data.get('days'),
        'Departure': data.get('departure'),
        'Arrival': data.get('arrival'),
        'Close_Date': data.get('close_date'),
        'Booked': data.get('capacity'),
        'Sold': data.get('sold'),
        'Remain': data.get('remain'),
        'Price_10apx': data.get('price_10apx'),
        'Price_10up': data.get('price_10up'),
        'GIT_FIT': data.get('git_fit'),
        'Status': data.get('status'),
        'Ticket': data.get('ticket'),
        'Remark': data.get('remark')
    }
    
    try:
        with engine.begin() as conn:
            existing_cols = [row[0] for row in conn.execute(text(f"SHOW COLUMNS FROM {table_name}")).fetchall()]
            col_map_lower = {c.lower(): c for c in existing_cols}
            
            if 'destinations' in col_map_lower and 'destination' not in col_map_lower: col_map_lower['destination'] = col_map_lower['destinations']
            if 'ticket ' in col_map_lower and 'ticket' not in col_map_lower: col_map_lower['ticket'] = col_map_lower['ticket ']
            if 'remain ' in col_map_lower and 'remain' not in col_map_lower: col_map_lower['remain'] = col_map_lower['remain ']
                
            update_cols = {}
            for k, v in cols_mapping.items():
                k_low = k.lower()
                if k_low in col_map_lower:
                    actual_col_name = col_map_lower[k_low]
                    if v is None or str(v).strip() == '': update_cols[actual_col_name] = None
                    else:
                        if any(x in k_low for x in ['day', 'booked', 'sold', 'remain', 'price', 'capacity']):
                            try: update_cols[actual_col_name] = float(str(v).replace(',', '').replace('$', '').strip())
                            except ValueError: update_cols[actual_col_name] = 0.0
                        else:
                            update_cols[actual_col_name] = str(v).strip()
            
            if is_edit:
                identifier_col = None
                for col in ['Tour_Code', 'Destinations', 'Destination', 'Tour Code']:
                    if col.lower() in col_map_lower:
                        identifier_col = col_map_lower[col.lower()]
                        break
                
                if not identifier_col: return jsonify({"error": "No valid identifier column found in database."}), 400

                set_clause = ", ".join([f"`{k}` = :{k}" for k in update_cols.keys()])
                query = f"UPDATE {table_name} SET {set_clause} WHERE `{identifier_col}` = :orig_tc"
                update_cols['orig_tc'] = orig_tc
                
                if orig_dep and orig_dep not in ['None', '', '---', 'nan']:
                    query += " AND (`Departure` = :orig_dep OR `Departure` LIKE :orig_dep_like)"
                    update_cols['orig_dep'] = orig_dep
                    update_cols['orig_dep_like'] = f"{orig_dep}%"
                    
                result = conn.execute(text(query), update_cols)
                if result.rowcount == 0:
                    query_fallback = f"UPDATE {table_name} SET {set_clause} WHERE `{identifier_col}` = :orig_tc"
                    conn.execute(text(query_fallback), update_cols)
            else:
                col_names = ", ".join([f"`{k}`" for k in update_cols.keys()])
                val_names = ", ".join([f":{k}" for k in update_cols.keys()])
                query = f"INSERT INTO {table_name} ({col_names}) VALUES ({val_names})"
                conn.execute(text(query), update_cols)
                
        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Database Update Error: {str(e)}"}), 500


@tools_bp.route('/compare-tools', methods=['GET', 'POST'])
@login_required
@permission_required('compare_tools')
def compare_tools():
    if request.method == 'POST':
        sales_path, digital_path = request.form.get('sales_path'), request.form.get('digital_path')
        sales_sheet, digital_sheet = request.form.get('sales_sheet'), request.form.get('digital_sheet')
        
        def load_with_header(path, sheet):
            temp_df = pd.read_excel(path, sheet_name=sheet, header=None, nrows=20)
            h_idx = 0
            for i, row in temp_df.iterrows():
                vals = [str(v).strip().lower() for v in row.values]
                if any(k in v for v in vals for k in ['customer name', 'contact number', 'name', 'phone']):
                    h_idx = i; break
            df = pd.read_excel(path, sheet_name=sheet, header=h_idx)
            df.columns = [str(c).strip() for c in df.columns]
            return df

        try:
            sales_df, digital_df = load_with_header(sales_path, sales_sheet), load_with_header(digital_path, digital_sheet)
            def clean_phone(p):
                p_str = "".join(filter(str.isdigit, str(p)))
                return '0' + p_str[3:] if p_str.startswith('855') else p_str
            def clean_text(t): return "" if pd.isna(t) else str(t).strip().lower()
            
            s_name_col = next((c for c in sales_df.columns if any(k in c.lower() for k in ['customer name', 'name', 'customer'])), None)
            s_phone_col = next((c for c in sales_df.columns if any(k in c.lower() for k in ['contact number', 'phone', 'number'])), None)
            sales_names = {clean_text(v) for v in sales_df[s_name_col].dropna() if clean_text(v) not in ['', 'nan']} if s_name_col else set()
            sales_phones = {clean_phone(v) for v in sales_df[s_phone_col].dropna() if clean_phone(v) != ''} if s_phone_col else set()

            d_name_col = next((c for c in digital_df.columns if any(k in c.lower() for k in ['customer name', 'name', 'customer'])), 'Customer Name')
            d_phone_col = next((c for c in digital_df.columns if any(k in c.lower() for k in ['contact number', 'phone', 'number'])), 'Contact Number')
            
            missing = []
            for _, row in digital_df.iterrows():
                c_name, c_phone = clean_text(row.get(d_name_col)), clean_phone(row.get(d_phone_col))
                if not c_name and not c_phone: continue
                if not ((c_name and c_name in sales_names) or (c_phone and c_phone in sales_phones)):
                    def get_val(keys): return next((row.get(k) for k in keys if row.get(k) is not None and str(row.get(k)).lower() != 'nan'), None)
                    missing.append({
                        'Date': str(get_val(['Date', 'Drop Date', 'Contacted Date']) or '---').replace(' 00:00:00', ''),
                        'Name': str(row.get(d_name_col)).strip() if c_name else 'Unknown', 
                        'Phone': str(row.get(d_phone_col)).strip() if c_phone else 'No Phone',
                        'Channel': str(get_val(['Channel', 'Source']) or '---').strip(), 
                        'Product': str(get_val(['Product', 'Type']) or '---').strip(),
                        'Destination': str(get_val(['Destination']) or '---').strip(), 
                        'Sale Rep': str(get_val(['Sale Rep', 'Rep', 'Agent']) or '---').strip(),
                        'Remark': str(get_val(['Remark', 'Noted', 'Note']) or '---').strip()
                    })
            
            unique_reps = sorted(list(set([r['Sale Rep'] for r in missing if r['Sale Rep'] not in ['---', 'nan', '']])))
            unique_dests = sorted(list(set([r['Destination'] for r in missing if r['Destination'] not in ['---', 'nan', '']])))
            unique_channels = sorted(list(set([r['Channel'] for r in missing if r['Channel'] not in ['---', 'nan', '']])))
            unique_products = sorted(list(set([r['Product'] for r in missing if r['Product'] not in ['---', 'nan', '']])))

            return render_template('compare_tools.html', data=missing, match_count=len(missing), 
                                   unique_reps=unique_reps, unique_dests=unique_dests, 
                                   unique_channels=unique_channels, unique_products=unique_products)
        except Exception as e: flash(f"Error: {e}", "danger"); return redirect(url_for('tools.compare_tools'))
    return render_template('compare_tools.html')

@tools_bp.route('/compare-tools/analyze', methods=['POST'])
@login_required
@admin_required
def compare_tools_analyze():
    try:
        f_sales, f_digital = request.files.get('file_sales'), request.files.get('file_digital')
        s_path, d_path = os.path.join(tempfile.gettempdir(), f"s_{int(time.time())}.xlsx"), os.path.join(tempfile.gettempdir(), f"d_{int(time.time())}.xlsx")
        f_sales.save(s_path); f_digital.save(d_path)
        return jsonify({'status': 'success', 'sales_path': s_path, 'digital_path': d_path, 'sales_sheets': list(pd.ExcelFile(s_path).sheet_names), 'digital_sheets': list(pd.ExcelFile(d_path).sheet_names)})
    except Exception as e: return jsonify({'error': str(e)})

@tools_bp.route('/ocr-tool', methods=['GET', 'POST'])
@login_required
@permission_required('ocr_tool')
def ocr_tool_page():
    quotes = json.dumps(get_fun_quotes())
    
    if request.method == 'POST':
        try: extracted_items = json.loads(request.form.get('batch_results'))
        except: return render_template('ocr_tool.html', error="Failed to parse batch results.", quotes=quotes)

        # Standardize the output dictionaries for the front-end JS.
        # Fallback safely if front-end passed Depature Date or Departure Date
        results = [{
            "No": "", 
            "Customer Name": i.get("Customer Name", ""), 
            "Contact Number": format_phone_number(i.get("Contact Number", "")), 
            "Contacted Date": i.get("Contacted Date", ""), 
            "Source": i.get("Source", ""), 
            "Type": i.get("Type", ""), 
            "Destination": i.get("Destination", ""), 
            "Zone": i.get("Zone", ""), 
            "Pax": i.get("Pax", 1), 
            "Departure Date": i.get("Departure Date", i.get("Depature Date", "")), 
            "Status": i.get("Status", ""), 
            "Next Follow up Date": i.get("Next Follow up Date", ""), 
            "Why Failed": i.get("Why Failed", ""), 
            "Sale Rep": i.get("Sale Rep", ""), 
            "Success(%)": i.get("Success(%)", ""), 
            "Noted": i.get("Noted", ""), 
            "Department": i.get("Department", "")
        } for i in extracted_items]
        
        df_new = pd.DataFrame(results)
        excel_path = os.path.join(current_app.config['UPLOAD_FOLDER'], "OCR_CLIENT_CONTACTS.xlsx")
        
        with excel_lock:
            if os.path.exists(excel_path):
                try: pd.concat([pd.read_excel(excel_path), df_new], ignore_index=True).to_excel(excel_path, index=False)
                except: df_new.to_excel(excel_path, index=False)
            else: df_new.to_excel(excel_path, index=False)

        # Base64 export logic removed to save processing power since Javascript handles downloads now
        return render_template('ocr_tool.html', results=results, quotes=quotes)

    return render_template('ocr_tool.html', quotes=quotes)

@tools_bp.route('/api/extract_chat', methods=['POST'])
@login_required
@permission_required('ocr_tool')
def api_extract_chat():
    file = request.files.get('chat_image') or request.files.get('file')
    if not file: return jsonify({"error": "No file uploaded."}), 400
    
    image_bytes = file.read()
    
    data, error = extract_data_from_image(image_bytes)
    if error: return jsonify({"status": "Failed", "error": error})
    return jsonify({"status": "Success", "filename": file.filename, "name": data.get("name", "N/A"), "phone_number": data.get("phone_number", "N/A")})

@tools_bp.route('/api/roles/matrix', methods=['GET', 'POST'])
@login_required
@admin_required
def role_matrix_api():
    engine = get_db_connection()
    
    # Application Modules matching your system
    modules = [
        {"key": "sales_dashboard", "label": "Sales Dashboard", "icon": "layout-dashboard"},
        {"key": "revenue_dashboard", "label": "Revenue Dashboard", "icon": "dollar-sign"},
        {"key": "compare_tools", "label": "Compare Tools", "icon": "git-compare"},
        {"key": "meeting_report", "label": "Group Tours Report", "icon": "monitor"},
        {"key": "ocr_tool", "label": "Chat OCR Helper", "icon": "message-square"},
        {"key": "user_management", "label": "User Management (Admin Only)", "icon": "user", "admin_only": True}
    ]

    if request.method == 'POST':
        data = request.json
        try:
            with engine.begin() as conn:
                # Ensure the exact table structure exists
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS `role_permissions` (
                      `id` int NOT NULL AUTO_INCREMENT,
                      `role_name` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
                      `menu_key` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
                      `can_access` tinyint(1) DEFAULT '0',
                      PRIMARY KEY (`id`),
                      UNIQUE KEY `unique_role_menu` (`role_name`,`menu_key`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """))
                
                # Insert or Update permissions for each role
                for role_data in data:
                    role_name = role_data.get('role')
                    permissions = role_data.get('permissions', {}) 
                    
                    for menu_key, can_access in permissions.items():
                        val = 1 if can_access else 0
                        conn.execute(
                            text("""
                                INSERT INTO role_permissions (role_name, menu_key, can_access) 
                                VALUES (:r, :m, :c) 
                                ON DUPLICATE KEY UPDATE can_access = :c
                            """), 
                            {'r': role_name, 'm': menu_key, 'c': val}
                        )
            return jsonify({"status": "success", "message": "Role matrix updated."})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500

    # GET Request: Fetch Data for UI
    try:
        with engine.connect() as conn:
            # Check table existence safely
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS `role_permissions` (
                  `id` int NOT NULL AUTO_INCREMENT,
                  `role_name` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
                  `menu_key` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
                  `can_access` tinyint(1) DEFAULT '0',
                  PRIMARY KEY (`id`),
                  UNIQUE KEY `unique_role_menu` (`role_name`,`menu_key`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """))
            
            # Fetch dynamic roles from users table (fallback to admin/viewer if empty)
            try:
                roles_query = conn.execute(text("SELECT DISTINCT role FROM users WHERE role IS NOT NULL AND role != ''")).fetchall()
                roles = [row[0] for row in roles_query]
            except:
                roles = ['admin', 'viewer']
                
            if 'admin' not in roles: roles.insert(0, 'admin')
            if len(roles) == 1 and roles[0] == 'admin': roles.append('viewer')

            # Build matrix map
            perms_query = conn.execute(text("SELECT role_name, menu_key, can_access FROM role_permissions")).fetchall()
            matrix = {}
            for r in roles:
                matrix[r] = {m['key']: False for m in modules}
                if r == 'admin':
                    matrix[r]['user_management'] = True # Admin permanently has user access
            
            for r_name, m_key, c_access in perms_query:
                if r_name in matrix and m_key in matrix[r_name]:
                    matrix[r_name][m_key] = bool(c_access)

        return jsonify({
            "roles": sorted(roles),
            "modules": modules,
            "matrix": matrix
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500