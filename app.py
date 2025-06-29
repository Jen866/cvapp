@app.route("/export", methods=["POST"])
def export_all_to_sheet():
    if not sheets_service or not EXPORT_SHEET_ID:
        return Response(json.dumps({"error": "Server configuration error. Check logs."}), status=500, mimetype='application/json')

    data = request.get_json()
    if not isinstance(data, list) or not data:
        return Response(json.dumps({"error": "Invalid or empty data."}), status=400, mimetype='application/json')

    try:
        # This new version loops through each CV and appends it one by one.
        # This is more robust and avoids timeouts or payload size limits.
        for entry in data:
            # The row data for a single CV
            row_to_append = [entry["row"]]

            # Append just this single row to the sheet
            sheets_service.spreadsheets().values().append(
                spreadsheetId=EXPORT_SHEET_ID,
                range="A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": row_to_append}
            ).execute()
        
        # After the loop finishes, return the success URL
        sheet_url = f"https://docs.google.com/spreadsheets/d/{EXPORT_SHEET_ID}"
        return Response(json.dumps({"sheets": {"all_candidates": sheet_url}}), mimetype='application/json')

    except Exception as e:
        print(f"Sheet Export Failed during row-by-row append: {e}")
        return Response(json.dumps({"error": f"Export process failed: {e}"}), status=500, mimetype='application/json')
