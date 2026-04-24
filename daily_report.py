import pandas as pd
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox

# We use pandas to handle the CSV and Excel data manipulation easily
# Make sure to run: pip install pandas openpyxl

def format_date_range(dep_date_str, arr_date_str):
    """Formats departure and arrival dates into a readable string."""
    try:
        # Convert strings to datetime objects
        dep_date = pd.to_datetime(dep_date_str)
        arr_date = pd.to_datetime(arr_date_str)
        
        # If dates are the same or arrival date is missing
        if pd.isna(arr_date) or dep_date == arr_date:
            return dep_date.strftime('%b %d, %Y')
            
        # If same month and same year: May 1–3, 2026
        if dep_date.month == arr_date.month and dep_date.year == arr_date.year:
            return f"{dep_date.strftime('%b %-d')}–{arr_date.strftime('%-d, %Y')}"
            
        # If different month, same year: May 28 – Jun 3, 2026
        elif dep_date.year == arr_date.year:
            return f"{dep_date.strftime('%b %-d')} – {arr_date.strftime('%b %-d, %Y')}"
            
        # Different year
        else:
            return f"{dep_date.strftime('%b %-d, %Y')} – {arr_date.strftime('%b %-d, %Y')}"
            
    except Exception:
        # Fallback if date parsing fails
        return f"{dep_date_str} to {arr_date_str}"

def clean_currency(value):
    """Cleans currency strings (e.g., '$ 1,000.00') into floats."""
    if isinstance(value, str):
        value = value.replace('$', '').replace(',', '').strip()
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0

def generate_daily_report(file_path, target_date_str, total_leads):
    """
    Reads the Master Data CSV or Excel, extracts the target date's data,
    groups by sales representative, and prints the formatted report.
    """
    # 1. Load the Data
    # Note: Using skiprows=4 based on the structure of your uploaded file
    try:
        # Check the file extension to read correctly
        if file_path.lower().endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file_path, skiprows=4)
        else:
            df = pd.read_csv(file_path, skiprows=4)
    except FileNotFoundError:
        print(f"Error: Could not find the file '{file_path}'")
        return

    # Clean column names (strip whitespace)
    df.columns = df.columns.str.strip()
    
    # 2. Format columns and filter data by the exact Date
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    target_date = pd.to_datetime(target_date_str)
    
    # Filter for the target date
    daily_df = df[df['Date'] == target_date].copy()
    
    if daily_df.empty:
        print(f"No records found for the date: {target_date.strftime('%d-%b-%Y')}")
        return
        
    # Clean up necessary numeric columns
    daily_df['QTY'] = pd.to_numeric(daily_df['QTY'], errors='coerce').fillna(0)
    daily_df['Total'] = daily_df['Total'].apply(clean_currency)
    
    # 3. Calculate Global Totals
    total_bookings = len(daily_df) # Number of rows/invoices
    total_pax = int(daily_df['QTY'].sum())
    total_revenue = daily_df['Total'].sum()
    
    # 4. Group by Sale Rep, Destination, and Dates
    # This combines duplicate destinations for the same person (e.g., 4 pax + 1 pax for Xin Jiang)
    grouped = daily_df.groupby(['Sale Rep', 'Destination', 'Dep. Date', 'Arr. Date']).agg({
        'QTY': 'sum',
        'Total': 'sum'
    }).reset_index()
    
    # Calculate totals per Sales Rep to sort them by highest revenue
    rep_totals = grouped.groupby('Sale Rep').agg({
        'QTY': 'sum',
        'Total': 'sum'
    }).sort_values(by='Total', ascending=False)
    
    # --- 5. Construct the Report String ---
    formatted_date = target_date.strftime('%d-%B-%Y')
    
    report = f"Dear Hea @SD31999 and Team,\n\n"
    report += f"Please find today's sales and revenue report below ({formatted_date}), detailing our lead generation, confirmed bookings, passenger (Pax) metrics, and total revenue separated by salesperson.\n\n"
    
    report += "DAILY PERFORMANCE SUMMARY\n\n"
    report += f"Total Leads: {total_leads}\n"
    report += f"Total Confirmed Bookings: {total_bookings}\n"
    report += f"Total Passengers (Pax): {total_pax}\n"
    report += f"Total Daily Revenue: ${total_revenue:,.2f}\n\n"
    
    report += "CONFIRMED BOOKINGS & REVENUE BY SALESPERSON\n\n"
    
    # Loop through each sales rep in descending order of revenue
    for idx, (rep_name, rep_data) in enumerate(rep_totals.iterrows(), 1):
        rep_pax = int(rep_data['QTY'])
        rep_rev = rep_data['Total']
        
        # Clean rep name if needed
        clean_rep_name = str(rep_name).strip().upper()
        
        report += f"{idx}. {clean_rep_name} (Total: {rep_pax} Pax | Revenue: ${rep_rev:,.2f})\n"
        
        # Get individual trips for this rep
        rep_trips = grouped[grouped['Sale Rep'] == rep_name]
        
        for _, trip in rep_trips.iterrows():
            dest = str(trip['Destination']).strip().title()
            pax = int(trip['QTY'])
            rev = trip['Total']
            
            # Format the date range
            date_range = format_date_range(trip['Dep. Date'], trip['Arr. Date'])
            
            report += f"🛫 —  {dest} ({date_range}) | {pax} Pax | Revenue: ${rev:,.2f}\n"
            
        report += "\n" # Add blank line between reps
        
    # Destinations list for footer summary
    unique_dests = grouped['Destination'].str.title().unique()
    dest_str = ", ".join(unique_dests[:-1]) + ", and " + unique_dests[-1] if len(unique_dests) > 1 else unique_dests[0]
        
    report += "LEAD CONVERSION ANALYSIS\n"
    report += f"Please note that out of the {total_leads} leads generated today, {total_bookings} bookings were successfully confirmed by our sales team across {dest_str}.\n\n"
    report += "Best regards,\nSunny"

    # 6. Print the Result
    print(report)

if __name__ == "__main__":
    # --- UI Configuration Setup ---
    # Hide the main tkinter root window
    root = tk.Tk()
    root.withdraw()
    
    # Open the file selection dialog supporting both CSV and Excel
    file_path = filedialog.askopenfilename(
        title="Select the Master Data File",
        filetypes=[
            ("Data Files", "*.csv *.xlsx *.xls"),
            ("Excel Files", "*.xlsx *.xls"),
            ("CSV Files", "*.csv"),
            ("All Files", "*.*")
        ]
    )
    
    if not file_path:
        messagebox.showwarning("Cancelled", "No file was selected. Exiting script.")
    else:
        # Ask for the date and leads dynamically using UI dialog pop-ups
        TARGET_DATE = simpledialog.askstring(
            "Input Date", 
            "Enter the date you want the report for (e.g., 2026-04-23):", 
            parent=root
        )
        
        if TARGET_DATE:
            TOTAL_LEADS_TODAY = simpledialog.askinteger(
                "Input Leads", 
                "Enter the total number of leads generated today:", 
                parent=root, 
                minvalue=0
            )
            
            if TOTAL_LEADS_TODAY is not None:
                print(f"Selected File: {file_path}\n")
                print("="*50 + "\n")
                
                # Generate the report
                generate_daily_report(file_path, TARGET_DATE.strip(), TOTAL_LEADS_TODAY)
            else:
                print("Report generation cancelled: No leads entered.")
        else:
            print("Report generation cancelled: No date entered.")