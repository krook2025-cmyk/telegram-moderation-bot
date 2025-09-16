import logging
import re
import json
import os
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ChatMemberHandler,
)
from telegram.constants import ParseMode
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Configuration (use environment variables on Render) ---
# You provided these; using environment variables is recommended.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8289869796:AAEyNWi1ApCl7IPd_ERxJJ2eziYqVT7NdkQ")
SHEET_ID = os.environ.get("SHEET_ID", "1zmzf3lsQndZpIjPcc00CobpXoclW4hQNGzqrIvvE9vU")
DOC_ID = os.environ.get("DOC_ID", "1to39YjgE7MgD1zsds-2RQC-ih-Hl4dvsWAakdByg6UQ")

# If you store your Google service account JSON in an environment variable named GOOGLE_CREDENTIALS,
# write it to credentials.json on startup so gspread can use it.
if os.environ.get("GOOGLE_CREDENTIALS"):
    try:
        with open("credentials.json", "w", encoding="utf-8") as f:
            f.write(os.environ["GOOGLE_CREDENTIALS"])
        logger.info("credentials.json created from GOOGLE_CREDENTIALS env var")
    except Exception as e:
        logger.error("Failed to write credentials.json from GOOGLE_CREDENTIALS: %s", e)

# --- Google Sheets setup ---
worksheet = None
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    # This expects a file named credentials.json present in the working dir (created above from env var)
    credentials = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    gc = gspread.authorize(credentials)
    worksheet = gc.open_by_key(SHEET_ID).sheet1
    logger.info("Connected to Google Sheet: %s", SHEET_ID)
except FileNotFoundError:
    logger.error("Error setting up Google Sheets: credentials.json not found")
    worksheet = None
except Exception as e:
    logger.error("Error setting up Google Sheets: %s", e)
    worksheet = None

# --- Load knowledge from credentials.json if present (optional) ---
KNOWLEDGE_BASE = {}
try:
    if os.path.exists("credentials.json"):
        with open("credentials.json", "r", encoding="utf-8") as f:
            KNOWLEDGE_BASE = json.load(f)
except Exception:
    # ignore parsing errors — knowledge base is optional
    KNOWLEDGE_BASE = {}

# --- Inappropriate words list (one-line list you requested) ---
INAPPROPRIATE_WORDS = {
    "fuck", "shit", "bitch", "asshole", "bastard", "damn", "crap", "dick", "pussy", "cock",
    "prick", "porn", "slut", "whore", "sex", "nude", "xxx", "milf", "fetish", "suck",
    "blowjob", "cum", "anal", "dildo", "racist", "nigger", "fag", "chink", "spic", "terrorist",
    "nazi", "kkk", "coon", "gaylord", "queer", "idiot", "stupid", "moron", "dumbass", "loser",
    "ugly", "fatso", "psycho", "freak", "retard", "scam", "fraud", "hack", "cheat", "giveaway",
    "free money", "click here", "investment scheme", "airdrop", "pump and dump"
}

# Warnings tracking (in-memory). For persistence, save to database or sheet.
user_warnings = {}

# --- Helpers ---
def safe_username(user):
    if not user:
        return ""
    return user.username if getattr(user, "username", None) else f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '') or ''}".strip()


def log_to_sheet(data):
    """Append a row to the configured Google Sheet. If sheet not available, just log a warning."""
    if not worksheet:
        logger.warning("Google Sheets not configured, skipping log: %s", data)
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
            data.get("warning_count", ""),
        ]
        worksheet.append_row(row)
    except Exception as e:
        logger.error("Error logging to Google Sheets: %s", e)

