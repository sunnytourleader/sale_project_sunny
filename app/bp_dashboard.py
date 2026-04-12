import json
import logging
import pandas as pd
from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import login_required
from sqlalchemy import inspect
from .core import get_db_connection, permission_required, get_session_context, get_machine_info

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/')
@login_required
@permission_required('sales_dashboard')
def index():
    return render_template('index.html')

@dashboard_bp.route('/revenue')
@login_required
@permission_required('revenue_dashboard')
def revenue_page():
    return render_template('revenue.html')

@dashboard_bp.route('/api/log_activity', methods=['POST'])
def log_activity():
    data = request.json or {}
    ip = request.remote_addr
    ip_str = str(ip) if ip else "UNKNOWN"
    
    # Fixed: Using safe %s formatting prevents f-string crashes in Python logging
    logging.getLogger('activity_tracker').info(
        "IP: %s | Machine: %s | Action: %s | Details: %s", 
        ip_str.ljust(15), 
        get_machine_info(ip_str).ljust(30), 
        str(data.get('action', 'UNKNOWN')).ljust(10), 
        str(data.get('details', ''))
    )
    return jsonify({"status": "success"})

@dashboard_bp.route('/api/check_session')
@login_required
def check_session():
    try:
        engine = get_db_connection()
        table_name = request.args.get('table') or ('financial_sales_ledger' if 'REVENUE' in (request.referrer or '').upper() else 'client_contacts_leads')
        if inspect(engine).has_table(table_name):
            df = pd.read_sql_table(table_name, con=engine)
            if not df.empty: return jsonify({'active': True, 'status': f"Sync Active", 'filters': get_session_context(df)})
    except: pass
    return jsonify({'active': False})

