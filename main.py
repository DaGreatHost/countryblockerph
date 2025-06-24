import os
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
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
        
        # Enhanced reminder notifications tracking table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminder_notifications (
                user_id INTEGER PRIMARY KEY,
                first_reminder_date TIMESTAMP,
                reminder_count INTEGER DEFAULT 0,
                last_activity_date TIMESTAMP,
                last_reminder_date TIMESTAMP,
                verification_started BOOLEAN DEFAULT FALSE,
                reminder_paused BOOLEAN DEFAULT FALSE
            )
        ''')
        
        # New table for tracking verification attempts
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS verification_attempts (
                user_id INTEGER,
                attempt_date TIMESTAMP,
                phone_number TEXT,
                success BOOLEAN DEFAULT FALSE,
                error_message TEXT,
                PRIMARY KEY (user_id, attempt_date)
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
        
        # Mark verification as completed in attempts table
        cursor.execute('''
            INSERT INTO verification_attempts 
            (user_id, attempt_date, phone_number, success)
            VALUES (?, ?, ?, TRUE)
        ''', (user_id, datetime.now(), phone_number))
        
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
    
    # Enhanced reminder system methods
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
            (user_id, first_reminder_date, reminder_count, last_activity_date, last_reminder_date)
            VALUES (?, ?, 1, ?, ?)
        ''', (user_id, datetime.now(), datetime.now(), datetime.now()))
        conn.commit()
        conn.close()
    
    def update_user_activity(self, user_id: int):
        """Update user's last activity"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO reminder_notifications 
            (user_id, first_reminder_date, reminder_count, last_activity_date, last_reminder_date, verification_started, reminder_paused)
            VALUES (?, 
                    COALESCE((SELECT first_reminder_date FROM reminder_notifications WHERE user_id = ?), ?),
                    COALESCE((SELECT reminder_count FROM reminder_notifications WHERE user_id = ?), 0),
                    ?,
                    COALESCE((SELECT last_reminder_date FROM reminder_notifications WHERE user_id = ?), ?),
                    COALESCE((SELECT verification_started FROM reminder_notifications WHERE user_id = ?), FALSE),
                    COALESCE((SELECT reminder_paused FROM reminder_notifications WHERE user_id = ?), FALSE))
        ''', (user_id, user_id, datetime.now(), user_id, datetime.now(), user_id, datetime.now(), user_id, user_id))
        conn.commit()
        conn.close()
    
    def mark_verification_started(self, user_id: int):
        """Mark that user started verification process"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO reminder_notifications 
            (user_id, first_reminder_date, reminder_count, last_activity_date, last_reminder_date, verification_started, reminder_paused)
            VALUES (?, 
                    COALESCE((SELECT first_reminder_date FROM reminder_notifications WHERE user_id = ?), ?),
                    COALESCE((SELECT reminder_count FROM reminder_notifications WHERE user_id = ?), 0),
                    ?,
                    COALESCE((SELECT last_reminder_date FROM reminder_notifications WHERE user_id = ?), ?),
                    TRUE,
                    COALESCE((SELECT reminder_paused FROM reminder_notifications WHERE user_id = ?), FALSE))
        ''', (user_id, user_id, datetime.now(), user_id, datetime.now(), user_id, datetime.now(), user_id))
        conn.commit()
        conn.close()
    
    def get_incomplete_verifications(self) -> list:
        """Get users who started verification but didn't complete it"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Users who received reminders but are not verified
        # and haven't been reminded in the last 24 hours
        cursor.execute('''
            SELECT rn.user_id, rn.first_reminder_date, rn.reminder_count, rn.last_activity_date
            FROM reminder_notifications rn
            LEFT JOIN verified_users vu ON rn.user_id = vu.user_id
            WHERE (vu.user_id IS NULL OR vu.is_banned = TRUE)
            AND rn.last_activity_date < datetime('now', '-24 hours')
            AND rn.reminder_count < 3
            AND rn.first_reminder_date < datetime('now', '-24 hours')
            AND rn.reminder_paused = FALSE
            AND (rn.last_reminder_date IS NULL OR rn.last_reminder_date < datetime('now', '-24 hours'))
        ''')
        
        result = cursor.fetchall()
        conn.close()
        return result
    
    def increment_reminder_count(self, user_id: int):
        """Increment reminder count for a user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE reminder_notifications 
            SET reminder_count = reminder_count + 1,
                last_reminder_date = ?,
                last_activity_date = ?
            WHERE user_id = ?
        ''', (datetime.now(), datetime.now(), user_id))
        conn.commit()
        conn.close()
    
    def add_verification_attempt(self, user_id: int, phone_number: str, success: bool, error_message: str = None):
        """Track verification attempts"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO verification_attempts 
            (user_id, attempt_date, phone_number, success, error_message)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, datetime.now(), phone_number, success, error_message))
        conn.commit()
        conn.close()
    
    def pause_reminders(self, user_id: int):
        """Pause reminders for a user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE reminder_notifications 
            SET reminder_paused = TRUE
            WHERE user_id = ?
        ''', (user_id,))
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
        self.bot = None
        self.reminder_task = None
    
    def start_reminder_scheduler(self):
        """Start the reminder scheduler task"""
        if self.reminder_task is None or self.reminder_task.done():
            self.reminder_task = asyncio.create_task(self._reminder_scheduler_loop())
            logger.info("🔔 Reminder scheduler started")
    
    async def _reminder_scheduler_loop(self):
        """Reminder scheduler loop that runs every 6 hours"""
        while True:
            try:
                # Wait 6 hours (21600 seconds)
                await asyncio.sleep(21600)
                await self.send_proactive_reminders()
            except Exception as e:
                logger.error(f"Error in reminder scheduler: {e}")
                # Wait 1 hour before retry on error
                await asyncio.sleep(3600)
    
    async def send_proactive_reminders(self):
        """Send proactive reminders to users who haven't completed verification"""
        try:
            incomplete_users = self.db.get_incomplete_verifications()
            
            if not incomplete_users:
                logger.info("📅 No users need proactive reminders")
                return
            
            logger.info(f"📅 Sending proactive reminders to {len(incomplete_users)} users")
            success_count = 0
            
            for user_data in incomplete_users:
                user_id, first_reminder, reminder_count, last_activity = user_data
                
                try:
                    # Get user info for personalized message
                    user_info = await self.get_user_info(user_id)
                    first_name = user_info.get('first_name', 'Kababayan')
                    
                    # Send different messages based on reminder count
                    if reminder_count == 1:
                        # Second reminder (first proactive - after 24 hours)
                        reminder_msg = self.get_second_reminder_message(first_name)
                    elif reminder_count == 2:
                        # Third reminder (final - after 48 hours)
                        reminder_msg = self.get_final_reminder_message(first_name)
                    else:
                        continue  # Skip if already sent 3 reminders
                    
                    # Send the reminder
                    await self.bot.send_message(user_id, reminder_msg, parse_mode=ParseMode.MARKDOWN)
                    
                    # Update reminder count
                    self.db.increment_reminder_count(user_id)
                    
                    success_count += 1
                    logger.info(f"✅ Sent proactive reminder #{reminder_count + 1} to user {user_id}")
                    
                    # Small delay to avoid rate limiting
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    logger.error(f"❌ Failed to send reminder to user {user_id}: {e}")
            
            # Notify admin about reminder batch
            try:
                admin_msg = f"""
📅 **Mga Proactive Reminders Naipadala**

✅ **Successful reminders:** {success_count}/{len(incomplete_users)}
🕐 **Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

**Reminder Schedule:**
• Reminder #1: Sa join request/group join (instant)
• Reminder #2: After 24 hours (proactive)
• Reminder #3: After 48 hours (final reminder)
• Maximum: 3 reminders per user

**Next batch:** Sa susunod na 6 hours
                """
                await self.bot.send_message(ADMIN_ID, admin_msg, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.error(f"Failed to notify admin about reminder batch: {e}")
                
        except Exception as e:
            logger.error(f"Error in send_proactive_reminders: {e}")
    
    async def get_user_info(self, user_id: int) -> dict:
        """Get user info from Telegram API"""
        try:
            chat = await self.bot.get_chat(user_id)
            return {
                'first_name': chat.first_name or 'Kababayan',
                'username': chat.username
            }
        except Exception as e:
            logger.error(f"Could not get user info for {user_id}: {e}")
            return {'first_name': 'Kababayan', 'username': None}
    
    def get_second_reminder_message(self, first_name: str) -> str:
        """Get second reminder message (24 hours after first)"""
        return f"""
🔔 **Paalala: Verification Reminder**

Hi {first_name}! 

Nakita ko na nag-start ka ng verification process kahapon, pero hindi pa natatapos. 🤔

**Bakit hindi pa natapos?**
• Nakalimutan mo lang siguro i-click ang /start
• Busy ka sa ibang gawain
• Hindi mo alam kung paano mag-continue

**Simple lang pala:**
📱 Para ma-verify ka bilang Filipino user
✅ Auto-approval sa lahat ng Filipino groups
🚀 Mas convenient para sa future

**Paano tapusin NGAYON:**
1. I-click ang /start
2. I-share ang Philippine phone number mo
3. Tapos na! 🎉

**Bakit mo kailangan?**
• Mas mabilis na group approvals
• Trusted member status
• One-time lang 'to
• Walang hassle sa future join requests

**Hindi ka na makakakuha ng maraming reminders - 1 pa lang after nito.**

👇 **I-click para tapusin ngayon:**
/start

---
*Automatic reminder lang 'to - hindi mo kailangan mag-reply*
        """
    
    def get_final_reminder_message(self, first_name: str) -> str:
        """Get final reminder message (48 hours after first)"""
        return f"""
🔔 **Huling Paalala: Filipino Verification**

Hi {first_name}! 

Ito na ang huling reminder tungkol sa verification. Final na 'to! 

**Recap:**
• Nag-start ka ng verification process
• Hindi pa natatapos hanggang ngayon
• 48 hours na ang nakalipas
• Ito na ang 3rd at final reminder

**Last chance benefits:**
✅ **Auto-approval** sa lahat ng Filipino groups
🚀 **Walang hintay** sa manual approval
🛡️ **Trusted member** status agad
📱 **One-time process** lang
🎯 **VIP treatment** sa future groups

**Paano tapusin RIGHT NOW:**
1. I-click ang /start
2. I-share ang Philippine phone number
3. Verified ka na! 🎉
4. Tapos na lahat ng reminders!

**IMPORTANT:**
• Walang susunod na reminders after nito
• Optional lang naman, pero sobrang convenient
• Madaling gawin, 2 minutes lang
• Maraming Filipino users na satisfied sa benefits

**Testimonial from verified users:**
💬 "Sobrang convenient! Auto-approve na agad sa groups!"
💬 "Hindi na ako naghihintay ng manual approval!"
💬 "One-time verification lang, lifetime benefits!"

👇 **I-click para sa FINAL verification:**
/start

---
*Huling reminder na 'to. Salamat sa pagintindi! 🇵🇭*
*Para ma-stop ang reminders, i-type lang ang /pause_reminders*
        """
    
    # Handle join requests - Enhanced
    async def handle_join_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle chat join requests - Enhanced with better reminder system"""
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
                # ❌ UNVERIFIED USER - Send verification message
                is_first_reminder = not self.db.has_received_reminder(user.id)
                
                try:
                    if is_first_reminder:
                        # Enhanced first-time reminder message
                        verification_msg = f"""
🔔 **WELCOME! Join Request Received**

Hi {user.first_name}! 

Nakita kong nag-request ka to join:
📢 **{chat.title}**

⏳ **Current Status:** Pending approval

🔔 **SPECIAL OPPORTUNITY:** Para sa mas mabilis na approval sa future at better experience, mag-verify ka na bilang Filipino user!

**Benefits ng Verification:**
✅ **Auto-approval** sa lahat ng Filipino groups (instant!)
🚀 **No more waiting** for manual approval
🛡️ **Trusted member status** sa community
📱 **One-time process lang** - lifetime benefits
🎯 **VIP treatment** sa future join requests
⚡ **Priority access** sa Filipino channels

**Paano mag-verify (2 minutes lang):**
1. I-click ang /start dito sa private chat
2. I-share ang Philippine phone number mo
3. Instant verification!
4. Auto-approve na sa future groups!

**IMPORTANT:** 
• Pwede ka pa rin ma-approve ng admin kahit hindi verified
• Pero mas convenient kapag verified ka na
• Maraming verified users na satisfied sa benefits
• Optional lang, pero highly recommended

**Smart Reminder System:**
• May 3 gentle reminders lang max
• Hindi spam - may 24-hour intervals
• Pwede mo i-pause anytime with /pause_reminders

*Ito ang first reminder mo. Next reminder sa 24 hours kung hindi ka pa mag-verify.*

👇 **I-click para mag-verify ngayon (recommended):**
/start
                        """
                        
                        # Mark reminder as sent
                        self.db.add_reminder_notification(user.id)
                        logger.info(f"🔔 Sent ENHANCED first-time reminder to user {user.id}")
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

**Para ma-stop ang reminders:** /pause_reminders
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
**Reminder:** {'Enhanced first-time sent 🔔' if is_first_reminder else 'Repeat user (regular message)'}

**Actions:**
• User was sent verification instructions
• Manual approval still required through Telegram
• Smart reminder system will follow up automatically
• User can pause reminders with /pause_reminders
                    """
                    await context.bot.send_message(ADMIN_ID, admin_notification, parse_mode=ParseMode.MARKDOWN)
                    
                except Exception as e:
                    logger.warning(f"❌ Could not send verification to join requester {user.id}: {e}")
                    
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
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command - Enhanced with better tracking"""
        user = update.effective_user
        
        # Update user activity and mark verification as started
        self.db.update_user_activity(user.id)
        self.db.mark_verification_started(user.id)
        
        if self.db.is_verified(user.id):
            await update.message.reply_text(
                "✅ **Na-verify ka na!**\n\nWelcome sa Filipino community! 🇵🇭\n\n**Benefits:** Auto-approval sa lahat ng Filipino groups!\n\n**Commands:**\n• /help - Show help\n• /stats - Your verification info",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Check if this is a retry from reminder
        if self.db.has_received_reminder(user.id):
            # User is retrying after reminder
            retry_msg = f"""
🔄 **Salamat sa pagbalik!** 

Great choice, {user.first_name}! Tapusin na natin ang verification process:

**Benefits mo after verification:**
✅ Auto-approval sa lahat ng Filipino groups
🚀 No more manual approval waiting
🛡️ Trusted member status
📱 One-time lang 'to!

**Ready na? Let's do this!**
👇 **I-click para mag-share ng phone number:**
            """
            await update.message.reply_text(retry_msg, parse_mode=ParseMode.MARKDOWN)
        
        # Start verification process
        await self.start_verification(update, context)
    
    async def start_verification(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start phone verification process - Enhanced"""
        user = update.effective_user
        
        contact_keyboard = [[KeyboardButton("📱 I-Share ang Phone Number Ko", request_contact=True)]]
        contact_markup = ReplyKeyboardMarkup(
            contact_keyboard, 
            one_time_keyboard=True, 
            resize_keyboard=True
        )
        
        verification_msg = f"""
🇵🇭 **Filipino Verification Process**

Hi {user.first_name}! Para ma-verify ka bilang Filipino user, i-share lang ang phone number mo.

**Requirements:**
• Philippine number (+63) lang accepted
• I-click lang ang button sa baba
• Automatic approval kapag verified

**Benefits after verification:**
✅ **Auto-approval** sa lahat ng Filipino groups
🚀 **No more waiting** for manual approval
🛡️ **Trusted member status** sa community
📱 **One-time verification** - lifetime benefits
🎯 **VIP treatment** sa future join requests

**Privacy Note:**
• Phone number mo ay private at secure
• Hindi namin ito ishare sa iba
• For verification purposes lang

👇 **I-click para mag-share (safe 'to):**
        """
        
        await update.message.reply_text(
            verification_msg,
            reply_markup=contact_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def handle_contact(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle shared contact information - Enhanced with better validation"""
        user = update.effective_user
        contact = update.message.contact
        
        # Update user activity
        self.db.update_user_activity(user.id)
        
        # Remove keyboard
        await update.message.reply_text(
            "📱 **Phone number received!** Processing...",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        
        if not contact or not contact.phone_number:
            await update.message.reply_text(
                "❌ **Hindi nakakuha ng phone number.** Try ulit:\n\n/start",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Verify phone number
        verification_result = self.verifier.verify_phone_number(contact.phone_number)
        
        if verification_result['is_filipino']:
            # ✅ SUCCESSFUL VERIFICATION
            self.db.add_verified_user(
                user.id, 
                user.username, 
                user.first_name, 
                verification_result['formatted_number']
            )
            
            success_msg = f"""
🎉 **VERIFIED SUCCESSFULLY!** ✅

Congratulations {user.first_name}! 

✅ **Status:** Verified Filipino User
📱 **Number:** {verification_result['formatted_number']}
🇵🇭 **Country:** Philippines

**Benefits mo ngayon:**
✅ **Auto-approval** sa lahat ng Filipino groups
🚀 **No more waiting** for manual approval
🛡️ **Trusted member status** sa community
📱 **One-time verification** - lifetime benefits
🎯 **Priority access** sa Filipino channels

**Next Steps:**
• Join any Filipino group - auto-approve ka na!
• Share sa friends mo ang bot para ma-verify din sila
• Enjoy ang seamless group experience!

**Commands:**
• /help - Show help commands
• /stats - View your verification info

**Welcome sa verified Filipino community!** 🇵🇭🎉

*Hindi ka na makakakuha ng verification reminders.*
            """
            
            await update.message.reply_text(success_msg, parse_mode=ParseMode.MARKDOWN)
            
            # Record successful attempt
            self.db.add_verification_attempt(
                user.id, 
                contact.phone_number, 
                True
            )
            
            # Notify admin
            admin_msg = f"""
🎉 **New Verified User!**

**User:** {user.first_name} (@{user.username or 'no_username'})
**ID:** `{user.id}`
**Phone:** {verification_result['formatted_number']}
**Region:** {verification_result['region']}
**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

**Total Verified Users:** {self.get_verified_count()}
            """
            await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode=ParseMode.MARKDOWN)
            
            logger.info(f"✅ User {user.id} verified successfully with PH number")
            
        else:
            # ❌ FAILED VERIFICATION
            error_reasons = []
            if not verification_result['is_valid']:
                error_reasons.append("Invalid phone number format")
            if verification_result['country_code'] != 63:
                error_reasons.append(f"Not a Philippine number (Country: {verification_result['region'] or 'Unknown'})")
            
            error_msg = f"""
❌ **Verification Failed**

Sorry {user.first_name}, hindi ma-verify ang phone number mo.

**Problema:**
• {' • '.join(error_reasons) if error_reasons else 'Hindi Philippine number'}

**Requirements:**
📱 **Philippine number lang** (+63) ang accepted
🇵🇭 **Format examples:** 
   • +639171234567
   • 09171234567
   • 9171234567

**Paano mag-retry:**
1. I-click ulit ang /start
2. I-share ang tamang PH number
3. Automatic verification

**Need help?** Contact admin or try ulit with correct PH number.

👇 **I-click para mag-retry:**
/start
            """
            
            await update.message.reply_text(error_msg, parse_mode=ParseMode.MARKDOWN)
            
            # Record failed attempt
            self.db.add_verification_attempt(
                user.id, 
                contact.phone_number, 
                False, 
                f"Not PH number: {verification_result.get('region', 'Unknown')}"
            )
            
            # Notify admin of failed attempt
            admin_msg = f"""
❌ **Verification Failed**

**User:** {user.first_name} (@{user.username or 'no_username'})
**ID:** `{user.id}`
**Phone:** {contact.phone_number}
**Formatted:** {verification_result['formatted_number']}
**Country:** {verification_result.get('region', 'Unknown')}
**Reason:** {' | '.join(error_reasons) if error_reasons else 'Not PH number'}
            """
            await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode=ParseMode.MARKDOWN)
            
            logger.info(f"❌ User {user.id} verification failed - not PH number")
    
    def get_verified_count(self) -> int:
        """Get total count of verified users"""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM verified_users WHERE is_banned = FALSE')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help information"""
        user = update.effective_user
        is_verified = self.db.is_verified(user.id)
        
        if is_verified:
            help_msg = """
🇵🇭 **Filipino Bot Help - Verified User**

✅ **Your Status:** Verified Filipino User

**Available Commands:**
• `/start` - Show verification status
• `/help` - Show this help message
• `/stats` - View your verification details
• `/pause_reminders` - Pause reminder notifications (if any)

**Benefits:**
✅ Auto-approval sa lahat ng Filipino groups
🚀 No more waiting for manual approval
🛡️ Trusted member status
📱 Priority access sa Filipino channels

**How it works:**
• Kapag mag-join ka sa Filipino group, auto-approve ka agad
• Hindi ka na kailangan mag-wait ng manual approval
• One-time verification lang, lifetime benefits

**Need help?** Contact admin o mag-message sa support.
            """
        else:
            help_msg = """
🇵🇭 **Filipino Bot Help - Unverified User**

❌ **Your Status:** Not verified yet

**Available Commands:**
• `/start` - Begin verification process
• `/help` - Show this help message
• `/pause_reminders` - Pause reminder notifications

**Para ma-verify:**
1. I-click ang `/start`
2. I-share ang Philippine phone number mo
3. Automatic verification!

**Benefits after verification:**
✅ Auto-approval sa lahat ng Filipino groups
🚀 No more waiting for manual approval
🛡️ Trusted member status
📱 One-time verification - lifetime benefits

**Requirements:**
📱 Philippine phone number (+63) lang
🇵🇭 Valid format (09XX, +639XX, etc.)

**Ready to verify?** I-click ang `/start`
            """
        
        await update.message.reply_text(help_msg, parse_mode=ParseMode.MARKDOWN)
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user statistics and verification info"""
        user = update.effective_user
        
        if self.db.is_verified(user.id):
            # Get verification details
            conn = sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT phone_number, verified_date FROM verified_users 
                WHERE user_id = ? AND is_banned = FALSE
            ''', (user.id,))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                phone, verified_date = result
                verified_datetime = datetime.fromisoformat(verified_date)
                
                stats_msg = f"""
📊 **Your Verification Stats**

✅ **Status:** Verified Filipino User
👤 **Name:** {user.first_name}
📱 **Phone:** {phone}
📅 **Verified:** {verified_datetime.strftime('%B %d, %Y')}
⏰ **Time:** {verified_datetime.strftime('%I:%M %p')}
🕐 **Days verified:** {(datetime.now() - verified_datetime).days} days

**Benefits Active:**
✅ Auto-approval sa Filipino groups
🚀 Priority access sa channels
🛡️ Trusted member status
📱 No manual approval needed

**Global Stats:**
👥 **Total verified users:** {self.get_verified_count()}
🇵🇭 **Community:** Filipino Telegram Users

**Thank you for being part of our verified community!** 🎉
                """
            else:
                stats_msg = "❌ **Error:** Cannot find your verification details."
        else:
            stats_msg = f"""
📊 **Your Account Stats**

❌ **Status:** Not verified yet
👤 **Name:** {user.first_name}
🆔 **User ID:** `{user.id}`

**To get verified:**
1. I-click ang `/start`
2. I-share ang Philippine phone number
3. Enjoy auto-approvals!

**After verification benefits:**
✅ Auto-approval sa Filipino groups
🚀 Priority access sa channels
🛡️ Trusted member status
📱 Lifetime benefits

**Ready to verify?** `/start`
            """
        
        await update.message.reply_text(stats_msg, parse_mode=ParseMode.MARKDOWN)
    
    async def pause_reminders_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Pause reminder notifications for user"""
        user = update.effective_user
        
        # Pause reminders for the user
        self.db.pause_reminders(user.id)
        
        pause_msg = f"""
