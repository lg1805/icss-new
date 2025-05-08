from flask import Flask, request, render_template, send_file
import pandas as pd
import os
from datetime import datetime
import xlsxwriter
from rapidfuzz import fuzz
from concurrent.futures import ThreadPoolExecutor
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads/processed/'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Load RPN data
RPN_FILE = os.path.join(os.path.dirname(__file__), 'ProcessedData', 'RPN.xlsx')
if not os.path.exists(RPN_FILE):
    raise FileNotFoundError(f"RPN file not found at {RPN_FILE}")
rpn_data = pd.read_excel(RPN_FILE)
known_components = rpn_data["Component"].dropna().unique().tolist()

# Thread pool
executor = ThreadPoolExecutor(max_workers=4)

# Helper Functions
def extract_component(obs):
    obs = str(obs).strip()
    best_match, highest_score = None, 0
    for comp in known_components:
        score = fuzz.partial_ratio(comp.lower(), obs.lower())
        if score > highest_score and score >= 80:
            best_match, highest_score = comp, score
    return best_match or "Unknown"

def get_rpn_values(component):
    row = rpn_data[rpn_data["Component"] == component]
    if not row.empty:
        return (int(row["Severity (S)"].values[0]),
                int(row["Occurrence (O)"].values[0]),
                int(row["Detection (D)"].values[0]))
    return 1, 1, 10

def determine_priority(rpn):
    return "High" if rpn >= 200 else "Moderate" if rpn >= 100 else "Low"

def format_creation_date(date_str, month_hint):
    try:
        dt = pd.to_datetime(str(date_str).strip(), errors='coerce', dayfirst=True)
        if pd.notna(dt):
            return dt.strftime('%d/%m/%Y'), (datetime.now() - dt).days
    except:
        return None, None
    return None, None

