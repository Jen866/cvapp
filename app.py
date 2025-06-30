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

# Google API scopes
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Load credentials
try:
    service_account_info = json.loads(os.environ["GOOGLE_CREDS"])


    gemini_creds = service_account.Credentials.from_service_account_info(info)
    scoped_creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    genai.configure(credentials=gemini_creds)
    gemini_json = genai.GenerativeModel("models/gemini-1.5-pro", generation_config={"response_mime_type": "application/json"})

    sheets_service = build("sheets", "v4", credentials=scoped_creds)
    drive_service = build("drive", "v3", credentials=scoped_creds)

    print("✅ Gemini + Sheets + Drive configured")
except Exception as e:
    print(f"❌ Config error: {e}")
    gemini_json = None
    sheets_service = None
    drive_service = None

# Sheet ID (single sheet to store all CVs)
SHEET_ID = "17VWBbw2BwBC3eeuPVNqAESGdSVr7AVeMztKZLNfedb4"  # <-- Replace with your own sheet ID
SHEET_RANGE = "A1"

# Fields to extract (must match front-end headers)
FIELDS = [
    "Name and Surname", "Contact number", "Email address", "Suburb", "City", "Province",
    "Race", "Qualification", "University of Qualification", "Year of Qualification",
    "Current place of work", "First Language", "Second Language"
]

@app.route("/")
def home():
    return render_template("index.html")


def extract_text(file_stream):
    doc = fitz.open(stream=file_stream.read(), filetype="pdf")
    return "".join(page.get_text() for page in doc)


def extract_info_from_text(text):
    prompt = f"""
    Extract the following fields from this CV text. Use "null" for missing values. Return only JSON in this exact order:
    {FIELDS}
    
    CV Text:
    ---
    {text}
    ---
    """
    try:
        response = gemini_json.generate_content(prompt)
        return json.loads(response.text)
    except Exception as e:
        return {"error": f"Gemini error: {e}"}


def flatten(value):
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return value


@app.route("/extract", methods=["POST"])
def extract_route():
    if "files" not in request.files:
        return Response(json.dumps({"error": "No files provided"}), status=400)

    rows = []
    for file in request.files.getlist("files"):
        text = extract_text(file)
        info = extract_info_from_text(text)
        row = [flatten(info.get(field, "null")) for field in FIELDS]
        rows.append(row)

    return {"rows": rows}


@app.route("/export", methods=["POST"])
def export_route():
    try:
        if not sheets_service:
            raise Exception("Sheets service not configured.")

        data = request.get_json()
        headers = data.get("headers")
        rows = data.get("rows")

        if not headers or not rows:
            return Response(json.dumps({"error": "Invalid payload"}), status=400)

        values = [[""]] + rows  # Add a blank row before each batch
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range=SHEET_RANGE,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": values}
        ).execute()

        # Return the sheet link
        sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"
        return {"sheetUrl": sheet_url}

    except Exception as e:
        return Response(json.dumps({"error": f"Export failed: {e}"}), status=500)


if __name__ == "__main__":
    app.run(debug=True)

