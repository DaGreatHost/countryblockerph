import os
import logging
import asyncio
import re
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import sqlite3
from dataclasses import dataclass
import phonenumbers
from phonenumbers import NumberParseException

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    ChatMemberUpdated, ChatPermissions, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ChatMemberStatus, ParseMode

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration - Use environment variables for security
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))

@dataclass
class UserData:
    user_id: int
    username: str
    first_name: str
    phone_number: str
    join_date: datetime
    verification_status: str
    strike_count: int
    is_whitelisted: bool

class DatabaseManager:
    def __init__(self, db_path: str = "filipino_bot.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                phone_number TEXT,
                join_date TIMESTAMP,
                verification_status TEXT DEFAULT 'pending',
                strike_count INTEGER DEFAULT 0,
                is_whitelisted BOOLEAN DEFAULT FALSE,
                last_activity TIMESTAMP
            )
        ''')
        
        # Banned users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                ban_reason TEXT,
                ban_date TIMESTAMP,
                banned_by INTEGER
            )
        ''')
        
        # Activity logs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_user(self, user_data: UserData):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO users 
            (user_id, username, first_name, phone_number, join_date, verification_status, 
             strike_count, is_whitelisted, last_activity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_data.user_id, user_data.username, user_data.first_name,
            user_data.phone_number, user_data.join_date, user_data.verification_status,
            user_data.strike_count, user_data.is_whitelisted, datetime.now()
        ))
        conn.commit()
        conn.close()
    
    def get_user(self, user_id: int) -> Optional[UserData]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return UserData(
                user_id=row[0], username=row[1], first_name=row[2],
                phone_number=row[3], join_date=datetime.fromisoformat(row[4]),
                verification_status=row[5], strike_count=row[6],
                is_whitelisted=bool(row[7])
            )
        return None
    
    def ban_user(self, user_id: int, username: str, reason: str, banned_by: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO banned_users 
            (user_id, username, ban_reason, ban_date, banned_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, reason, datetime.now(), banned_by))
        conn.commit()
        conn.close()
    
    def is_banned(self, user_id: int) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM banned_users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def add_strike(self, user_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET strike_count = strike_count + 1 WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    
    def log_activity(self, user_id: int, action: str, details: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO activity_logs (user_id, action, details)
            VALUES (?, ?, ?)
        ''', (user_id, action, details))
        conn.commit()
        conn.close()

class PhoneVerifier:
    @staticmethod
    def verify_phone_number(phone_number: str) -> Dict[str, any]:
        """Verify if phone number is from Philippines"""
        try:
            parsed = phonenumbers.parse(phone_number)
            country_code = parsed.country_code
            region = phonenumbers.region_code_for_number(parsed)
            
            is_ph = region == 'PH' and country_code == 63
            
            return {
                'is_filipino': is_ph,
                'country_code': country_code,
                'region': region,
                'formatted_number': phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
            }
        except NumberParseException:
            return {
                'is_filipino': False,
                'country_code': None,
                'region': None,
                'formatted_number': phone_number
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
        
        welcome_message = f"""
🇵🇭 *Maligayang pagdating sa Filipino Verification Bot!*

Simple lang ang process dito - I-share mo lang ang phone number mo para ma-verify na Filipino user ka.

*Mga Commands:*
• `/verify` - I-verify ang sarili mo
• `/status` - Check verification status
• `/help` - Show all commands

*Admin Commands:*
• `/stats` - Bot statistics
• `/whitelist <user_id>` - Add user to whitelist
• `/ban <user_id> <reason>` - Manual ban
• `/unban <user_id>` - Remove ban

Salamat sa paggamit ng bot! 🚀
        """
        
        await update.message.reply_text(
            welcome_message, 
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = """
🆘 *Bot Help*

*User Commands:*
• `/start` - Welcome message
• `/verify` - I-verify ang sarili mo gamit ang PH phone number
• `/status` - Check verification status

*Paano gumana ang verification:*
1. I-click ang `/verify` command
2. I-click ang "📱 Share Phone Number" button
3. Automatic ma-ve-verify kung PH number (+63)
4. Tapos na! 🎉

*Admin Commands:*
• `/stats` - View bot statistics
• `/whitelist <user_id>` - Add to whitelist
• `/ban <user_id> <reason>` - Manual ban
• `/unban <user_id>` - Remove ban

Para sa mga tanong, makipag-ugnayan sa admin.
        """
        
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def verify_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /verify command"""
        user = update.effective_user
        
        # Check if user is already verified
        user_data = self.db.get_user(user.id)
        if user_data and user_data.verification_status == "verified":
            await update.message.reply_text(
                "✅ *Na-verify ka na!*\n\nHindi mo na kailangan mag-verify ulit. Welcome sa community! 🇵🇭",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Check if banned
        if self.db.is_banned(user.id):
            await update.message.reply_text(
                "🚫 *Banned ka sa bot na ito.*\n\nContact the admin kung may appeal ka.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Start phone verification
        await self.start_phone_verification(update, context)
    
    async def start_phone_verification(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start phone number verification process"""
        user = update.effective_user
        
        # Create user record if not exists
        user_data = self.db.get_user(user.id)
        if not user_data:
            user_data = UserData(
                user_id=user.id,
                username=user.username or "",
                first_name=user.first_name or "",
                phone_number="",
                join_date=datetime.now(),
                verification_status="pending",
                strike_count=0,
                is_whitelisted=False
            )
            self.db.add_user(user_data)
        
        # Create contact sharing keyboard
        contact_keyboard = [[KeyboardButton("📱 I-Share ang Phone Number Ko", request_contact=True)]]
        contact_markup = ReplyKeyboardMarkup(
            contact_keyboard, 
            one_time_keyboard=True, 
            resize_keyboard=True
        )
        
        verification_msg = f"""
🇵🇭 *Philippine Phone Number Verification*

Hi {user.first_name}! Para ma-verify ka, kailangan mo lang i-share ang phone number mo.

**Important:**
• Dapat Philippine number (+63) ang gagamitin mo
• Automatic ma-de-detect kung PH number or hindi
• Secure ito - hindi makikita ng iba ang number mo
• One-click lang, tapos na!

👇 *I-click ang button sa baba para ma-share ang phone number mo:*
        """
        
        await update.message.reply_text(
            verification_msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=contact_markup
        )
    
    async def handle_contact_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle phone number sharing - SECURE VERSION"""
        if not update.message.contact:
            return
        
        contact = update.message.contact
        user_id = update.effective_user.id
        
        # SECURITY CHECK: Ensure user can only verify their own number
        if contact.user_id != user_id:
            await update.message.reply_text(
                "❌ **Security Error!**\n\n"
                "Sariling phone number mo lang ang pwedeng i-verify!\n"
                "Hindi pwedeng mag-verify ng number ng iba.\n\n"
                "Please share your own contact.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        # Remove keyboard after contact sharing
        await update.message.reply_text(
            "📱 Ini-process ang phone number mo...",
            reply_markup=ReplyKeyboardRemove()
        )
        
        # Verify phone number
        phone_result = self.verifier.verify_phone_number(contact.phone_number)
        
        if phone_result['is_filipino']:
            # SUCCESS - Update user data
            user_data = self.db.get_user(user_id)
            if user_data:
                user_data.phone_number = contact.phone_number
                user_data.verification_status = "verified"
                user_data.strike_count = 0  # Reset strikes on successful verification
                self.db.add_user(user_data)
                
                success_msg = f"""
✅ **Phone Number Verified Successfully!**

🇵🇭 **Philippine Number Confirmed**
• Number: {phone_result['formatted_number']}
• Country: Philippines 🇵🇭
• Status: **VERIFIED** ✅

**🎉 Welcome to the community!** 

Hindi mo na kailangan mag-verify ulit sa ibang groups/channels na may same bot.

Salamat sa pagiging verified Filipino user! 🚀

*Pwede mo na mag-enjoy sa lahat ng features ng community!*
                """
                
                await update.message.reply_text(success_msg, parse_mode=ParseMode.MARKDOWN)
                self.db.log_activity(user_id, "verified", f"PH phone verified: {contact.phone_number}")
                
                # Notify admin of successful verification
                try:
                    admin_msg = f"""
✅ *New Verified User*

**User:** {user_data.first_name} (@{user_data.username or 'no_username'})
**User ID:** `{user_id}`
**Phone:** {phone_result['formatted_number']}
**Status:** Verified ✅
                    """
                    await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode=ParseMode.MARKDOWN)
                except Exception as e:
                    logger.error(f"Error notifying admin: {e}")
        else:
            # FAILED - Add strike and handle accordingly
            self.db.add_strike(user_id)
            user_data = self.db.get_user(user_id)
            strikes = user_data.strike_count if user_data else 1
            
            country_name = phone_result.get('region', 'Unknown')
            if country_name == 'Unknown':
                country_name = "Hindi ma-identify ang bansa"
            
            fail_msg = f"""
❌ **Phone Number Verification Failed**

**Nakitang Issue:**
• Number: {phone_result['formatted_number']}
• Country: {country_name}
• Expected: Philippines 🇵🇭 (+63)

**Strike Added:** {strikes}/3

{'⚠️ **Warning:** Isa pa lang, automatic ban na!' if strikes == 2 else ''}

**Ano ang pwede mong gawin:**
• Gamitin ang totoong Philippine number mo
• Siguraduhing naka-register sa Telegram ang PH number mo
• Contact admin kung may problema

**Bakit need ng PH number?**
Para ma-ensure na Filipino users lang ang nasa community.
            """
            
            if strikes >= 3:
                # Auto-ban user
                self.db.ban_user(user_id, user_data.username if user_data else "", "3 strikes - Non-PH phone attempts", 0)
                fail_msg += "\n\n🚫 **BANNED:** Sobrang daming failed attempts na. Contact admin para sa appeal."
                
                # Notify admin of ban
                try:
                    admin_msg = f"""
🚫 *User Auto-Banned*

**User:** {user_data.first_name if user_data else 'Unknown'} (@{user_data.username if user_data and user_data.username else 'no_username'})
**User ID:** `{user_id}`
**Reason:** 3 strikes - Multiple non-PH phone attempts
**Last Number:** {phone_result['formatted_number']} ({country_name})
**Status:** Auto-banned 🚫
                    """
                    await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode=ParseMode.MARKDOWN)
                except Exception as e:
                    logger.error(f"Error notifying admin about ban: {e}")
            
            await update.message.reply_text(fail_msg, parse_mode=ParseMode.MARKDOWN)
            self.db.log_activity(user_id, "strike", f"Non-PH phone: {contact.phone_number} ({country_name})")
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check user verification status"""
        user = update.effective_user
        user_data = self.db.get_user(user.id)
        
        if not user_data:
            status_msg = """
ℹ️ **Status: Hindi pa nag-start ang verification**

Para mag-start, i-type lang ang `/verify`
            """
        elif self.db.is_banned(user.id):
            status_msg = """
🚫 **Status: BANNED**

Banned ka sa bot na ito. Contact admin para sa appeal.
            """
        elif user_data.verification_status == "verified":
            status_msg = f"""
✅ **Status: VERIFIED** 🇵🇭

**User Details:**
• Name: {user_data.first_name}
• Phone: {user_data.phone_number}
• Verified Date: {user_data.join_date.strftime('%Y-%m-%d %H:%M')}

**Congratulations!** Verified Filipino user ka! 🎉
            """
        else:
            status_msg = f"""
⏳ **Status: PENDING VERIFICATION**

**Current Details:**
• Name: {user_data.first_name}
• Strikes: {user_data.strike_count}/3
• Join Date: {user_data.join_date.strftime('%Y-%m-%d %H:%M')}

Para ma-verify, i-type ang `/verify`
            """
        
        await update.message.reply_text(status_msg, parse_mode=ParseMode.MARKDOWN)
    
    async def verify_new_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Auto-verify new chat members"""
        if not update.chat_member or not update.chat_member.new_chat_member:
            return
        
        new_member = update.chat_member.new_chat_member.user
        chat_id = update.effective_chat.id
        
        # Skip if bot or admin
        if new_member.is_bot or new_member.id == ADMIN_ID:
            return
        
        # Check if already banned
        if self.db.is_banned(new_member.id):
            try:
                await context.bot.ban_chat_member(chat_id, new_member.id)
                await context.bot.send_message(
                    chat_id, 
                    f"🚫 User {new_member.first_name} is banned from this community."
                )
                return
            except Exception as e:
                logger.error(f"Error banning user {new_member.id}: {e}")
        
        # Check if already verified
        user_data = self.db.get_user(new_member.id)
        if user_data and user_data.verification_status == "verified":
            welcome_msg = f"""
🇵🇭 Welcome {new_member.first_name}! 

✅ **Already verified Filipino user** - Welcome sa community! 🎉
            """
            await context.bot.send_message(chat_id, welcome_msg)
            return
        
        # Send verification message to new member
        verification_msg = f"""
🇵🇭 Hi {new_member.first_name}! Welcome sa group!

Para ma-join officially sa community, kailangan mo ma-verify na Filipino user ka.

**Simple lang ang process:**
1. I-type ang `/verify` 
2. I-click ang "Share Phone Number" button
3. Tapos na! 🎉

**Bakit need ng verification?**
Para ma-ensure na Filipino community lang ito at safe para sa lahat.

👇 *I-type ang /verify para mag-start:*
        """
        
        try:
            await context.bot.send_message(new_member.id, verification_msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            # If can't send DM, send in group
            await context.bot.send_message(
                chat_id, 
                f"{new_member.first_name}, please DM the bot and type `/verify` to get verified! 🇵🇭"
            )
    
    # Admin Commands
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot statistics (Admin Only)"""
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.message.reply_text("❌ Admin command lang ito.")
            return
        
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # Get statistics
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM users WHERE verification_status = "verified"')
        verified_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM users WHERE verification_status = "pending"')
        pending_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM banned_users')
        banned_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM users WHERE is_whitelisted = TRUE')
        whitelisted_users = cursor.fetchone()[0]
        
        conn.close()
        
        stats_msg = f"""
📊 **Bot Statistics**

**Users:**
• Total Users: {total_users}
• ✅ Verified: {verified_users}
• ⏳ Pending: {pending_users}
• 🚫 Banned: {banned_users}
• ⭐ Whitelisted: {whitelisted_users}

**Verification Rate:** {(verified_users/total_users*100):.1f}% kung may users na
        """
        
        await update.message.reply_text(stats_msg, parse_mode=ParseMode.MARKDOWN)
    
    async def whitelist_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Whitelist a user (Admin Only)"""
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.message.reply_text("❌ Admin command lang ito.")
            return
        
        if not context.args:
            await update.message.reply_text("Usage: `/whitelist <user_id>`", parse_mode=ParseMode.MARKDOWN)
            return
        
        try:
            target_user_id = int(context.args[0])
            
            # Get or create user
            user_data = self.db.get_user(target_user_id)
            if not user_data:
                # Create new user record
                try:
                    target_user = await context.bot.get_chat(target_user_id)
                    user_data = UserData(
                        user_id=target_user_id,
                        username=target_user.username or "",
                        first_name=target_user.first_name or "",
                        phone_number="",
                        join_date=datetime.now(),
                        verification_status="verified",
                        strike_count=0,
                        is_whitelisted=True
                    )
                except Exception as e:
                    await update.message.reply_text(f"❌ Hindi ma-find ang user: {e}")
                    return
            else:
                user_data.is_whitelisted = True
                user_data.verification_status = "verified"
                user_data.strike_count = 0
            
            self.db.add_user(user_data)
            self.db.log_activity(target_user_id, "whitelisted", f"Whitelisted by admin {user_id}")
            
            await update.message.reply_text(
                f"✅ User `{target_user_id}` has been whitelisted and verified!",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID. Dapat number lang.")
    
    async def ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ban a user (Admin Only)"""
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.message.reply_text("❌ Admin command lang ito.")
            return
        
        if len(context.args) < 2:
            await update.message.reply_text("Usage: `/ban <user_id> <reason>`", parse_mode=ParseMode.MARKDOWN)
            return
        
        try:
            target_user_id = int(context.args[0])
            reason = " ".join(context.args[1:])
            
            # Get user info
            user_data = self.db.get_user(target_user_id)
            username = user_data.username if user_data else "Unknown"
            
            self.db.ban_user(target_user_id, username, reason, user_id)
            self.db.log_activity(target_user_id, "banned", f"Banned by admin {user_id}: {reason}")
            
            await update.message.reply_text(
                f"🚫 User `{target_user_id}` has been banned!\n**Reason:** {reason}",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID. Dapat number lang.")
    
    async def unban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Unban a user (Admin Only)"""
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.message.reply_text("❌ Admin command lang ito.")
            return
        
        if not context.args:
            await update.message.reply_text("Usage: `/unban <user_id>`", parse_mode=ParseMode.MARKDOWN)
            return
        
        try:
            target_user_id = int(context.args[0])
            
            # Remove from banned users
            conn = sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM banned_users WHERE user_id = ?', (target_user_id,))
            conn.commit()
            conn.close()
            
            # Reset strikes
            user_data = self.db.get_user(target_user_id)
            if user_data:
                user_data.strike_count = 0
                self.db.add_user(user_data)
            
            self.db.log_activity(target_user_id, "unbanned", f"Unbanned by admin {user_id}")
            
            await update.message.reply_text(
                f"✅ User `{target_user_id}` has been unbanned!",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID. Dapat number lang.")

def main():
    """Main function to run the bot"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is required!")
        return
    
    if not ADMIN_ID:
        logger.error("ADMIN_ID environment variable is required!")
        return
    
    # Create bot manager
    bot_manager = FilipinoBotManager()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", bot_manager.start_command))
    application.add_handler(CommandHandler("help", bot_manager.help_command))
    application.add_handler(CommandHandler("verify", bot_manager.verify_command))
    application.add_handler(CommandHandler("status", bot_manager.status_command))
    
    # Admin commands
    application.add_handler(CommandHandler("stats", bot_manager.stats_command))
    application.add_handler(CommandHandler("whitelist", bot_manager.whitelist_command))
    application.add_handler(CommandHandler("ban", bot_manager.ban_command))
    application.add_handler(CommandHandler("unban", bot_manager.unban_command))
    
    # Contact handler for phone verification
    application.add_handler(MessageHandler(filters.CONTACT, bot_manager.handle_contact_message))
    
    # Chat member handler for new members
    application.add_handler(ChatMemberHandler(bot_manager.verify_new_member, ChatMemberHandler.CHAT_MEMBER))
    
    # Start the bot
    logger.info("🇵🇭 Filipino Verification Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
