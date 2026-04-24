from flask import Blueprint, jsonify, render_template, request
import pymysql
from datetime import datetime

bp_analytics = Blueprint('analytics', __name__, url_prefix='/analytics')

# Helper function to get DB connection 
def get_db():
    return pymysql.connect(
        host='127.0.0.1', 
        user='root', 
        password='', 
        db='sale_dash_db', 
        cursorclass=pymysql.cursors.DictCursor
    )

# ==========================================
# 1. EXECUTIVE CHARTS DASHBOARD ROUTES
# ==========================================
@bp_analytics.route('/dashboard')
def executive_dashboard():
    """Renders the HTML page for the executive dashboard charts."""
    return render_template('executive_dashboard.html')

@bp_analytics.route('/api/data')
def get_analytics_data():
    """Returns JSON data for the charts (Conversion, Activity vs Revenue)."""
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            # Conversion Analytics
            cursor.execute("SELECT COUNT(*) as total_leads FROM client_contacts_leads")
            total_leads = cursor.fetchone()['total_leads'] or 1 
            
            cursor.execute("SELECT COUNT(*) as closed_deals FROM financial_sales_ledger")
            closed_deals = cursor.fetchone()['closed_deals'] or 0
            
            conversion_data = {'closed_deals': closed_deals, 'total_leads': total_leads}

            # Activity vs Revenue 
            cursor.execute("""
                SELECT Sale_Rep, COUNT(No) as total_meetings
                FROM client_contacts_leads
                WHERE Sale_Rep IS NOT NULL AND Sale_Rep != ''
                GROUP BY Sale_Rep
            """)
            leads_data = {row['Sale_Rep']: row['total_meetings'] for row in cursor.fetchall()}

            cursor.execute("""
                SELECT Sale_Rep, SUM(Total) as total_revenue
                FROM financial_sales_ledger
                WHERE Sale_Rep IS NOT NULL AND Sale_Rep != ''
                GROUP BY Sale_Rep
            """)
            revenue_data = {row['Sale_Rep']: row['total_revenue'] for row in cursor.fetchall()}

            all_reps = set(leads_data.keys()).union(set(revenue_data.keys()))
            activity_revenue_data = []
            for rep in all_reps:
                activity_revenue_data.append({
                    'Sale_Person': rep,
                    'total_meetings': leads_data.get(rep, 0),
                    'total_revenue': float(revenue_data.get(rep, 0) or 0)
                })

            # Tour Package Performance
            cursor.execute("""
                SELECT Destination as package_name, SUM(Total) as total_revenue
                FROM financial_sales_ledger
                WHERE Destination IS NOT NULL AND Destination != ''
                GROUP BY Destination
                ORDER BY total_revenue DESC
                LIMIT 5
            """)
            tour_performance_data = cursor.fetchall()
            for row in tour_performance_data:
                row['total_revenue'] = float(row['total_revenue'] or 0)

        return jsonify({
            'status': 'success',
            'conversion': conversion_data,
            'activity_vs_revenue': activity_revenue_data,
            'tour_performance': tour_performance_data
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn and conn.open:
            conn.close()


# ==========================================
# 2. DAILY MANAGER TEXT REPORT ROUTES
# ==========================================
@bp_analytics.route('/daily-report')
def daily_manager_report():
    """Renders the HTML page for the Executive Summary Text Report."""
    return render_template('manager_report.html')

@bp_analytics.route('/api/daily-report-data')
def api_daily_report_data():
    """API endpoint to fetch the specific executive text report data based on date."""
    # Get date from the frontend, or default to today
    target_date_str = request.args.get('date')
    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d')
        except ValueError:
            target_date = datetime.now()
    else:
        target_date = datetime.now()

    # Create the 3 date formats your DB might be using
    today_ymd = target_date.strftime('%Y-%m-%d')
    today_d_b = target_date.strftime('%d-%b')
    today_d_b_y = target_date.strftime('%d-%b-%Y')
    
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            # Total Leads
            cursor.execute("""
                SELECT COUNT(*) as leads FROM client_contacts_leads 
                WHERE Contacted_Date LIKE %s OR Contacted_Date LIKE %s OR Contacted_Date LIKE %s
            """, (f"%{today_ymd}%", f"%{today_d_b}%", f"%{today_d_b_y}%"))
            total_leads = cursor.fetchone()['leads'] or 0

            # Total Bookings, Revenue, Pax
            cursor.execute("""
                SELECT COUNT(*) as bookings, SUM(Total) as rev, SUM(Pax) as pax
                FROM financial_sales_ledger 
                WHERE DateStr LIKE %s OR DateStr LIKE %s OR DateStr LIKE %s
            """, (f"%{today_ymd}%", f"%{today_d_b}%", f"%{today_d_b_y}%"))
            sales_stats = cursor.fetchone()
            total_bookings = sales_stats['bookings'] or 0
            total_revenue = float(sales_stats['rev'] or 0)
            total_pax = int(sales_stats['pax'] or 0)
            conversion = (total_bookings / total_leads * 100) if total_leads > 0 else 0.0

            # Salesperson Stats
            cursor.execute("""
                SELECT Sale_Rep, SUM(Total) as rev, SUM(Pax) as pax
                FROM financial_sales_ledger
                WHERE DateStr LIKE %s OR DateStr LIKE %s OR DateStr LIKE %s
                GROUP BY Sale_Rep ORDER BY rev DESC
            """, (f"%{today_ymd}%", f"%{today_d_b}%", f"%{today_d_b_y}%"))
            
            sales_reps = []
            for idx, row in enumerate(cursor.fetchall()):
                rev = float(row['rev'] or 0)
                pct = (rev / total_revenue * 100) if total_revenue > 0 else 0
                sales_reps.append({
                    'name': row['Sale_Rep'],
                    'rev': rev,
                    'pax': int(row['pax'] or 0),
                    'is_top': idx == 0,
                    'pct': round(pct)
                })

            # Destination Stats
            cursor.execute("""
                SELECT Destination, SUM(Pax) as pax, GROUP_CONCAT(DISTINCT Sale_Rep SEPARATOR ' & ') as reps
                FROM financial_sales_ledger
                WHERE DateStr LIKE %s OR DateStr LIKE %s OR DateStr LIKE %s
                GROUP BY Destination ORDER BY pax DESC
            """, (f"%{today_ymd}%", f"%{today_d_b}%", f"%{today_d_b_y}%"))
            
            destinations = []
            for row in cursor.fetchall():
                destinations.append({
                    'dest': row['Destination'],
                    'pax': int(row['pax'] or 0),
                    'reps': row['reps']
                })

        return jsonify({
            'status': 'success',
            'requested_date': today_ymd,
            'summary': {
                'total_revenue': total_revenue,
                'total_leads': total_leads,
                'total_bookings': total_bookings,
                'conversion': round(conversion, 1),
                'total_pax': total_pax
            },
            'sales_reps': sales_reps,
            'destinations': destinations
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn and conn.open:
            conn.close()