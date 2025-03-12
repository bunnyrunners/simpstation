import os
import psycopg2
import requests
import json
from flask import Flask, request

# Flask App
app = Flask(__name__)

# ✅ Load Environment Variables & Debug Print
DATABASE_URL = os.getenv("DATABASE_URL")  # Fetch from Render environment variables
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

print(f"🔍 DEBUG: DATABASE_URL = {DATABASE_URL}")  # Log to see if Render is passing the DB URL

# ✅ Debugging: Exit if DATABASE_URL is missing
if not DATABASE_URL:
    raise Exception("❌ ERROR: DATABASE_URL is not set! Check Render Environment Variables.")

def get_db_connection():
    """Connect to PostgreSQL database with detailed error logging."""
    try:
        print("🔍 DEBUG: Attempting to connect to PostgreSQL...")
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        print("✅ SUCCESS: Connected to PostgreSQL!")
        return conn
    except Exception as e:
        print(f"❌ ERROR: Database connection failed: {e}")  # Log error
        return None  # Return None so we can debug further

def init_db():
    """Ensure the database and table exist before running queries."""
    print("🔍 DEBUG: Initializing PostgreSQL database...")

    conn = get_db_connection()
    if conn is None:
        print("❌ ERROR: Database connection failed during init_db()")
        return

    cursor = conn.cursor()

    # ✅ Debugging SQL Execution
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS simps (
                simp_id SERIAL PRIMARY KEY,
                simp_name TEXT NOT NULL,
                status TEXT NOT NULL,
                intent TEXT,
                phone BIGINT UNIQUE NOT NULL,
                duration INTEGER,
                created DATE
            )
        """)
        conn.commit()
        print("✅ SUCCESS: Database initialized!")
    except Exception as e:
        print(f"❌ ERROR: SQL Execution failed: {e}")
    
    cursor.close()
    conn.close()
    
    print("🔍 DEBUG: Running Airtable sync...")
    sync_airtable_to_postgres()

def sync_airtable_to_postgres():
    """Fetch data from Airtable and store it in PostgreSQL."""
    print("🔍 DEBUG: Syncing Airtable data to PostgreSQL...")

    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"❌ ERROR: Failed to fetch Airtable data: {response.text}")
        return

    records = response.json().get("records", [])
    conn = get_db_connection()
    if conn is None:
        print("❌ ERROR: Database connection failed during sync_airtable_to_postgres()")
        return

    cursor = conn.cursor()
    cursor.execute("DELETE FROM simps")  # Clear existing data

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
                fields.get("Phone"),
                fields.get("Duration"),
                fields.get("Created")
            ))
        except Exception as e:
            print(f"❌ ERROR: Failed to insert Airtable record: {e}")
    
    conn.commit()
    cursor.close()
    conn.close()
    print("✅ SUCCESS: Airtable data synced to PostgreSQL!")

@app.route("/receive_text", methods=["POST"])
def receive_text():
    """Receive text from Macrodroid and send to Telegram."""
    data = request.json
    print(f"🔍 DEBUG: Received request: {data}")

    phone_number = data.get("phone")
    text_message = data.get("message")

    if not phone_number or not text_message:
        return {"error": "Missing phone number or message"}, 400

    conn = get_db_connection()
    if conn is None:
        print("❌ ERROR: Database connection failed during receive_text()")
        return {"error": "Database connection failed"}, 500

    cursor = conn.cursor()
    cursor.execute("SELECT simp_id, simp_name FROM simps WHERE phone = %s", (phone_number,))
    simp = cursor.fetchone()
    cursor.close()
    conn.close()

    if simp:
        simp_id, simp_name = simp
        formatted_message = f"{simp_id} | {simp_name} - {text_message}"
        send_to_telegram(formatted_message)
        return {"status": "Message sent"}, 200
    else:
        return {"error": "Phone number not found"}, 404

def send_to_telegram(message):
    """Send message to Telegram group."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    response = requests.post(url, json=payload)
    print(f"🔍 DEBUG: Telegram Response: {response.text}")

@app.route("/check_db", methods=["GET"])
def check_db():
    """Check if the database is connected and list tables."""
    print("🔍 DEBUG: Checking database connection...")

    conn = get_db_connection()
    if conn is None:
        return {"error": "Database connection failed"}, 500

    cursor = conn.cursor()
    try:
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        tables = cursor.fetchall()
    except Exception as e:
        print(f"❌ ERROR: Failed to fetch tables: {e}")
        return {"error": "Database query failed"}, 500

    cursor.close()
    conn.close()
    return {"tables": tables}

if __name__ == "__main__":
    print("🔍 DEBUG: Starting Flask app...")
    init_db()
