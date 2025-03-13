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

# A simple in-memory store for processed update IDs.
processed_updates = set()


def get_db_connection():
    try:
        print("🔍 DB: Attempting to connect to PostgreSQL...", flush=True)
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        print("✅ DB: Successfully connected to PostgreSQL!", flush=True)
        return conn
    except Exception as e:
        print(f"❌ DB: Connection failed: {e}", flush=True)
        return None


def init_db():
    print("🔍 DB: Initializing database...", flush=True)
    conn = get_db_connection()
    if not conn:
        print("❌ DB: No connection available during init.", flush=True)
        return
    cursor = conn.cursor()
    try:
        # Create the table if it doesn't exist.
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
        print("✅ DB: Database initialized (table 'simps' created if not exists).", flush=True)
    except Exception as e:
        print(f"❌ DB: Error during DB initialization: {e}", flush=True)
    
    # Alter the table to add the subscription column if it doesn't exist.
    try:
        cursor.execute("""
            ALTER TABLE simps
            ADD COLUMN IF NOT EXISTS subscription NUMERIC
        """)
        conn.commit()
        print("✅ DB: Ensured 'subscription' column exists.", flush=True)
    except Exception as e:
        print(f"⚠️ DB: Could not alter 'subscription' column (it might already exist): {e}", flush=True)
    
    # Force the phone column to be TEXT even if table existed before.
    try:
        cursor.execute("ALTER TABLE simps ALTER COLUMN phone TYPE TEXT USING phone::text;")
        conn.commit()
        print("✅ DB: Ensured 'phone' column is TEXT.", flush=True)
    except Exception as e:
        print(f"⚠️ DB: Could not alter 'phone' column to TEXT: {e}", flush=True)
    
    cursor.close()
    conn.close()
    print("🔍 DB: Starting Airtable sync...", flush=True)
    sync_airtable_to_postgres()


def sync_airtable_to_postgres():
    print("🔍 Sync: Fetching Airtable data...", flush=True)
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"❌ Sync: Airtable fetch error: {response.text}", flush=True)
        return
    records = response.json().get("records", [])
    print(f"🔍 Sync: Retrieved {len(records)} records from Airtable.", flush=True)
    conn = get_db_connection()
    if not conn:
        print("❌ Sync: No DB connection available during sync.", flush=True)
        return
    cursor = conn.cursor()
    print("🔍 Sync: Deleting existing records in 'simps' table...", flush=True)
    cursor.execute("DELETE FROM simps")
    for record in records:
        fields = record.get("fields", {})
        # Process the Subscription field.
        sub_raw = fields.get("Subscription")
        sub_value = None
        if sub_raw is not None:
            print(f"🔍 Sync: Raw Subscription value: {sub_raw}", flush=True)
            if isinstance(sub_raw, (int, float)):
                sub_value = float(sub_raw)
            else:
                try:
                    # Remove % sign and whitespace then convert.
                    sub_value = float(str(sub_raw).replace("%", "").strip())
                except Exception as e:
                    print(f"❌ Sync: Error processing Subscription value: {e}", flush=True)
                    sub_value = None
        else:
            print("🔍 Sync: Subscription field is missing.", flush=True)
        
        try:
            cursor.execute("""
                INSERT INTO simps (simp_id, simp_name, status, intent, phone, subscription, duration, created)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (phone) DO UPDATE SET
                    simp_name = EXCLUDED.simp_name,
                    status = EXCLUDED.status,
                    intent = EXCLUDED.intent,
                    subscription = EXCLUDED.subscription,
                    duration = EXCLUDED.duration,
                    created = EXCLUDED.created
            """, (
                fields.get("Simp_ID"),
                fields.get("Simp"),
                fields.get("Status"),
                fields.get("🤝Intent"),
                str(fields.get("Phone")),  # Ensure TEXT storage.
                sub_value,  # Subscription as a number.
                fields.get("Duration"),
                fields.get("Created")
            ))
            print(f"✅ Sync: Inserted/Updated record for simp_id: {fields.get('Simp_ID')} with Subscription: {sub_value}", flush=True)
        except Exception as e:
            print(f"❌ Sync: Error inserting record: {e}", flush=True)
    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Sync: Airtable sync complete!", flush=True)


def select_emoji(subscription):
    """
    Returns an emoji based on the subscription value.
    """
    if subscription is None:
        return "❓"  # Unknown subscription value
    try:
        sub = float(subscription)
    except (ValueError, TypeError):
        return "❓"
    
    if sub >= 92:
        return "😍"
    elif sub >= 62:
        return "😀"
    elif sub >= 37:
        return "🙂"
    elif sub >= 18:
        return "😐"
    elif sub > 0:
        return "😨"
    elif sub == 0:
        return "💀"
    else:
        return "❓"


def send_to_telegram(message):
    print(f"🔍 Telegram: Sending message to Telegram: '{message}'", flush=True)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    response = requests.post(url, json=payload)
    print(f"🔍 Telegram: Sent message, response: {response.text}", flush=True)


