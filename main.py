import os
import logging
import threading
import requests
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Environment variables
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_KEY = os.environ.get("THEYWORKFORYOU_API_KEY")
PORT = int(os.environ.get("PORT", 5000))

# Flask App for Health Check
app = Flask(__name__)

@app.route('/')
def health_check():
    return "The Hansard Bot is alive!"

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

# Telegram Bot Logic
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Welcome to The Hansard Bot! Ask me something like 'Who spoke about teenage boys in care last year?'"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    if not query:
        return

    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Searching Hansard for: '{query}'...")

    try:
        # Query TheyWorkForYou API
        url = "https://www.theyworkforyou.com/api/getHansard"
        params = {
            "key": API_KEY,
            "search": query,
            "output": "js"
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if not data or "rows" not in data:
             await context.bot.send_message(chat_id=update.effective_chat.id, text="No results found or invalid response.")
             return

        rows = data["rows"]
        if not rows:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="No debates found matching your query.")
            return

        # Deduplicate speakers and collect debates
        # We'll show the top 5 distinct debates/speakers to avoid spamming
        results = []
        seen_speakers_in_debate = set()
        
        count = 0
        for row in rows:
            if count >= 5:
                break
            
            speaker = row.get("speaker", {}).get("name", "Unknown MP")
            body = row.get("body", "No content")
            listurl = row.get("listurl", "")
            
            # Simple deduplication key: speaker + listurl (debate thread)
            # This prevents listing the same MP multiple times for the same debate thread
            # But allows the same MP for different debates, or different MPs for the same debate.
            # However, the user requirement says "Deduplicate speakers (if an MP spoke 5 times in one debate, list them once)."
            
            # Let's group by debate (listurl) maybe? 
            # Or just list distinct speakers per debate.
            
            # Let's try to present a list of "Speaker in Debate"
            
            unique_key = f"{speaker}_{listurl}"
            if unique_key in seen_speakers_in_debate:
                continue
            
            seen_speakers_in_debate.add(unique_key)
            
            # Clean up body text a bit (it might be HTML)
            # For simplicity, we just take the first 100 chars
            summary = body[:100] + "..." if len(body) > 100 else body
            
            results.append(f"🗣 **{speaker}**\n📄 {summary}\n🔗 https://www.theyworkforyou.com{listurl}")
            count += 1

        if results:
            response_text = "\n\n".join(results)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=response_text, parse_mode='Markdown')
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="No relevant speakers found in the top results.")

    except Exception as e:
        logging.error(f"Error querying API: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Sorry, something went wrong while searching.")

if __name__ == '__main__':
    if not TOKEN or not API_KEY:
        print("Error: TELEGRAM_BOT_TOKEN and THEYWORKFORYOU_API_KEY must be set.")
    else:
        # Start Flask in a separate thread
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()

        # Start Telegram Bot
        application = ApplicationBuilder().token(TOKEN).build()
        
        start_handler = CommandHandler('start', start)
        message_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
        
        application.add_handler(start_handler)
        application.add_handler(message_handler)
        
        application.run_polling()
