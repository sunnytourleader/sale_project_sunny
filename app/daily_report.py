import telebot
import mysql.connector
from datetime import datetime

# ==========================================
# 1. CONFIGURATION
# ==========================================
# Replace with your actual token
TELEGRAM_BOT_TOKEN = '8686320285:AAHK5f2vKhm_KE4zGKRozPuiLxLgrUgs8Ug'
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

DB_HOST = '127.0.0.1'
DB_USER = 'root'
DB_PASSWORD = ''
DB_NAME = 'sale_dash_db'

# ==========================================
# 2. DATABASE FETCH LOGIC
# ==========================================
def fetch_report_data(target_date):
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )
        cursor = conn.cursor(dictionary=True)
        
        # 1. Get Total Leads for the specific date
        cursor.execute("SELECT COUNT(*) as total_leads FROM client_contacts_leads WHERE Contacted_Date = %s", (target_date,))
        leads_data = cursor.fetchone()
        total_leads = leads_data['total_leads'] if leads_data['total_leads'] else 0
        
        # 2. Get Overall Sales Totals (Counting Confirmed Booking_No as 1 booking)
        cursor.execute("""
            SELECT COUNT(DISTINCT SO_No) as confirmed_bookings,
                   SUM(Total) as total_rev,
                   SUM(Deposit) as total_dep,
                   SUM(Pax) as total_pax
            FROM financial_sales_ledger
            WHERE Contacted_Date = %s
        """, (target_date,))
        sales_totals = cursor.fetchone()
        
        total_rev = sales_totals['total_rev'] or 0.0
        total_dep = sales_totals['total_dep'] or 0.0
        confirmed_bookings = sales_totals['confirmed_bookings'] or 0
        total_pax = sales_totals['total_pax'] or 0
        
        # Calculate Conversion
        conversion_rate = (confirmed_bookings / total_leads * 100) if total_leads > 0 else 0.0
        
        # 3. Get Salesperson Performance
        cursor.execute("""
            SELECT Sale_Rep, SUM(Total) as rev, SUM(Pax) as pax, SUM(Deposit) as Dep
            FROM financial_sales_ledger
            WHERE Contacted_Date = %s
            GROUP BY Sale_Rep
            ORDER BY rev DESC
        """, (target_date,))
        sales_reps = cursor.fetchall()
        
        # 4. Get Zone Breakdown (Deposit and Pax only)
        cursor.execute("""
            SELECT Zone, SUM(Deposit) as dep, SUM(Pax) as pax
            FROM financial_sales_ledger
            WHERE Contacted_Date = %s
            GROUP BY Zone
            ORDER BY dep DESC
        """, (target_date,))
        zones = cursor.fetchall()

        # 5. Get Destinations Breakdown Grouped by Zone
        # We fetch raw Sale_Rep and pax pairs to format with numbers in Python
        cursor.execute("""
            SELECT 
                Zone, 
                Destination, 
                SUM(sub_pax) as total_pax, 
                GROUP_CONCAT(CONCAT(Sale_Rep, ':', CAST(sub_pax AS CHAR)) SEPARATOR '|') as sold_by_raw
            FROM (
                SELECT Zone, Destination, Sale_Rep, SUM(Pax) as sub_pax
                FROM financial_sales_ledger
                WHERE Contacted_Date = %s
                GROUP BY Zone, Destination, Sale_Rep
            ) as sub
            GROUP BY Zone, Destination
            ORDER BY Zone ASC, total_pax DESC
        """, (target_date,))
        destinations = cursor.fetchall()

        # 6. Fetch Lead Analysis (Summarized Notes)
        cursor.execute("""
            SELECT Noted, Status 
            FROM client_contacts_leads 
            WHERE Contacted_Date = %s AND Noted IS NOT NULL AND Noted != ''
        """, (target_date,))
        lead_notes = cursor.fetchall()
        
        conn.close()
        
        return {
            "total_dep": total_dep,
            "total_rev": total_rev,
            "total_leads": total_leads,
            "confirmed_bookings": confirmed_bookings,
            "total_pax": int(total_pax),
            "conversion_rate": conversion_rate,
            "sales_reps": sales_reps,
            "zones": zones,
            "destinations": destinations,
            "lead_notes": lead_notes
        }
        
    except Exception as e:
        print(f"Database error: {e}")
        return None

