from flask import Flask, request, render_template, send_file
import pandas as pd
import os
from datetime import datetime
import xlsxwriter
from textblob import TextBlob
from rapidfuzz import fuzz

app = Flask(__name__, template_folder="icss-backend/templates", static_folder="static")

# Define the upload folder and ensure it exists
UPLOAD_FOLDER = os.path.expanduser('~') + '\\uploads'  # This will place the folder in your user directory
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Path for the RPN file
RPN_FILE = r"D:\KOEL\ICSS\Deployment\icss-backend\ProcessedData\RPN.xlsx"
rpn_data = pd.read_excel(RPN_FILE)
known_components = rpn_data["Component"].dropna().unique().tolist()

threshold = 80  # Fuzzy matching threshold

# Function to extract components based on fuzzy matching
def extract_component(observation):
    try:
        corrected = str(TextBlob(observation).correct())  # Fixes spelling
        # Your actual keyword/component matching logic goes here using `corrected`
        return corrected  # Or return matched component if you have logic
    except Exception as e:
        print(f"Error processing observation: {observation}, Error: {e}")
        return observation  # Fallback to original

        best_match = None
        highest_score = 0
        for component in known_components:
            score = fuzz.partial_ratio(component.lower(), str(corrected).lower())
            if score > highest_score and score >= threshold:
                highest_score = score
                best_match = component
                
        return best_match or "Unknown"
    return "Unknown"  # If observation is not a string, return "Unknown"

# Function to get RPN values
def get_rpn_values(component):
    row = rpn_data[rpn_data["Component"] == component]
    if not row.empty:
        severity = int(row["Severity (S)"].values[0])
        occurrence = int(row["Occurrence (O)"].values[0])
        detection = int(row["Detection (D)"].values[0])
        return severity, occurrence, detection
    return 1, 1, 10

# Function to determine priority based on RPN
def determine_priority(rpn):
    if rpn >= 200:
        return "High"
    elif rpn >= 100:
        return "Moderate"
    else:
        return "Low"

# Function to convert month string to number
def month_str_to_num(month_hint):
    month_map = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12"
    }
    return month_map.get(month_hint.lower(), None)

# Function to format the creation date
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

# Set up Flask route for file upload
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

        df["Component"] = df["Observation"].apply(extract_component)
        df[["Severity (S)", "Occurrence (O)", "Detection (D)"]] = df["Component"].apply(lambda comp: pd.Series(get_rpn_values(comp)))
        df["RPN"] = df["Severity (S)"] * df["Occurrence (O)"] * df["Detection (D)"]
        df["Priority"] = df["RPN"].apply(determine_priority)

        spn_df = df[df["Observation"].str.contains("spn", case=False, na=False)]
        non_spn_df = df[~df["Observation"].str.contains("spn", case=False, na=False)]

        priority_order = {"High": 1, "Moderate": 2, "Low": 3}
        spn_df = spn_df.sort_values(by="Priority", key=lambda x: x.map(priority_order))
        non_spn_df = non_spn_df.sort_values(by="Priority", key=lambda x: x.map(priority_order))

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

# Start Flask application
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
