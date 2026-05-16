import telebot
import mysql.connector
from datetime import datetime
import time

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
        
        # 1b. NEW: Get Unified Leads Breakdown (Zone -> Source -> Destination)
        cursor.execute("""
            SELECT 
                Zone,
                Source,
                Destination, 
                COUNT(*) as total_leads,
                SUM(CASE WHEN Status = '6-CONFIRMED / BOOKED' THEN 1 ELSE 0 END) as confirmed_leads
            FROM client_contacts_leads 
            WHERE Contacted_Date = %s 
            GROUP BY Zone, Source, Destination 
            ORDER BY Zone ASC, Source ASC, total_leads DESC
        """, (target_date,))
        leads_breakdown = cursor.fetchall()

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
        
        # 3. Get Unified Sales Hierarchy (Zone -> Destination -> Sales Rep)
        cursor.execute("""
            SELECT Zone, Destination, Sale_Rep, SUM(Deposit) as dep, SUM(Pax) as pax
            FROM financial_sales_ledger
            WHERE Contacted_Date = %s
            GROUP BY Zone, Destination, Sale_Rep
            ORDER BY Zone ASC, Destination ASC, dep DESC
        """, (target_date,))
        sales_hierarchy = cursor.fetchall()

        # 6. Fetch Lead Analysis (Summarized Notes)
        cursor.execute("""
            SELECT Noted, Status 
            FROM client_contacts_leads 
            WHERE Contacted_Date = %s AND Noted IS NOT NULL AND Noted != ''
        """, (target_date,))
        lead_notes = cursor.fetchall()
        
        # 7. NEW: Fetch upcoming group tours (Status "on sale", Sales deadline < 30 days)
        # Sales deadline is Departure - 7 days (Visa process). Grouped by Zone
        # Added subqueries for total leads and confirmed leads filtering only Source = 'Page'
        cursor.execute("""
            SELECT g.Zone, g.Destinations, g.Departure, g.Arrival, g.Booked, g.Sold, g.Remain,
                   DATEDIFF(DATE_SUB(g.Departure, INTERVAL 7 DAY), %s) AS days_left,
                   (SELECT COUNT(*) FROM client_contacts_leads c 
                    WHERE c.Destination = g.Destinations 
                      AND c.Contacted_Date = %s
                      AND LOWER(c.Source) = 'page') AS lead_count,
                   (SELECT COUNT(*) FROM client_contacts_leads c 
                    WHERE c.Destination = g.Destinations 
                      AND c.Contacted_Date = %s
                      AND c.Status = '6-CONFIRMED / BOOKED'
                      AND LOWER(c.Source) = 'page') AS lead_confirmed
            FROM group_tours_report g
            WHERE LOWER(g.Status) = 'on sale'
              AND DATEDIFF(DATE_SUB(g.Departure, INTERVAL 7 DAY), %s) BETWEEN 0 AND 30
            ORDER BY g.Zone ASC, g.Departure ASC
        """, (target_date, target_date, target_date, target_date))
        upcoming_groups = cursor.fetchall()
        
        conn.close()
        
        return {
            "total_dep": total_dep,
            "total_rev": total_rev,
            "total_leads": total_leads,
            "confirmed_bookings": confirmed_bookings,
            "total_pax": int(total_pax),
            "conversion_rate": conversion_rate,
            "sales_hierarchy": sales_hierarchy,
            "lead_notes": lead_notes,
            "leads_breakdown": leads_breakdown,
            "upcoming_groups": upcoming_groups
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
    report += "<b><u>1. Daily Executive Sales &amp; Revenue Summary</u></b>\n"
    report += f"✤ Total Sale: <b>${data['total_rev']:,.2f}</b>\n"
    report += f"✤ Total Deposit: <b>${data['total_dep']:,.2f}</b>\n"
    report += f"✤ Total Leads: <b>{data['total_leads']} leads</b>\n"
    report += f"✤ Total Confirmed Bookings: <b>{data['confirmed_bookings']} Confirmed</b>\n"
    report += f"✤ Lead Conversion: <b>{data['conversion_rate']:.1f}%</b>\n"
    report += f"✤ Total Volume: <b>{data['total_pax']} Passengers (Pax)</b>\n\n"
    
    # NEW Section: Unified Performance by Zone, Destination, and Sales Rep
    report += f"<b><u>2. Sale Details <i>({data['confirmed_bookings']} Confirmed | {data['total_pax']} Pax)</i></u></b> \n"
    
    if data['sales_hierarchy']:
        # Build hierarchy dictionary
        hierarchy = {}
        for row in data['sales_hierarchy']:
            z = row['Zone'] or "Other Zones"
            d = row['Destination'] or "Unknown"
            r = row['Sale_Rep'] or "Unknown"
            dep = float(row['dep'] or 0.0)
            pax = int(row['pax'] or 0)
            
            if z not in hierarchy:
                hierarchy[z] = {'dep': 0.0, 'pax': 0, 'dests': {}}
            hierarchy[z]['dep'] += dep
            hierarchy[z]['pax'] += pax
            
            if d not in hierarchy[z]['dests']:
                hierarchy[z]['dests'][d] = {'dep': 0.0, 'pax': 0, 'reps': []}
            hierarchy[z]['dests'][d]['dep'] += dep
            hierarchy[z]['dests'][d]['pax'] += pax
            
            hierarchy[z]['dests'][d]['reps'].append({'rep': r, 'dep': dep, 'pax': pax})
            
        # Format hierarchy into report
        for z, z_data in hierarchy.items():
            report += f"📍 <b>{z}</b>: <b>${z_data['dep']:,.2f}</b> ({z_data['pax']} Pax)\n"
            for d, d_data in z_data['dests'].items():
                report += f"       ✈️ <b>{d}: ${d_data['dep']:,.2f} ({d_data['pax']} Pax)</b>\n"
                for r_data in d_data['reps']:
                    report += f"              ✤ <i>{r_data['rep']}: ${r_data['dep']:,.2f} ({r_data['pax']} Pax)</i>\n"
            report += "\n" # Add a little breathing room between zones
    else:
        report += "No sales data available.\n\n"

    # Section 2 & 3 Combined: Leads Breakdown by Zone, Source and Destination
    report += "<b><u>3. Leads Breakdown</u></b>\n"
    if data['leads_breakdown']:
        # Build hierarchy dictionary for leads
        leads_dict = {}
        for row in data['leads_breakdown']:
            z = row['Zone'] or "Other Zones"
            s = row['Source'] or "Unknown"
            d = row['Destination'] or "Unknown"
            tot = row['total_leads']
            conf = int(row['confirmed_leads']) if row['confirmed_leads'] else 0
            
            if z not in leads_dict:
                leads_dict[z] = {'tot': 0, 'conf': 0, 'sources': {}}
            
            leads_dict[z]['tot'] += tot
            leads_dict[z]['conf'] += conf
            
            if s not in leads_dict[z]['sources']:
                leads_dict[z]['sources'][s] = []
            
            leads_dict[z]['sources'][s].append({'dest': d, 'tot': tot, 'conf': conf})
            
        # Format leads hierarchy into report
        for z, z_data in leads_dict.items():
            report += f"🌍 <b>{z}</b>: <i><b>{z_data['tot']} leads (Confirmed: {z_data['conf']})</b></i>\n"
            for s, dests in z_data['sources'].items():
                report += f"       📍 <b>{s}</b>\n"
                for d_info in dests:
                    report += f"              ✤ <i>{d_info['dest']}: {d_info['tot']} leads (Confirmed: {d_info['conf']})</i>\n"
        report += "\n"
    else:
        report += "No lead data available.\n\n"
    
    # NEW Section: Upcoming Group Tours Breakdown (Beautified)
    total_upcoming_groups = len(data['upcoming_groups']) if data['upcoming_groups'] else 0
    report += f"<b><u>4. Urgent Groups ({total_upcoming_groups} Groups &lt; 30 Days)</u></b>\n"
    
    if data['upcoming_groups']:
        # Group by Zone first
        grouped_tours = {}
        for grp in data['upcoming_groups']:
            z_name = grp['Zone'] if grp['Zone'] else "Other Zones"
            if z_name not in grouped_tours:
                grouped_tours[z_name] = []
            grouped_tours[z_name].append(grp)
            
        # Display the groups organized by Zone
        for z_name, groups_in_zone in grouped_tours.items():
            report += f"📍<b>{z_name} ({len(groups_in_zone)} Groups)</b>\n"
            
            for grp in groups_in_zone:
                g_dest = grp['Destinations'] or "Unknown"
                g_dep = grp['Departure'] or "N/A"
                g_arr = grp['Arrival'] or "N/A"
                g_seat = int(grp['Booked']) if grp['Booked'] else 0
                g_sold = int(grp['Sold']) if grp['Sold'] else 0
                g_remain = int(grp['Remain']) if grp['Remain'] else 0
                days_left = int(grp['days_left']) if grp['days_left'] is not None else 0
                
                # Add visual cues for availability
                if days_left < 14:
                    avail_emoji = "🔴" # Full
                else:
                    avail_emoji = "🟡" # Selling fast
                
                urgency_alert = ""
                if days_left < 10:
                    urgency_alert = " ➔ 🚨<b>VERY URGENT</b>"
                
                # Check for leads today and confirmed leads on this specific group
                lead_count = int(grp['lead_count']) if grp['lead_count'] else 0
                lead_confirmed = int(grp['lead_confirmed']) if grp['lead_confirmed'] else 0
                if lead_count > 0:
                    lead_status = f"       ✤ Lead Today: {lead_count} leads (Confirmed: {lead_confirmed})" 
                    if lead_confirmed > 0 :
                       lead_status +=f" ✅"
                    else:
                        lead_status +=f" ⭕️"
                else:
                    lead_status = f"       ✤ <i>No Lead from Digital. ❎</i>"
                
                report += f"       ✈️ <b>{g_dest}</b> 📅 <i>{g_dep} ➔ {g_arr}</i> (⏳ {days_left} Days Left)\n"
                report += f"       {avail_emoji} Seat: {g_seat} | Sold: {g_sold} | Remain: {g_remain} {urgency_alert}\n"
                report += f"{lead_status}\n\n"                

    else:
        report += " • No lead from page today.\n\n"

    report += f"Best regards,\n<b>STB Sales Team</b>"

    bot.send_message(message.chat.id, report, parse_mode="HTML")

if __name__ == "__main__":
    print("Bot is running and listening for dates...")
    bot.infinity_polling()