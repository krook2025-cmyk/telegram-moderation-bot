import logging
import re
import json
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler
from telegram.constants import ParseMode
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN')
SHEET_ID = os.environ.get('SHEET_ID')
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')

if not BOT_TOKEN:
    logger.error("BOT_TOKEN environment variable is not set")
    exit(1)

# Google Sheets setup
worksheet = None
if GOOGLE_CREDENTIALS_JSON and SHEET_ID:
    try:
        # Parse the JSON credentials from environment variable
        credentials_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
        gc = gspread.authorize(credentials)
        
        # Open the spreadsheet
        worksheet = gc.open_by_key(SHEET_ID).sheet1
        logger.info("Google Sheets connection established successfully")
    except Exception as e:
        logger.error(f"Error setting up Google Sheets: {e}")
        worksheet = None
else:
    logger.warning("Google Sheets credentials or Sheet ID not set. Google Sheets logging disabled.")

# Load knowledge base from environment variable or create default
KNOWLEDGE_BASE = {}
try:
    knowledge_base_json = os.environ.get('KNOWLEDGE_BASE_JSON')
    if knowledge_base_json:
        KNOWLEDGE_BASE = json.loads(knowledge_base_json)
    else:
        # Default knowledge base
        KNOWLEDGE_BASE = {
            "welcome_message": "Welcome to our group. Please read the rules and enjoy your stay.",
            "rules": "1. Be respectful to all members. 2. No spam or advertisements. 3. No inappropriate content. 4. Keep discussions relevant to the group topic.",
            "faq": "Frequently asked questions will be answered here. Please ask the admin to add more FAQs.",
            "contact": "Please contact the group admin for any questions or concerns."
        }
except Exception as e:
    logger.error(f"Error loading knowledge base: {e}")
    KNOWLEDGE_BASE = {}

# Inappropriate words list
INAPPROPRIATE_WORDS = {
    'fuck', 'shit', 'bitch', 'asshole', 'bastard', 'damn', 'crap', 'dick', 'pussy', 'cock',
    'prick', 'porn', 'slut', 'whore', 'sex', 'nude', 'xxx', 'milf', 'fetish', 'suck',
    'blowjob', 'cum', 'anal', 'dildo', 'racist', 'nigger', 'fag', 'chink', 'spic', 'terrorist',
    'nazi', 'kkk', 'coon', 'gaylord', 'queer', 'idiot', 'stupid', 'moron', 'dumbass', 'loser',
    'ugly', 'fatso', 'psycho', 'freak', 'retard', 'scam', 'fraud', 'hack', 'cheat', 'giveaway',
    'free money', 'click here', 'investment scheme', 'airdrop', 'pump and dump'
}

# User warnings tracking
user_warnings = {}

