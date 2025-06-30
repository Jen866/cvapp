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
    # We now expect TWO permanent sheet IDs from the environment
    service_account_info = json.loads(os.environ["GOOGLE_CREDS"])
    ACTUARIAL_SHEET_ID = os.environ["ACTUARIAL_SHEET_ID"]
    BUSINESS_SHEET_ID = os.environ["BUSINESS_SHEET_ID"]

    scoped_creds = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    sheets_service = build('sheets', 'v4', credentials=scoped_creds)

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
            if isinstance(text, dict): continue
            cv_data = extract_cv_info(text)
            if "error" in cv_data: continue
            
            # Re-introducing the role logic
            role = determine_role(cv_data.get("Qualification"))
            results.append({
                "headers": final_order,
                "row": [cv_data.get(k) for k in final_order],
                "role": role
            })
    return Response(json.dumps(results), mimetype='application/json')


@app.route("/export", methods=["POST"])
def export_all_to_sheet():
    print("--- EXPORT ROUTE STARTED ---")
    if not all([sheets_service, ACTUARIAL_SHEET_ID, BUSINESS_SHEET_ID]):
        print("FATAL: Server configuration error. A service or Sheet ID is missing from environment variables.")
        return Response(json.dumps({"error": "Server configuration error. Check logs."}), status=500, mimetype='application/json')

    data = request.get_json()
    if not isinstance(data, list) or not data:
        print(f"ERROR: Invalid data received from frontend. Type: {type(data)}")
        return Response(json.dumps({"error": "Invalid or empty data."}), status=400, mimetype='application/json')

    # Map roles to their permanent Sheet IDs
    sheet_id_map = {
        "actuarial": ACTUARIAL_SHEET_ID,
        "business": BUSINESS_SHEET_ID
    }

    try:
        print(f"Processing {len(data)} CV entries.")
        # Loop through each CV entry one-by-one
        for i, entry in enumerate(data):
            role = entry.get("role", "business")
            sheet_id = sheet_id_map.get(role)
            row_data = entry.get("row")

            print(f"--- Processing Entry #{i+1} ---")
            print(f"Assigned Role: '{role}'")
            print(f"Target Sheet ID: '{sheet_id}'")
            # print(f"Data to append: {row_data}") # Commented out to keep logs clean

            if not sheet_id:
                print("WARNING: Skipping entry because the target Sheet ID is missing or role is invalid.")
                continue

            # This is the API call to Google
            sheets_service.spreadsheets().values().append(
                spreadsheetId=sheet_id,
                range="A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [row_data]}
            ).execute()

            print(f"SUCCESS: Appended entry #{i+1} to its sheet.")

        # If the loop finishes without error
        sheet_urls = {
            "actuarial": f"https://docs.google.com/spreadsheets/d/{ACTUARIAL_SHEET_ID}",
            "business": f"https://docs.google.com/spreadsheets/d/{BUSINESS_SHEET_ID}"
        }
        print("--- EXPORT ROUTE FINISHED SUCCESSFULLY ---")
        return Response(json.dumps({"sheets": sheet_urls}), mimetype='application/json')

    except Exception as e:
        # This will now give us the most detailed error possible
        print("---!!! EXCEPTION CAUGHT !!!---")
        print(f"ERROR: The process failed on Entry #{i+1} with role '{role}'.")
        print(f"ERROR TYPE: {type(e)}")
        print(f"ERROR DETAILS: {e}")
        return Response(json.dumps({"error": f"Export process failed: {e}"}), status=500, mimetype='application/json')


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