@dashboard_bp.route('/api/data')
@login_required
def get_data():
    try:
        engine = get_db_connection()
        table_name = request.args.get('table') or ('financial_sales_ledger' if 'REVENUE' in (request.referrer or '').upper() else 'client_contacts_leads')
        
        df = pd.DataFrame()
        if inspect(engine).has_table(table_name): df = pd.read_sql_table(table_name, con=engine)
        if df.empty: return jsonify({'error': f'No data available for {table_name}.'})

        # Clean Column Names
        df.rename(columns={c: c.replace('_', ' ') for c in df.columns}, inplace=True)
        df.rename(columns={'Dep Date': 'Dep. Date', 'Arr Date': 'Arr. Date', 'S O No': 'S.O No', 'SO No': 'S.O No'}, inplace=True)

        for col in ['Revenue', 'Amount', 'Tour Fare']: 
            if col not in df.columns: df[col] = 0.0
        if 'QTY' not in df.columns: df['QTY'] = 1.0

        # Math Calculations
        df['Clean_Revenue'] = pd.to_numeric(df['Revenue'], errors='coerce').fillna(0)
        df.loc[df['Clean_Revenue'] == 0, 'Clean_Revenue'] = pd.to_numeric(df['Amount'], errors='coerce').fillna(0)
        df.loc[df['Clean_Revenue'] == 0, 'Clean_Revenue'] = pd.to_numeric(df['Tour Fare'], errors='coerce').fillna(0) * pd.to_numeric(df['QTY'], errors='coerce').fillna(1)
        df['Revenue'] = df['Clean_Revenue']
        
        df['Pax'] = pd.to_numeric(df['Pax'] if 'Pax' in df.columns else df['QTY'], errors='coerce').fillna(0)
        df['Is_Confirmed'] = df['Status'].astype(str).str.contains('6-CONFIRMED|BOOKED|CLOSED|PAID|SUCCESS', na=False, case=False).astype(int) if 'Status' in df.columns else 1
        df['Confirmed_Pax_Val'] = df['Pax'].where(df['Is_Confirmed'] == 1, 0)

        # Filters
        dest, zone, rep = request.args.get('destination'), request.args.get('zone'), request.args.get('rep')
        status_filter, start, end = request.args.get('status'), request.args.get('start_date'), request.args.get('end_date')
        
        if dest and dest != 'all': df = df[df['Destination'].astype(str).str.lower() == dest.lower()]
        if zone and zone != 'all': df = df[df['Zone'].astype(str).str.lower() == zone.lower()]
        if rep and rep != 'all': df = df[df['Sale Rep'].astype(str) == rep]
        if status_filter and status_filter != 'all': df = df[df['Status'].astype(str) == status_filter]
        
        # Time Formatting
        date_col = 'Contacted Date' if 'Contacted Date' in df.columns else ('Date' if 'Date' in df.columns else None)
        if date_col:
            if start: df = df[pd.to_datetime(df[date_col], errors='coerce') >= pd.to_datetime(start)]
            if end: df = df[pd.to_datetime(df[date_col], errors='coerce') <= pd.to_datetime(end)]
            valid_dates = pd.to_datetime(df[date_col], errors='coerce')
            df['DateStr'] = valid_dates.dt.strftime('%Y-%m-%d')
            df['Month'] = valid_dates.dt.strftime('%Y-%m')
            df['Week'] = valid_dates.dt.strftime('%G-W%V')
        else:
            df['DateStr'] = df['Month'] = df['Week'] = ''

        # Grouping Helper
        agg_map = {'Leads': ('Pax', 'count'), 'Confirmed': ('Is_Confirmed', 'sum'), 'Pax': ('Pax', 'sum'), 'Confirmed_Pax': ('Confirmed_Pax_Val', 'sum'), 'Revenue': ('Revenue', 'sum')}
        def safe_group(df_in, key): return json.loads(df_in.groupby(key).agg(**agg_map).reset_index().to_json(orient='records')) if key in df_in.columns and not df_in.empty else []

        daily, weekly, monthly = safe_group(df, 'DateStr'), safe_group(df, 'Week'), safe_group(df, 'Month')
        reps_perf, zones_perf, dest_perf, sources_perf = safe_group(df, 'Sale Rep'), safe_group(df, 'Zone'), safe_group(df, 'Destination'), safe_group(df, 'Source')
        reasons_perf = safe_group(df[~df['Why Failed'].astype(str).str.strip().str.lower().isin(['', '0', 'nan', 'none', 'not specified'])], 'Why Failed') if 'Why Failed' in df.columns else []

        total_conf_bookings = int(df['Is_Confirmed'].sum())
        
        # Extract Workload and Pipeline logic specifically for the charts
        funnel_engaged = 0
        untouched_leads = 0
        workload_perf = []
        active_pipeline = []
        
        if 'Status' in df.columns:
            engaged_mask = df['Status'].astype(str).str.upper().str.contains('CHECK INFO|PENDING|NEGOTIATING|FOLLOWING UP|APPOINTMENT', na=False)
            funnel_engaged = int(engaged_mask.sum()) + total_conf_bookings
            
            pending_df = df[engaged_mask].copy()
            if not pending_df.empty and 'Sale Rep' in pending_df.columns:
                wl_df = pending_df.groupby('Sale Rep').size().reset_index(name='Pending_Leads')
                workload_perf = json.loads(wl_df.to_json(orient='records'))
            
            if date_col: pending_df = pending_df.sort_values(by=date_col, ascending=False)
            active_pipeline = json.loads(pending_df.to_json(orient='records'))
            
        if 'Status' in df.columns and 'Noted' in df.columns:
            untouched_mask = df['Status'].astype(str).str.upper().str.contains('NEW LEAD|NO ANSWER', na=False) & df['Noted'].astype(str).str.lower().isin(['', 'nan', 'none', '0'])
            untouched_leads = int(untouched_mask.sum())

        # Grab Financial Ledger data
        financial_data_list = []
        financial_pax_total = 0
        has_fin_table = False
        fin_maps = {'rep': {}, 'zone': {}, 'dest': {}, 'source': {}, 'daily': {}, 'weekly': {}, 'monthly': {}}

        try:
            if inspect(engine).has_table('financial_sales_ledger'):
                has_fin_table = True
                fin_df = pd.read_sql_table('financial_sales_ledger', con=engine).fillna('')
                fin_df.rename(columns={c: c.replace('_', ' ') for c in fin_df.columns}, inplace=True)
                fin_df.rename(columns={'Dep Date': 'Dep. Date', 'Arr Date': 'Arr. Date', 'S O No': 'S.O No', 'SO No': 'S.O No'}, inplace=True)
                
                # Apply Date Filter from Request
                date_cols_fin = [c for c in fin_df.columns if 'date' in c.lower()]
                fin_date_col = None
                for c in date_cols_fin:
                    if any(x in c.lower() for x in ['contact', 'sale', 'issue', 'book']):
                        fin_date_col = c
                        break
                if not fin_date_col and date_cols_fin:
                    non_dep_arr = [c for c in date_cols_fin if 'dep' not in c.lower() and 'arr' not in c.lower()]
                    if non_dep_arr: fin_date_col = non_dep_arr[0]
                
                if fin_date_col:
                    fin_df['TempFinDate'] = pd.to_datetime(fin_df[fin_date_col], errors='coerce')
                    if start: fin_df = fin_df[fin_df['TempFinDate'] >= pd.to_datetime(start)]
                    if end:   fin_df = fin_df[fin_df['TempFinDate'] <= pd.to_datetime(end)]
                    
                    # Generate date groupings for financial data to map back
                    valid_fin = fin_df.dropna(subset=['TempFinDate']).copy()
                    valid_fin['DateStr'] = valid_fin['TempFinDate'].dt.strftime('%Y-%m-%d')
                    valid_fin['Month'] = valid_fin['TempFinDate'].dt.strftime('%Y-%m')
                    valid_fin['Week'] = valid_fin['TempFinDate'].dt.strftime('%G-W%V')
                    fin_df = valid_fin
                    fin_df = fin_df.drop(columns=['TempFinDate'], errors='ignore')

                # Apply Zone, Rep, Dest Filters to Financial Data
                rep_col_fin = next((c for c in fin_df.columns if 'rep' in c.lower() or 'agent' in c.lower()), None)
                dest_col_fin = next((c for c in fin_df.columns if 'dest' in c.lower()), None)
                zone_col_fin = next((c for c in fin_df.columns if 'zone' in c.lower()), None)
                source_col_fin = next((c for c in fin_df.columns if 'source' in c.lower() or 'channel' in c.lower()), None)

                if rep and rep != 'all' and rep_col_fin: fin_df = fin_df[fin_df[rep_col_fin].astype(str).str.lower() == rep.lower()]
                if dest and dest != 'all' and dest_col_fin: fin_df = fin_df[fin_df[dest_col_fin].astype(str).str.lower() == dest.lower()]
                if zone and zone != 'all' and zone_col_fin: fin_df = fin_df[fin_df[zone_col_fin].astype(str).str.lower() == zone.lower()]

                # Calculate Pax strictly from Financial Ledger
                pax_col_fin = next((c for c in fin_df.columns if 'pax' in c.lower() or 'adult' in c.lower() or 'qty' in c.lower() or 'quantity' in c.lower()), None)
                if pax_col_fin:
                    fin_df['Pax_Num'] = pd.to_numeric(fin_df[pax_col_fin].astype(str).replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)
                    financial_pax_total = int(fin_df['Pax_Num'].sum())

                    # Map overrides back to the original Dashboard tables
                    if rep_col_fin: fin_maps['rep'] = fin_df.groupby(rep_col_fin)['Pax_Num'].sum().to_dict()
                    if zone_col_fin: fin_maps['zone'] = fin_df.groupby(zone_col_fin)['Pax_Num'].sum().to_dict()
                    if dest_col_fin: fin_maps['dest'] = fin_df.groupby(dest_col_fin)['Pax_Num'].sum().to_dict()
                    if source_col_fin: fin_maps['source'] = fin_df.groupby(source_col_fin)['Pax_Num'].sum().to_dict()
                    if fin_date_col:
                        fin_maps['daily'] = fin_df.groupby('DateStr')['Pax_Num'].sum().to_dict()
                        fin_maps['weekly'] = fin_df.groupby('Week')['Pax_Num'].sum().to_dict()
                        fin_maps['monthly'] = fin_df.groupby('Month')['Pax_Num'].sum().to_dict()

                    fin_df = fin_df.drop(columns=['Pax_Num'], errors='ignore')

                for row in fin_df.to_dict(orient='records'):
                    financial_data_list.append({k: ('' if str(v).strip().lower() in ['none', 'nan', '<na>'] else v) for k, v in row.items()})
        except Exception as e: 
            import traceback
            traceback.print_exc()

        def override_pax(data_list, key_name, map_dict):
            for item in data_list:
                k = str(item.get(key_name, ''))
                if k in map_dict: item['Confirmed_Pax'] = int(map_dict[k])
                else:
                    matched = False
                    for m_key, m_val in map_dict.items():
                        if str(m_key).lower() == k.lower():
                            item['Confirmed_Pax'] = int(m_val)
                            matched = True; break
                    if not matched: item['Confirmed_Pax'] = 0

        if has_fin_table:
            override_pax(reps_perf, 'Sale Rep', fin_maps['rep'])
            override_pax(zones_perf, 'Zone', fin_maps['zone'])
            override_pax(dest_perf, 'Destination', fin_maps['dest'])
            override_pax(sources_perf, 'Source', fin_maps['source'])
            override_pax(daily, 'DateStr', fin_maps['daily'])
            override_pax(weekly, 'Week', fin_maps['weekly'])
            override_pax(monthly, 'Month', fin_maps['monthly'])

        # Final Cleanup
        clean_list = [{k: ('' if str(v).strip().lower() in ['none', 'nan', '<na>'] else v) for k, v in row.items()} for row in df.fillna('').to_dict(orient='records')]

        return jsonify({
            'daily': daily, 'weekly': weekly, 'monthly': monthly, 'reps': reps_perf, 'zones': zones_perf, 'destinations': dest_perf,
            'sources': sources_perf, 'reasons': reasons_perf, 'raw_data': clean_list, 
            'financial_data': financial_data_list, 'active_pipeline': active_pipeline, 'workload': workload_perf,
            'metrics': {
                'leads': len(df), 
                'confirmed': total_conf_bookings, 
                'confirmed_pax': financial_pax_total if has_fin_table else int(df['Confirmed_Pax_Val'].sum()), 
                'revenue': float(df['Clean_Revenue'].sum()), 
                'conversion': round((total_conf_bookings / len(df) * 100), 1) if not df.empty else 0, 
                'repeat_clients': int((df['Type'].astype(str).str.strip().str.upper() == 'REPEAT').sum()) if 'Type' in df.columns else 0, 
                'total_sold': int(df['Pax'].sum()), 
                'untouched_leads': untouched_leads, 
                'funnel_engaged': funnel_engaged
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500