🔕 **Reminders Paused**

Hi {user.first_name}!

✅ **Status:** Reminder notifications ay na-pause na
🔕 **Effect:** Hindi ka na makakakuha ng verification reminders
⏸️ **Duration:** Permanent (until you verify)

**Note:**
• Pwede ka pa rin mag-verify anytime with `/start`
• Auto-approvals ay available pa rin after verification
• Reminder pause ay para sa notifications lang

**To verify later:** `/start`
**Need help:** `/help`

**Salamat sa feedback!** 🙏
        """
        
        await update.message.reply_text(pause_msg, parse_mode=ParseMode.MARKDOWN)
        
        # Notify admin
        admin_msg = f"""
🔕 **User Paused Reminders**

**User:** {user.first_name} (@{user.username or 'no_username'})
**ID:** `{user.id}`
**Action:** Paused verification reminders
**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

User can still verify with /start but won't receive proactive reminders.
        """
        try:
            await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Failed to notify admin about paused reminders: {e}")
        
        logger.info(f"🔕 User {user.id} paused reminder notifications")
    
    # Admin commands
    async def admin_stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin command to get bot statistics"""
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("❌ **Admin access lang.**")
            return
        
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # Get various statistics
        cursor.execute('SELECT COUNT(*) FROM verified_users WHERE is_banned = FALSE')
        verified_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM verified_users WHERE is_banned = TRUE')
        banned_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM join_requests')
        total_join_requests = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM join_requests WHERE status = "approved"')
        approved_requests = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM reminder_notifications')
        total_reminders_sent = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM verification_attempts WHERE success = TRUE')
        successful_verifications = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM verification_attempts WHERE success = FALSE')
        failed_verifications = cursor.fetchone()[0]
        
        # Get recent activity (last 24 hours)
        cursor.execute('''
            SELECT COUNT(*) FROM verified_users 
            WHERE verified_date > datetime('now', '-24 hours') AND is_banned = FALSE
        ''')
        recent_verifications = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT COUNT(*) FROM reminder_notifications 
            WHERE reminder_paused = TRUE
        ''')
        paused_reminders = cursor.fetchone()[0]
        
        conn.close()
        
        admin_stats = f"""
📊 **Admin Bot Statistics**

**User Statistics:**
👥 **Verified users:** {verified_count}
🚫 **Banned users:** {banned_count}
📱 **Recent verifications (24h):** {recent_verifications}

**Join Request Statistics:**
📋 **Total join requests:** {total_join_requests}
✅ **Auto-approved requests:** {approved_requests}
📈 **Approval rate:** {(approved_requests/total_join_requests*100):.1f}% (if > 0)

**Verification Attempts:**
✅ **Successful:** {successful_verifications}
❌ **Failed:** {failed_verifications}
📊 **Success rate:** {(successful_verifications/(successful_verifications+failed_verifications)*100):.1f}% (if > 0)

**Reminder System:**
🔔 **Total reminders sent:** {total_reminders_sent}
🔕 **Users with paused reminders:** {paused_reminders}

**System Status:**
🟢 **Bot Status:** Online
🔄 **Reminder Scheduler:** {"Running" if self.reminder_task and not self.reminder_task.done() else "Stopped"}
📅 **Last Update:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

**Commands:**
• `/admin_recent` - Recent user activity
• `/admin_pending` - Users pending verification
• `/ban_user <user_id>` - Ban a user
• `/unban_user <user_id>` - Unban a user
        """
        
        await update.message.reply_text(admin_stats, parse_mode=ParseMode.MARKDOWN)
    
    async def ban_user_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin command to ban a user"""
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("❌ **Admin access lang.**")
            return
        
        if not context.args:
            await update.message.reply_text("**Usage:** `/ban_user <user_id>`", parse_mode=ParseMode.MARKDOWN)
            return
        
        try:
            user_id = int(context.args[0])
            self.db.ban_user(user_id)
            
            ban_msg = f"""
🚫 **User Banned**

**User ID:** `{user_id}`
**Status:** Banned from verification system
**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

User will no longer have auto-approval privileges.
            """
            
            await update.message.reply_text(ban_msg, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"🚫 Admin banned user {user_id}")
            
        except ValueError:
            await update.message.reply_text("❌ **Invalid user ID.** Must be numeric.")
        except Exception as e:
            await update.message.reply_text(f"❌ **Error banning user:** {str(e)}")
    
    async def handle_chat_member_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle chat member updates (when users join groups)"""
        try:
            if not update.chat_member:
                return
            
            member_update = update.chat_member
            user = member_update.from_user
            chat = update.effective_chat
            
            # Skip bots and admin
            if user.is_bot or user.id == ADMIN_ID:
                return
            
            # Check if user was added to group (not just status change)
            if (member_update.old_chat_member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED] and
                member_update.new_chat_member.status == ChatMemberStatus.MEMBER):
                
                logger.info(f"👥 User {user.id} ({user.first_name}) joined chat {chat.id} ({chat.title})")
                
                # Update user activity
                self.db.update_user_activity(user.id)
                
                # Send verification reminder if not verified
                if not self.db.is_verified(user.id):
                    try:
                        join_msg = f"""
🎉 **Welcome sa {chat.title}!**

Hi {user.first_name}! 

**Para sa better experience:**
✅ Mag-verify ka bilang Filipino user
🚀 Auto-approval sa future groups
📱 One-time verification lang

**Benefits:**
• No more manual approval waiting
• Trusted member status
• Priority access sa Filipino channels

**Paano mag-verify:**
1. I-click ang /start dito sa private chat
2. I-share ang Philippine phone number
3. Verified ka na!

👇 **I-click para mag-verify:**
/start

*Optional lang 'to - enjoy sa group!* 🇵🇭
                        """
                        
                        await context.bot.send_message(user.id, join_msg, parse_mode=ParseMode.MARKDOWN)
                        logger.info(f"📱 Sent group join verification reminder to user {user.id}")
                        
                    except Exception as e:
                        logger.warning(f"Could not send group join message to user {user.id}: {e}")
                
        except Exception as e:
            logger.error(f"Error in handle_chat_member_update: {e}")
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Exception while handling an update: {context.error}")
        
        # Notify admin of critical errors
        if ADMIN_ID:
            try:
                error_msg = f"""
⚠️ **Bot Error**

**Error:** `{str(context.error)}`
**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Update:** {str(update)[:500]}...

Please check bot logs for more details.
                """
                await context.bot.send_message(ADMIN_ID, error_msg, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.error(f"Could not notify admin of error: {e}")
    
    def run(self):
        """Run the bot"""
        try:
            # Create application
            app = Application.builder().token(BOT_TOKEN).build()
            self.bot = app.bot
            
            # Add handlers
            app.add_handler(CommandHandler("start", self.start_command))
            app.add_handler(CommandHandler("help", self.help_command))
            app.add_handler(CommandHandler("stats", self.stats_command))
            app.add_handler(CommandHandler("pause_reminders", self.pause_reminders_command))
            
            # Admin commands
            app.add_handler(CommandHandler("admin_stats", self.admin_stats_command))
            app.add_handler(CommandHandler("ban_user", self.ban_user_command))
            
            # Message handlers
            app.add_handler(MessageHandler(filters.CONTACT, self.handle_contact))
            
            # Chat event handlers
            app.add_handler(ChatJoinRequestHandler(self.handle_join_request))
            app.add_handler(ChatMemberHandler(self.handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
            
            # Error handler
            app.add_error_handler(self.error_handler)
            
            # Start reminder scheduler
            self.start_reminder_scheduler()
            
            logger.info("🚀 Filipino Bot started successfully!")
            logger.info("🔔 Enhanced 3-tier reminder system active")
            logger.info("📱 Auto-approval system ready")
            
            # Run the bot
            app.run_polling()
            
        except Exception as e:
            logger.error(f"Failed to start bot: {e}")
            raise

def main():
    """Main function"""
    try:
        bot_manager = FilipinoBotManager()
        bot_manager.run()
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        raise

if __name__ == '__main__':
    main()
