from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
# No need to import datetime, the library handles it for you!

async def track_replies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        # 1. Get the senders and text
        replier = update.message.from_user.username
        reply_text = update.message.text
        original_text = update.message.reply_to_message.text
        
        # 2. Get the exact time the messages were dropped
        # This returns a timezone-aware datetime object (UTC by default)
        reply_time = update.message.date 
        original_time = update.message.reply_to_message.date
        
        # 3. Calculate how long it took to reply (optional but useful!)
        response_time = reply_time - original_time
        
        # 4. Format the time to be human-readable (e.g., YYYY-MM-DD HH:MM:SS)
        formatted_reply_time = reply_time.strftime("%Y-%m-%d %H:%M:%S")
        
        print(f"User @{replier} replied at {formatted_reply_time}.")
        print(f"It took them {response_time} to reply to the original message.")
        print(f"Reply Text: {reply_text}")

# Setup the bot application
app = Application.builder().token("YOUR_BOT_TOKEN").build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_replies))
app.run_polling()