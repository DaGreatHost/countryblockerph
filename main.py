import os
import logging
import sqlite3
from datetime import datetime
from typing import Optional
import phonenumbers
from phonenumbers import NumberParseException

from telegram import (
    Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    ChatMemberUpdated, ChatMember
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    ContextTypes, filters
)
from telegram.constants import ChatMemberStatus, ParseMode

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))

class DatabaseManager:
    def __init__(self, db_path: str = "filipino_bot.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS verified_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                phone_number TEXT,
                verified_date TIMESTAMP,
                is_banned BOOLEAN DEFAULT FALSE
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_verified_user(self, user_id: int, username: str, first_name: str, phone_number: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO verified_users 
            (user_id, username, first_name, phone_number, verified_date, is_banned)
            VALUES (?, ?, ?, ?, ?, FALSE)
        ''', (user_id, username or "", first_name or "", phone_number, datetime.now()))
        conn.commit()
        conn.close()
    
    def is_verified(self, user_id: int) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM verified_users WHERE user_id = ? AND is_banned = FALSE', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def ban_user(self, user_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE verified_users SET is_banned = TRUE WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()

class PhoneVerifier:
    @staticmethod
    def verify_phone_number(phone_number: str) -> dict:
        """Verify if phone number is from Philippines - Enhanced for PH formats"""
        try:
            # Handle common PH number formats
            cleaned_number = phone_number.replace(" ", "").replace("-", "")
            
            # If number starts with 09, convert to +639
            if cleaned_number.startswith("09"):
                cleaned_number = "+63" + cleaned_number[1:]
            # If number starts with 9, convert to +639
            elif cleaned_number.startswith("9") and len(cleaned_number) == 10:
                cleaned_number = "+63" + cleaned_number
            # If number starts with 63, add +
            elif cleaned_number.startswith("63") and not cleaned_number.startswith("+63"):
                cleaned_number = "+" + cleaned_number
            # If number doesn't have country code, assume PH
            elif not cleaned_number.startswith("+") and len(cleaned_number) == 10:
                cleaned_number = "+63" + cleaned_number[1:] if cleaned_number.startswith("0") else "+63" + cleaned_number
            
            parsed = phonenumbers.parse(cleaned_number)
            region = phonenumbers.region_code_for_number(parsed)
            is_valid = phonenumbers.is_valid_number(parsed)
            
            is_ph = region == 'PH' and parsed.country_code == 63 and is_valid
            
            return {
                'is_filipino': is_ph,
                'country_code': parsed.country_code,
                'region': region,
                'formatted_number': phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
                'is_valid': is_valid
            }
        except NumberParseException as e:
            logger.error(f"Phone parsing error: {e}")
            return {
                'is_filipino': False,
                'country_code': None,
                'region': None,
                'formatted_number': phone_number,
                'is_valid': False
            }

class FilipinoBotManager:
    def __init__(self):
        if not BOT_TOKEN:
            raise ValueError("BOT_TOKEN environment variable is required!")
        if not ADMIN_ID:
            raise ValueError("ADMIN_ID environment variable is required!")
            
        self.db = DatabaseManager()
        self.verifier = PhoneVerifier()
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        
        if self.db.is_verified(user.id):
            await update.message.reply_text(
                "✅ *Na-verify ka na!*\n\nWelcome sa Filipino community! 🇵🇭",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Start verification process immediately
        await self.start_verification(update, context)
    
    async def start_verification(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start phone verification process"""
        user = update.effective_user
        
        contact_keyboard = [[KeyboardButton("📱 I-Share ang Phone Number Ko", request_contact=True)]]
        contact_markup = ReplyKeyboardMarkup(
            contact_keyboard, 
            one_time_keyboard=True, 
            resize_keyboard=True
        )
        
        verification_msg = f"""
🇵🇭 *Filipino Verification*

Hi {user.first_name}! Para ma-verify ka bilang Filipino user, i-share lang ang phone number mo.

**Requirements:**
• Philippine number (+63) lang
• I-click lang ang button sa baba
• Automatic approval kapag verified

👇 *I-click para mag-share:*
        """
        
        await update.message.reply_text(
            verification_msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=contact_markup
        )
    
    async def handle_contact_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle phone number verification"""
        if not update.message.contact:
            return
        
        contact = update.message.contact
        user = update.effective_user
        
        # Security check
        if contact.user_id != user.id:
            await update.message.reply_text(
                "❌ Sariling phone number mo lang ang pwedeng i-verify!",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        # Remove keyboard
        await update.message.reply_text(
            "📱 Ini-verify ang phone number...",
            reply_markup=ReplyKeyboardRemove()
        )
        
        # Verify phone number
        phone_result = self.verifier.verify_phone_number(contact.phone_number)
        
        if phone_result['is_filipino']:
            # SUCCESS - Add to verified users
            self.db.add_verified_user(
                user.id, 
                user.username, 
                user.first_name, 
                contact.phone_number
            )
            
            success_msg = f"""
✅ **VERIFIED!** 🇵🇭

Welcome sa Filipino community, {user.first_name}!

📱 **Verified Number:** {phone_result['formatted_number']}
🎉 **Status:** Approved for all Filipino channels/groups

Hindi mo na kailangan mag-verify ulit sa ibang groups!
            """
            
            await update.message.reply_text(success_msg, parse_mode=ParseMode.MARKDOWN)
            
            # Notify admin
            try:
                admin_msg = f"""
✅ *New Verified User*

**User:** {user.first_name} (@{user.username or 'no_username'})
**ID:** `{user.id}`
**Phone:** {phone_result['formatted_number']}
                """
                await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.error(f"Error notifying admin: {e}")
        else:
            # FAILED
            country_info = phone_result.get('region', 'Unknown')
            fail_msg = f"""
❌ **Hindi Philippine Number**

**Detected:**
• Number: {phone_result['formatted_number']}
• Country: {country_info}
• Expected: Philippines 🇵🇭 (+63)

**Para ma-verify:**
• Gamitin ang Philippine number mo
• I-try ulit ang `/start`
            """
            
            await update.message.reply_text(fail_msg, parse_mode=ParseMode.MARKDOWN)
    
    async def handle_chat_member_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle new members joining chat - Enhanced version"""
        try:
            if not update.chat_member:
                logger.info("No chat_member in update")
                return
                
            chat_member_update = update.chat_member
            new_member = chat_member_update.new_chat_member
            old_member = chat_member_update.old_chat_member
            user = new_member.user
            chat_id = update.effective_chat.id
            chat = update.effective_chat
            
            logger.info(f"Chat member update: User {user.id} ({user.first_name}) in chat {chat_id}")
            logger.info(f"Old status: {old_member.status}, New status: {new_member.status}")
            
            # Skip bots and admin
            if user.is_bot or user.id == ADMIN_ID:
                logger.info(f"Skipping bot/admin user {user.id}")
                return
            
            # Check various join scenarios
            is_new_member = False
            
            # Scenario 1: User joined group/supergroup
            if (old_member.status == ChatMemberStatus.LEFT and 
                new_member.status == ChatMemberStatus.MEMBER):
                is_new_member = True
                logger.info(f"User {user.id} joined group {chat_id}")
            
            # Scenario 2: User was invited/added to channel
            elif (old_member.status == ChatMemberStatus.LEFT and 
                  new_member.status == ChatMemberStatus.RESTRICTED):
                is_new_member = True
                logger.info(f"User {user.id} added to channel {chat_id}")
            
            # Scenario 3: User approved to join (for approval-required groups)
            elif (old_member.status == ChatMemberStatus.RESTRICTED and 
                  new_member.status == ChatMemberStatus.MEMBER):
                is_new_member = True
                logger.info(f"User {user.id} approved in {chat_id}")
            
            if not is_new_member:
                return
            
            # Check if user is verified  
            if self.db.is_verified(user.id):
                # Verified user - send welcome
                try:
                    welcome_msg = f"🇵🇭 Welcome {user.first_name}! Verified Filipino user ka na. 🎉"
                    
                    # For channels, try to send message
                    if chat.type == 'channel':
                        await context.bot.send_message(chat_id, welcome_msg)
                    else:
                        # For groups/supergroups
                        await context.bot.send_message(chat_id, welcome_msg)
                    
                    logger.info(f"Welcomed verified user {user.id} in chat {chat_id}")
                    
                except Exception as e:
                    logger.error(f"Error welcoming user {user.id} in chat {chat_id}: {e}")
            else:
                # Unverified user - send verification reminder
                try:
                    # Send message in the chat first
                    verify_msg = f"""
🇵🇭 Hi {user.first_name}!

Para ma-join permanently sa community, kailangan mo ma-verify na Filipino user ka.

I-message lang ako privately: @{context.bot.username}
Tapos i-type ang `/start` para mag-verify! 👇
                    """
                    
                    if chat.type == 'channel':
                        await context.bot.send_message(chat_id, verify_msg)
                    else:
                        await context.bot.send_message(chat_id, verify_msg)
                    
                    # Try to send private message
                    try:
                        private_msg = f"""
🇵🇭 Hi {user.first_name}!

Nakita kong sumali ka sa {chat.title or 'Filipino community'}.

Para ma-verify ka bilang Filipino user:
👇 I-type lang ang `/start` dito sa chat na ito

Verification requirement lang ito para sa lahat ng Filipino channels/groups.
                        """
                        await context.bot.send_message(user.id, private_msg)
                        logger.info(f"Sent private verification message to user {user.id}")
                    except Exception as e:
                        logger.info(f"Could not send private message to user {user.id}: {e}")
                        
                    logger.info(f"Sent verification reminder for user {user.id} in chat {chat_id}")
                        
                except Exception as e:
                    logger.error(f"Error handling unverified user {user.id} in chat {chat_id}: {e}")
                    
        except Exception as e:
            logger.error(f"Error in handle_chat_member_update: {e}")
            logger.error(f"Update: {update}")

    async def handle_my_chat_member_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle when bot is added/removed from chats"""
        try:
            if not update.my_chat_member:
                return
                
            chat_member_update = update.my_chat_member
            new_status = chat_member_update.new_chat_member.status
            chat = update.effective_chat
            
            if new_status == ChatMemberStatus.ADMINISTRATOR:
                logger.info(f"Bot became admin in chat {chat.id} ({chat.title})")
                # Send setup message
                setup_msg = """
🇵🇭 **Filipino Verification Bot Active!**

Bot is now set up sa channel/group na ito.

**Features:**
✅ Auto-welcome verified Filipino users
📱 Auto-send verification reminders sa mga bagong members
🚫 Protection against non-Filipino users

**Setup Complete!** 🎉
                """
                try:
                    await context.bot.send_message(chat.id, setup_msg, parse_mode=ParseMode.MARKDOWN)
                except:
                    pass  # Channel might not allow bot messages
                    
            elif new_status == ChatMemberStatus.MEMBER:
                logger.info(f"Bot added as member to chat {chat.id} ({chat.title})")
                
        except Exception as e:
            logger.error(f"Error in handle_my_chat_member_update: {e}")
    
    # Simple admin commands
    async def ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ban a user (Admin only)"""
        if update.effective_user.id != ADMIN_ID:
            return
            
        if not context.args:
            await update.message.reply_text("Usage: `/ban <user_id>`", parse_mode=ParseMode.MARKDOWN)
            return
            
        try:
            user_id = int(context.args[0])
            self.db.ban_user(user_id)
            await update.message.reply_text(f"🚫 User `{user_id}` banned!", parse_mode=ParseMode.MARKDOWN)
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID")
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show stats (Admin only)"""
        if update.effective_user.id != ADMIN_ID:
            return
            
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM verified_users WHERE is_banned = FALSE')
        verified_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM verified_users WHERE is_banned = TRUE')
        banned_count = cursor.fetchone()[0]
        
        conn.close()
        
        stats_msg = f"""
📊 **Bot Stats**

✅ Verified Users: {verified_count}
🚫 Banned Users: {banned_count}
        """
        
        await update.message.reply_text(stats_msg, parse_mode=ParseMode.MARKDOWN)

def main():
    """Main function"""
    if not BOT_TOKEN or not ADMIN_ID:
        logger.error("BOT_TOKEN and ADMIN_ID environment variables are required!")
        return
    
    bot_manager = FilipinoBotManager()
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", bot_manager.start_command))
    application.add_handler(MessageHandler(filters.CONTACT, bot_manager.handle_contact_message))
    
    # Chat member handlers - BOTH are important!
    application.add_handler(ChatMemberHandler(bot_manager.handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(ChatMemberHandler(bot_manager.handle_my_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER))
    
    # Admin commands
    application.add_handler(CommandHandler("ban", bot_manager.ban_command))
    application.add_handler(CommandHandler("stats", bot_manager.stats_command))
    
    # Add debug command for testing
    application.add_handler(CommandHandler("test", bot_manager.test_command))
    
    logger.info("🇵🇭 Filipino Verification Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
