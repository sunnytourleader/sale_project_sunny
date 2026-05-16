# pyrefly: ignore [missing-import]
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import mysql.connector
from datetime import datetime, timedelta
import time
import calendar

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
        
        # 1. Get Total Leads and Confirmed Leads for the specific date
        cursor.execute("""
            SELECT 
                COUNT(*) as total_leads,
                SUM(CASE WHEN Status = '6-CONFIRMED / BOOKED' THEN 1 ELSE 0 END) as total_confirmed
            FROM client_contacts_leads 
            WHERE Contacted_Date = %s
        """, (target_date,))
        leads_data = cursor.fetchone()
        total_leads = leads_data['total_leads'] if leads_data['total_leads'] else 0
        confirmed_bookings = int(leads_data['total_confirmed']) if leads_data['total_confirmed'] else 0


        # 1b. NEW: Get Unified Leads Breakdown (Zone -> Source)
        cursor.execute("""
            SELECT 
                Zone,
                Source,
                COUNT(*) as total_leads,
                SUM(CASE WHEN Status = '6-CONFIRMED / BOOKED' THEN 1 ELSE 0 END) as confirmed_leads
            FROM client_contacts_leads 
            WHERE Contacted_Date = %s 
            GROUP BY Zone, Source 
            ORDER BY Zone ASC, total_leads DESC
        """, (target_date,))
        leads_breakdown = cursor.fetchall()

        # 2. Get Overall Sales Totals (Pax, Revenue, Deposit from ledger)
        cursor.execute("""
            SELECT SUM(CASE WHEN Noted <> 'Paid Off' THEN total ELSE 0 END) as total_rev,
                   SUM(CASE WHEN Noted <> 'Paid Off' THEN Deposit ELSE 0 END) as total_dep,
                   SUM(CASE WHEN Noted <> 'Paid Off' THEN Pax ELSE 0 END) as total_pax,
                   SUM(CASE WHEN Noted = 'Paid Off' THEN Deposit ELSE 0 END) as total_paid_off
            FROM financial_sales_ledger
            WHERE Contacted_Date = %s
        """, (target_date,))
        sales_totals = cursor.fetchone()
        
        total_rev = sales_totals['total_rev'] or 0.0
        total_dep = sales_totals['total_dep'] or 0.0
        total_pax = sales_totals['total_pax'] or 0
        total_paid_off = sales_totals['total_paid_off'] or 0.0

        
        # Calculate Conversion
        conversion_rate = (confirmed_bookings / total_leads * 100) if total_leads > 0 else 0.0
        
        # 3. Get Unified Sales Hierarchy (Zone -> Destination -> Sale_Rep)
        cursor.execute("""
            SELECT Zone, Destination, Sale_Rep, SUM(Deposit) as dep, SUM(Pax) as pax
            FROM financial_sales_ledger
            WHERE Contacted_Date = %s and Noted <> "Paid Off"
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
        
        # 7. NEW: Fetch upcoming group tours (Same logic as meeting_report.html: Status "on sale", <= 30 days left, Remain > 0)
        # Target date is Close_Date if available, else Departure. Days left relative to TODAY.
        cursor.execute("""
            SELECT 
                g.Zone, 
                COALESCE(g.Tour_Code, g.Destinations) AS Tour_Code, 
                COALESCE(g.Destinations, g.Tour_Code) AS Destinations, 
                g.Departure, 
                g.Arrival, 
                g.Close_Date, 
                g.Booked, 
                g.Sold, 
                g.Remain, 
                g.Status,
                COALESCE(lc.lead_count, 0) AS lead_count,
                COALESCE(lc.lead_confirmed, 0) AS lead_confirmed
            FROM 
                group_tours_report g
            LEFT JOIN (
                SELECT 
                    Destination,
                    COUNT(*) AS lead_count,
                    SUM(CASE WHEN Status = '6-CONFIRMED / BOOKED' THEN 1 ELSE 0 END) AS lead_confirmed
                FROM 
                    client_contacts_leads
                WHERE 
                    Contacted_Date = %s 
                    AND TRIM(LOWER(Source)) = 'page'
                GROUP BY 
                    Destination
            ) AS lc ON (lc.Destination = g.Destinations OR lc.Destination = g.Tour_Code);
        """, (target_date,))
        all_groups = cursor.fetchall()
        
        upcoming_groups = []
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        for g in all_groups:
            st = str(g['Status']).upper().strip() if g['Status'] else ''
            is_risk_status = ('SALE' in st) or (st in ['', 'NAN', 'NONE', '---', 'NULL'])
            
            try:
                remain = int(float(g['Remain'])) if g['Remain'] else 0
            except ValueError:
                remain = 0
                
            if remain <= 0 or not is_risk_status:
                continue
                
            dep_raw = str(g['Departure']).strip() if g['Departure'] else ''
            close_raw = str(g['Close_Date']).strip() if g['Close_Date'] else ''
            
            target_date_str = close_raw if close_raw and close_raw.lower() not in ['---', 'nan', 'none', ''] else dep_raw
            
            days_left = None
            if target_date_str and target_date_str.lower() not in ['---', 'nan', 'none', '']:
                try:
                    clean_date = target_date_str.split(' ')[0].split('T')[0]
                    t_date = datetime.strptime(clean_date, '%Y-%m-%d')
                    days_left = (t_date - today).days
                except ValueError:
                    pass
            
            if days_left is not None and 0 <= days_left <= 30:
                g['days_left'] = days_left
                upcoming_groups.append(g)
                
        upcoming_groups.sort(key=lambda x: (str(x['Zone'] or ''), x['days_left']))
        
        conn.close()
        
        return {
            "total_dep": total_dep,
            "total_rev": total_rev,
            "total_paid_off": total_paid_off,
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

def generate_calendar(year, month):
    """Helper function to create an inline calendar markup"""
    markup = InlineKeyboardMarkup()
    
    # First row - Month and Year
    row = []
    row.append(InlineKeyboardButton(f"{calendar.month_name[month]} {year}", callback_data="calendar-ignore"))
    markup.row(*row)

    # Second row - Days of Week
    days = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
    row = [InlineKeyboardButton(day, callback_data="calendar-ignore") for day in days]
    markup.row(*row)

    # Calendar rows - Days of Month
    my_calendar = calendar.monthcalendar(year, month)
    for week in my_calendar:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="calendar-ignore"))
            else:
                row.append(InlineKeyboardButton(str(day), callback_data=f"calendar-day-{year}-{month:02d}-{day:02d}"))
        markup.row(*row)

    # Last row - Controls
    row = []
    row.append(InlineKeyboardButton("<", callback_data=f"calendar-prev-{year}-{month}"))
    row.append(InlineKeyboardButton("Today", callback_data="calendar-today"))
    row.append(InlineKeyboardButton(">", callback_data=f"calendar-next-{year}-{month}"))
    markup.row(*row)

    return markup


@bot.message_handler(commands=['start', 'help', 'report'])
def send_welcome(message):
    now = datetime.now()
    markup = generate_calendar(now.year, now.month)
    bot.reply_to(message, "Hello! Please select a date to generate the Executive Sales Report, or type it manually in `YYYY-MM-DD` format.", parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('calendar-'))
def handle_query(call):
    action = call.data.split('-')[1]

    if action == "ignore":
        bot.answer_callback_query(call.id)
        return

    if action == "today":
        target_date = datetime.now().strftime('%Y-%m-%d')
        formatted_date_str = datetime.strptime(target_date, '%Y-%m-%d').strftime('%d-%b-%Y')
        
        bot.answer_callback_query(call.id, "Generating report for today...")
        
        # Replace the calendar message immediately with a clear "in progress" status
        loading_text = f"✅ <b>Date Selected:</b> {formatted_date_str}\n⏳ <i>Processing your report, please hold on...</i>"
        bot.edit_message_text(loading_text, call.message.chat.id, call.message.message_id, reply_markup=None, parse_mode="HTML")
        
        generate_and_send_report(call.message.chat.id, target_date, formatted_date_str)
        return

    if action == "day":
        _, _, year, month, day = call.data.split('-')
        target_date = f"{year}-{month}-{day}"
        formatted_date_str = datetime.strptime(target_date, '%Y-%m-%d').strftime('%d-%b-%Y')
        
        bot.answer_callback_query(call.id, f"Generating report for {target_date}...")
        
        # Replace the calendar message immediately with a clear "in progress" status
        loading_text = f"✅ <b>Date Selected:</b> {formatted_date_str}\n⏳ <i>Processing your report, please hold on...</i>"
        bot.edit_message_text(loading_text, call.message.chat.id, call.message.message_id, reply_markup=None, parse_mode="HTML")
        
        generate_and_send_report(call.message.chat.id, target_date, formatted_date_str)
        return

    if action in ["prev", "next"]:
        bot.answer_callback_query(call.id)
        _, _, year, month = call.data.split('-')
        year, month = int(year), int(month)

        if action == "prev":
            month -= 1
            if month < 1:
                month = 12
                year -= 1
        else:
            month += 1
            if month > 12:
                month = 1
                year += 1

        markup = generate_calendar(year, month)
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_date_input(message):
    input_text = message.text.strip()
    
    try:
        date_obj = datetime.strptime(input_text, '%Y-%m-%d')
        formatted_date_str = date_obj.strftime('%d-%b-%Y')
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid format. Please send the date exactly as YYYY-MM-DD (e.g., 2026-04-23).")
        return

    loading_text = f"✅ <b>Date Received:</b> {formatted_date_str}\n⏳ <i>Processing your report, please hold on...</i>"
    bot.reply_to(message, loading_text, parse_mode="HTML")
    generate_and_send_report(message.chat.id, input_text, formatted_date_str)

def generate_and_send_report(chat_id, target_date, formatted_date_str):
    # Show typing status while fetching data
    bot.send_chat_action(chat_id, 'typing')
    data = fetch_report_data(target_date)
    
    if not data or (data['confirmed_bookings'] == 0 and data['total_leads'] == 0):
        bot.send_message(chat_id, f"No leads or sales recorded for {formatted_date_str}.")
        return
        
    # Build the report string
    report = f"Dear Hea,\n\n"
    report += f"Please find today's sales report on <b>{formatted_date_str}</b>, detailing our lead generation, confirmed bookings, zone performance, and revenue summary.\n\n"
    
    # Section 1: Summary
    report += "<b><u>1. Daily Executive Sales &amp; Revenue</u></b>\n"
    report += f"✤ Total Sale       : <b>${data['total_rev']}</b>\n"
    report += f"✤ Total Deposit : <b>${data['total_dep']}</b>\n"
    report += f"✤ Total Paid Off: <b>${data['total_paid_off']}</b>\n"
    report += f"✤ Total Leads    : <b>{data['total_leads']} leads</b>\n"
    report += f"✤ Total Confirmed Bookings: <b>{data['confirmed_bookings']} Confirmed</b>\n"
    report += f"✤ Lead Conversion: <b>{data['conversion_rate']:.1f}%</b>\n"
    report += f"✤ Total Volume: <b>{data['total_pax']} Passengers (Pax)</b>\n\n"
    
    # NEW Section: Unified Performance by Zone, Destination, and Sales Rep
    report += f"<b><u>2. Sale Details <i>({data['confirmed_bookings']} Confirmed | {data['total_pax']} Pax)</i></u></b> \n"
    
    if data['sales_hierarchy']:
        # Build hierarchy dictionary
        hierarchy = {}
        rep_totals = {}
        for row in data['sales_hierarchy']:
            z = row['Zone'] or "Other Zones"
            d = row['Destination'] or "Unknown"
            r = row['Sale_Rep'] or "Unknown"
            dep = float(row['dep'] or 0.0)
            pax = int(row['pax'] or 0)
            
            # Overall Rep Totals
            if r not in rep_totals:
                rep_totals[r] = {'dep': 0.0, 'pax': 0}
            rep_totals[r]['dep'] += dep
            rep_totals[r]['pax'] += pax
            
            # Zone -> Destination -> Rep Hierarchy
            if z not in hierarchy:
                hierarchy[z] = {'dep': 0.0, 'pax': 0, 'dests': {}}
            hierarchy[z]['dep'] += dep
            hierarchy[z]['pax'] += pax
            
            if d not in hierarchy[z]['dests']:
                hierarchy[z]['dests'][d] = {'dep': 0.0, 'pax': 0, 'reps': []}
            hierarchy[z]['dests'][d]['dep'] += dep
            hierarchy[z]['dests'][d]['pax'] += pax
            
            hierarchy[z]['dests'][d]['reps'].append({'rep': r, 'dep': dep, 'pax': pax})
            
        # Format Sales Person Summary
        report += f"<i>🔔 Summary by Sale Person:</i>\n"
        sorted_reps = sorted(rep_totals.items(), key=lambda x: x[1]['dep'], reverse=True)
        for r, r_data in sorted_reps:
            report += f"   👤 <b>{r}</b>: ${r_data['dep']} ({r_data['pax']} Pax)\n"
        report += "\n"

        # Format Zone & Destination Summary
        report += f"<i>🔔 Details by Zone & Destination:</i>\n"
        # Format hierarchy into report
        for z, z_data in hierarchy.items():
            report += f"📍 <b>{z}</b>: <b>${z_data['dep']}</b> ({z_data['pax']} Pax)\n"
            for d, d_data in z_data['dests'].items():
                report += f"      ✈️ <b>{d}: ${d_data['dep']} ({d_data['pax']} Pax)</b>\n"
                for r_data in d_data['reps']:
                    report += f"             ✤ <i>{r_data['rep']}: ${r_data['dep']} ({r_data['pax']} Pax)</i>\n"
            report += "\n" # Add a little breathing room between zones
    else:
        report += "No sales data available.\n\n"

    # Section 2 & 3 Combined: Leads Breakdown by Zone and Source (Summarized)
    report += "<b><u>3. Leads Breakdown</u></b>\n"
    
    if data['leads_breakdown']:
        # Sum total leads by source first
        source_totals = {}
        for row in data['leads_breakdown']:
            s = row['Source'] or "Unknown"
            if s not in source_totals:
                source_totals[s] = {'tot': 0, 'conf': 0}
            source_totals[s]['tot'] += row['total_leads']
            source_totals[s]['conf'] += int(row['confirmed_leads']) if row['confirmed_leads'] else 0
            
        report += f"<i>🔔 Total by Source:</i>\n"
        for s, s_data in source_totals.items():
            s_tot = s_data['tot']
            s_conf = s_data['conf']
            s_conv = (s_conf / s_tot * 100) if s_tot > 0 else 0.0
            report += f"   ✤ <b>{s}</b>: {s_tot} leads (Conf: {s_conf}) ➙ <b>{s_conv:.0f}%</b>\n"
        report += "\n"
        
        report += f"<i>🔔 Details by Zone:</i>\n"
        # Build hierarchy dictionary for leads
        leads_dict = {}
        for row in data['leads_breakdown']:
            z = row['Zone'] or "Other Zones"
            s = row['Source'] or "Unknown"
            tot = row['total_leads']
            conf = int(row['confirmed_leads']) if row['confirmed_leads'] else 0
            
            if z not in leads_dict:
                leads_dict[z] = {'tot': 0, 'conf': 0, 'sources': []}
            
            leads_dict[z]['tot'] += tot
            leads_dict[z]['conf'] += conf
            leads_dict[z]['sources'].append({'source': s, 'tot': tot, 'conf': conf})
            
        # Format leads hierarchy into report
        for z, z_data in leads_dict.items():
            report += f"📍 <b>{z}</b>: <i><b>{z_data['tot']} leads ({z_data['conf']} Confirmed)</b></i>\n"
            for s_info in z_data['sources']:
                report += f"       ✤ <i>{s_info['source']}: {s_info['tot']} leads (Conf: {s_info['conf']})</i>\n"
        report += "\n"
    else:
        report += "No lead data available.\n\n"
    
    # NEW Section: Upcoming Group Tours Breakdown (Summarized)
    total_upcoming_groups = len(data['upcoming_groups']) if data['upcoming_groups'] else 0
    
    if data['upcoming_groups']:
        report += f"<b><u>4. Urgent Groups ({total_upcoming_groups} Groups &lt; 30 Days)</u></b>\n"
        report += f"<i>🔔 All groups close 7days before departure.</i>\n\n"
        
        # Collect unique destinations for the lead summary
        urgent_dest_leads = {}
        
        for grp in data['upcoming_groups']:
            # Store unique destination lead counts and accumulate group counts
            g_dest = grp['Destinations'] or "Unknown"
            if g_dest not in urgent_dest_leads:
                urgent_dest_leads[g_dest] = {
                    'leads': int(grp['lead_count']) if grp['lead_count'] else 0,
                    'conf': int(grp['lead_confirmed']) if grp['lead_confirmed'] else 0,
                    'group_count': 1,
                    'groups': [grp]
                }
            else:
                urgent_dest_leads[g_dest]['group_count'] += 1
                urgent_dest_leads[g_dest]['groups'].append(grp)
        
        if urgent_dest_leads:
            for dest, counts in urgent_dest_leads.items():
                lead_count = counts['leads']
                lead_confirmed = counts['conf']
                group_count = counts['group_count']
                
                # Mobile friendly clean design (Name on one line, data on next)
                report += f"  ✤ <b>{dest} ({group_count} Groups)</b>\n"
                
                for idx, grp in enumerate(counts['groups'], 1):
                    g_dep_raw = grp['Departure']
                    g_arr_raw = grp['Arrival']
                    g_dep = "N/A"
                    g_arr = "N/A"
                    tour_days = ""
                    
                    if g_dep_raw:
                        try:
                            dep_obj = datetime.strptime(str(g_dep_raw).strip(), '%Y-%m-%d')
                            g_dep = dep_obj.strftime('%d-%b-%Y')
                        except ValueError:
                            g_dep = str(g_dep_raw)
                            dep_obj = None
                    else:
                        dep_obj = None
                        
                    if g_arr_raw:
                        try:
                            arr_obj = datetime.strptime(str(g_arr_raw).strip(), '%Y-%m-%d')
                            g_arr = arr_obj.strftime('%d-%b-%Y')
                        except ValueError:
                            g_arr = str(g_arr_raw)
                            arr_obj = None
                    else:
                        arr_obj = None
                        
                    if dep_obj and arr_obj:
                        duration = (arr_obj - dep_obj).days + 1
                        tour_days = f"-{duration}D"
                            
                    g_seat = int(grp['Booked']) if grp['Booked'] else 0
                    g_sold = int(grp['Sold']) if grp['Sold'] else 0
                    g_remain = int(grp['Remain']) if grp['Remain'] else 0
                    days_left = int(grp['days_left']) if grp['days_left'] is not None else 0
                    
                    avail_emoji = "🔴" if days_left < 14 else "🟡"
                    
                    report += f"      {idx}— ({g_dep} to {g_arr}){tour_days}\n"
                    report += f"      {avail_emoji} Sold: {g_sold} (Remain: {g_remain})\n"
                    report += f"      📝 We have only <i><b>{days_left}days</b></i> left to sale.\n"
                
                if lead_count > 0:
                    conv = (lead_confirmed / lead_count * 100)
                    status_icon = "✅" if lead_confirmed > 0 else "⭕️"
                    report += f"      {status_icon} <b>{lead_count} leads (Confirmed: {lead_confirmed}) ➙ {conv:.0f}%</b>\n\n"
                else:
                    report += f"      ❎ <b><i>No lead from digital</i></b> ➙ <b>PUSH</b>\n\n"
                    
        else:
            report += "   ✤ <i>No urgent groups found.</i>\n\n"

    else:
        report += " • No upcoming urgent groups.\n\n"

    report += f"Best regards,\n<b>STB Sales Team</b>"

    # Send typing action again to cover the 5-second artificial delay
    bot.send_chat_action(chat_id, 'typing')
    # time.sleep(5)
    bot.send_message(chat_id, report, parse_mode="HTML")

if __name__ == "__main__":
    print("Bot is running and listening for dates...")
    bot.infinity_polling()