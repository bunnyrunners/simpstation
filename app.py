import sqlite3
import requests
import json
from flask import Flask, request

# Constants
AIRTABLE_API_KEY = "patPuTMAeNxOLZdfF.eae8fe1269153d6dffd899b697f8e4c43bab4981c95e0ada95afae69b6ffea40"
AIRTABLE_BASE_ID = "appwzeZmxDEaLc2Cv"
AIRTABLE_TABLE_NAME = "Simps"
TELEGRAM_BOT_TOKEN = "7029812889:AAFinrJKT61P0qwl0NjWkM-R_pr4niTxEDE"
TELEGRAM_CHAT_ID = "-1002184021600"

# Flask App
app = Flask(__name__)

def init_db():
    """Initialize SQLite database and sync data from Airtable"""
    conn = sqlite3.connect("simps.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS simps (
            simp_id INTEGER PRIMARY KEY,
            simp_name TEXT,
            status TEXT,
            intent TEXT,
            phone INTEGER UNIQUE,
            duration INTEGER,
            created DATE
        )
    """)
    conn.commit()
    conn.close()
    sync_airtable_to_sqlite()

def sync_airtable_to_sqlite():
    """Fetch data from Airtable and store it in SQLite."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        records = response.json().get("records", [])
        conn = sqlite3.connect("simps.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM simps")  # Clear existing data
        for record in records:
            fields = record.get("fields", {})
            cursor.execute("""
                INSERT INTO simps (simp_id, simp_name, status, intent, phone, duration, created)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(phone) DO UPDATE SET
                simp_name=excluded.simp_name,
                status=excluded.status,
                intent=excluded.intent,
                duration=excluded.duration,
                created=excluded.created
            """, (
                fields.get("Simp_ID"),
                fields.get("Simp"),
                fields.get("Status"),
                fields.get("🤝Intent"),
                fields.get("Phone"),
                fields.get("Duration"),
                fields.get("Created")
            ))
        conn.commit()
        conn.close()
    else:
        print("Failed to sync Airtable data")

@app.route("/receive_text", methods=["POST"])
def receive_text():
    """Receive text from Macrodroid and send to Telegram."""
    data = request.json
    phone_number = data.get("phone")
    text_message = data.get("message")
    
    if not phone_number or not text_message:
        return {"error": "Missing phone number or message"}, 400
    
    conn = sqlite3.connect("simps.db")
    cursor = conn.cursor()
    cursor.execute("SELECT simp_id, simp_name FROM simps WHERE phone = ?", (phone_number,))
    simp = cursor.fetchone()
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
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    requests.post(url, json=payload)

if __name__ == "__main__":
    print("Initializing database...")  # Log to confirm it runs
    init_db()  # Ensure the database and table exist
    app.run(host="0.0.0.0", port=5000)

