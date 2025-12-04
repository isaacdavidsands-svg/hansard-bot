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
    
    if not keywords:
        return text
        
    return " ".join(keywords)

def split_sentences(text):
    return re.split(r'(?<=[.!?])\s+', text)

def get_debate_context(query):
    """
    (Disabled) Uses DuckDuckGo to find the official debate title or context.
    Returns (context_snippet, expanded_query_terms)
    """
    return "", ""

def generate_ai_summary(query, snippets):
    """
    (Disabled) Uses DuckDuckGo AI Chat to synthesize a stance analysis for each MP.
    """
    return ""

def search_theyworkforyou(raw_query):
    # 1. Smart Filter: Clean the query
    base_keywords = extract_keywords(raw_query)
    
    # 2. Web Context Expansion (Disabled)
    web_context_msg, extra_context_title = get_debate_context(base_keywords)
    
    search_terms = base_keywords.lower().split()
    if extra_context_title:
        search_terms.extend(extra_context_title.lower().split())
    
    search_terms = list(set(search_terms))

    url = "https://www.theyworkforyou.com/api/getHansard"
    params = {
        "key": TWFY_API_KEY,
        "search": base_keywords, 
        "output": "js",
        "num": 50
    }

    if not TWFY_API_KEY:
        return "⚠️ Configuration Error: API Key is missing."

    try:
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code != 200:
            return f"⚠️ API Error: Received status code {response.status_code} from TheyWorkForYou."

        data = response.json()
        
        if "rows" not in data or len(data["rows"]) == 0:
            return f"{web_context_msg}I searched the records for '{base_keywords}', but found no recent mentions."

        # Count speakers and store relevant snippets
        strict_counts = {}
        fuzzy_counts = {}
        loose_counts = {}
        debate_info = {} 

        for item in data["rows"]:
            speaker_name = "Unknown MP"
            party = "Unknown Party"
            constituency = ""
            
            if "speaker" in item and isinstance(item["speaker"], dict):
                first = item['speaker'].get('first_name', '')
                last = item['speaker'].get('last_name', '')
                speaker_name = f"{first} {last}".strip()
                party = item['speaker'].get('party', 'Unknown Party')
                constituency = item['speaker'].get('constituency', '')
            
            if not speaker_name or speaker_name == "Unknown MP": 
                continue

            body_text = item.get("body", "")
            debate_title = ""
            if "parent" in item and isinstance(item["parent"], dict):
                debate_title = item["parent"].get("body", "")

            sentences = split_sentences(body_text)
            
            # --- SCORING LOGIC ---
            best_snippet = ""
            max_score = 0
            
            def calculate_score(text_window):
                context = f"{debate_title} {text_window} {extra_context_title}".lower()
                user_terms = base_keywords.lower().split()
                matches = sum(1 for term in user_terms if term in context)
                return matches / len(user_terms) if user_terms else 0

            if len(sentences) <= 3:
                max_score = calculate_score(body_text)
                best_snippet = body_text
            else:
                for i in range(len(sentences) - 2):
                    window = " ".join(sentences[i:i+3])
                    score = calculate_score(window)
                    if score > max_score:
                        max_score = score
                        best_snippet = window + "..."
            
            # --- CLASSIFICATION ---
            list_url = item.get('listurl', '')
            info_payload = {
                "link": f"https://www.theyworkforyou.com{list_url}", 
                "snippet": best_snippet,
                "title": debate_title,
                "party": party,
                "constituency": constituency
            }

            if max_score == 1.0: # STRICT: 100% Match
                if speaker_name in strict_counts: strict_counts[speaker_name] += 1
                else: strict_counts[speaker_name] = 1
                
                if speaker_name not in debate_info or debate_info[speaker_name]["type"] != "Strict":
                    info_payload["type"] = "Strict"
                    debate_info[speaker_name] = info_payload
                    
            elif max_score >= 0.5: # FUZZY: >50% Match
                if speaker_name in fuzzy_counts: fuzzy_counts[speaker_name] += 1
                else: fuzzy_counts[speaker_name] = 1
                
                if speaker_name not in debate_info or debate_info[speaker_name]["type"] == "Loose":
                    info_payload["type"] = "Fuzzy"
                    debate_info[speaker_name] = info_payload
            
            else: # LOOSE: Fallback
                if speaker_name in loose_counts: loose_counts[speaker_name] += 1
                else: loose_counts[speaker_name] = 1
                
                if speaker_name not in debate_info:
                    info_payload["type"] = "Loose"
                    info_payload["snippet"] = body_text[:200] + "..." # Use generic snippet for loose
                    debate_info[speaker_name] = info_payload

        # Decide which results to show (Priority: Strict > Fuzzy > Loose)
        if strict_counts:
            sorted_speakers = sorted(strict_counts.items(), key=lambda item: item[1], reverse=True)[:5]
            result_type = "Strict"
        elif fuzzy_counts:
            sorted_speakers = sorted(fuzzy_counts.items(), key=lambda item: item[1], reverse=True)[:5]
            result_type = "Fuzzy"
        elif loose_counts:
            sorted_speakers = sorted(loose_counts.items(), key=lambda item: item[1], reverse=True)[:5]
            result_type = "Loose"
        else:
            return f"{web_context_msg}I searched for '{base_keywords}', but found no matches even with loose filtering."

        # --- AI SUMMARIZATION (Disabled) ---
        ai_summary = ""
        
        # Build message
        safe_query = html.escape(base_keywords)
        top_mp, top_count = sorted_speakers[0]
        safe_top_mp = html.escape(top_mp)
        
        if ai_summary:
            message = ai_summary
        else:
            # message = web_context_msg + "⚠️ <i>(AI Analysis unavailable, showing raw results)</i>\n\n"
            message = web_context_msg # Simplified message since AI is permanently disabled
        
        if result_type == "Strict":
            message += f"🏆 <b>{safe_top_mp}</b> is the leading voice on '<i>{safe_query}</i>', with {top_count} verified mentions.\n\n"
            message += "<b>Top Speakers & Context:</b>\n"
        elif result_type == "Fuzzy":
             message += f"⚠️ <b>Note:</b> Exact phrase not found. Showing best partial matches for '<i>{safe_query}</i>'.\n"
             message += f"🏆 <b>{safe_top_mp}</b> has {top_count} relevant mentions.\n\n"
        else:
            message += f"⚠️ <b>Note:</b> Keywords not found closely together. Showing general mentions for '<i>{safe_query}</i>'.\n"
            message += f"🏆 <b>{safe_top_mp}</b> has {top_count} mentions.\n\n"

        rank = 1
        for speaker, count in sorted_speakers:
            info = debate_info.get(speaker, {})
            link = info.get("link", "")
            snippet = info.get("snippet", "No preview")
            title = info.get("title", "Unknown Debate")
            party = info.get("party", "Unknown")
            
            if len(snippet) > 150: snippet = snippet[:150] + "..."
                
            safe_speaker = html.escape(speaker)
            safe_link = html.escape(link)
            safe_snippet = html.escape(snippet)
            safe_title = html.escape(title)
            safe_party = html.escape(party)
            
            google_query = requests.utils.quote(f"{title} UK Parliament")
            google_link = f"https://www.google.com/search?q={google_query}"
            
            message += f"{rank}. <b>{safe_speaker}</b> ({safe_party}) ({count})\n"
            message += f"  📂 <i>{safe_title}</i> | <a href='{google_link}'>🌍 Context</a>\n"
            message += f"  💬 <i>\"{safe_snippet}\"</i>\n"
            message += f"  <a href='{safe_link}'>Read Speech</a>\n\n"
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
