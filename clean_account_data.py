import pandas as pd
import re
from openpyxl.styles import Font, PatternFill, Alignment

def format_start_date(raw_string):
    """
    Scans the raw text to extract the first valid day, month, and year, 
    ignoring digits related to durations (like 4D, 3N, 9DAYS).
    Returns date in MM/DD/YYYY format.
    """
    if not raw_string:
        return ""
    
    raw_upper = raw_string.upper()
    
    # 1. Extract the Year (Look for 2024, 2025, 2026)
    year_match = re.search(r'(202[456])\b', raw_upper)
    if year_match:
        year = year_match.group(1)
    else:
        # Match short years (24, 25, 26) but avoid matching a day like 25JAN
        year_match_short = re.search(r'(?<!\d)(2[456])(?!\d|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)', raw_upper)
        if year_match_short:
             year = "20" + year_match_short.group(1)
        else:
             year = "2025" # Default fallback
        
    # 2. Extract the First Month (Includes handling for typos like JUY, NON, DC, FE)
    month_match = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|JUY|AUG|SEP|OCT|NOV|DEC|DC|FE\b)', raw_upper)
    if not month_match:
        if "NON" in raw_upper: # Typo for NOV
            month = "11"
        else:
            return "" # No month found
    else:
        month_str = month_match.group(1)
        months = {
            "JAN":"01", "FEB":"02", "MAR":"03", "APR":"04", "MAY":"05", "JUN":"06", 
            "JUL":"07", "JUY":"07", "AUG":"08", "SEP":"09", "OCT":"10", "NOV":"11", 
            "DEC":"12", "DC":"12", "FE":"02"
        }
        month = months.get(month_str, "")
    
    # 3. Extract the First Day
    day = ""
    if month_match:
        # Look at text before the month
        prefix = raw_upper[:month_match.start()]
        
        # Strip out numbers attached to durations or seating to avoid false positives
        prefix_clean = re.sub(r'\d+D\b|\d+N\b|\d+DAYS\b|\d+SEAT\b', ' ', prefix)
        
        day_matches = re.findall(r'(\d{1,2})', prefix_clean)
        
        if day_matches:
             for d in day_matches:
                  # Extra safety: Ensure the number isn't followed closely by D, N, or SEAT in the original string
                  idx = raw_upper.find(d)
                  if idx != -1:
                      next_part = raw_upper[idx+len(d):idx+len(d)+5].strip()
                      if next_part.startswith('D') or next_part.startswith('N') or next_part.startswith('SEAT'):
                          continue
                  day = d.zfill(2)
                  break # Grab the first valid one
        
        # Fallback: if no day found BEFORE the month, check right AFTER the month
        if not day:
             suffix = raw_upper[month_match.end():]
             day_matches_after = re.findall(r'(\d{1,2})', suffix)
             for d in day_matches_after:
                  if d == year[2:]: continue # Ignore the year (e.g., 25)
                  day = d.zfill(2)
                  break
    
    if month and day and year:
        return f"{month}/{day}/{year}"
    else:
        return ""


# --- 1. Read Data from Text File ---
file_path = 'destinations.txt' # Ensure this file exists in the same directory

try:
    with open(file_path, 'r', encoding='utf-8') as file:
        # Read lines, strip whitespace, and ignore empty lines
        lines = [line.strip() for line in file if line.strip()]
except FileNotFoundError:
    print(f"Error: The file '{file_path}' was not found. Please create it and paste your raw data inside.")
    exit()


# --- 2. Parsing Logic ---
parsed_data = []

for line in lines:
    line_upper = line.upper()
    
    # Extract Status (Refund/Cancel vs Confirmed)
    if "REFUND" in line_upper or "CANCEL" in line_upper:
        status = "REFUND/CANCEL"
    else:
        status = "CONFIRMED"
        
    # Extract PAX (Finds the last matching passenger count)
    pax_match = re.findall(r'(\d+)\s*(?:PAX|P|PAXES)\b', line_upper)
    pax = int(pax_match[-1]) if pax_match else None
    
    # Extract Destination 
    dest_match = re.search(r'TOURS?\s+([A-Z\s]+?)(?=\s*\d|\(|\b\d[A-Z]\b)', line_upper)
    destination = dest_match.group(1).strip() if dest_match else "UNKNOWN"
    
    # Fix common spelling typos in destinations
    if "MINE" in destination or "MUINE" in destination:
        destination = "DALAT MUINE"
    elif "TRAL" in destination or "TRAIL" in destination:
        destination = "KOH TRAL"
        
    # Extract and format the start date using our robust function
    clean_date = format_start_date(line_upper)

    parsed_data.append({
        "Status": status,
        "Destination": destination,
        "Start_Date": clean_date,
        "Pax": pax,
        "Original_Text": line
    })

# Convert parsed data to a Pandas DataFrame
df = pd.DataFrame(parsed_data)


# --- 3. Excel Export and Styling Logic ---
output_path = 'Cleaned_Tour_Bookings.xlsx'

# Ensure pandas doesn't mess up the date format string
df['Start_Date'] = df['Start_Date'].astype(str)

# Initialize Excel writer using openpyxl engine
writer = pd.ExcelWriter(output_path, engine='openpyxl')
df.to_excel(writer, index=False, sheet_name='Parsed Records')

# Access the workbook and worksheet objects for styling
workbook = writer.book
worksheet = writer.sheets['Parsed Records']

# Apply styles to Header (Bold, White Text, Blue Background)
header_font = Font(bold=True, color="FFFFFF")
header_fill = PatternFill(start_color="305496", end_color="305496", fill_type="solid")

for cell in worksheet["1:1"]:
    cell.font = header_font
    cell.fill = header_fill
    cell.alignment = Alignment(horizontal="center", vertical="center")

# Auto-adjust column widths based on the length of the data inside
for col in worksheet.columns:
    max_length = 0
    column = col[0].column_letter
    for cell in col:
        try:
            if len(str(cell.value)) > max_length:
                max_length = len(str(cell.value))
        except:
            pass
    # Cap column width at 60 characters so super long text doesn't break layout
    adjusted_width = min(max_length + 2, 60)
    worksheet.column_dimensions[column].width = adjusted_width

# Freeze the top row and add drop-down filters
worksheet.freeze_panes = "A2"
worksheet.auto_filter.ref = worksheet.dimensions

# Save the final file
writer.close()
print(f"Data successfully cleaned and saved to {output_path}!")