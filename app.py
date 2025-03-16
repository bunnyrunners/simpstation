import os
import re
import random
import time
import threading
import io
import uuid
import psycopg2
import requests
from flask import Flask, request
from pydub import AudioSegment

# Google Drive API imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# Environment variables
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL URL from Render
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ElevenLabs credentials (for voice generation)
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")

# Google Drive folder ID for storing voice files (the "Voice" folder)
DRIVE_VOICE_FOLDER_ID = os.getenv("DRIVE_VOICE_FOLDER_ID")

# Base URL for Macrodroid endpoints.
# For audio messages, we send to /getaudio; for text messages, /reply.
MACROTRIGGER_BASE_URL = "https://trigger.macrodroid.com/9ddf8fe0-30cd-4343-b88a-4d14641c850f"

# Scopes for Google Drive
SCOPES = ['https://www.googleapis.com/auth/drive.file']

# In-memory store for processed Telegram update IDs (to avoid duplicate processing)
processed_updates = set()

# Global flag for diary update mode (triggered by /note command)
pending_diary = False

# Global pending voice message store (for ElevenLabs voice integration)
# It will store: simp_id (if extracted), voice_text (cleaned text sent to ElevenLabs),
# voice_data (binary at 320kbps), and phone (intended recipient's phone)
pending_voice = None

# Smart strings dictionary (used for text messages)
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
    "Well, damn! 😏",
    "Respect! 🙌",
    "Not bad! 😎",
    "Let’s go! 🚀",
    "That’s insane! 🤪",
    "You got it! 💪",
    "Sounds good! 🎵",
    "Oh heck! 😮",
    "Whoa, okay! 😵‍💫",
    "True that! 📜",
    "Oh snap! 📸",
    "Can’t lie! 🤥",
    "That’s crazy! 🌀",
    "Say less! 🤫",
    "Alright then! 🤷‍♂️",
    "Big mood! 🎭",
    "Sheesh! 🥶",
    "Wild stuff! 🦁",
    "Love that! 💖",
    "I’m shook! 🌊",
    "Facts! 🔎",
    "Big vibes! ✨",
    "Bet! 🎲",
    "Oh shoot! 🎯",
    "So true! ✅",
    "Good call! 📞",
    "Absolutely! 💯",
    "I see! 👁️",
    "That’s deep! 🌊",
    "Wow, okay! 😮‍💨",
    "Makes sense! 🤓",
    "That tracks! 🚆",
    "No doubt! 🤝",
    "I feel that! 🎶",
    "Well, alright! 🤠",
    "That’s cool! ❄️",
    "Big energy! ⚡",
    "Say what! 🤨",
    "Go off! 🔥",
    "So be it! 🕊️",
    "Okay then! 🤔"
]

# ---------- Google Drive Service Functions ----------
import json
from google.oauth2.service_account import Credentials

def get_drive_service():
    service_account_info = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not service_account_info:
        raise Exception("Service account credentials not provided in environment variables.")
    service_account_info = json.loads(service_account_info)
    creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    service = build('drive', 'v3', credentials=creds)
    return service


def upload_audio_to_gdrive(audio_data, file_name):
    """
    Uploads audio_data to Google Drive in the "Voice" folder,
    names the file as file_name, and returns its public download URL.
    """
    service = get_drive_service()
    file_metadata = {
        'name': file_name,
        'parents': [DRIVE_VOICE_FOLDER_ID]
    }
    media = MediaIoBaseUpload(io.BytesIO(audio_data), mimetype='audio/mpeg')
    file = service.files().create(body=file_metadata,
                                  media_body=media,
                                  fields='id').execute()
    file_id = file.get('id')
    permission = {'type': 'anyone', 'role': 'reader'}
    service.permissions().create(fileId=file_id, body=permission).execute()
    file_info = service.files().get(fileId=file_id, fields='webContentLink').execute()
    audio_url = file_info.get('webContentLink')
    print(f"DEBUG: Uploaded audio to Google Drive as '{file_name}', URL: {audio_url}", flush=True)
    return audio_url