# ==========================================
# 3. TELEGRAM BOT LOGIC
# ==========================================
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Hello! Send me a date in `YYYY-MM-DD` format (e.g., 2026-04-23) to generate the Executive Sales Report.", parse_mode="Markdown")

@bot.message_handler(func=lambda message: True)
def handle_date_input(message):
    input_text = message.text.strip()
    
    try:
        date_obj = datetime.strptime(input_text, '%Y-%m-%d')
        formatted_date_str = date_obj.strftime('%d-%B-%Y')
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid format. Please send the date exactly as YYYY-MM-DD (e.g., 2026-04-23).")
        return

    bot.reply_to(message, f"⏳ Fetching data for {formatted_date_str}...")
    
    data = fetch_report_data(input_text)
    
    if not data or (data['confirmed_bookings'] == 0 and data['total_leads'] == 0):
        bot.send_message(message.chat.id, f"No leads or sales recorded for {formatted_date_str}.")
        return
        
    # Build the report string
    report = f"Dear Hea,\n\n"
    report += f"Please find today's sales report on <b>{formatted_date_str}</b>, detailing our lead generation, confirmed bookings, zone performance, and revenue summary.\n\n"
    
    # Section 1: Summary
    report += "<b><u>Daily Executive Sales & Revenue Summary</u></b>\n"
    report += f"Total Sale: <b>${data['total_rev']:,.2f}</b>\n"
    report += f"Total Deposit: <b>${data['total_dep']:,.2f}</b>\n"
    report += f"Total Leads: <b>{data['total_leads']} leads </b>\n"
    report += f"Total Confirmed Bookings: <b>{data['confirmed_bookings']} (Confirmed Booking) </b>\n"
    report += f"Lead Conversion: <b>{data['conversion_rate']:.1f}%</b>\n"
    report += f"Total Volume: <b>{data['total_pax']} Passengers (Pax) </b>\n\n"
    
    # Section 2: Report by Zone (Deposit and Pax Only)
    report += "<b><u>Performance by Zone</u></b>\n"
    if data['zones']:
        for idx, zone in enumerate(data['zones'], start=1):
            z_dep = zone['dep'] or 0.0
            z_pax = int(zone['pax']) if zone['pax'] else 0
            z_name = zone['Zone'] if zone['Zone'] else "Unknown"
            report += f"{idx}—{z_name}: <b>${z_dep:,.2f}</b> ({z_pax} Pax)\n"
    else:
        report += "No zone data available.\n"
    report += "\n"

    # Section 3: Salesperson Performance
    report += "<b><u>Report by Salesperson</u></b>\n"
    for idx, rep in enumerate(data['sales_reps'], start=1):
        rep_dep = rep['Dep'] or 0.0
        rep_pax = int(rep['pax']) if rep['pax'] else 0
        report += f"{idx}—{rep['Sale_Rep']}: <b>${rep_dep:,.2f}</b> ({int(rep_pax)} Pax)\n"
        
    # Section 4: Destination Breakdown (Separated by Zone)
    report += f"\n<b><u>Destinations ({data['confirmed_bookings']} Bookings | {data['total_pax']} Total Pax)</u></b>"
    
    current_zone = None
    for dest in data['destinations']:
        zone_name = dest['Zone'] if dest['Zone'] else "Other Zones"
        if zone_name != current_zone:
            current_zone = zone_name
            report += f"\n📍<b>{current_zone}</b>\n"
        
        dest_pax = int(dest['total_pax']) if dest['total_pax'] else 0
        report += f" • <u><i>{dest['Destination']}</i></u> : <b>{dest_pax} Pax</b>\n"
        
        # Split and number the sales reps
        if dest['sold_by_raw']:
            reps_list = dest['sold_by_raw'].split('|')
            for i, rep_info in enumerate(reps_list, 1):
                name, pax = rep_info.split(':')
                report += f"      {i}. {name}: {int(float(pax))} Pax\n"
    
    # Conclusion
    report += f"\nBest regards,\n<b>STB Sales Team</b>"

    # Send the final report
    bot.send_message(message.chat.id, report, parse_mode="HTML")

if __name__ == "__main__":
    print("Bot is running and listening for dates...")
    bot.infinity_polling()