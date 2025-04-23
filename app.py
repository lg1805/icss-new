from flask import Flask, request, render_template, send_file
import pandas as pd
import os
from datetime import datetime
import xlsxwriter
from textblob import TextBlob
from rapidfuzz import fuzz
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads/processed/'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

RPN_FILE = os.path.join(os.path.dirname(__file__), 'ProcessedData', 'RPN.xlsx')
if not os.path.exists(RPN_FILE):
    raise FileNotFoundError(f"RPN file not found at {RPN_FILE}")

# Load the RPN data file here
rpn_data = pd.read_excel(RPN_FILE)

# Now it's safe to access 'rpn_data'
known_components = rpn_data["Component"].dropna().unique().tolist()

# Use ThreadPoolExecutor for parallel execution
executor = ThreadPoolExecutor(max_workers=4)  # You can adjust based on available CPU cores

def extract_component(observation):
    try:
        # Step 1: Correct spelling using TextBlob
        corrected = TextBlob(observation).correct()
        corrected_str = str(corrected)

        # Step 2: Fuzzy match with known components
        best_match = None
        highest_score = 0
        threshold = 80  # Adjust this threshold as needed

        for component in known_components:
            score = fuzz.partial_ratio(component.lower(), corrected_str.lower())
            if score > highest_score and score >= threshold:
                highest_score = score
                best_match = component

        return best_match if best_match else "Unknown"
    except Exception as e:
        print(f"Error processing observation: {observation}, Error: {e}")
        return "Unknown"

def get_rpn_values(component):
    row = rpn_data[rpn_data["Component"] == component]
    if not row.empty:
        severity = int(row["Severity (S)"].values[0])
        occurrence = int(row["Occurrence (O)"].values[0])
        detection = int(row["Detection (D)"].values[0])
        return severity, occurrence, detection
    return 1, 1, 10  # Default values if no match

def determine_priority(rpn):
    if rpn >= 200:
        return "High"
    elif rpn >= 100:
        return "Moderate"
    else:
        return "Low"

def month_str_to_num(month_hint):
    month_map = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12"
    }
    return month_map.get(month_hint.lower(), None)

def format_creation_date(date_str, month_hint):
    target_month = month_str_to_num(month_hint)
    if not target_month:
        return None, None

    try:
        date_str = str(date_str).strip()
        dt = pd.to_datetime(date_str, errors='coerce', dayfirst=True)

        if pd.notna(dt):
            dd, mm, yyyy = dt.day, dt.month, dt.year
            if str(dd).zfill(2) == "01" and str(mm).zfill(2) == "01":
                dd, mm = mm, int(target_month)
            return f"{str(dd).zfill(2)}/{target_month}/{yyyy}", (datetime.now() - dt).days
    except Exception:
        return None, None

    return None, None

@app.route('/')
def index():
    return render_template('frontNEW.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'complaint_file' not in request.files:
        return "No complaint_file part", 400

    file = request.files['complaint_file']
    if file.filename == '':
        return "No selected file", 400

    month_hint = request.form.get('month_hint', 'default')

    if file:
        filepath = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(filepath)

        try:
            df = pd.read_excel(filepath)
        except Exception as e:
            return f"Error reading file: {e}", 400

        if 'Observation' not in df.columns or 'Creation Date' not in df.columns or 'Incident Id' not in df.columns:
            return "Required columns missing", 400

        formatted_dates = df['Creation Date'].apply(lambda x: format_creation_date(x, month_hint))
        df['Creation Date'] = formatted_dates.apply(lambda x: x[0])
        days_elapsed = formatted_dates.apply(lambda x: x[1])

        def get_color(elapsed):
            if elapsed == 1:
                return '#ADD8E6'
            elif elapsed == 2:
                return '#FFFF00'
            elif elapsed == 3:
                return '#FF1493'
            elif elapsed > 3:
                return '#FF0000'
            else:
                return None

        # Step 3: Run NLP and matching in parallel using ThreadPoolExecutor
        df["Component"] = list(executor.map(extract_component, df["Observation"]))

        # Step 4: Get RPN values and assign priority in parallel
        rpn_values = list(executor.map(get_rpn_values, df["Component"]))
        df[["Severity (S)", "Occurrence (O)", "Detection (D)"]] = pd.DataFrame(rpn_values, index=df.index)
        df["RPN"] = df["Severity (S)"] * df["Occurrence (O)"] * df["Detection (D)"]
        df["Priority"] = df["RPN"].apply(determine_priority)

        # Step 5: Segregate and sort the Data
        spn_df = df[df["Observation"].str.contains("spn", case=False, na=False)]
        non_spn_df = df[~df["Observation"].str.contains("spn", case=False, na=False)]

        priority_order = {"High": 1, "Moderate": 2, "Low": 3}
        spn_df = spn_df.sort_values(by="Priority", key=lambda x: x.map(priority_order))
        non_spn_df = non_spn_df.sort_values(by="Priority", key=lambda x: x.map(priority_order))

        # Generate Processed Excel File
        processed_filepath = os.path.join(UPLOAD_FOLDER, 'processed_' + file.filename)

        spn_df = spn_df.fillna("")
        non_spn_df = non_spn_df.fillna("")

        with pd.ExcelWriter(processed_filepath, engine='xlsxwriter', engine_kwargs={'options': {'nan_inf_to_errors': True}}) as writer:
            for sheet_name, sheet_df in zip(["SPN", "Non-SPN"], [spn_df, non_spn_df]):
                sheet_df.to_excel(writer, sheet_name=sheet_name, index=False)
                workbook = writer.book
                worksheet = writer.sheets[sheet_name]

                green_fmt = workbook.add_format({'bg_color': '#C6EFCE'})

                for idx, row_idx in enumerate(sheet_df.index):
                    elapsed = days_elapsed.loc[row_idx]
                    color = get_color(elapsed)
                    incident_status = str(sheet_df.loc[row_idx, "Incident Status"]).lower()

                    if "closed" in incident_status or "complete" in incident_status:
                        worksheet.write(idx + 1, sheet_df.columns.get_loc("Incident Status"), sheet_df.loc[row_idx, "Incident Status"], green_fmt)
                    elif color:
                        fmt = workbook.add_format({'bg_color': color})
                        worksheet.write(idx + 1, sheet_df.columns.get_loc("Incident Id"), sheet_df.loc[row_idx, "Incident Id"], fmt)

        return send_file(processed_filepath, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

