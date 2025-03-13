import os
import re
import random
import time
import threading
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

# In-memory store for processed Telegram update IDs (to avoid duplicate processing)
processed_updates = set()

# Global flag for diary update mode (triggered by /note command)
pending_diary = False

# Smart strings dictionary (keys stored in lower-case)
smart_strings = {
    "venmo": "Kelly_marie2697",
    "cashapp": "Marie2697",
    "instagram": "four4kelly",
    "onlyfans": "4kkelly"
}

# Diary responses array.
diary_responses = [
    "Love it! ❤️",
    "Dang, okay! 😳",
    "No way! 🤯",
    "That’s wild! 🌪️",
    "I’m in! ✅",
    "For real? 👀",
    "Oh wow! 😲",
    "Nice one! 👍",
    "Big yes! 🔥",
    "Well, damn. 😏",
    "Respect. 🙌",
    "Not bad! 😎",
    "Let’s go! 🚀",
    "That’s insane! 🤪",
    "You got it. 💪",
    "Sounds good. 🎵",
    "Oh heck! 😮",
    "Whoa, okay! 😵‍💫",
    "True that. 📜",
    "Oh snap! 📸",
    "Can’t lie! 🤥",
    "That’s crazy. 🌀",
    "Say less. 🤫",
    "Alright then! 🤷‍♂️",
    "Big mood. 🎭",
    "Sheesh! 🥶",
    "Wild stuff! 🦁",
    "Love that! 💖",
    "I’m shook. 🌊",
    "Facts. 🔎",
    "Big vibes! ✨",
    "Bet. 🎲",
    "Oh shoot! 🎯",
    "So true. ✅",
    "Good call! 📞",
    "Absolutely! 💯",
    "I see! 👁️",
    "That’s deep. 🌊",
    "Wow, okay. 😮‍💨",
    "Makes sense. 🤓",
    "That tracks. 🚆",
    "No doubt. 🤝",
    "I feel that. 🎶",
    "Well, alright! 🤠",
    "That’s cool. ❄️",
    "Big energy! ⚡",
    "Say what? 🤨",
    "Go off! 🔥",
    "So be it. 🕊️",
    "Okay then! 🤔"
]


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
    
    # Add the subscription column if it doesn't exist.
    try:
        cursor.execute("""
            ALTER TABLE simps
            ADD COLUMN IF NOT EXISTS subscription NUMERIC
        """)
        conn.commit()
        print("✅ DB: Ensured 'subscription' column exists.", flush=True)
    except Exception as e:
        print(f"⚠️ DB: Could not alter 'subscription' column: {e}", flush=True)
    
    # Add the new notes column if it doesn't exist.
    try:
        cursor.execute("""
            ALTER TABLE simps
            ADD COLUMN IF NOT EXISTS notes TEXT
        """)
        conn.commit()
        print("✅ DB: Ensured 'notes' column exists.", flush=True)
    except Exception as e:
        print(f"⚠️ DB: Could not alter 'notes' column: {e}", flush=True)
    
    # Ensure the phone column is stored as TEXT.
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
            try:
                if isinstance(sub_raw, str):
                    sub_value = float(sub_raw.replace("%", "").strip())
                else:
                    sub_value = float(sub_raw)
                if sub_value <= 1:
                    sub_value *= 100
            except Exception as e:
                print(f"❌ Sync: Error processing Subscription value: {e}", flush=True)
                sub_value = None
        # Fetch the Notes field.
        notes = fields.get("Notes")
        try:
            cursor.execute("""
                INSERT INTO simps (simp_id, simp_name, status, intent, phone, subscription, duration, created, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (phone) DO UPDATE SET
                    simp_name = EXCLUDED.simp_name,
                    status = EXCLUDED.status,
                    intent = EXCLUDED.intent,
                    subscription = EXCLUDED.subscription,
                    duration = EXCLUDED.duration,
                    created = EXCLUDED.created,
                    notes = EXCLUDED.notes
            """, (
                fields.get("Simp_ID"),
                fields.get("Simp"),
                fields.get("Status"),
                fields.get("🤝Intent"),
                str(fields.get("Phone")),
                sub_value,
                fields.get("Duration"),
                fields.get("Created"),
                notes
            ))
            print(f"✅ Sync: Inserted/Updated record for simp_id: {fields.get('Simp_ID')}", flush=True)
        except Exception as e:
            print(f"❌ Sync: Error inserting record: {e}", flush=True)
    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Sync: Airtable sync complete!", flush=True)


def select_emoji(subscription):
    if subscription is None or subscription == "":
        return "💀"
    try:
        sub = float(subscription)
    except (ValueError, TypeError):
        return "💀"
    
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
        return "💀"


def send_to_telegram(message):
    print(f"🔍 Telegram: Sending message to Telegram: '{message}'", flush=True)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    response = requests.post(url, json=payload)
    print(f"🔍 Telegram: Sent message, response: {response.text}", flush=True)


