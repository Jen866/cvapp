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
    prompt = f"""Based on the following CV text, extract the specified fields. If a field is not found, use `null` as the value. Return the fields in this exact order: "Name and Surname", "Contact number", "Email address", "Suburb", "City", "Province", "Qualification", "University of Qualification", "Year of Qualification", "Current place of work", "First Language", "Second Language". CV Text: --- {cv_text} ---"""
    try:
        return json.loads(json_model.generate_content(prompt).text)
    except Exception as e:
        return {"error": f"Error calling Gemini for CV data: {e}"}

def determine_role(qualification):
    if not qualification:
        return "business"
    lower_q = qualification.lower()
    if "actuarial" in lower_q or "actuary" in lower_q:
        return "actuarial"
    return "business"

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/extract", methods=["POST"])
def extract_multiple():
    files = request.files.getlist("cv_files")
    if not files:
        return Response(json.dumps({"error": "No files received"}), status=400, mimetype='application/json')

    results = []
    for file in files[:5]:
        if not file.filename.lower().endswith(".pdf"):
            continue
        text = extract_text_from_pdf(file)
        if isinstance(text, dict) and "error" in text:
            continue
        cv_data = extract_cv_info(text)
        if "error" in cv_data:
            continue

        role = determine_role(cv_data.get("Qualification"))

        final_order = [
            "Name and Surname", "Contact number", "Email address", "Suburb", "City", "Province",
            "Qualification", "University of Qualification", "Year of Qualification",
            "Current place of work", "First Language", "Second Language"
        ]

        results.append({
            "headers": final_order,
            "row": [cv_data.get(k) for k in final_order],
            "role": role
        })

    return Response(json.dumps(results), mimetype='application/json')

@app.route("/export", methods=["POST"])
def export_all_to_sheet():
    if not sheets_service or not drive_service:
        return Response(json.dumps({"error": "Google API service not available."}), status=500, mimetype='application/json')

    data = request.get_json()
    if not isinstance(data, list):
        return Response(json.dumps({"error": "Invalid format. Expected a list of CV results."}), status=400, mimetype='application/json')

    sheet_ids = {
        "actuarial": "sheet_id_actuarial.txt",
        "business": "sheet_id_business.txt"
    }
    sheet_urls = {}

    try:
        grouped = {"actuarial": [], "business": []}
        headers = data[0]["headers"]

        for entry in data:
            role = entry.get("role", "business")
            grouped[role].append(entry["row"])

        for role, entries in grouped.items():
            if not entries:
                continue

            file_path = sheet_ids[role]
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    sheet_id = f.read().strip()
            else:
                sheet = sheets_service.spreadsheets().create(
                    body={"properties": {"title": f"{role.capitalize()} Candidates"}},
                    fields="spreadsheetId"
                ).execute()
                sheet_id = sheet["spreadsheetId"]
                with open(file_path, "w") as f:
                    f.write(sheet_id)
                drive_service.permissions().create(
                    fileId=sheet_id,
                    body={"type": "anyone", "role": "writer"}
                ).execute()
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=sheet_id,
                    range="A1",
                    valueInputOption="RAW",
                    body={"values": [headers]}
                ).execute()

            sheets_service.spreadsheets().values().append(
                spreadsheetId=sheet_id,
                range="A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": entries}
            ).execute()

            sheet_urls[role] = f"https://docs.google.com/spreadsheets/d/{sheet_id}"

        return Response(json.dumps({"sheets": sheet_urls}), mimetype='application/json')

    except Exception as e:
        return Response(json.dumps({"error": f"Sheet export failed: {e}"}), status=500, mimetype='application/json')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


