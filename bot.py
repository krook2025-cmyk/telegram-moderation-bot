import logging
import re
import json
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler
from telegram.constants import ParseMode
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration (use environment variables for Render)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
DOC_ID = os.environ.get("DOC_ID")  # Not used yet, but kept

# Google Sheets setup
worksheet = None
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    credentials = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    gc = gspread.authorize(credentials)
    worksheet = gc.open_by_key(SHEET_ID).sheet1
except Exception as e:
    logger.error(f"Error setting up Google Sheets: {e}")
    worksheet = None

# Load knowledge from credentials.json (optional knowledge base)
KNOWLEDGE_BASE = {}
try:
    with open("credentials.json", "r") as f:
        KNOWLEDGE_BASE = json.load(f)
except FileNotFoundError:
    logger.warning("credentials.json not found, using empty knowledge base")
    KNOWLEDGE_BASE = {}

# Inappropriate words list
INAPPROPRIATE_WORDS = {
    "fuck", "shit", "bitch", "asshole", "bastard", "damn", "crap", "dick", "pussy", "cock",
    "prick", "porn", "slut", "whore", "sex", "nude", "xxx", "milf", "fetish", "suck",
    "blowjob", "cum", "anal", "dildo", "racist", "nigger", "fag", "chink", "spic", "terrorist",
    "nazi", "kkk", "coon", "gaylord", "queer", "idiot", "stupid", "moron", "dumbass", "loser",
    "ugly", "fatso", "psycho", "freak", "retard", "scam", "fraud", "hack", "cheat", "giveaway",
    "free money", "click here", "investment scheme", "airdrop", "pump and dump"
}

# User warnings tracking
user_warnings = {}


async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greet new members when they join the group."""
    chat_member = update.chat_member
    new_members = chat_member.new_chat_members
    if not new_members:
        return

    for member in new_members:
        try:
            name = member.first_name or "User"
            if member.last_name:
                name += f" {member.last_name}"

            welcome_message = f"Welcome {name} to the group!\n\n"
            if "welcome_message" in KNOWLEDGE_BASE:
                welcome_message += KNOWLEDGE_BASE["welcome_message"]
            else:
                welcome_message += "Please read the group rules and enjoy your stay!"

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=welcome_message,
                parse_mode=ParseMode.HTML
            )

            log_to_sheet({
                "timestamp": datetime.now().isoformat(),
                "chat_id": update.effective_chat.id,
                "chat_title": update.effective_chat.title,
                "user_id": member.id,
                "username": member.username,
                "action": "join",
                "message": "New member joined"
            })

        except Exception as e:
            logger.error(f"Error in welcome_new_members: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages and check for inappropriate content."""
    if not update.message or not update.message.text:
        return

    message = update.message.text.lower()
    user_id = update.message.from_user.id
    chat_id = update.effective_chat.id

    found_inappropriate = False
    inappropriate_words_found = []

    for word in INAPPROPRIATE_WORDS:
        if re.search(r"\b" + re.escape(word) + r"\b", message):
            found_inappropriate = True
            inappropriate_words_found.append(word)

    if found_inappropriate:
        user_warnings[user_id] = user_warnings.get(user_id, 0) + 1
        warning_count = user_warnings[user_id]

        warning_message = f"Warning {warning_count}/3 for {update.message.from_user.first_name}\n"
        warning_message += f"Detected inappropriate words: {', '.join(inappropriate_words_found)}"
        await update.message.reply_text(warning_message)

        log_to_sheet({
            "timestamp": datetime.now().isoformat(),
            "chat_id": chat_id,
            "chat_title": update.effective_chat.title,
            "user_id": user_id,
            "username": update.message.from_user.username,
            "action": "warning",
            "message": f"Inappropriate words detected: {', '.join(inappropriate_words_found)}",
            "warning_count": warning_count
        })

        if warning_count >= 3:
            try:
                until_time = datetime.now() + timedelta(minutes=5)
                await context.bot.ban_chat_member(chat_id, user_id, until_date=until_time)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"{update.message.from_user.first_name} has been temporarily removed for repeated violations."
                )
                log_to_sheet({
                    "timestamp": datetime.now().isoformat(),
                    "chat_id": chat_id,
                    "chat_title": update.effective_chat.title,
                    "user_id": user_id,
                    "username": update.message.from_user.username,
                    "action": "kick",
                    "message": "User kicked for repeated violations"
                })
                user_warnings[user_id] = 0
            except Exception as e:
                logger.error(f"Error kicking user: {e}")

    for keyword, response in KNOWLEDGE_BASE.items():
        if keyword.lower() in message and keyword != "welcome_message":
            await update.message.reply_text(response)
            break

    log_to_sheet({
        "timestamp": datetime.now().isoformat(),
        "chat_id": update.effective_chat.id,
        "chat_title": update.effective_chat.title,
        "user_id": update.message.from_user.id,
        "username": update.message.from_user.username,
        "action": "message",
        "message": update.message.text[:100]
    })


def log_to_sheet(data):
    """Log data to Google Sheets."""
    if not worksheet:
        logger.warning("Google Sheets not configured, skipping log")
        return

    try:
        row = [
            data.get("timestamp", ""),
            data.get("chat_id", ""),
            data.get("chat_title", ""),
            data.get("user_id", ""),
            data.get("username", ""),
            data.get("action", ""),
            data.get("message", ""),
            data.get("warning_count", "")
        ]
        worksheet.append_row(row)
    except Exception as e:
        logger.error(f"Error logging to Google Sheets: {e}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello! I'm a moderation bot. I can:\n"
        "- Welcome new members\n"
        "- Moderate inappropriate content\n"
        "- Answer questions based on my knowledge base\n"
        "- Log suspicious activity"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "Available commands:\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/rules - Show group rules\n\n"
        "I also automatically:\n"
        "- Welcome new members\n"
        "- Moderate inappropriate language\n"
        "- Answer questions based on my knowledge\n"
        "- Log suspicious activity"
    )
    await update.message.reply_text(help_text)


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules = KNOWLEDGE_BASE.get("rules", "No rules defined in knowledge base.")
    await update.message.reply_text(rules)


def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("rules", rules_command))
    application.add_handler(ChatMemberHandler(welcome_new_members, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
