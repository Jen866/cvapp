import os
import json
import fitz  # PyMuPDF
import google.generativeai as genai
from flask import Flask, request, Response
from flask_cors import CORS
from google.oauth2 import service_account
from googleapiclient.discovery import build
from collections import OrderedDict

# --- 1. INITIALIZATION & CONFIGURATION ---

app = Flask(__name__)
CORS(app)
app.config['JSON_SORT_KEYS'] = False

API_SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

try:
    # Load service account credentials from environment variable
    service_account_info = json.loads(os.environ["GOOGLE_CREDS"])

    # Gemini credentials (no scopes needed)
    gemini_creds = service_account.Credentials.from_service_account_info(service_account_info)
    genai.configure(credentials=gemini_creds)

    # Google Sheets and Drive API credentials (with scopes)
    scoped_creds = service_account.Credentials.from_service_account_info(service_account_info, scopes=API_SCOPES)

    sheets_service = build('sheets', 'v4', credentials=scoped_creds)
    drive_service = build('drive', 'v3', credentials=scoped_creds)

    text_model = genai.GenerativeModel("models/gemini-1.5-pro")
    json_model = genai.GenerativeModel(
        "models/gemini-1.5-pro",
        generation_config={"response_mime_type": "application/json"}
    )

    print("Gemini, Google Sheets, and Google Drive services configured successfully.")

except Exception as e:
    print(f"FATAL ERROR during API configuration: {e}")
    text_model = None
    json_model = None
    sheets_service = None
    drive_service = None

# --- 2. HELPER FUNCTIONS ---

def extract_text_from_pdf(file_stream):
    try:
        doc = fitz.open(stream=file_stream.read(), filetype="pdf")
        return "".join(page.get_text() for page in doc)
    except Exception as e:
        return {"error": f"Failed to parse PDF: {e}"}

def extract_cv_info(cv_text):
    if not json_model: return {"error": "JSON Model not configured."}
    prompt = f"""
    Based on the following CV text, extract the specified fields.
    If a field is not found, use `null` as the value.
    Return the fields in this exact order: "Name and Surname", "Contact number", "Email address", "Town", "City", "Province", "Race", "Qualification", "University of Qualification", "Year of Qualification", "Current place of work", "First Language", "Second Language".
    CV Text: --- {cv_text} ---
    """
    try:
        return json.loads(json_model.generate_content(prompt).text)
    except Exception as e:
        return {"error": f"Error calling Gemini for CV data: {e}"}

def get_language_from_name(name_and_surname):
    if not text_model: return "Error"
    if not name_and_surname: return None
    prompt = f"""Analyze the South African name "{name_and_surname}" for the most likely native language. Choose one from: isiZulu, isiXhosa, Afrikaans, Sepedi, English, Setswana, Sesotho, Xitsonga, siSwati, Tshivenda, isiNdebele. Default to English for generic names. Return only the language name."""
    try:
        return text_model.generate_content(prompt).text.strip()
    except Exception:
        return "Error"

def get_province_from_location(location_name):
    if not text_model: return "Error"
    if not location_name: return None
    prompt = f"""For the SA location "{location_name}", identify its province. Choose from: Eastern Cape, Free State, Gauteng, KwaZulu-Natal, Limpopo, Mpumalanga, North West, Northern Cape, Western Cape. If invalid, return "Unknown". Return only the province name."""
    try:
        return text_model.generate_content(prompt).text.strip()
    except Exception:
        return "Error"

def get_dominant_language_for_province(province_name):
    if not text_model: return "Error"
    if not province_name or province_name in ["Unknown", "Error"]: return None
    prompt = f"""What is the single most spoken NATIVE language in the SA province of "{province_name}"? Choose from: isiZulu, isiXhosa, Afrikaans, Sepedi, English, Setswana, Sesotho, Xitsonga, siSwati, Tshivenda, isiNdebele. Return only the language name."""
    try:
        return text_model.generate_content(prompt).text.strip()
    except Exception:
        return "Error"

def reinvestigate_language_discrepancy(name, name_lang, province, province_lang):
    if not text_model: return "Error"
    prompt = f"""Final analysis: A candidate's language is unclear. Name: "{name}" (suggests {name_lang}). Province: "{province}" (dominant language is {province_lang}). What is the MOST LIKELY native language? Consider name and location. Choose one from: isiZulu, isiXhosa, Afrikaans, Sepedi, English, Setswana, Sesotho, Xitsonga, siSwati, Tshivenda, isiNdebele. Return ONLY the language name."""
    try:
        return text_model.generate_content(prompt).text.strip()
    except Exception:
        return "Error"

