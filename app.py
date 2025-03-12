import os
import re
import psycopg2
import requests
from flask import Flask, request

# Environment variables
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL URL from Render
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Macrodroid trigger URL for replies
MACROTRIGGER_URL = "https://trigger.macrodroid.com/9ddf8fe0-30cd-4343-b88a-4d14641c850f/reply"


def get_db_connection():
    try:
        print("🔍 DB: Attempting to connect to PostgreSQL...")
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        print("✅ DB: Successfully connected to PostgreSQL!")
        return conn
    except Exception as e:
        print(f"❌ DB: Connection failed: {e}")
        return None


def init_db():
    print("🔍 DB: Initializing database...")
    conn = get_db_connection()
    if not conn:
        print("❌ DB: No connection available during init.")
        return
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS simps (
                simp_id SERIAL PRIMARY KEY,
                simp_name TEXT NOT NULL,
                status TEXT NOT NULL,
                intent TEXT,
                phone TEXT UNIQUE NOT NULL,
                duration INTEGER,
                created DATE
            )
        """)
        conn.commit()
        print("✅ DB: Database initialized (table 'simps' created if not exists).")
    except Exception as e:
        print(f"❌ DB: Error during DB initialization: {e}")
    cursor.close()
    conn.close()
    print("🔍 DB: Starting Airtable sync...")
    sync_airtable_to_postgres()


def sync_airtable_to_postgres():
    print("🔍 Sync: Fetching Airtable data...")
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"❌ Sync: Airtable fetch error: {response.text}")
        return
    records = response.json().get("records", [])
    print(f"🔍 Sync: Retrieved {len(records)} records from Airtable.")
    conn = get_db_connection()
    if not conn:
        print("❌ Sync: No DB connection available during sync.")
        return
    cursor = conn.cursor()
    print("🔍 Sync: Deleting existing records in 'simps' table...")
    cursor.execute("DELETE FROM simps")
    for record in records:
        fields = record.get("fields", {})
        try:
            cursor.execute("""
                INSERT INTO simps (simp_id, simp_name, status, intent, phone, duration, created)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (phone) DO UPDATE SET
                    simp_name = EXCLUDED.simp_name,
                    status = EXCLUDED.status,
                    intent = EXCLUDED.intent,
                    duration = EXCLUDED.duration,
                    created = EXCLUDED.created
            """, (
                fields.get("Simp_ID"),
                fields.get("Simp"),
                fields.get("Status"),
                fields.get("🤝Intent"),
                fields.get("Phone"),  # This value will now be stored as text.
                fields.get("Duration"),
                fields.get("Created")
            ))
            print(f"✅ Sync: Inserted/Updated record for simp_id: {fields.get('Simp_ID')}")
        except Exception as e:
            print(f"❌ Sync: Error inserting record: {e}")
    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Sync: Airtable sync complete!")


def send_to_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    response = requests.post(url, json=payload)
    print(f"🔍 Telegram: Sent message, response: {response.text}")


def create_app():
    app = Flask(__name__)
    print(f"🔍 App: DATABASE_URL = {DATABASE_URL}")
    if not DATABASE_URL:
        raise Exception("❌ App: DATABASE_URL not set!")
    
    # Initialize the database (with --preload, this will run once)
    with app.app_context():
        init_db()

    @app.route("/receive_text", methods=["POST"])
    def receive_text():
        data = request.json
        print(f"🔍 /receive_text: Data received: {data}")
        phone_number = data.get("phone")
        text_message = data.get("message")
        if not phone_number or not text_message:
            print("❌ /receive_text: Missing phone number or message.")
            return {"error": "Missing phone number or message"}, 400
        conn = get_db_connection()
        if not conn:
            print("❌ /receive_text: DB connection failed.")
            return {"error": "DB connection failed"}, 500
        cursor = conn.cursor()
        print(f"🔍 /receive_text: Querying DB for phone: {phone_number}")
        cursor.execute("SELECT simp_id, simp_name FROM simps WHERE phone = %s", (phone_number,))
        simp = cursor.fetchone()
        print(f"🔍 /receive_text: DB query result: {simp}")
        cursor.close()
        conn.close()
        if simp:
            simp_id, simp_name = simp
            formatted_message = f"{simp_id} | {simp_name} - {text_message}"
            print(f"🔍 /receive_text: Forwarding formatted message: '{formatted_message}'")
            send_to_telegram(formatted_message)
            return {"status": "Message sent"}, 200
        else:
            print("❌ /receive_text: Phone number not found in DB.")
            return {"error": "Phone number not found"}, 404

    @app.route("/check_db", methods=["GET"])
    def check_db():
        print("🔍 /check_db: Checking database tables...")
        conn = get_db_connection()
        if not conn:
            print("❌ /check_db: DB connection failed.")
            return {"error": "DB connection failed"}, 500
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            tables = cursor.fetchall()
            print(f"🔍 /check_db: Retrieved tables: {tables}")
        except Exception as e:
            print(f"❌ /check_db: Error querying tables: {e}")
            return {"error": "DB query failed"}, 500
        cursor.close()
        conn.close()
        return {"tables": tables}

    @app.route("/handle_telegram", methods=["POST"])
    def handle_telegram():
        # Process incoming Telegram update (non-bot message)
        update = request.json
        print(f"🔍 /handle_telegram: Update received: {update}")
        message_obj = update.get("message")
        if not message_obj:
            print("❌ /handle_telegram: No 'message' found in update.")
            return {"error": "No message found"}, 400

        # Skip messages sent by the bot
        sender = message_obj.get("from", {})
        if sender.get("is_bot"):
            print("❌ /handle_telegram: Message sent by bot, ignoring.")
            return {"status": "Ignored bot message"}, 200

        text = message_obj.get("text", "")
        print(f"🔍 /handle_telegram: Raw text received: '{text}'")

        # Split the message so that the first token is the simp_id and the rest is the message.
        tokens = text.strip().split(maxsplit=1)
        if len(tokens) < 2:
            print("❌ /handle_telegram: Message format invalid; not enough tokens.")
            return {"error": "Invalid message format"}, 400

        simp_id_str = tokens[0]
        message_text = tokens[1]
        print(f"🔍 /handle_telegram: Extracted simp_id string: '{simp_id_str}', message_text: '{message_text}'")

        try:
            simp_id = int(simp_id_str)
            print(f"🔍 /handle_telegram: Converted simp_id to integer: {simp_id}")
        except ValueError:
            print("❌ /handle_telegram: Simp_ID is not a valid integer.")
            return {"error": "Invalid Simp_ID"}, 400

        # Query the database for the phone number using the simp_id
        conn = get_db_connection()
        if not conn:
            print("❌ /handle_telegram: DB connection failed during lookup.")
            return {"error": "DB connection failed"}, 500
        cursor = conn.cursor()
        print(f"🔍 /handle_telegram: Querying DB for simp_id: {simp_id}")
        cursor.execute("SELECT phone FROM simps WHERE simp_id = %s", (simp_id,))
        record = cursor.fetchone()
        print(f"🔍 /handle_telegram: DB query returned: {record}")
        cursor.close()
        conn.close()
        if not record:
            print(f"❌ /handle_telegram: No record found for simp_id: {simp_id}")
            return {"error": "Simp_ID not found"}, 404
        phone = record[0]
        print(f"🔍 /handle_telegram: Retrieved phone number '{phone}' for simp_id: {simp_id}")

        # Build payload for Macrodroid and log it
        payload = {"Phone": phone, "Message": message_text}
        print(f"🔍 /handle_telegram: Forwarding payload: {payload}")
        response = requests.post(MACROTRIGGER_URL, json=payload)
        print(f"✅ /handle_telegram: Macrodroid response: {response.text}")
        return {"status": "Message forwarded"}, 200

    return app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