def create_app():
    app = Flask(__name__)
    print(f"🔍 App: DATABASE_URL = {DATABASE_URL}", flush=True)
    if not DATABASE_URL:
        raise Exception("❌ App: DATABASE_URL not set!")
    
    # Initialize the database (with --preload, this will run once)
    with app.app_context():
        init_db()

    @app.route("/receive_text", methods=["POST"])
    def receive_text():
        print("🔍 /receive_text: Received a POST request", flush=True)
        data = request.json
        print(f"🔍 /receive_text: Data received: {data}", flush=True)
        phone_number = data.get("phone")
        text_message = data.get("message")
        if not phone_number or not text_message:
            print("❌ /receive_text: Missing phone number or message.", flush=True)
            return {"error": "Missing phone number or message"}, 400
        conn = get_db_connection()
        if not conn:
            print("❌ /receive_text: DB connection failed.", flush=True)
            return {"error": "DB connection failed"}, 500
        cursor = conn.cursor()
        print(f"🔍 /receive_text: Querying DB for phone: {phone_number}", flush=True)
        cursor.execute("SELECT simp_id, simp_name, subscription FROM simps WHERE phone = %s", (phone_number,))
        simp = cursor.fetchone()
        print(f"🔍 /receive_text: DB query result: {simp}", flush=True)
        cursor.close()
        conn.close()
        if simp:
            simp_id, simp_name, subscription = simp
            emoji = select_emoji(subscription)
            formatted_message = f"{emoji} {simp_id} | {simp_name}: {text_message}"
            print(f"🔍 /receive_text: Forwarding formatted message: '{formatted_message}'", flush=True)
            send_to_telegram(formatted_message)
            return {"status": "Message sent"}, 200
        else:
            print("❌ /receive_text: Phone number not found in DB.", flush=True)
            return {"error": "Phone number not found"}, 404

    @app.route("/check_db", methods=["GET"])
    def check_db():
        print("🔍 /check_db: Checking database tables...", flush=True)
        conn = get_db_connection()
        if not conn:
            print("❌ /check_db: DB connection failed.", flush=True)
            return {"error": "DB connection failed"}, 500
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            tables = cursor.fetchall()
            print(f"🔍 /check_db: Retrieved tables: {tables}", flush=True)
        except Exception as e:
            print(f"❌ /check_db: Error querying tables: {e}", flush=True)
            return {"error": "DB query failed"}, 500
        cursor.close()
        conn.close()
        return {"tables": tables}

    @app.route("/receive_telegram_message", methods=["POST"])
    def receive_telegram_message():
        print("🔍 /receive_telegram_message: Received a POST request", flush=True)
        update = request.json
        print(f"🔍 /receive_telegram_message: Update received: {update}", flush=True)
        
        # Track processed update IDs to prevent duplicate processing.
        update_id = update.get("update_id")
        if update_id in processed_updates:
            print(f"🔍 /receive_telegram_message: Duplicate update received: {update_id}. Ignoring.", flush=True)
            return {"status": "OK"}, 200
        else:
            processed_updates.add(update_id)

        # Extract the text from the nested Telegram update structure.
        text_message = update.get("message", {}).get("text")
        if not text_message:
            print("❌ /receive_telegram_message: Missing message text.", flush=True)
            return {"error": "Missing message text"}, 200

        # Extract all numbers from the text to form the simp_id.
        numbers = re.findall(r'\d+', text_message)
        if not numbers:
            print("❌ /receive_telegram_message: No numbers found in the message.", flush=True)
            return {"error": "No numbers found in message"}, 200
        simp_id_str = ''.join(numbers)
        try:
            simp_id_int = int(simp_id_str)
        except ValueError as e:
            print(f"❌ /receive_telegram_message: Error converting simp_id to integer: {e}", flush=True)
            return {"error": "Invalid simp_id"}, 200

        print(f"🔍 /receive_telegram_message: Extracted simp_id: {simp_id_int}", flush=True)

        # Query the DB for a record with simp_id equal to simp_id_int.
        # Also retrieve subscription and simp_name.
        conn = get_db_connection()
        if not conn:
            print("❌ /receive_telegram_message: DB connection failed.", flush=True)
            return {"error": "DB connection failed"}, 200
        cursor = conn.cursor()
        print(f"🔍 /receive_telegram_message: Querying DB for simp_id: {simp_id_int}", flush=True)
        try:
            cursor.execute("SELECT phone, subscription, simp_name FROM simps WHERE simp_id = %s", (simp_id_int,))
            record = cursor.fetchone()
        except Exception as e:
            print(f"❌ /receive_telegram_message: DB query error: {e}", flush=True)
            cursor.close()
            conn.close()
            return {"error": "DB query failed"}, 200
        cursor.close()
        conn.close()
        if record:
            phone, subscription, simp_name = record
            emoji = select_emoji(subscription)
            # Remove the leading simp_id from the text.
            cleaned_message = re.sub(r'^\s*\d+\s*', '', text_message)
            final_message = f"{emoji} {simp_id_int} | {simp_name}: {cleaned_message}"
            print(f"🔍 /receive_telegram_message: Sending payload to Macrodroid: {final_message}", flush=True)
            payload = {"phone": phone, "message": final_message}
            try:
                response = requests.post(MACROTRIGGER_URL, json=payload)
                print(f"🔍 /receive_telegram_message: Sent payload, response: {response.text}", flush=True)
            except Exception as e:
                print(f"❌ /receive_telegram_message: Error sending payload to Macrodroid: {e}", flush=True)
                return {"error": "Failed to send to Macrodroid"}, 200
            return {"status": "Trigger sent"}, 200
        else:
            print("❌ /receive_telegram_message: No record found with that simp_id.", flush=True)
            return {"error": "No record found for simp_id"}, 200

    return app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