def run_periodic_sync():
    # Run the Airtable sync every 30 minutes.
    while True:
        time.sleep(1800)
        print("🔍 Periodic sync triggered.", flush=True)
        sync_airtable_to_postgres()


def create_app():
    app = Flask(__name__)
    print(f"🔍 App: DATABASE_URL = {DATABASE_URL}", flush=True)
    if not DATABASE_URL:
        raise Exception("❌ App: DATABASE_URL not set!")
    
    # Initialize the database on startup.
    with app.app_context():
        init_db()
    
    # Start the periodic sync in a background thread.
    threading.Thread(target=run_periodic_sync, daemon=True).start()

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
        cursor.execute("SELECT simp_id, simp_name, subscription FROM simps WHERE phone = %s", (phone_number,))
        simp = cursor.fetchone()
        cursor.close()
        conn.close()
        if simp:
            simp_id, simp_name, subscription = simp
            emoji = select_emoji(subscription)
            # Extract the leading number (simp_id) from the message and then remove it.
            m = re.match(r'^\s*\d+\s*(.*)', text_message)
            cleaned_message = m.group(1) if m else text_message
            # Include both emoji and simp_id and simp_name.
            formatted_message = f"{emoji} {simp_id} | {simp_name}: {cleaned_message}"
            print(f"🔍 /receive_text: Forwarding formatted message: '{formatted_message}'", flush=True)
            send_to_telegram(formatted_message)
            return {"status": "Message sent"}, 200
        else:
            print("❌ /receive_text: Phone number not found in DB.", flush=True)
            return {"error": "Phone number not found"}, 404

    @app.route("/check_db", methods=["GET"])
    def check_db():
        conn = get_db_connection()
        if not conn:
            return {"error": "DB connection failed"}, 500
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            tables = cursor.fetchall()
            print(f"🔍 /check_db: Retrieved tables: {tables}", flush=True)
        except Exception as e:
            cursor.close()
            conn.close()
            return {"error": "DB query failed"}, 500
        cursor.close()
        conn.close()
        return {"tables": tables}

    @app.route("/receive_telegram_message", methods=["POST"])
    def receive_telegram_message():
        global pending_diary
        print("🔍 /receive_telegram_message: Received a POST request", flush=True)
        update = request.json
        print(f"🔍 /receive_telegram_message: Update received: {update}", flush=True)
        
        # Track processed update IDs to prevent duplicate processing.
        update_id = update.get("update_id")
        if update_id in processed_updates:
            print(f"🔍 Duplicate update {update_id} received. Ignoring.", flush=True)
            return {"status": "OK"}, 200
        else:
            processed_updates.add(update_id)
        
        message = update.get("message", {})
        text_message = message.get("text")
        if not text_message:
            print("❌ /receive_telegram_message: Missing message text.", flush=True)
            return {"error": "Missing message text"}, 200

        # Smart string replacement: Look for words inside curly braces.
        smart_matches = re.findall(r'\{([^}]+)\}', text_message)
        for key in smart_matches:
            key_lower = key.lower()
            if key_lower not in smart_strings:
                error_msg = f"Message failed. Cannot find {{{key}}}."
                print(f"🔍 {error_msg}", flush=True)
                send_to_telegram(error_msg)
                return {"status": "Error: Unknown smart string"}, 200
            else:
                text_message = text_message.replace("{" + key + "}", smart_strings[key_lower])
        
        # If the message contains "/smartwords", list all smart strings.
        if "/smartwords" in text_message:
            wordbank_lines = [f"🪪 {{{k}}} - {v}" for k, v in smart_strings.items()]
            wordbank_msg = "\n".join(wordbank_lines)
            print(f"🔍 /receive_telegram_message: Sending smartwords:\n{wordbank_msg}", flush=True)
            send_to_telegram(wordbank_msg)
            return {"status": "Smartwords sent"}, 200

        # If the message contains "/diary", fetch and list all diary notes.
        if "/diary" in text_message:
            print("🔍 /receive_telegram_message: /diary command detected.", flush=True)
            conn = get_db_connection()
            if not conn:
                return {"error": "DB connection failed"}, 200
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT simp_id, simp_name, notes, subscription FROM simps ORDER BY simp_id DESC")
                records = cursor.fetchall()
            except Exception as e:
                cursor.close()
                conn.close()
                return {"error": "DB query failed"}, 200
            cursor.close()
            conn.close()
            if not records:
                reply_message = "No diary notes found."
            else:
                lines = []
                for rec in records:
                    simp_id, simp_name, notes, subscription = rec
                    emoji = select_emoji(subscription)
                    note_field = notes if notes else "empty"
                    line = f"{emoji} {simp_id} | {simp_name} | 📔 {note_field}"
                    lines.append(line)
                reply_message = "\n".join(lines)
            print(f"🔍 /receive_telegram_message: Sending diary reply:\n{reply_message}", flush=True)
            send_to_telegram(reply_message)
            return {"status": "Diary reply sent"}, 200

        # If the message contains "/note", trigger diary update mode.
        if "/note" in text_message:
            print("🔍 /receive_telegram_message: /note command detected.", flush=True)
            send_to_telegram("✍🏼When you're ready, leave a note on a simp. (e.g. \"8 gets paid on thursdays\")")
            pending_diary = True
            return {"status": "Diary update mode activated"}, 200

        # If diary update mode is pending, process the diary update.
        if pending_diary:
            m = re.match(r'^\s*(\d+)\s*(.*)', text_message)
            if not m:
                print("❌ /receive_telegram_message: Could not extract simp_id from diary update.", flush=True)
                return {"error": "Could not extract simp_id"}, 200
            simp_id_int = int(m.group(1))
            note_text = m.group(2)
            conn = get_db_connection()
            if not conn:
                return {"error": "DB connection failed"}, 200
            cursor = conn.cursor()
            try:
                cursor.execute("UPDATE simps SET notes = %s WHERE simp_id = %s", (note_text, simp_id_int))
                conn.commit()
                print(f"🔍 /receive_telegram_message: Updated notes for simp_id {simp_id_int} with note: {note_text}", flush=True)
            except Exception as e:
                cursor.close()
                conn.close()
                print(f"❌ /receive_telegram_message: DB update error in diary update: {e}", flush=True)
                return {"error": "DB update failed"}, 200
            cursor.close()
            conn.close()
            # Retrieve simp_name for confirmation.
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("SELECT simp_name FROM simps WHERE simp_id = %s", (simp_id_int,))
                    result = cursor.fetchone()
                    simp_name = result[0] if result else f"ID {simp_id_int}"
                except Exception as e:
                    simp_name = f"ID {simp_id_int}"
                cursor.close()
                conn.close()
            else:
                simp_name = f"ID {simp_id_int}"
            response_text = f"{random.choice(diary_responses)} Updated {simp_name} successfully."
            send_to_telegram(response_text)
            pending_diary = False
            return {"status": "Diary note updated"}, 200

        # If the message contains "/fetchsimps", fetch and list all records (excluding diary notes).
        if "/fetchsimps" in text_message:
            print("🔍 /receive_telegram_message: /fetchsimps command detected.", flush=True)
            conn = get_db_connection()
            if not conn:
                return {"error": "DB connection failed"}, 200
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT simp_id, simp_name, intent, subscription, duration FROM simps ORDER BY simp_id DESC")
                records = cursor.fetchall()
            except Exception as e:
                cursor.close()
                conn.close()
                return {"error": "DB query failed"}, 200
            cursor.close()
            conn.close()
            if not records:
                reply_message = "No simps found."
            else:
                lines = []
                for rec in records:
                    simp_id, simp_name, intent, subscription, duration = rec
                    emoji = select_emoji(subscription)
                    line = f"{emoji} {simp_id} | {simp_name} | {intent} | {duration} days"
                    lines.append(line)
                reply_message = "\n".join(lines)
            print(f"🔍 /receive_telegram_message: Sending fetchsimps reply:\n{reply_message}", flush=True)
            send_to_telegram(reply_message)
            return {"status": "Fetchsimps trigger sent"}, 200

        # Process as a regular text message.
        # Extract only the leading number as the simp_id and remove it from the message.
        m = re.match(r'^\s*(\d+)\s*(.*)', text_message)
        if not m:
            print("❌ /receive_telegram_message: Could not extract simp_id from message.", flush=True)
            return {"error": "Could not extract simp_id"}, 200
        simp_id_int = int(m.group(1))
        cleaned_message = m.group(2)  # The remaining message text after the simp_id
        
        conn = get_db_connection()
        if not conn:
            return {"error": "DB connection failed"}, 200
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT phone, subscription, simp_name FROM simps WHERE simp_id = %s", (simp_id_int,))
            record = cursor.fetchone()
        except Exception as e:
            cursor.close()
            conn.close()
            return {"error": "DB query failed"}, 200
        cursor.close()
        conn.close()
        if record:
            phone, subscription, simp_name = record
            emoji = select_emoji(subscription)
            # Final message excludes simp_id, simp_name, and emoji.
            final_message = f"{cleaned_message}"
            print(f"🔍 /receive_telegram_message: Sending payload to Macrodroid: {final_message}", flush=True)
            payload = {"phone": phone, "message": final_message}
            try:
                response = requests.post(MACROTRIGGER_URL, json=payload)
                print(f"🔍 /receive_telegram_message: Sent payload, response: {response.text}", flush=True)
            except Exception as e:
                return {"error": "Failed to send to Macrodroid"}, 200
            return {"status": "Trigger sent"}, 200
        else:
            return {"error": "No record found for simp_id"}, 200

    return app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