async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greet new members when they join the group."""
    for member in update.chat_member.new_chat_members:
        try:
            # Get the new member's name
            name = member.first_name
            if member.last_name:
                name += f" {member.last_name}"
            
            # Welcome message
            welcome_message = f"Welcome {name} to the group.\n\n"
            
            # Add knowledge from knowledge base if available
            if 'welcome_message' in KNOWLEDGE_BASE:
                welcome_message += KNOWLEDGE_BASE['welcome_message']
            else:
                welcome_message += "Please read the group rules and enjoy your stay."
            
            # Send welcome message
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=welcome_message,
                parse_mode=ParseMode.HTML
            )
            
            # Log the new member
            log_to_sheet({
                'timestamp': datetime.now().isoformat(),
                'chat_id': update.effective_chat.id,
                'chat_title': update.effective_chat.title if update.effective_chat.title else "Unknown",
                'user_id': member.id,
                'username': member.username if member.username else "No username",
                'action': 'join',
                'message': 'New member joined'
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
    user_name = update.message.from_user.first_name
    
    # Check for inappropriate words
    found_inappropriate = False
    inappropriate_words_found = []
    
    for word in INAPPROPRIATE_WORDS:
        if re.search(r'\b' + re.escape(word) + r'\b', message):
            found_inappropriate = True
            inappropriate_words_found.append(word)
    
    if found_inappropriate:
        # Initialize warning count for user if not exists
        if user_id not in user_warnings:
            user_warnings[user_id] = 0
        
        # Increment warning count
        user_warnings[user_id] += 1
        
        # Create warning message
        warning_count = user_warnings[user_id]
        warning_message = f"Warning {warning_count}/3 for {user_name}. "
        warning_message += f"Detected inappropriate words: {', '.join(inappropriate_words_found)}"
        
        # Send warning
        await update.message.reply_text(warning_message)
        
        # Log the warning
        log_to_sheet({
            'timestamp': datetime.now().isoformat(),
            'chat_id': chat_id,
            'chat_title': update.effective_chat.title if update.effective_chat.title else "Unknown",
            'user_id': user_id,
            'username': update.message.from_user.username if update.message.from_user.username else "No username",
            'action': 'warning',
            'message': f'Inappropriate words detected: {", ".join(inappropriate_words_found)}',
            'warning_count': warning_count
        })
        
        # Take action if too many warnings
        if warning_count >= 3:
            try:
                # Kick user after 3 warnings
                await context.bot.ban_chat_member(chat_id, user_id, until_date=datetime.now().timestamp() + 300)  # 5 minutes
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"{user_name} has been temporarily removed for repeated violations."
                )
                
                # Log the action
                log_to_sheet({
                    'timestamp': datetime.now().isoformat(),
                    'chat_id': chat_id,
                    'chat_title': update.effective_chat.title if update.effective_chat.title else "Unknown",
                    'user_id': user_id,
                    'username': update.message.from_user.username if update.message.from_user.username else "No username",
                    'action': 'kick',
                    'message': 'User kicked for repeated violations'
                })
                
                # Reset warnings for this user
                user_warnings[user_id] = 0
                
            except Exception as e:
                logger.error(f"Error kicking user: {e}")
                await update.message.reply_text("I need admin privileges to remove users.")
    
    # Check if message contains keywords from knowledge base
    for keyword, response in KNOWLEDGE_BASE.items():
        if keyword.lower() in message and keyword != 'welcome_message':
            await update.message.reply_text(response)
            break

    # Log all messages for analysis
    log_to_sheet({
        'timestamp': datetime.now().isoformat(),
        'chat_id': update.effective_chat.id,
        'chat_title': update.effective_chat.title if update.effective_chat.title else "Unknown",
        'user_id': user_id,
        'username': update.message.from_user.username if update.message.from_user.username else "No username",
        'action': 'message',
        'message': update.message.text[:100]  # Log first 100 characters
    })

def log_to_sheet(data):
    """Log data to Google Sheets."""
    if not worksheet:
        return
    
    try:
        # Prepare row data
        row = [
            data.get('timestamp', ''),
            data.get('chat_id', ''),
            data.get('chat_title', ''),
            data.get('user_id', ''),
            data.get('username', ''),
            data.get('action', ''),
            data.get('message', ''),
            data.get('warning_count', '')
        ]
        
        # Append to worksheet
        worksheet.append_row(row)
    except Exception as e:
        logger.error(f"Error logging to Google Sheets: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command."""
    await update.message.reply_text(
        "Hello. I am a moderation bot. I can welcome new members, moderate inappropriate content, answer questions based on my knowledge base, and log suspicious activity."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /help command."""
    help_text = """
Available commands:
/start - Start the bot
/help - Show this help message
/rules - Show group rules

I also automatically:
- Welcome new members
- Moderate inappropriate language
- Answer questions based on my knowledge
- Log suspicious activity
"""
    await update.message.reply_text(help_text)

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /rules command."""
    rules = KNOWLEDGE_BASE.get('rules', 'No rules defined in knowledge base.')
    await update.message.reply_text(rules)

def main():
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("rules", rules_command))
    application.add_handler(ChatMemberHandler(welcome_new_members, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start the Bot
    logger.info("Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