# ---------- Audio Processing Functions ----------
def compress_audio(audio_data, target_bitrate="320k"):
    try:
        audio = AudioSegment.from_file(io.BytesIO(audio_data), format="mp3")
        output_buffer = io.BytesIO()
        audio.export(output_buffer, format="mp3", bitrate=target_bitrate)
        compressed_data = output_buffer.getvalue()
        print(f"DEBUG: Compressed audio to {len(compressed_data)} bytes", flush=True)
        return compressed_data
    except Exception as e:
        print(f"❌ Error compressing audio: {e}", flush=True)
        return audio_data

def generate_voice_message(voice_text):
    elevenlabs_url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "PostmanRuntime/7.43.0"
    }
    data = {
        "text": voice_text,
        "voice_settings": {
            "stability": 0.26,
            "similarity_boost": 0.51,
            "speed": 0.76,
            "style": 0.31,
            "model_id": "eleven_multilingual_v2"
        }
    }
    response = requests.post(elevenlabs_url, json=data, headers=headers)
    print(f"DEBUG: ElevenLabs response status: {response.status_code}", flush=True)
    print(f"DEBUG: ElevenLabs response length: {len(response.content)} bytes", flush=True)
    if response.status_code == 200:
        compressed = compress_audio(response.content, target_bitrate="320k")
        return compressed
    else:
        print(f"❌ ElevenLabs: Error generating voice message: {response.text}", flush=True)
        return None


# ---------- Messaging Functions ----------
def send_to_telegram(message):
    print(f"🔍 Telegram: Sending text: '{message}'", flush=True)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    response = requests.post(url, json=payload)
    print(f"🔍 Telegram: Sent text, response: {response.text}", flush=True)

def send_voice_to_telegram(audio_data, caption="Yay or nay?"):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAudio"
    files = {"audio": ("voice.mp3", audio_data, "audio/mpeg")}
    data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
    response = requests.post(url, data=data, files=files)
    print(f"DEBUG: send_voice_to_telegram response: {response.text}", flush=True)

def send_voice_url_to_macrodroid(audio_url, phone, cleaned_text):
    endpoint = f"{MACROTRIGGER_BASE_URL}/getaudio"
    payload = {
        "phone": phone,
        "message": cleaned_text,
        "audio_url": audio_url
    }
    response = requests.post(endpoint, json=payload)
    print(f"DEBUG: send_voice_url_to_macrodroid response: {response.text}", flush=True)
    return response


# ---------- Database and Airtable Sync Functions ----------
def get_db_connection():
    try:
        print("🔍 DB: Attempting connection...", flush=True)
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        print("✅ DB: Connected.", flush=True)
        return conn
    except Exception as e:
        print(f"❌ DB: Connection failed: {e}", flush=True)
        return None

def init_db():
    print("🔍 DB: Initializing...", flush=True)
    conn = get_db_connection()
    if not conn:
        print("❌ DB: No connection.", flush=True)
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
        print("✅ DB: Table ensured.", flush=True)
    except Exception as e:
        print(f"❌ DB: Error: {e}", flush=True)
    try:
        cursor.execute("ALTER TABLE simps ADD COLUMN IF NOT EXISTS subscription NUMERIC")
        conn.commit()
        print("✅ DB: 'subscription' column ensured.", flush=True)
    except Exception as e:
        print(f"⚠️ DB: Could not alter 'subscription': {e}", flush=True)
    try:
        cursor.execute("ALTER TABLE simps ADD COLUMN IF NOT EXISTS notes TEXT")
        conn.commit()
        print("✅ DB: 'notes' column ensured.", flush=True)
    except Exception as e:
        print(f"⚠️ DB: Could not alter 'notes': {e}", flush=True)
    try:
        cursor.execute("ALTER TABLE simps ALTER COLUMN phone TYPE TEXT USING phone::text;")
        conn.commit()
        print("✅ DB: 'phone' column ensured as TEXT.", flush=True)
    except Exception as e:
        print(f"⚠️ DB: Could not alter 'phone' column: {e}", flush=True)
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
        print(f"❌ Sync: Airtable error: {response.text}", flush=True)
        return
    records = response.json().get("records", [])
    print(f"🔍 Sync: Retrieved {len(records)} records.", flush=True)
    conn = get_db_connection()
    if not conn:
        print("❌ Sync: No DB connection.", flush=True)
        return
    cursor = conn.cursor()
    cursor.execute("DELETE FROM simps")
    for record in records:
        fields = record.get("fields", {})
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
                print(f"❌ Sync: Error processing Subscription: {e}", flush=True)
                sub_value = None
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
            print(f"✅ Sync: Record inserted/updated for simp_id: {fields.get('Simp_ID')}", flush=True)
        except Exception as e:
            print(f"❌ Sync: Error inserting record: {e}", flush=True)
    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Sync: Airtable sync complete!", flush=True)

