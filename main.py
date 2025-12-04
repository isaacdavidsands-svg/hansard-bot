import os
import logging
import threading
import requests
import html
import re
from flask import Flask
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TWFY_API_KEY = os.getenv("THEYWORKFORYOU_API_KEY")

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- FLASK KEEP-ALIVE SERVER ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "The Hansard is Active (v3 - Simple Count)."

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- THE HANSARD LOGIC ---

def extract_keywords(text):
    """
    Extracts core topic keywords from a natural language query.
    Removes common stop words and question phrasing.
    """
    stop_words = {
        "who", "spoke", "about", "the", "most", "in", "context", "of", 
        "what", "did", "say", "regarding", "concerning", "a", "an", "and", 
        "to", "for", "with", "by", "on", "is", "are", "was", "were"
    }
    
    # Keep alphanumeric words, preserve simple structure
    words = text.lower().split()
    keywords = [w for w in words if w.strip("?,.!").lower() not in stop_words]
    
    if not keywords:
        return text
        
    return " ".join(keywords)

def search_theyworkforyou(raw_query):
    # 1. Clean the query
    base_keywords = extract_keywords(raw_query)
    
    url = "https://www.theyworkforyou.com/api/getHansard"
    params = {
        "key": TWFY_API_KEY,
        "search": base_keywords, 
        "output": "js",
        "num": 100 # Fetch more results to get a better count
    }

    if not TWFY_API_KEY:
        return "⚠️ Configuration Error: API Key is missing."

    try:
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code != 200:
            return f"⚠️ API Error: Received status code {response.status_code} from TheyWorkForYou."

        data = response.json()
        
        if "rows" not in data or len(data["rows"]) == 0:
             return f"I searched the records for '{base_keywords}', but found no mentions."

        # --- SIMPLE COUNTING LOGIC ---
        speaker_counts = {}
        speaker_details = {}

        for item in data["rows"]:
            # Try to extract speaker name from various possible locations
            name = "Unknown"
            party = "Unknown Party"
            
            # 1. Check 'speaker' dictionary
            if "speaker" in item and isinstance(item["speaker"], dict):
                spk = item["speaker"]
                if "name" in spk:
                    name = spk["name"]
                elif "first_name" in spk or "last_name" in spk:
                    name = f"{spk.get('first_name', '')} {spk.get('last_name', '')}".strip()
                
                party = spk.get("party", party)
            
            # 2. If not found, check if 'speaker' is just a string name
            elif "speaker" in item and isinstance(item["speaker"], str):
                name = item["speaker"]

            # 3. Fallback: Check parent (sometimes context has the speaker)
            if (not name or name == "Unknown") and "parent" in item and isinstance(item["parent"], dict):
                parent = item["parent"]
                if "speaker" in parent and isinstance(parent["speaker"], dict):
                    spk = parent["speaker"]
                    name = f"{spk.get('first_name', '')} {spk.get('last_name', '')}".strip()
                    party = spk.get("party", party)

            if not name or name == "Unknown":
                continue

            # Count occurrences
            speaker_counts[name] = speaker_counts.get(name, 0) + 1
            
            # Store details (party, link) if we haven't seen this speaker yet
            if name not in speaker_details:
                list_url = item.get('listurl', '')
                speaker_details[name] = {
                    "party": party,
                    "link": f"https://www.theyworkforyou.com{list_url}"
                }

        # Sort speakers by count (highest first)
        sorted_speakers = sorted(speaker_counts.items(), key=lambda item: item[1], reverse=True)[:10]

        if not sorted_speakers:
             return f"I found mentions of '{base_keywords}', but couldn't identify specific speakers."

        # Build the simple message
        safe_query = html.escape(base_keywords)
        message = f"📊 <b>Mentions of '<i>{safe_query}</i>':</b>\n\n"
        
        rank = 1
        for name, count in sorted_speakers:
            details = speaker_details.get(name, {})
            party = details.get("party", "Unknown")
            link = details.get("link", "")
            
            safe_name = html.escape(name)
            safe_party = html.escape(party)
            safe_link = html.escape(link)
            
            message += f"{rank}. <b>{safe_name}</b> ({safe_party}) - <b>{count}</b> times\n"
            message += f"   <a href='{safe_link}'>View Speeches</a>\n\n"
            rank += 1

        return message

    except requests.exceptions.Timeout:
        logging.error("Request timed out.")
        return "⚠️ Error: The parliamentary archives are taking too long to respond. Please try again."
    except requests.exceptions.ConnectionError:
        logging.error("Connection error.")
        return "⚠️ Error: Could not connect to the server. Please check your internet connection."
    except Exception as e:
        logging.error(f"Error fetching data: {e}")
        return f"⚠️ An error occurred: {str(e)}"

# --- TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏛 <b>The Hansard Initialized.</b>\n"
        "<i>Version: Simple Count v1</i>\n\n"
        "I am your parliamentary clerk. Tell me a topic (e.g. 'teenage boys care system') "
        "and I will tell you who is talking about it.",
        parse_mode=ParseMode.HTML
    )

async def version_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 <b>Bot Version:</b> Simple Count v1 (Direct API)", parse_mode=ParseMode.HTML)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_query = update.message.text
    status_msg = await update.message.reply_text("🔍 Searching the records...")
    
    # Run search
    result_text = search_theyworkforyou(user_query)
    
    # Use ParseMode.HTML
    await status_msg.edit_text(result_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# --- MAIN EXECUTION ---

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found.")
    else:
        application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        application.add_handler(CommandHandler('start', start))
        application.add_handler(CommandHandler('version', version_check))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        application.run_polling()