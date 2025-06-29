import os
import json
import fitz  # PyMuPDF
import google.generativeai as genai
from flask import Flask, request, Response, render_template
from flask_cors import CORS
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)
CORS(app)
app.config['JSON_SORT_KEYS'] = False

# --- CONSTANTS ---
API_SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# --- API AND MODEL CONFIGURATION ---
try:
    service_account_info = json.loads(os.environ["GOOGLE_CREDS"])
    # Retrieve Sheet IDs from environment variables
    ACTUARIAL_SHEET_ID = os.environ["ACTUARIAL_SHEET_ID"]
    BUSINESS_SHEET_ID = os.environ["BUSINESS_SHEET_ID"]

    gemini_creds = service_account.Credentials.from_service_account_info(service_account_info)
    genai.configure(credentials=gemini_creds)
    scoped_creds = service_account.Credentials.from_service_account_info(service_account_info, scopes=API_SCOPES)
    sheets_service = build('sheets', 'v4', credentials=scoped_creds)
    drive_service = build('drive', 'v3', credentials=scoped_creds)
    json_model = genai.GenerativeModel(
        "models/gemini-1.5-pro",
        generation_config={"response_mime_type": "application/json"}
    )
except KeyError as e:
    print(f"FATAL ERROR: Missing environment variable: {e}")
    # Set services to None so the app can start but API calls will fail gracefully
    sheets_service = None
    drive_service = None
    json_model = None
except Exception as e:
    print(f"FATAL ERROR during API configuration: {e}")
    sheets_service = None
    drive_service = None
    json_model = None

# --- CV ANALYSIS HELPERS ---

def extract_text_from_pdf(file_stream):
    try:
        doc = fitz.open(stream=file_stream.read(), filetype="pdf")
        return "".join(page.get_text() for page in doc)
    except Exception as e:
        print(f"PDF Parsing Error: {e}")
        return {"error": f"Failed to parse PDF: {e}"}

def extract_cv_info(cv_text):
    if not json_model:
        return {"error": "Model not configured due to startup error."}
    prompt = f"""Based on the following CV text, extract the specified fields. If a field is not found, use `null` as the value. Return the fields in this exact order: "Name and Surname", "Contact number", "Email address", "Suburb", "City", "Province", "Qualification", "University of Qualification", "Year of Qualification", "Current place of work", "First Language", "Second Language". CV Text: --- {cv_text} ---"""
    try:
        # Use .parts[0].text for robustness with the new SDK
        response_text = json_model.generate_content(prompt).parts[0].text
        return json.loads(response_text)
    except Exception as e:
        print(f"Gemini Call Error: {e}")
        return {"error": f"Error calling Gemini for CV data: {e}"}

def determine_role(qualification):
    if not qualification or not isinstance(qualification, str):
        return "business"
    lower_q = qualification.lower()
    if "actuarial" in lower_q or "actuary" in lower_q:
        return "actuarial"
    return "business"

# --- FLASK ROUTES ---

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/extract", methods=["POST"])
def extract_multiple():
    files = request.files.getlist("cv_files")
    if not files:
        return Response(json.dumps({"error": "No files received"}), status=400, mimetype='application/json')

    results = []
    final_order = [
        "Name and Surname", "Contact number", "Email address", "Suburb", "City", "Province",
        "Qualification", "University of Qualification", "Year of Qualification",
        "Current place of work", "First Language", "Second Language"
    ]

    for file in files[:5]:
        if file and file.filename.lower().endswith(".pdf"):
            text = extract_text_from_pdf(file)
            if isinstance(text, dict) and "error" in text:
                continue
            
            cv_data = extract_cv_info(text)
            if "error" in cv_data:
                continue
            
            role = determine_role(cv_data.get("Qualification"))

            results.append({
                "headers": final_order,
                "row": [cv_data.get(k) for k in final_order],
                "role": role
            })

    return Response(json.dumps(results), mimetype='application/json')


@app.route("/export", methods=["POST"])
def export_all_to_sheet():
    # Check if services or Sheet IDs are missing from the environment
    if not all([sheets_service, drive_service, ACTUARIAL_SHEET_ID, BUSINESS_SHEET_ID]):
        return Response(json.dumps({"error": "Server configuration error. Check logs."}), status=500, mimetype='application/json')

    data = request.get_json()
    if not isinstance(data, list) or not data:
        return Response(json.dumps({"error": "Invalid or empty data received."}), status=400, mimetype='application/json')

    # Map roles to their permanent Sheet IDs
    sheet_id_map = {
        "actuarial": ACTUARIAL_SHEET_ID,
        "business": BUSINESS_SHEET_ID
    }
    
    # Group rows by role
    grouped = {"actuarial": [], "business": []}
    for entry in data:
        role = entry.get("role", "business")
        if role in grouped:
            grouped[role].append(entry["row"])

    try:
        # Loop through the grouped data and append to the correct sheet
        for role, entries in grouped.items():
            if not entries:
                continue  # Skip if no CVs for this role

            sheet_id = sheet_id_map[role]

            sheets_service.spreadsheets().values().append(
                spreadsheetId=sheet_id,
                range="A1",  # Appending to the first sheet/tab is fine
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": entries}
            ).execute()
        
        # Return the permanent URLs to the user
        sheet_urls = {
            "actuarial": f"https://docs.google.com/spreadsheets/d/{ACTUARIAL_SHEET_ID}",
            "business": f"https://docs.google.com/spreadsheets/d/{BUSINESS_SHEET_ID}"
        }
        return Response(json.dumps({"sheets": sheet_urls}), mimetype='application/json')

    except Exception as e:
        print(f"Sheet Export Failed: {e}")
        # Provide a more specific error message back to the frontend
        return Response(json.dumps({"error": f"An error occurred during the export process. Please check server logs. Error: {e}"}), status=500, mimetype='application/json')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