# --- 3. FLASK API ENDPOINTS ---

@app.route("/", methods=["GET"])
def home():
    return "✅ CV App is running. Use /upload or /export."

@app.route("/upload", methods=["POST"])
def upload_and_process_cv():
    if "file" not in request.files: return Response(json.dumps({"error": "No file part"}), status=400, mimetype='application/json')
    file = request.files["file"]
    if file.filename == "": return Response(json.dumps({"error": "No file selected"}), status=400, mimetype='application/json')
    if not file.filename.lower().endswith('.pdf'): return Response(json.dumps({"error": "Please upload a PDF"}), status=400, mimetype='application/json')

    text = extract_text_from_pdf(file)
    if isinstance(text, dict): return Response(json.dumps(text), status=500, mimetype='application/json')

    cv_data = extract_cv_info(text)
    if "error" in cv_data: return Response(json.dumps(cv_data), status=500, mimetype='application/json')

    name = cv_data.get("Name and Surname")
    province = cv_data.get("Province")
    if not province:
        location = cv_data.get("City") or cv_data.get("Town")
        province = get_province_from_location(location) if location else None
        cv_data["Province"] = province

    name_based_language = get_language_from_name(name)
    province_based_language = get_dominant_language_for_province(province)
    final_language = name_based_language
    assessment_note = "Name-based prediction."
    if name_based_language and province_based_language and name_based_language != province_based_language:
        final_language = reinvestigate_language_discrepancy(name, name_based_language, province, province_based_language)
        assessment_note = f"Discrepancy (Name: {name_based_language}, Province: {province_based_language}). Re-investigated."
    elif name_based_language and province_based_language and name_based_language == province_based_language:
        assessment_note = f"Name ({name_based_language}) & Province ({province_based_language}) align."
    cv_data["Dominant Province Language"] = province_based_language
    cv_data["Final Predicted Native Language"] = final_language
    cv_data["Language Assessment Note"] = assessment_note

    final_order = ["Name and Surname", "Contact number", "Email address", "Town", "City", "Province", "Race", "Qualification", "University of Qualification", "Year of Qualification", "Current place of work", "First Language", "Second Language", "Dominant Province Language", "Final Predicted Native Language", "Language Assessment Note"]
    ordered_cv_data = OrderedDict((key, cv_data.get(key)) for key in final_order)
    json_string = json.dumps(ordered_cv_data, indent=4)
    return Response(json_string, mimetype='application/json')

@app.route("/export", methods=["POST"])
def export_to_sheets():
    print("\n--- Received request to /export ---")
    if not sheets_service or not drive_service:
        return Response(json.dumps({"error": "Google API service not available. Check backend logs."}), status=500, mimetype='application/json')

    cv_data = request.get_json()
    if not cv_data:
        return Response(json.dumps({"error": "No data received for export."}), status=400, mimetype='application/json')
    
    candidate_name = cv_data.get("Name and Surname", "New Candidate")

    try:
        spreadsheet_body = {'properties': {'title': f"CV Analysis - {candidate_name}"}}
        spreadsheet = sheets_service.spreadsheets().create(body=spreadsheet_body, fields='spreadsheetUrl,spreadsheetId').execute()
        sheet_url = spreadsheet.get('spreadsheetUrl')
        sheet_id = spreadsheet.get('spreadsheetId')

        values_to_write = list(map(list, cv_data.items()))
        update_body = {'values': values_to_write}
        sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range='A1',
            valueInputOption='RAW',
            body=update_body
        ).execute()

        permission_body = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=sheet_id, body=permission_body).execute()

        return Response(json.dumps({'sheetUrl': sheet_url}), mimetype='application/json')

    except Exception as e:
        print(f"\n!!!!!!!!!! EXPORT FAILED !!!!!!!!!!!")
        print(f"An error occurred during the export process: {e}")
        print(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n")
        return Response(json.dumps({"error": f"Failed to create Google Sheet. Check the backend terminal for detailed errors."}), status=500, mimetype='application/json')

# --- 4. RUN THE APPLICATION ---
if __name__ == "__main__":
    app.run(debug=True, port=5000)