# ---------- Periodic Sync ----------
def run_periodic_sync():
    while True:
        time.sleep(1800)
        print("🔍 Periodic sync triggered.", flush=True)
        sync_airtable_to_postgres()

# ---------- Flask App ----------
def create_app():
    app = Flask(__name__)
    print(f"🔍 App: DATABASE_URL = {DATABASE_URL}", flush=True)
    if not DATABASE_URL:
        raise Exception("❌ App: DATABASE_URL not set!")
    with app.app_context():
        init_db()
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
            emoji = ""  # For text messages, adjust as desired.
            m = re.match(r'^\s*\d+\s*(.*)', text_message)
            cleaned_message = m.group(1) if m else text_message
            formatted_message = f"{emoji}{simp_id} | {simp_name}: {cleaned_message}"
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
        global pending_diary, pending_voice
        print("🔍 /receive_telegram_message: Received a POST request", flush=True)
        update = request.json
        print(f"🔍 /receive_telegram_message: Update received: {update}", flush=True)
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

        # If a confirmation command ("send", "next", "cancel") is received but no pending voice exists:
        if text_message.lower() in ["send", "next", "cancel"] and not pending_voice:
            send_to_telegram("No pending voice message.")
            return {"status": "No pending voice message"}, 200

        # Voice message command handling: expected format "prefix v/voice_text"
        if "v/" in text_message:
            parts = text_message.split("v/", 1)
            prefix = parts[0].strip()   # Intended recipient info, e.g., "13"
            voice_text = parts[1].strip()  # Text to be synthesized
            phone = ""
            simp_id = None
            if prefix:
                m = re.match(r'^(\d+)', prefix)
                if m:
                    simp_id = int(m.group(1))
                    conn = get_db_connection()
                    if conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT phone FROM simps WHERE simp_id = %s", (simp_id,))
                        record = cursor.fetchone()
                        cursor.close()
                        conn.close()
                        if record:
                            phone = record[0]
            pending_voice = {
                "simp_id": simp_id,
                "voice_text": voice_text,
                "voice_data": generate_voice_message(voice_text),
                "phone": phone
            }
            if pending_voice["voice_data"]:
                # Send an audio preview with caption "Yay or nay?"
                send_voice_to_telegram(pending_voice["voice_data"], caption="Yay or nay?")
                return {"status": "Voice generation triggered, awaiting confirmation"}, 200
            else:
                send_to_telegram("Error generating voice message.")
                return {"error": "Voice generation failed"}, 200

        # Handle confirmation for pending voice message
        if pending_voice and text_message.lower() in ["send", "next", "cancel"]:
            if text_message.lower() == "send":
                file_name = pending_voice["voice_text"].replace(" ", "_") + ".mp3"
                gdrive_url = upload_audio_to_gdrive(pending_voice["voice_data"], file_name)
                if gdrive_url:
                    phone = pending_voice.get("phone", "")
                    # Replace every space with "_" in the final voice message sent to Macrodroid
                    cleaned_text = pending_voice["voice_text"].replace(" ", "_")
                    send_voice_url_to_macrodroid(gdrive_url, phone, cleaned_text)
                    send_to_telegram("Voice message sent!")
                else:
                    send_to_telegram("Error uploading voice message to Google Drive.")
                pending_voice = None
                return {"status": "Voice message sent"}, 200
            elif text_message.lower() == "next":
                new_voice_data = generate_voice_message(pending_voice["voice_text"])
                if new_voice_data:
                    pending_voice["voice_data"] = new_voice_data
                    next_captions = [
                        "Good or garbage? 🗑️",
                        "Approve or disapprove? ✅",
                        "Delete this? 🤔",
                        "Fire or flop? 🔥",
                        "Worth sending? 📤",
                        "Should I be embarrassed? 😳",
                        "Thoughts? 💭",
                        "Did I ruin everything? 😬",
                        "Rate this: 10 or 0? 🌟",
                        "Would you reply? 📩",
                        "Decent or disaster? 🚀",
                        "Listenable or unbearable? 🎧",
                        "Love it or leave? ❤️",
                        "Forward this? 🔁",
                        "Forget this happened? 🤭",
                        "Will I regret this? 😓",
                        "Genius or nonsense? 🧠",
                        "Should I be proud? 🏆",
                        "Roast or respect? 🔥",
                        "Keep or delete? 💾",
                        "Send to more people? 📤",
                        "Big reaction incoming? 😮",
                        "Waste of time? ⏳",
                        "Thumbs up or down? 👍",
                        "Listen again? 🔄",
                        "Try again? 🤷",
                        "Overthinking this? 🤔",
                        "Worth a response? 📩",
                        "Listen twice? 🎧",
                        "Awful or okay? 😬",
                        "Save or scrap? 💾",
                        "Would this annoy you? 😡",
                        "Passable or pathetic? 🤨",
                        "Apology needed? 😅",
                        "Does this make sense? 🤯",
                        "Will this get laughs? 😂",
                        "Shareable or shameful? 🤦",
                        "Mom-approved? 👩‍👦",
                        "Too much? 😳",
                        "Say too much? 😶",
                        "Ignore this? 🚫",
                        "Sound normal? 🤨",
                        "Stop talking? 🤐",
                        "Argument starter? ⚡",
                        "Necessary or nah? 🤔",
                        "Rethink this? 🤦",
                        "Bold or bad? 😵",
                        "Anyone else get this? 🤷",
                        "Trash this? 🗑️",
                        "Open in public? 📢"
                    ]
                    caption = random.choice(next_captions)
                    send_voice_to_telegram(new_voice_data, caption=caption)
                else:
                    send_to_telegram("Error generating new voice message.")
                return {"status": "Voice message updated"}, 200
            elif text_message.lower() == "cancel":
                pending_voice = None
                send_to_telegram("Voice message canceled.")
                return {"status": "Voice message canceled"}, 200

        # Process other commands (smart strings, diary, etc.)
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
        
        if "/smartwords" in text_message:
            wordbank_lines = [f"🪪 {{{k}}} - {v}" for k, v in smart_strings.items()]
            wordbank_msg = "\n".join(wordbank_lines)
            print(f"🔍 /receive_telegram_message: Sending smartwords:\n{wordbank_msg}", flush=True)
            send_to_telegram(wordbank_msg)
            return {"status": "Smartwords sent"}, 200

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
                    line = f"{simp_id} | {simp_name} | {notes if notes else 'empty'}"
                    lines.append(line)
                reply_message = "\n".join(lines)
            print(f"🔍 /receive_telegram_message: Sending diary reply:\n{reply_message}", flush=True)
            send_to_telegram(reply_message)
            return {"status": "Diary reply sent"}, 200

        if "/note" in text_message:
            print("🔍 /receive_telegram_message: /note command detected.", flush=True)
            send_to_telegram("✍🏼When you're ready, leave a note on a simp. (e.g. \"8 gets paid on thursdays\")")
            pending_diary = True
            return {"status": "Diary update mode activated"}, 200

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
                print(f"❌ /receive_telegram_message: DB update error: {e}", flush=True)
                return {"error": "DB update failed"}, 200
            cursor.close()
            conn.close()
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
                    line = f"{simp_id} | {simp_name} | {intent} | {duration} days"
                    lines.append(line)
                reply_message = "\n".join(lines)
            print(f"🔍 /receive_telegram_message: Sending fetchsimps reply:\n{reply_message}", flush=True)
            send_to_telegram(reply_message)
            return {"status": "Fetchsimps trigger sent"}, 200

        m = re.match(r'^\s*(\d+)\s*(.*)', text_message)
        if not m:
            print("❌ /receive_telegram_message: Could not extract simp_id from message.", flush=True)
            return {"error": "Could not extract simp_id"}, 200
        simp_id_int = int(m.group(1))
        cleaned_message = m.group(2)
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
            final_message = f"{cleaned_message}"
            print(f"🔍 /receive_telegram_message: Sending payload to Macrodroid: {final_message}", flush=True)
            payload = {"phone": phone, "message": final_message}
            try:
                response = requests.post(MACROTRIGGER_BASE_URL + "/reply", json=payload)
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
