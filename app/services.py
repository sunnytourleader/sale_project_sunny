import pandas as pd
import re
import traceback

class DataService:
    @staticmethod
    def process_dataframe(df):
        """
        Comprehensive cleaning and normalization for Lead and Departure reports.
        Guarantees essential columns exist to prevent backend crashes.
        """
        try:
            if df is None or df.empty:
                return pd.DataFrame()

            # 1. Clean column names
            df.columns = [str(c).strip() for c in df.columns]
            
            # 2. Case-insensitive Name Concatenation
            cols_lower = [c.lower() for c in df.columns]
            if 'first name' in cols_lower and 'last name' in cols_lower:
                fname_idx = cols_lower.index('first name')
                lname_idx = cols_lower.index('last name')
                df['Customer Name'] = df.iloc[:, fname_idx].astype(str).str.replace('nan', '', case=False) + " " + \
                                     df.iloc[:, lname_idx].astype(str).str.replace('nan', '', case=False)
                df['Customer Name'] = df['Customer Name'].str.strip()

            # 3. Comprehensive Column Mapping (Expanded to cover Database fields)
            target_cols = {
                'Contacted Date': ['contacted date', 'contact date', 'date', 'departure', 'dep. date', 'dep date', 'departure date'],
                'Status': ['status', 'lead status', 'booking status'],
                'Sale Rep': ['sale rep', 'rep', 'seller', 'sales rep', 'staff', 'sale team'],
                'Customer Name': ['customer name', 'name', 'client'],
                'Destination': ['destination', 'detination', 'trip', 'place', 'to destination'], 
                'Zone': ['zone', 'region', 'area'], 
                'Pax': ['pax', 'qty', 'person', 'sold', 'pax count', 'member'],
                'Revenue': ['total', 'amount', 'total fare', 'revenue', 'price', 'deal value'],
                'Deposit': ['deposit', 'dep.'],
                'Balance': ['balance', 'remain', 'bal.'],
                'Seats': ['seats', 'capacity', 'book seat', 'total seat'],
                'Days': ['days', 'day'],
                'Adult10p': ['adult 10 pax', 'price adult', 'adult'],
                'Adult10up': ['adult 10up'],
                'Child': ['child'],
                'Infant': ['infant'],
                'Single': ['single'],
                'FlightInfo': ['flight detial', 'flight info', 'flight'],
                'GroupType': ['group type'],
                'OP': ['op', 'operator'],
                'TourLeader': ['tour leader', 'tl'],
                'TLSupport': ['tl support'],
                'DayLeft': ['day left', 'days left'],
                'Why Failed': ['why failed', 'reason', 'fail reason', 'lost reason'],
                'Noted': ['noted', 'remark', 'remarks', 'note'],
                'Next Follow up Date': ['next follow up date', 'follow up', 'fup date']
            }
            
            final_mapping = {}
            processed_actuals = set()

            for target, aliases in target_cols.items():
                for actual in df.columns:
                    if actual in processed_actuals: continue
                    lower_actual = actual.lower()
                    if lower_actual == target.lower() or any(alias == lower_actual for alias in aliases):
                        final_mapping[actual] = target
                        processed_actuals.add(actual)
                        break

            df = df.rename(columns=final_mapping)

            # --- CRITICAL FIX: Ensure essential columns exist ---
            if 'Contacted Date' not in df.columns: df['Contacted Date'] = pd.NaT
            if 'Status' not in df.columns: df['Status'] = 'Not Specified'
            if 'Seats' not in df.columns: df['Seats'] = 0
            if 'Pax' not in df.columns: df['Pax'] = 0
            if 'Revenue' not in df.columns: df['Revenue'] = 0
            if 'DayLeft' not in df.columns: df['DayLeft'] = 999 

            # 4. Data Cleaning
            # Formatting Date handles mixed formats (e.g. DD-MM-YYYY vs MM-DD-YYYY) without crashing
            df['Contacted Date'] = pd.to_datetime(df['Contacted Date'], errors='coerce', format='mixed')
            
            # Numeric Cleaning (Improved Regex to eliminate internal spaces like "1 000")
            numeric_cols = ['Revenue', 'Pax', 'Seats', 'Adult10p', 'Adult10up', 'Child', 'Infant', 'Single', 'DayLeft', 'Deposit', 'Balance']
            for col in numeric_cols:
                if col in df.columns:
                    s = df[col].astype(str).replace(r'[^\d.-]', '', regex=True)
                    df[col] = pd.to_numeric(s, errors='coerce').fillna(0)

            # Revenue correction logic
            df.loc[(df['Revenue'] > 0) & (df['Pax'] == 0), 'Pax'] = 1
            df['Occupancy'] = df.apply(lambda r: (r['Pax'] / r['Seats'] * 100) if r['Seats'] > 0 else 0, axis=1)
            
            # 5. Normalization
            def normalize_status(row):
                curr_status = str(row.get('Status', '')).upper()
                if any(kw in curr_status for kw in ["6-CONFIRMED", "BOOKED", "CLOSED", "FINISHED"]):
                    return "6-CONFIRMED / BOOKED"
                if row.get('Revenue', 0) > 0:
                    return "6-CONFIRMED / BOOKED"
                return row.get('Status', 'Not Specified')

            df['Status'] = df.apply(normalize_status, axis=1)
            
            # Text Normalization
            text_cols = ['Sale Rep', 'Destination', 'Zone', 'Status', 'GroupType', 'OP', 'TourLeader', 'TLSupport', 'FlightInfo', 'Customer Name']
            for col in text_cols:
                if col in df.columns:
                    df[col] = df[col].fillna('Not Specified').astype(str).str.strip()
                    df[col] = df[col].replace(['nan', 'None', 'NAN', 'N/A', 'destination', 'sale rep'], 'Not Specified')
                else:
                    df[col] = 'Not Specified'
            
            # Derived Time Columns
            df['Week'] = df['Contacted Date'].dt.isocalendar().week
            df['Month'] = df['Contacted Date'].dt.strftime('%Y-%m-%d').str[:7] # YYYY-MM
            df['DateStr'] = df['Contacted Date'].dt.strftime('%Y-%m-%d')
            
            return df
        except Exception as e:
            print(f"Internal Processing Error: {e}")
            traceback.print_exc()
            return pd.DataFrame()

    @staticmethod
    def find_header_row(temp_df):
        for i, row in temp_df.iterrows():
            row_str = [str(s).lower().strip() for s in row.values]
            matches = 0
            keywords = ['date', 'destination', 'detination', 'rep', 'staff', 'pax', 'sold', 'seats', 'status', 'departure', 'qty', 'amt']
            for kw in keywords:
                if any(kw in s for s in row_str): matches += 1
            if matches >= 3:
                return i
        return 0