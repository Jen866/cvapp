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
    print("Gemini, Google Sheets, and Google Drive services configured successfully.")

except Exception as e:
    print(f"FATAL ERROR during API configuration: {e}")
    text_model = json_model = sheets_service = drive_service = None

def extract_text_from_pdf(file_stream):
    try:
        doc = fitz.open(stream=file_stream.read(), filetype="pdf")
        return "".join(page.get_text() for page in doc)
    except Exception as e:
        return {"error": f"Failed to parse PDF: {e}"}

def extract_cv_info(cv_text):
    if not json_model: return {"error": "JSON Model not configured."}
    prompt = f"""
    Extract the following CV fields from the text below. Use `null` for missing fields.
    Fields (in order): "Name and Surname", "Contact number", "Email address", "Suburb", "City", "Province",
    "Qualification", "University of Qualification", "Year of Qualification", "Current place of work",
    "First Language", "Second Language"
    CV Text: --- {cv_text} ---
    """
    try:
        return json.loads(json_model.generate_content(prompt).text)
    except Exception as e:
        return {"error": f"Error calling Gemini for CV data: {e}"}

def get_language_from_name(name):
    if not text_model or not name: return "Error"
    prompt = f"What is the most likely native language of the South African name '{name}'? Choose from: isiZulu, isiXhosa, Afrikaans, Sepedi, English, Setswana, Sesotho, Xitsonga, siSwati, Tshivenda, isiNdebele."
    return text_model.generate_content(prompt).text.strip()

def get_province_from_location(location):
    if not text_model or not location: return "Unknown"
    prompt = f"Identify the South African province for the location '{location}'. Return only the province name."
    return text_model.generate_content(prompt).text.strip()

def get_dominant_language_for_province(province):
    if not text_model or not province or province in ["Unknown", "Error"]: return "Error"
    prompt = f"What is the most spoken native language in the province '{province}'? Return only one."
    return text_model.generate_content(prompt).text.strip()

def reinvestigate_language_discrepancy(name, name_lang, province, province_lang):
    prompt = f"A candidate named '{name}' may speak {name_lang}, but their province '{province}' speaks {province_lang}. What is the most likely native language? Return one."
    return text_model.generate_content(prompt).text.strip()

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload_and_process_cv():
    if "file" not in request.files:
        return Response(json.dumps({"error": "No file part"}), status=400, mimetype='application/json')

    file = request.files["file"]
    if file.filename == "" or not file.filename.lower().endswith('.pdf'):
        return Response(json.dumps({"error": "Please upload a PDF"}), status=400, mimetype='application/json')

    text = extract_text_from_pdf(file)
    if isinstance(text, dict): return Response(json.dumps(text), status=500, mimetype='application/json')

    cv_data = extract_cv_info(text)
    if "error" in cv_data: return Response(json.dumps(cv_data), status=500, mimetype='application/json')

    name = cv_data.get("Name and Surname")
    province = cv_data.get("Province")
    if not province:
        location = cv_data.get("City") or cv_data.get("Suburb")
        province = get_province_from_location(location)
        cv_data["Province"] = province

    name_lang = get_language_from_name(name)
    province_lang = get_dominant_language_for_province(province)
    final_lang = name_lang
    note = "Name-based prediction."

    if name_lang != province_lang:
        final_lang = reinvestigate_language_discrepancy(name, name_lang, province, province_lang)
        note = f"Discrepancy (Name: {name_lang}, Province: {province_lang}). Re-evaluated."
    elif name_lang == province_lang:
        note = f"Name and province both suggest {name_lang}."

    cv_data["Dominant Province Language"] = province_lang
    cv_data["Final Predicted Native Language"] = final_lang
    cv_data["Language Assessment Note"] = note

    ordered_keys = [
        "Name and Surname", "Contact number", "Email address", "Suburb", "City", "Province",
        "Qualification", "University of Qualification", "Year of Qualification", "Current place of work",
        "First Language", "Second Language", "Dominant Province Language",
        "Final Predicted Native Language", "Language Assessment Note"
    ]
    ordered_cv_data = OrderedDict((k, cv_data.get(k)) for k in ordered_keys)

    # Transpose for frontend
    transposed = {
        "headers": list(ordered_cv_data.keys()),
        "row": list(ordered_cv_data.values())
    }
    return Response(json.dumps(transposed), mimetype='application/json')

if __name__ == "__main__":
    app.run(debug=True, port=5000)


