import os
import logging
import sqlite3
from datetime import datetime
from typing import Optional
import phonenumbers
from phonenumbers import NumberParseException

from telegram import (
    Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    ChatMemberUpdated, ChatMember, ChatJoinRequest
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    ChatJoinRequestHandler, ContextTypes, filters
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
        
        # Add table for join requests tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS join_requests (
                user_id INTEGER,
                chat_id INTEGER,
                request_date TIMESTAMP,
                status TEXT DEFAULT 'pending',
                PRIMARY KEY (user_id, chat_id)
            )
        ''')
        
        # 🔔 NEW: Reminder notifications tracking table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminder_notifications (
                user_id INTEGER PRIMARY KEY,
                first_reminder_date TIMESTAMP,
                reminder_count INTEGER DEFAULT 0,
                last_activity_date TIMESTAMP
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
    
    def add_join_request(self, user_id: int, chat_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO join_requests 
            (user_id, chat_id, request_date, status)
            VALUES (?, ?, ?, 'pending')
        ''', (user_id, chat_id, datetime.now()))
        conn.commit()
        conn.close()
    
    def update_join_request_status(self, user_id: int, chat_id: int, status: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE join_requests 
            SET status = ? 
            WHERE user_id = ? AND chat_id = ?
        ''', (status, user_id, chat_id))
        conn.commit()
        conn.close()
    
    # 🔔 NEW: Reminder system methods
    def has_received_reminder(self, user_id: int) -> bool:
        """Check if user has received a reminder before"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM reminder_notifications WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def add_reminder_notification(self, user_id: int):
        """Add reminder notification record"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO reminder_notifications 
            (user_id, first_reminder_date, reminder_count, last_activity_date)
            VALUES (?, ?, 1, ?)
        ''', (user_id, datetime.now(), datetime.now()))
        conn.commit()
        conn.close()
    
    def update_user_activity(self, user_id: int):
        """Update user's last activity"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO reminder_notifications 
            (user_id, first_reminder_date, reminder_count, last_activity_date)
            VALUES (?, 
                    COALESCE((SELECT first_reminder_date FROM reminder_notifications WHERE user_id = ?), ?),
                    COALESCE((SELECT reminder_count FROM reminder_notifications WHERE user_id = ?), 0),
                    ?)
        ''', (user_id, user_id, datetime.now(), user_id, datetime.now()))
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
    
    # NEW: Handle join requests
    async def handle_join_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle chat join requests - MOST IMPORTANT for private groups"""
        try:
            if not update.chat_join_request:
                return
            
            join_request = update.chat_join_request
            user = join_request.from_user
            chat = join_request.chat
            
            logger.info(f"📋 Join request: User {user.id} ({user.first_name}) wants to join chat {chat.id} ({chat.title})")
            
            # Skip bots and admin
            if user.is_bot or user.id == ADMIN_ID:
                logger.info(f"⏭️ Skipping bot/admin user {user.id}")
                return
            
            # Track join request
            self.db.add_join_request(user.id, chat.id)
            # Update user activity
            self.db.update_user_activity(user.id)
            
            if self.db.is_verified(user.id):
                # ✅ VERIFIED USER - Auto-approve and welcome
                try:
                    await context.bot.approve_chat_join_request(chat.id, user.id)
                    self.db.update_join_request_status(user.id, chat.id, 'approved')
                    logger.info(f"✅ Auto-approved verified user {user.id} for chat {chat.id}")
                    
                    # Send private welcome message
                    welcome_msg = f"""
🇵🇭 **Auto-Approved!** ✅

Hi {user.first_name}! 

Nag-auto approve ka sa:
📢 **{chat.title}**

✅ **Status:** Verified Filipino User
🚀 **Access:** Granted immediately!

Welcome sa community! 🎉
                    """
                    
                    await context.bot.send_message(user.id, welcome_msg, parse_mode=ParseMode.MARKDOWN)
                    logger.info(f"✅ Sent auto-approval welcome to user {user.id}")
                    
                    # Notify admin
                    admin_notification = f"""
✅ **Auto-Approved Join Request**

**User:** {user.first_name} (@{user.username or 'no_username'})
**ID:** `{user.id}`
**Chat:** {chat.title} (`{chat.id}`)
**Status:** Verified Filipino User - Auto-approved
                    """
                    await context.bot.send_message(ADMIN_ID, admin_notification, parse_mode=ParseMode.MARKDOWN)
                    
                except Exception as e:
                    logger.error(f"❌ Error auto-approving user {user.id}: {e}")
                    
            else:
                # ❌ UNVERIFIED USER - Send verification message but don't auto-approve
                # 🔔 Check if this is first-time reminder
                is_first_reminder = not self.db.has_received_reminder(user.id)
                
                try:
                    if is_first_reminder:
                        # 🔔 ENHANCED REMINDER MESSAGE - First time only
                        verification_msg = f"""
🔔 **REMINDER: Join Request Received!**

Hi {user.first_name}! 

Nakita kong nag-request ka to join:
📢 **{chat.title}**

⏳ **Current Status:** Pending approval

🔔 **ONE-TIME REMINDER:** Para sa mas mabilis na approval sa future, mag-verify ka na bilang Filipino user!

**Benefits ng Verification:**
✅ **Auto-approval** sa lahat ng Filipino groups
🚀 **No more waiting** for manual approval
🛡️ **Trusted member status**
📱 **One-time process lang**

**Paano mag-verify:**
1. I-click ang /start dito sa private chat
2. I-share ang Philippine phone number mo
3. Instant verification!

**IMPORTANT:** Pwede ka pa rin ma-approve ng admin kahit hindi verified, pero mas convenient kapag verified ka na.

*Hindi ka na makakatanggap ng reminder na ito ulit.*

👇 **I-click para mag-verify ngayon:**
/start
                        """
                        
                        # Mark reminder as sent
                        self.db.add_reminder_notification(user.id)
                        logger.info(f"🔔 Sent FIRST-TIME reminder to user {user.id}")
                    else:
                        # Regular message for repeat users
                        verification_msg = f"""
🇵🇭 **Join Request Received!**

Hi {user.first_name}! 

Nakita kong nag-request ka to join:
📢 **{chat.title}**

⏳ **Current Status:** Pending approval

**Para ma-approve agad sa future:**
• Mag-verify ka na bilang Filipino user
• I-click ang /start dito sa private chat
• I-share ang Philippine phone number mo

**Benefits:**
✅ Auto-approval sa Filipino groups
🚀 Mas mabilis na process
🛡️ Trusted member status

**IMPORTANT:** Pwede ka pa rin ma-approve ng admin kahit hindi verified.

👇 **I-click kung gusto mag-verify:**
/start
                        """
                        logger.info(f"📱 Sent regular verification message to repeat user {user.id}")
                    
                    await context.bot.send_message(user.id, verification_msg, parse_mode=ParseMode.MARKDOWN)
                    
                    # Notify admin about unverified join request
                    admin_notification = f"""
⏳ **New Join Request (Unverified)**

**User:** {user.first_name} (@{user.username or 'no_username'})
**ID:** `{user.id}`
**Chat:** {chat.title} (`{chat.id}`)
**Status:** Not verified - Manual approval needed
**Reminder:** {'First-time sent 🔔' if is_first_reminder else 'Repeat user (no reminder)'}

**Actions:**
• User was sent verification instructions
• Manual approval still required through Telegram
• Consider verifying user first for future auto-approvals
                    """
                    await context.bot.send_message(ADMIN_ID, admin_notification, parse_mode=ParseMode.MARKDOWN)
                    
                except Exception as e:
                    logger.warning(f"❌ Could not send verification to join requester {user.id}: {e}")
                    logger.warning("User might have disabled private messages from bots")
                    
                    # Still notify admin
                    admin_notification = f"""
⚠️ **Join Request (Could not contact user)**

**User:** {user.first_name} (@{user.username or 'no_username'})
**ID:** `{user.id}`
**Chat:** {chat.title} (`{chat.id}`)
**Issue:** Cannot send private message (user disabled bot messages)

**Manual approval needed through Telegram.**
                    """
                    await context.bot.send_message(ADMIN_ID, admin_notification, parse_mode=ParseMode.MARKDOWN)
                    
        except Exception as e:
            logger.error(f"Error in handle_join_request: {e}")
            logger.error(f"Update: {update}")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        
        # Update user activity
        self.db.update_user_activity(user.id)
        
        if self.db.is_verified(user.id):
            await update.message.reply_text(
                "✅ *Na-verify ka na!*\n\nWelcome sa Filipino community! 🇵🇭\n\n**Benefit:** Auto-approval sa lahat ng Filipino groups!",
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

**Benefits:**
✅ Auto-approval sa lahat ng Filipino groups
🚀 No more waiting for manual approval
🛡️ Trusted member status

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
        
        # Update user activity
        self.db.update_user_activity(user.id)
        
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

🚀 **NEW BENEFIT:** Auto-approval sa future join requests!
Hindi mo na kailangan maghintay sa admin approval.

**Next steps:**
• Pwede mo na i-rejoin ang mga groups na pending
• Auto-approve ka na sa new Filipino groups
• One-time verification lang ito!
            """
            
            await update.message.reply_text(success_msg, parse_mode=ParseMode.MARKDOWN)
            
            # Notify admin
            try:
                admin_msg = f"""
✅ *New Verified User*

**User:** {user.first_name} (@{user.username or 'no_username'})
**ID:** `{user.id}`
**Phone:** {phone_result['formatted_number']}
**Benefit:** Auto-approval enabled for join requests
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
        """Handle new members joining chat - Enhanced version with reminder system"""
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
            
            # Update user activity
            self.db.update_user_activity(user.id)
            
            # Check various join scenarios
            is_new_member = False
            
            # Scenario 1: User joined group/supergroup directly
            if (old_member.status == ChatMemberStatus.LEFT and 
                new_member.status == ChatMemberStatus.MEMBER):
                is_new_member = True
                logger.info(f"User {user.id} joined group {chat_id}")
            
            # Scenario 2: User was approved from restricted (join request approved)
            elif (old_member.status == ChatMemberStatus.RESTRICTED and 
                  new_member.status == ChatMemberStatus.MEMBER):
                is_new_member = True
                logger.info(f"User {user.id} approved in {chat_id}")
                # Update join request status
                self.db.update_join_request_status(user.id, chat_id, 'approved')
            
            if not is_new_member:
                return
            
            # Check if user is verified  
            if self.db.is_verified(user.id):
                # Verified user - send private welcome message only
                try:
                    welcome_msg = f"""
🇵🇭 **Welcome {user.first_name}!** ✅

Successfully joined:
📢 **{chat.title or 'Filipino Community'}**

✅ **Status:** Verified Filipino User
🛡️ **Access:** Full community privileges
🚀 **Benefit:** Auto-approve enabled for future groups
                    """
                    
                    await context.bot.send_message(user.id, welcome_msg, parse_mode=ParseMode.MARKDOWN)
                    logger.info(f"✅ Sent private welcome to verified user {user.id} for chat {chat_id}")
                    
                except Exception as e:
                    logger.info(f"❌ Could not send private welcome to user {user.id}: {e}")
            else:
                # Unverified user - PRIVATE MESSAGE with reminder system
                # 🔔 Check if this is first-time reminder
                is_first_reminder = not self.db.has_received_reminder(user.id)
                
                try:
                    if is_first_reminder:
                        # 🔔 ENHANCED MESSAGE for first-time users
                        private_verification_msg = f"""
🔔 **Welcome to Filipino Community!**

Hi {user.first_name}! 

Successfully joined:
📢 **{chat.title or 'Filipino Community'}**

🔔 **ONE-TIME REMINDER:** Para sa better experience at mas mabilis na approvals sa future groups, i-verify na Filipino user ka!

**Verification Benefits:**
✅ **Auto-approval** sa lahat ng Filipino groups
🚀 **No more waiting** for manual approval
🛡️ **Trusted member status**
📱 **One-time process lang**

**Paano mag-verify:**
1. I-click ang /start dito
2. I-share ang Philippine phone number mo
3. Instant verification!

**Optional lang ito, pero highly recommended para sa convenience!**

*Hindi ka na makakatanggap ng reminder na ito ulit.*

👇 **I-click kung gusto mo mag-verify ngayon:**
/start
                        """
                        
                        # Mark reminder as sent
                        self.db.add_reminder_notification(user.id)
                        logger.info(f"🔔 Sent FIRST-TIME group join reminder to user {user.id}")
                    else:
                        # Regular message for repeat users
                        private_verification_msg = f"""
🇵🇭 **Welcome to Filipino Community!**

Hi {user.first_name}! 

Successfully joined:
📢 **{chat.title or 'Filipino Community'}**

**Para sa better experience:**
📱 I-verify na Filipino user ka for faster approvals sa future groups

**Verification Benefits:**
✅ Auto-approval sa lahat ng Filipino groups
🚀 No more waiting for manual approval
🛡️ Trusted member status

**Optional lang ito, pero recommended para sa convenience!**

👇 **I-click kung gusto mo mag-verify:**
/start
                        """
                        logger.info(f"📱 Sent regular verification message to repeat user {user.id}")
                    
                    await context.bot.send_message(user.id, private_verification_msg, parse_mode=ParseMode.MARKDOWN)
                    
                except Exception as e:
                    logger.warning(f"❌ Could not send message to user {user.id}: {e}")
                    
        except Exception as e:
            logger.error(f"Error in handle_chat_member_update: {e}")

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
                try:
                    admin_setup_msg = f"""
🇵🇭 **Bot Setup Complete!**

Bot is now active sa:
📢 **{chat.title}** (`{chat.id}`)

**Features Enabled:**
✅ Auto-detect join requests  
📱 Private verification messages
🛡️ Auto-approval for verified users
🎯 Manual approval recommendation for unverified
🔔 Smart reminder system (one-time per user)

**Join Request Process:**
• Verified users = Auto-approve + welcome
• Unverified users = Verification message + manual approval needed
• First-time unverified = Enhanced reminder with 🔔
• Repeat unverified = Regular message (no spam)

**Bot Status:** Ready! 🚀
                    """
                    await context.bot.send_message(ADMIN_ID, admin_setup_msg, parse_mode=ParseMode.MARKDOWN)
                    logger.info(f"✅ Sent private setup confirmation to admin for chat {chat.id}")
                except Exception as e:
                    logger.error(f"Error notifying admin about setup: {e}")
                    
        except Exception as e:
            logger.error(f"Error in handle_my_chat_member_update: {e}")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Help command - Show verification instructions"""
        user = update.effective_user
        
        # Update user activity
        self.db.update_user_activity(user.id)
        
        if self.db.is_verified(user.id):
            help_msg = """
🇵🇭 **Na-verify ka na!** ✅

**Available Commands:**
• `/start` - Show verification status
• `/help` - Show this help message

**Your Status:** Verified Filipino User 🎉
**Benefits:** 
✅ Auto-approval sa join requests
🚀 Access sa lahat ng Filipino channels/groups
            """
        else:
            help_msg = """
🇵🇭 **Filipino Verification Bot**

**Para ma-verify:**
1. I-type ang `/start` 
2. I-click ang "Share Phone Number" button
3. Automatic approval kapag Philippine number (+63)

**Requirements:**
📱 Valid Philippine mobile number
🇵🇭 Must be from Philippines

**Benefits:**
✅ Auto-approval sa join requests
🛡️ Trusted member status
🚀 One-time verification lang

I-type ang `/start` para magsimula!
            """
        
        await update.message.reply_text(help_msg, parse_mode=ParseMode.MARKDOWN)
    
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
        """Show stats (Admin only) - Enhanced with reminder stats"""
        if update.effective_user.id != ADMIN_ID:
            return
            
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # Basic stats
        cursor.execute('SELECT COUNT(*) FROM verified_users WHERE is_banned = FALSE')
        verified_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM verified_users WHERE is_banned = TRUE')
        banned_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM join_requests WHERE status = "pending"')
        pending_requests = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM join_requests WHERE status = "approved"')
        approved_requests = cursor.fetchone()[0]
        
        # 🔔 NEW: Reminder stats
        cursor.execute('SELECT COUNT(*) FROM reminder_notifications')
        total_reminders_sent = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM reminder_notifications WHERE first_reminder_date IS NOT NULL')
        users_with_reminders = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM reminder_notifications WHERE last_activity_date > datetime("now", "-7 days")')
        active_users_week = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM reminder_notifications WHERE last_activity_date > datetime("now", "-30 days")')
        active_users_month = cursor.fetchone()[0]
        
        conn.close()
        
        stats_msg = f"""
📊 **Filipino Bot Statistics**

**👥 User Stats:**
✅ Verified Users: `{verified_count}`
🚫 Banned Users: `{banned_count}`
👤 Total Registered: `{verified_count + banned_count}`

**📋 Join Request Stats:**
⏳ Pending Requests: `{pending_requests}`
✅ Approved Requests: `{approved_requests}`
📊 Total Requests: `{pending_requests + approved_requests}`

**🔔 Reminder System Stats:**
📨 Total Reminders Sent: `{total_reminders_sent}`
👥 Users Who Received Reminders: `{users_with_reminders}`
🔥 Active Users (7 days): `{active_users_week}`
📈 Active Users (30 days): `{active_users_month}`

**💡 System Performance:**
• One-time reminder per user ✅
• Smart spam prevention ✅
• Activity tracking enabled ✅
• Auto-approval for verified users ✅

Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        await update.message.reply_text(stats_msg, parse_mode=ParseMode.MARKDOWN)
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Exception while handling update: {context.error}")
        logger.error(f"Update: {update}")

def main():
    """Start the bot"""
    bot_manager = FilipinoBotManager()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", bot_manager.start_command))
    application.add_handler(CommandHandler("help", bot_manager.help_command))
    application.add_handler(CommandHandler("ban", bot_manager.ban_command))
    application.add_handler(CommandHandler("stats", bot_manager.stats_command))
    
    # Handle phone number verification
    application.add_handler(MessageHandler(filters.CONTACT, bot_manager.handle_contact_message))
    
    # Handle join requests (MOST IMPORTANT for private groups)
    application.add_handler(ChatJoinRequestHandler(bot_manager.handle_join_request))
    
    # Handle chat member updates (when users join groups)
    application.add_handler(ChatMemberHandler(bot_manager.handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    
    # Handle bot being added to chats
    application.add_handler(ChatMemberHandler(bot_manager.handle_my_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER))
    
    # Error handler
    application.add_error_handler(bot_manager.error_handler)
    
    # Start bot
    logger.info("🇵🇭 Filipino Bot started!")
    logger.info("🔔 New Reminder Features Added:")
    logger.info("✅ One-time reminder system enabled")
    logger.info("✅ Smart spam prevention active")
    logger.info("✅ Activity tracking enabled")
    logger.info("✅ Enhanced messaging for first-time users")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