def send_alert_email(df_filtered, emission_category):
    if df_filtered.empty:
        return

    sender_email = "lakshyarubi@gmail.com"
    cc_email = "rubisisters2118@gmail.com"
    receiver_email = {
        'CPCBII': "lakshyarubi.gnana2021@vitstudent.ac.in",
        'CPCBIV+': ["rubisisters2118@gmail.com", "rubisisters2118@gmail.com"],
        'BSII': "amit.kate@kirloskar.com",
        'BSIV': "babalu.patil@kirloskar.com",
        'BSV': "rubisisters2118@gmail.com"
    }.get(emission_category, sender_email)

    # Ensure all values are string type to prevent serialization errors
    df_filtered = df_filtered.astype(str)

    html_table = df_filtered.to_html(index=False)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "🚨 OPEN Incidents (3+ days)"
    msg["From"] = sender_email
    msg["To"] = receiver_email if isinstance(receiver_email, str) else ", ".join(receiver_email)

    months = df_filtered['Month'].dropna().unique()
    creation_dates = pd.to_datetime(df_filtered['Creation Date'], dayfirst=True, errors='coerce')
    years = creation_dates.dt.year.dropna().unique()

    month_str = ", ".join(months)
    year_str = ", ".join(map(str, years))

    from_date = df_filtered['Creation Date'].min() if not df_filtered.empty else None
    to_date = df_filtered['Creation Date'].max() if not df_filtered.empty else None

    email_body = f"""
    <html>
      <body style="font-family:Arial,sans-serif;">
        <h3>🚨 Open & Pending Incidents Escalated ≥ 3 Days</h3>
        <p>Generated: {datetime.now().strftime('%d %b %Y, %H:%M:%S')}</p>
        <b>Emissions Category:</b> {emission_category}<br>
        <b>Year(s):</b> {year_str}<br>
        <b>Month(s):</b> {month_str}<br>
        <b>From Date:</b> {from_date}<br>
        <b>To Date:</b> {to_date}<br><br>
        {html_table}
        <p>Regards,<br/>ICSS Team</p>
      </body>
    </html>
    """
    msg.attach(MIMEText(email_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            recipients = [receiver_email] if isinstance(receiver_email, str) else receiver_email
            recipients += [cc_email]
            server.login(sender_email, "YOUR_APP_PASSWORD")  # Replace with secure method
            server.sendmail(sender_email, recipients, msg.as_string())
            print("Email alert sent successfully.")
    except Exception as e:
        print(f"Failed to send email: {e}")

# Routes
@app.route('/')
def index():
    return render_template('frontNEW.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        if 'complaint_file' not in request.files:
            return "No complaint_file part", 400

        file = request.files['complaint_file']
        if file.filename == '':
            return "No selected file", 400

        emission_category = request.form.get('emission_category')
        filepath = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(filepath)
        df = pd.read_excel(filepath)

        required_cols = ['Observation', 'Creation Date', 'Incident Id']
        if not all(col in df.columns for col in required_cols):
            return "Required columns missing", 400

        fmt = df['Creation Date'].apply(lambda x: format_creation_date(x, 'default'))
        df['Creation Date'] = fmt.apply(lambda x: x[0])
        df['Days Elapsed'] = fmt.apply(lambda x: x[1])
        df['Creation_DT'] = pd.to_datetime(df['Creation Date'], dayfirst=True, errors='coerce')
        from_date_str = request.form.get('from_date')
        to_date_str = request.form.get('to_date')
        if from_date_str and to_date_str:
            from_date = pd.to_datetime(from_date_str, errors='coerce')
            to_date = pd.to_datetime(to_date_str, errors='coerce')
            df = df[(df['Creation_DT'] >= from_date) & (df['Creation_DT'] <= to_date)]
            df['Month'] = df['Creation_DT'].dt.strftime('%b')
            df.drop(columns=['Creation_DT'], inplace=True)

        df['Component'] = list(executor.map(extract_component, df['Observation']))
        rpn_vals = list(executor.map(get_rpn_values, df['Component']))
        df[['Severity (S)', 'Occurrence (O)', 'Detection (D)']] = pd.DataFrame(rpn_vals, index=df.index)
        df['RPN'] = df['Severity (S)'] * df['Occurrence (O)'] * df['Detection (D)']
        df['Priority'] = df['RPN'].apply(determine_priority)

        if 'Incident Status' not in df.columns:
            return "Required column 'Incident Status' missing", 400

        spn_df = df[df['Observation'].str.contains('spn', case=False, na=False)]
        non_spn = df[~df['Observation'].str.contains('spn', case=False, na=False)]

        order_map = {'High': 1, 'Moderate': 2, 'Low': 3}
        spn_df = spn_df.sort_values(by='Priority', key=lambda x: x.map(order_map))
        non_spn = non_spn.sort_values(by='Priority', key=lambda x: x.map(order_map))

        out_path = os.path.join(UPLOAD_FOLDER, 'processed_' + file.filename)
        with pd.ExcelWriter(out_path, engine='xlsxwriter') as writer:
            for name, sheet in [('SPN', spn_df), ('Non-SPN', non_spn)]:
                sheet.fillna('', inplace=True)
                sheet.to_excel(writer, sheet_name=name, index=False)
                wb = writer.book
                ws = writer.sheets[name]

                colors = {
                    'green': wb.add_format({'bg_color': '#C6EFCE'}),
                    'blue': wb.add_format({'bg_color': '#9DC3E6'}),
                    'yellow': wb.add_format({'bg_color': '#FFF2CC'}),
                    'pink': wb.add_format({'bg_color': '#E4A1C6'}),
                    'red': wb.add_format({'bg_color': '#FF0000'}),
                    'gray': wb.add_format({'bg_color': '#D9D9D9'}),
                }

                col_status = sheet.columns.get_loc('Incident Status')
                col_days = sheet.columns.get_loc('Days Elapsed')
                col_incident = sheet.columns.get_loc('Incident Id')

                for i, idx in enumerate(sheet.index):
                    status = str(sheet.iat[i, col_status]).strip().lower()
                    days = sheet.iat[i, col_days]
                    if status in ['closed', 'completed']:
                        ws.write(i + 1, col_status, sheet.iat[i, col_status], colors['green'])
                    if status in ['open', 'pending'] and isinstance(days, (int, float)):
                        fmt = None
                        if days == 0:
                            fmt = colors['gray']
                        elif days == 1:
                            fmt = colors['blue']
                        elif days == 2:
                            fmt = colors['yellow']
                        elif days == 3:
                            fmt = colors['pink']
                        elif days > 3:
                            fmt = colors['red']
                        if fmt:
                            ws.write(i + 1, col_incident, sheet.iat[i, col_incident], fmt)

        alert_df = df[(df['Incident Status'].str.lower().isin(['open', 'pending'])) & (df['Days Elapsed'] >= 3)]
        alert_cols = ['Incident Id', 'Creation Date', 'Month', 'Days Elapsed', 'Observation', 'Engine no',
                      'Service Dealer Name', 'Incident Status', 'Priority']
        alert_df = alert_df[alert_cols]
        executor.submit(send_alert_email, alert_df, emission_category)

        return send_file(out_path, as_attachment=True)

    except Exception as e:
        print(f"❌ Error: {e}")
        return f"An error occurred: {e}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
