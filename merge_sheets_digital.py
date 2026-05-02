import pandas as pd
import os

def merge_excel_sheets():
    # --- Configuration ---
    # Put the exact name of your Excel file here
    input_file = 'Daily Customer Contact Report in APR 01-30.xlsx'
    
    # The name of the new file that will be generated
    output_file = 'Merged_Daily_Report.xlsx'
    
    # Number of rows to skip at the top of each sheet (the company titles)
    rows_to_skip = 5 
    
    # Check if the file exists before running
    if not os.path.exists(input_file):
        print(f"Error: '{input_file}' not found in the current folder.")
        print("Please make sure the script and the Excel file are in the same folder.")
        return

    print(f"Loading {input_file}...")
    
    # Load the Excel file
    xls = pd.ExcelFile(input_file)
    
    # List to hold the dataframes (one for each sheet)
    df_list = []
    
    # Iterate through all sheet names in the Excel file
    for sheet_name in xls.sheet_names:
        # We skip the 'Meta Data' and 'Master' sheets to avoid duplicating data 
        # or mixing different column structures.
        if "Meta Data" in sheet_name or "Master" in sheet_name:
            print(f"Skipping summary sheet: {sheet_name}")
            continue
            
        print(f"Processing sheet: {sheet_name}")
        
        # Read the sheet into a pandas DataFrame, skipping the title rows
        df = pd.read_excel(xls, sheet_name=sheet_name, skiprows=rows_to_skip)
        
        # Drop rows where ALL elements are empty (removes blank rows at the bottom)
        df.dropna(how='all', inplace=True)
        
        # Optional: Add a column to track exactly which sheet this row came from
        df['Source Sheet'] = sheet_name 
        
        # Add the cleaned sheet data to our list
        df_list.append(df)
        
    # Combine all the sheets into a single DataFrame
    if df_list:
        print("\nMerging all daily sheets together...")
        merged_df = pd.concat(df_list, ignore_index=True)
        
        # Save the combined DataFrame back to a new Excel file
        print(f"Saving merged data to {output_file}...")
        merged_df.to_excel(output_file, index=False)
        print("✅ Merge complete! Your file is ready.")
    else:
        print("No valid sheets were found to merge.")

if __name__ == "__main__":
    merge_excel_sheets()