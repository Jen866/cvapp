import os
import json
import fitz  # PyMuPDF
import google.generativeai as genai
from flask import Flask, request, Response, render_template
from flask_cors import CORS
from google.oauth2 import service_account
from googleapiclient.discovery import build
from collections import OrderedDict

app = Flask(__name__)
CORS(app)
app.config['JSON_SORT_KEYS'] = False

API_SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# ==== API CONFIG ====

try:
    service_account_info = json.loads(os.environ["GOOGLE_CREDS"])
    gemini_creds = service_account.Credentials.from_service_account_info(service_account_info)
    genai.configure(credentials=gemini_creds)
    scoped_creds = service_account.Credentials.from_service_account_info(service_account_info, scopes=API_SCOPES)
    sheets_service = build('sheets', 'v4', credentials=scoped_creds)
    drive_service = build('drive', 'v3', credentials=scoped_creds)
    text_model = genai.GenerativeModel("models/gemini-1.5-pro")
    json_model = genai.GenerativeModel(
        "models/gemini-1.5-pro",
        generation_config={"response_mime_type": "application/json"}
    )
except Exception as e:
    print(f"FATAL ERROR during API configuration: {e}")
    text_model = None
    json_model = None
    sheets_service = None
    drive_service = None

# ==== CV ANALYSIS HELPERS ====

def extract_text_from_pdf(file_stream):
    try:
        doc = fitz.open(stream=file_stream.read(), filetype="pdf")
        return "".join(page.get_text() for page in doc)
    except Exception as e:
        return {"error": f"Failed to parse PDF: {e}"}

def extract_cv_info(cv_text):
    if not json_model:
        return {"error": "JSON Model not configured."}
    prompt = f"""Based on the following CV text, extract the specified fields. If a field is not found, use `null` as the value. Return the fields in this exact order: "Name and Surname", "Contact number", "Email address", "Suburb", "City", "Province", "Race", "Qualification", "University of Qualification", "Year of Qualification", "Current place of work", "First Language", "Second Language". CV Text: --- {cv_text} ---"""
    try:
        return json.loads(json_model.generate_content(prompt).text)
    except Exception as e:
        return {"error": f"Error calling Gemini for CV data: {e}"}

# ... [Other helper functions unchanged: get_language_from_name, etc.] ...

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

# ==== MULTI-CV EXTRACTION ====

@app.route("/extract", methods=["POST"])
def extract_multiple():
    files = request.files.getlist("cv_files")
    if not files:
        return Response(json.dumps({"error": "No files received"}), status=400, mimetype='application/json')

    results = []
    for file in files[:10]:
        if not file.filename.lower().endswith(".pdf"):
            continue
        text = extract_text_from_pdf(file)
        if isinstance(text, dict) and "error" in text:
            continue
        cv_data = extract_cv_info(text)
        if "error" in cv_data:
            continue

        name = cv_data.get("Name and Surname")
        province = cv_data.get("Province")
        if not province:
            location = cv_data.get("City") or cv_data.get("Suburb")
            province = get_province_from_location(location) if location else None
            cv_data["Province"] = province

        name_based_language = get_language_from_name(name)
        province_based_language = get_dominant_language_for_province(province)
        final_language = name_based_language
        assessment_note = "Name-based prediction."

        if name_based_language and province_based_language and name_based_language != province_based_language:
            final_language = reinvestigate_language_discrepancy(name, name_based_language, province, province_based_language)
            assessment_note = f"Discrepancy (Name: {name_based_language}, Province: {province_based_language}). Re-investigated."
        elif name_based_language == province_based_language:
            assessment_note = f"Name ({name_based_language}) & Province ({province_based_language}) align."

        cv_data["Dominant Province Language"] = province_based_language
        cv_data["Final Predicted Native Language"] = final_language
        cv_data["Language Assessment Note"] = assessment_note

        final_order = [
            "Name and Surname", "Contact number", "Email address", "Suburb", "City", "Province", "Race",
            "Qualification", "University of Qualification", "Year of Qualification",
            "Current place of work", "First Language", "Second Language",
            "Dominant Province Language", "Final Predicted Native Language", "Language Assessment Note"
        ]

        results.append({
            "headers": final_order,
            "row": [cv_data.get(k) for k in final_order]
        })

    return Response(json.dumps(results), mimetype='application/json')

# ==== EXPORT COMPILED CVS ====

@app.route("/export", methods=["POST"])
def export_all_to_sheet():
    if not sheets_service or not drive_service:
        return Response(json.dumps({"error": "Google API service not available."}), status=500, mimetype='application/json')

    data = request.get_json()
    if not isinstance(data, list):
        return Response(json.dumps({"error": "Invalid format. Expected a list of CV results."}), status=400, mimetype='application/json')

    # Create or reuse a consistent sheet
    try:
        spreadsheet = sheets_service.spreadsheets().create(
            body={"properties": {"title": "CV Analysis"}},
            fields="spreadsheetId,spreadsheetUrl"
        ).execute()

        sheet_id = spreadsheet["spreadsheetId"]
        sheet_url = spreadsheet["spreadsheetUrl"]

        # Compose final rows
        all_rows = []
        for cv in data:
            all_rows.append(cv["row"])
            all_rows.append([""] * len(cv["headers"]))  # blank row

        sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range="A1",
            valueInputOption="RAW",
            body={"values": [data[0]["headers"]] + all_rows}
        ).execute()

        drive_service.permissions().create(
            fileId=sheet_id,
            body={"type": "anyone", "role": "reader"}
        ).execute()

        return Response(json.dumps({"sheetUrl": sheet_url}), mimetype='application/json')
    except Exception as e:
        return Response(json.dumps({"error": f"Sheet export failed: {e}"}), status=500, mimetype='application/json')

if __name__ == "__main__":
    app.run(debug=True, port=5000)



