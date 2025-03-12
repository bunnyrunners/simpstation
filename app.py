    @app.route("/handle_telegram", methods=["POST"])
    def handle_telegram():
        # Process incoming Telegram update (non-bot message)
        update = request.json
        print(f"🔍 /handle_telegram: Update received: {update}")
        message_obj = update.get("message")
        if not message_obj:
            print("❌ /handle_telegram: No message in update")
            return {"error": "No message found"}, 400

        # Skip messages sent by the bot
        sender = message_obj.get("from", {})
        if sender.get("is_bot"):
            print("❌ /handle_telegram: Bot message ignored")
            return {"status": "Ignored bot message"}, 200

        text = message_obj.get("text", "")
        if "-" not in text:
            print("❌ /handle_telegram: Format invalid")
            return {"error": "Invalid message format"}, 400

        # Extract Simp_ID and message text
        parts = text.split("-", 1)
        simp_id_str = parts[0].strip()
        message_text = parts[1].strip()
        try:
            simp_id = int(simp_id_str)
        except ValueError:
            print("❌ /handle_telegram: Simp_ID not integer")
            return {"error": "Invalid Simp_ID"}, 400

        # Query DB for phone number using Simp_ID
        conn = get_db_connection()
        if not conn:
            return {"error": "DB connection failed"}, 500
        cursor = conn.cursor()
        cursor.execute("SELECT phone FROM simps WHERE simp_id = %s", (simp_id,))
        record = cursor.fetchone()
        cursor.close()
        conn.close()
        if not record:
            print("❌ /handle_telegram: Simp_ID not found")
            return {"error": "Simp_ID not found"}, 404
        phone = record[0]

        # Build payload without the Simp_ID and hyphen, with correct key names.
        payload = {"Phone": str(phone), "Message": message_text}
        print(f"🔍 /handle_telegram: Forwarding payload: {payload}")
        response = requests.post(MACROTRIGGER_URL, json=payload)
        print(f"✅ /handle_telegram: Macrodroid response: {response.text}")
        return {"status": "Message forwarded"}, 200
