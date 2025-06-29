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

# --- API AND MODEL CONFIGURATION ---
try:
    # We now only need two environment variables
    service_account_info = json.loads(os.environ["GOOGLE_CREDS"])
    EXPORT_SHEET_ID = os.environ["EXPORT_SHEET_ID"]

    scoped_creds = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    sheets_service = build('sheets', 'v4', credentials=scoped_creds)
    
    # Gemini configuration (optional for export, but good to have)
    genai.configure(credentials=service_account.Credentials.from_service_account_info(service_account_info))
    json_model = genai.GenerativeModel(
        "models/gemini-1.5-pro",
        generation_config={"response_mime_type": "application/json"}
    )
except KeyError as e:
    print(f"FATAL ERROR: Missing environment variable: {e}")
    sheets_service = None
    json_model = None
except Exception as e:
    print(f"FATAL ERROR during API configuration: {e}")
    sheets_service = None
    json_model = None

# --- CV ANALYSIS HELPERS ---

def extract_text_from_pdf(file_stream):
    try:
        doc = fitz.open(stream=file_stream.read(), filetype="pdf")
        return "".join(page.get_text() for page in doc)
    except Exception as e:
        return {"error": f"Failed to parse PDF: {e}"}

def extract_cv_info(cv_text):
    if not json_model:
        return {"error": "Model not configured."}
    prompt = f"""Based on the following CV text, extract the specified fields. If a field is not found, use `null` as the value. Return the fields in this exact order: "Name and Surname", "Contact number", "Email address", "Suburb", "City", "Province", "Qualification", "University of Qualification", "Year of Qualification", "Current place of work", "First Language", "Second Language". CV Text: --- {cv_text} ---"""
    try:
        response_text = json_model.generate_content(prompt).parts[0].text
        return json.loads(response_text)
    except Exception as e:
        return {"error": f"Error calling Gemini for CV data: {e}"}

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
            if isinstance(text, dict): continue
            cv_data = extract_cv_info(text)
            if "error" in cv_data: continue
            
            # The "role" logic is now completely removed
            results.append({
                "headers": final_order,
                "row": [cv_data.get(k) for k in final_order]
            })
    return Response(json.dumps(results), mimetype='application/json')


@app.route("/export", methods=["POST"])
def export_all_to_sheet():
    if not sheets_service or not EXPORT_SHEET_ID:
        return Response(json.dumps({"error": "Server configuration error. Check logs."}), status=500, mimetype='application/json')

    data = request.get_json()
    if not isinstance(data, list) or not data:
        return Response(json.dumps({"error": "Invalid or empty data."}), status=400, mimetype='application/json')

    try:
        # Simplified logic: just collect all rows
        headers = data[0]["headers"]
        all_rows = [entry["row"] for entry in data]
        
        # Append all rows to the single sheet
        sheets_service.spreadsheets().values().append(
            spreadsheetId=EXPORT_SHEET_ID,
            range="A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": all_rows}
        ).execute()
        
        # We need to write the headers if the sheet is new, but for now, this simplifies the test
        # A robust version would check if A1 is empty before writing headers.
        
        sheet_url = f"https://docs.google.com/spreadsheets/d/{EXPORT_SHEET_ID}"
        return Response(json.dumps({"sheets": {"all_candidates": sheet_url}}), mimetype='application/json')

    except Exception as e:
        print(f"Sheet Export Failed: {e}")
        return Response(json.dumps({"error": f"Export process failed: {e}"}), status=500, mimetype='application/json')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
