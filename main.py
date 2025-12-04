import os
import logging
import threading
import requests
import html  # Added to safely escape special characters
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
    return "The Hansard is Active."

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
    
    # If we stripped everything (e.g. user just said "Who?"), fall back to original
    if not keywords:
        return text
        
    return " ".join(keywords)

def split_sentences(text):
    # Simple regex to split by . ! ? followed by space or end of string
    return re.split(r'(?<=[.!?])\s+', text)

def search_theyworkforyou(raw_query):
    # 1. Smart Filter: Clean the query
    query = extract_keywords(raw_query)
    search_terms = query.lower().split()
    
    url = "https://www.theyworkforyou.com/api/getHansard"
    params = {
        "key": TWFY_API_KEY,
        "search": query,
        "output": "js",
        "num": 50
    }

    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if "rows" not in data or len(data["rows"]) == 0:
            return "I searched the records, but found no recent mentions of that topic."

        # Count speakers and store relevant snippets
        speaker_counts = {}
        debate_info = {} # speaker -> {link, snippet, title}

        for item in data["rows"]:
            speaker_name = "Unknown MP"
            if "speaker" in item and isinstance(item["speaker"], dict):
                first = item['speaker'].get('first_name', '')
                last = item['speaker'].get('last_name', '')
                speaker_name = f"{first} {last}".strip()
            
            if not speaker_name or speaker_name == "Unknown MP": 
                continue

            # --- CONTEXT WINDOW VALIDATION ---
            body_text = item.get("body", "")
            # Get Debate Title (parent clause)
            debate_title = ""
            if "parent" in item and isinstance(item["parent"], dict):
                debate_title = item["parent"].get("body", "")
            
            # Combine Title + Body for context check
            # We treat the title as the "zeroth" sentence
            sentences = split_sentences(body_text)
            if debate_title:
                sentences.insert(0, f"[{debate_title}]")
            
            found_match = False
            relevant_snippet = ""

            # Sliding window of 3 sentences
            if len(sentences) <= 3:
                window = " ".join(sentences)
                if all(term in window.lower() for term in search_terms):
                    found_match = True
                    relevant_snippet = window
            else:
                for i in range(len(sentences) - 2):
                    window_sentences = sentences[i:i+3]
                    window = " ".join(window_sentences)
                    if all(term in window.lower() for term in search_terms):
                        found_match = True
                        relevant_snippet = window + "..."
                        break 
            
            if not found_match:
                continue
            # -----------------------------

            if speaker_name in speaker_counts:
                speaker_counts[speaker_name] += 1
            else:
                speaker_counts[speaker_name] = 1
                list_url = item.get('listurl', '')
                full_link = f"https://www.theyworkforyou.com{list_url}"
                debate_info[speaker_name] = {
                    "link": full_link, 
                    "snippet": relevant_snippet,
                    "title": debate_title
                }

        # Sort top 5
        sorted_speakers = sorted(speaker_counts.items(), key=lambda item: item[1], reverse=True)[:5]

        # Build message using HTML
        safe_query = html.escape(query)
        
        if sorted_speakers:
            top_mp, top_count = sorted_speakers[0]
            safe_top_mp = html.escape(top_mp)
            message = f"🏆 <b>{safe_top_mp}</b> is the leading voice on '<i>{safe_query}</i>', with {top_count} verified mentions.\n\n"
        else:
            return f"I found debates matching '{safe_query}', but none of them contained the keywords closely together (checking Speech + Debate Topic). Try fewer keywords."

        message += "<b>Top Speakers & Context:</b>\n"

        for speaker, count in sorted_speakers:
            info = debate_info.get(speaker, {})
            link = info.get("link", "")
            snippet = info.get("snippet", "No preview")
            title = info.get("title", "Unknown Debate")
            
            # Truncate snippet if too long
            if len(snippet) > 150:
                snippet = snippet[:150] + "..."
                
            safe_speaker = html.escape(speaker)
            safe_link = html.escape(link)
            safe_snippet = html.escape(snippet)
            safe_title = html.escape(title)
            
            message += f"• <b>{safe_speaker}</b> ({count})\n"
            message += f"  📂 <i>{safe_title}</i>\n"
            message += f"  💬 <i>\"{safe_snippet}\"</i>\n"
            message += f"  <a href='{safe_link}'>Read Speech</a>\n\n"

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