# --- Handlers ---
async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greet new members when they join the group. Works for ChatMember updates."""
    try:
        chat_member = update.chat_member
        new_members = getattr(chat_member, "new_chat_members", None)
        if not new_members:
            # fallback: sometimes ChatMember updates include 'from_user' with status changes; skip if none
            return

        for member in new_members:
            name = (member.first_name or "") + (f" {member.last_name}" if getattr(member, "last_name", None) else "")
            name = name.strip() or "User"

            welcome_message = f"Welcome {name} to the group!\n\n"
            if "welcome_message" in KNOWLEDGE_BASE:
                welcome_message += KNOWLEDGE_BASE.get("welcome_message", "")
            else:
                welcome_message += "Please read the group rules and enjoy your stay!"

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=welcome_message,
                parse_mode=ParseMode.HTML,
            )

            log_to_sheet(
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "chat_id": update.effective_chat.id,
                    "chat_title": update.effective_chat.title,
                    "user_id": member.id,
                    "username": safe_username(member),
                    "action": "join",
                    "message": "New member joined",
                }
            )
    except Exception as e:
        logger.error("Error in welcome_new_members: %s", e)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages and check for inappropriate content."""
    try:
        if not update.message or not update.message.text:
            return

        text = update.message.text
        message_lower = text.lower()
        user = update.message.from_user
        user_id = user.id
        chat_id = update.effective_chat.id

        # find inappropriate words (whole-word matches)
        found = []
        for word in INAPPROPRIATE_WORDS:
            # use word boundary regex; for multi-word phrases this still works
            if re.search(r"\b" + re.escape(word) + r"\b", message_lower):
                found.append(word)

        if found:
            user_warnings[user_id] = user_warnings.get(user_id, 0) + 1
            warning_count = user_warnings[user_id]

            warning_message = f"Warning {warning_count}/3 for {getattr(user, 'first_name', '')}\n"
            warning_message += f"Detected inappropriate words: {', '.join(found)}"

            await update.message.reply_text(warning_message)

            log_to_sheet(
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "chat_id": chat_id,
                    "chat_title": update.effective_chat.title,
                    "user_id": user_id,
                    "username": safe_username(user),
                    "action": "warning",
                    "message": f"Inappropriate words detected: {', '.join(found)}",
                    "warning_count": warning_count,
                }
            )

            if warning_count >= 3:
                # temporary ban (5 minutes)
                try:
                    until_time = datetime.utcnow() + timedelta(minutes=5)
                    # python-telegram-bot v20 accepts datetime for until_date
                    await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id, until_date=until_time)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"{getattr(user, 'first_name', '')} has been temporarily removed for repeated violations.",
                    )

                    log_to_sheet(
                        {
                            "timestamp": datetime.utcnow().isoformat(),
                            "chat_id": chat_id,
                            "chat_title": update.effective_chat.title,
                            "user_id": user_id,
                            "username": safe_username(user),
                            "action": "kick",
                            "message": "User temporarily banned for repeated violations",
                        }
                    )

                    # reset counter
                    user_warnings[user_id] = 0
                except Exception as e:
                    logger.error("Error banning user: %s", e)

        # Respond if any knowledge keywords match (skip welcome_message key)
        for keyword, response in KNOWLEDGE_BASE.items():
            if keyword == "welcome_message":
                continue
            try:
                if keyword.lower() in message_lower:
                    await update.message.reply_text(response)
                    break
            except Exception:
                continue

        # Log message (first 100 chars) to sheet for analysis
        log_to_sheet(
            {
                "timestamp": datetime.utcnow().isoformat(),
                "chat_id": update.effective_chat.id,
                "chat_title": update.effective_chat.title,
                "user_id": user_id,
                "username": safe_username(user),
                "action": "message",
                "message": text[:100],
            }
        )
    except Exception as e:
        logger.error("Error in handle_message: %s", e)


# Commands
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello. I am a moderation bot. I can welcome members, moderate inappropriate content, "
        "answer questions based on a knowledge base, and log suspicious activity."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "Available commands:\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/rules - Show group rules\n\n"
        "Automatically:\n"
        "- Welcome new members\n"
        "- Moderate inappropriate language\n"
        "- Answer questions based on knowledge\n"
        "- Log suspicious activity"
    )
    await update.message.reply_text(help_text)


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules = KNOWLEDGE_BASE.get("rules", "No rules defined in knowledge base.")
    await update.message.reply_text(rules)


def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set. Exiting.")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("rules", rules_command))

    # ChatMemberHandler to greet new members
    application.add_handler(ChatMemberHandler(welcome_new_members, ChatMemberHandler.CHAT_MEMBER))

    # Message handler for normal text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
