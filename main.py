import os
import logging
import threading
import requests
import html  # Added to safely escape special characters
from flask import Flask
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") # Updated to match your Render Variable
TWFY_API_KEY = os.getenv("THEYWORKFORYOU_API_KEY") # Updated to match your Render Variable

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- FLASK KEEP-ALIVE SERVER ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "The Hansard is Active."

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- THE HANSARD LOGIC ---

def search_theyworkforyou(query):
    url = "https://www.theyworkforyou.com/api/getHansard"
    params = {
        "key": TWFY_API_KEY,
        "search": query,
        "output": "js",
        "num": 20
    }

    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if "rows" not in data or len(data["rows"]) == 0:
            return "I searched the records, but found no recent mentions of that topic."

        # Count speakers
        speaker_counts = {}
        debate_links = {}

        for item in data["rows"]:
            # Safer speaker name extraction
            speaker_name = "Unknown MP"
            if "speaker" in item and isinstance(item["speaker"], dict):
                first = item['speaker'].get('first_name', '')
                last = item['speaker'].get('last_name', '')
                speaker_name = f"{first} {last}".strip()
            
            if not speaker_name or speaker_name == "Unknown MP": 
                continue

            if speaker_name in speaker_counts:
                speaker_counts[speaker_name] += 1
            else:
                speaker_counts[speaker_name] = 1
                # Construct the link. Sometimes API returns partial paths.
                list_url = item.get('listurl', '')
                if list_url.startswith('/'):
                    debate_links[speaker_name] = f"https://www.theyworkforyou.com{list_url}"
                else:
                    debate_links[speaker_name] = f"https://www.theyworkforyou.com{list_url}"

        # Sort top 5
        sorted_speakers = sorted(speaker_counts.items(), key=lambda item: item[1], reverse=True)[:5]

        # Build message using HTML
        # We use html.escape() to ensure names like "O'Brien" don't break things
        safe_query = html.escape(query)
        message = f"📜 <b>Hansard Search: '{safe_query}'</b>\n\n"
        message += "Most active speakers on this topic recently:\n\n"

        for speaker, count in sorted_speakers:
            link = debate_links.get(speaker, "")
            safe_speaker = html.escape(speaker)
            safe_link = html.escape(link)
            # HTML Link format: <a href="URL">TEXT</a>
            message += f"• <b>{safe_speaker}</b> ({count} mentions) - <a href='{safe_link}'>Read Speech</a>\n"

        return message

    except Exception as e:
        logging.error(f"Error fetching data: {e}")
        return "⚠️ An error occurred while consulting the archives."

# --- TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏛 <b>The Hansard Initialized.</b>\n\n"
        "I am your parliamentary clerk. Tell me a topic (e.g. 'teenage boys care system') "
        "and I will tell you who is talking about it.",
        parse_mode=ParseMode.HTML
    )

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
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        application.run_polling()