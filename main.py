Walang problema\! Naiintindihan ko na nakakalito ang sobrang backslashes. Ginawa ko 'yan dati para siguraduhin na hindi mag-e-error ang Markdown, pero tama ka, mas mahalaga ang kalinawan.

Inalis ko na ang lahat ng **sobrang backslashes** sa iyong code. Ngayon, ang mga text messages ng iyong bot ay dapat na maging mas malinis at mas madaling basahin, habang pinapanatili pa rin ang tamang Markdown formatting para sa bold (`**text**`) at italics (`_text_` or `*text*`).

Ang tanging mga backslash na naiwan ay 'yung **talagang kailangan** para maiwasan ang Markdown errors, tulad ng:

  * **Periods pagkatapos ng numero** sa isang listahan (e.g., `1\. I-click`). Kung walang `\`, pwedeng isipin ng Telegram na isa 'yang listahan at masira ang format.
  * **Hyphens o bullet points** sa simula ng linya na hindi mo intensyon na gawing Markdown list.
  * **Certain special characters** (tulad ng `!`) na posibleng maging bahagi ng Markdown syntax kung hindi escaped.

Narito na ang iyong code na may mga naayos na Markdown strings.

-----

### Inayos na Filipino Bot Manager Code

```python
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
    ChatJoinRequestHandler, ContextTypes, filters, ApplicationBuilder
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
    
    async def start_reminder_scheduler(self):
        """Start the reminder scheduler task - Now async"""
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
                    if reminder_count == 0: # This means it's the first proactive reminder (second overall)
                        reminder_msg = self.get_second_reminder_message(first_name)
                    elif reminder_count == 1: # This means it's the second proactive reminder (third overall)
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
1\. I-click ang /start
2\. I-share ang Philippine phone number mo
3\. Tapos na! 🎉

**Bakit mo kailangan?**
• Mas mabilis na group approvals
• Trusted member status
• One-time lang 'to
• Walang hassle sa future join requests

**Hindi ka na makakakuha ng maraming reminders - 1 pa lang after nito.**

👇 **I-click para tapusin ngayon:**
/start

---
_Automatic reminder lang 'to - hindi mo kailangan mag-reply_
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
1\. I-click ang /start
2\. I-share ang Philippine phone number
3\. Verified ka na! 🎉
4\. Tapos na lahat ng reminders!

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
_Huling reminder na 'to. Salamat sa pagintindi! 🇵🇭_
_Para ma-stop ang reminders, i-type lang ang /pause_reminders_
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
1\. I-click ang /start dito sa private chat
2\. I-share ang Philippine phone number mo
3\. Instant verification!
4\. Auto-approve na sa future groups!

**IMPORTANT:** • Pwede ka pa rin ma-approve ng admin kahit hindi verified
• Pero mas convenient kapag verified ka na
• Maraming verified users na satisfied sa benefits
• Optional lang, pero highly recommended

**Smart Reminder System:**
• May 3 gentle reminders lang max
• Hindi spam - may 24-hour intervals
• Pwede mo i-pause anytime with /pause_reminders

_Ito ang first reminder mo. Next reminder sa 24 hours kung hindi ka pa mag-verify._

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
1\. Mag-verify ka na bilang Filipino user
2\. I-click ang /start dito sa private chat
3\. I-share ang Philippine phone number mo

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
🔄 **Salamat sa pagbalik!** Great choice, {user.first_name}! Tapusin na natin ang verification process:

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
✅ Philippine number (+63) lang accepted
✅ I-click lang ang button sa baba
✅ Automatic approval kapag verified

**Benefits after verification:**
✅ **Auto-approval** sa lahat ng Filipino groups
🚀 **No more waiting** for manual approval
🛡️ **Trusted member status** agad
📱 **One-time process** - lifetime benefits
🎯 **Priority access** sa Filipino communities

**Security Note:**
🔒 Phone number mo ay hindi makikita ng iba
📝 Para lang sa verification purposes
👍 Safe at secure process

👇 **I-click ang button para mag-share:**
        """
        
        await update.message.reply_text(
            verification_msg,
            reply_markup=contact_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def handle_contact(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle contact sharing for verification"""
        user = update.effective_user
        contact = update.message.contact
        
        # Update user activity
        self.db.update_user_activity(user.id)
        
        if not contact:
            await update.message.reply_text(
                "❌ Walang phone number na nareceive. Try again.",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode=ParseMode.MARKDOWN # Added parse_mode here as well for consistency
            )
            return
        
        # Verify if the contact is from the user themselves
        if contact.user_id != user.id:
            await update.message.reply_text(
                "❌ Kailangan mo i-share ang sarili mong phone number, hindi ng iba.",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode=ParseMode.MARKDOWN # Added parse_mode here as well for consistency
            )
            return
        
        phone_number = contact.phone_number
        logger.info(f"📱 Received phone number from user {user.id}: {phone_number}")
        
        # Verify phone number
        verification_result = self.verifier.verify_phone_number(phone_number)
        
        if verification_result['is_filipino']:
            # ✅ VERIFICATION SUCCESSFUL
            self.db.add_verified_user(
                user.id, 
                user.username, 
                user.first_name, 
                verification_result['formatted_number']
            )
            
            success_msg = f"""
🎉 **VERIFICATION SUCCESSFUL!** ✅

Congratulations {user.first_name}! Successfully na-verify ka bilang Filipino user!

**Verified Details:**
📱 **Phone:** {verification_result['formatted_number']}
🇵🇭 **Country:** Philippines
✅ **Status:** Verified Filipino User

**Your Benefits (Active na agad!):**
🚀 **Auto-approval** sa lahat ng Filipino groups
⚡ **Instant access** - no more waiting
🛡️ **Trusted member** status sa community
🎯 **VIP treatment** sa future join requests
📢 **Priority** sa Filipino channels

**Commands available:**
• /help - Show all commands
• /stats - View your verification info
• /pause_reminders - Stop reminder notifications

**Welcome sa Filipino community!** 🇵🇭

_Maari ka na mag-join sa mga Filipino groups at auto-approve ka na agad!_
            """
            
            await update.message.reply_text(
                success_msg,
                reply_markup=ReplyKeyboardRemove(),
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Notify admin
            admin_msg = f"""
✅ **New User Verified Successfully**

**User:** {user.first_name} (@{user.username or 'no_username'})
**ID:** `{user.id}`
**Phone:** {verification_result['formatted_number']}
**Verified:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

**Status:** Active Filipino user - Auto-approval enabled
            """
            
            try:
                await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.error(f"Failed to notify admin: {e}")
            
            logger.info(f"✅ User {user.id} verified successfully with phone {verification_result['formatted_number']}")
            
        else:
            # ❌ VERIFICATION FAILED
            self.db.add_verification_attempt(
                user.id, 
                phone_number, 
                False, 
                f"Not Filipino number: {verification_result.get('region', 'Unknown')}"
            )
            
            error_msg = f"""
❌ **Verification Failed**

Sorry {user.first_name}, ang phone number na na-share mo ay hindi Filipino number.

**Details:**
📱 **Number:** {phone_number}
🌍 **Detected Country:** {verification_result.get('region', 'Unknown')}
🇵🇭 **Required:** Philippines (+63)

**Para ma-verify:**
1\. Gamitin ang Philippine phone number (+63)
2\. I-check kung tama ang format
3\. Subukan ulit ang /start

**Common Issues:**
• Hindi naka-save as Philippine format
• Gamit ang international number ng ibang bansa
• Wrong country code

**Need help?** Contact admin para sa assistance.

Try again: /start
            """
            
            await update.message.reply_text(
                error_msg,
                reply_markup=ReplyKeyboardRemove(),
                parse_mode=ParseMode.MARKDOWN
            )
            
            logger.warning(f"❌ User {user.id} verification failed - not Filipino number: {phone_number}")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help information"""
        user = update.effective_user
        self.db.update_user_activity(user.id)
        
        is_verified = self.db.is_verified(user.id)
        
        if is_verified:
            help_msg = """
🇵🇭 **Filipino Bot Help - Verified User**

**Your Status:** ✅ Verified Filipino User

**Available Commands:**
• /start - Show verification status
• /help - Show this help message
• /stats - View your verification details
• /pause_reminders - Stop reminder notifications

**Benefits (Active):**
✅ Auto-approval sa Filipino groups
🚀 Instant access sa communities
🛡️ Trusted member status
📱 Priority support

**Auto-Features:**
• Automatic approval sa join requests
• VIP treatment sa Filipino channels
• No manual approval needed
• Lifetime verification status

**Support:**
Para sa questions or issues, contact admin.
            """
        else:
            help_msg = """
🇵🇭 **Filipino Bot Help - Unverified User**

**Your Status:** ❌ Not verified

**Available Commands:**
• /start - Begin verification process
• /help - Show this help message
• /pause_reminders - Stop reminder notifications

**Verification Benefits:**
✅ Auto-approval sa Filipino groups
🚀 Instant access - no waiting
🛡️ Trusted member status
📱 One-time verification process

**How to Verify:**
1\. Type `/start`
2\. Share your Philippine phone number
3\. Get verified instantly!

**Requirements:**
✅ Must have Philippine phone number (+63)
✅ Number must be registered to you
✅ One-time process only

**Support:**
Para sa questions, contact admin.
            """
        
        await update.message.reply_text(help_msg, parse_mode=ParseMode.MARKDOWN)
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user verification statistics"""
        user = update.effective_user
        self.db.update_user_activity(user.id)
        
        if self.db.is_verified(user.id):
            # Get user details from database
            conn = sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT username, first_name, phone_number, verified_date 
                FROM verified_users 
                WHERE user_id = ? AND is_banned = FALSE
            ''', (user.id,))
            
            result = cursor.fetchone()
            conn.close()
            
            if result:
                username, first_name, phone_number, verified_date = result
                verified_dt = datetime.fromisoformat(verified_date)
                
                stats_msg = f"""
📊 **Your Verification Stats**

**Personal Info:**
👤 **Name:** {first_name}
📧 **Username:** @{username or 'Not set'}
🆔 **User ID:** `{user.id}`

**Verification Details:**
📱 **Phone:** {phone_number}
✅ **Status:** Verified Filipino User
📅 **Verified Date:** {verified_dt.strftime('%B %d, %Y at %H:%M')}
🕐 **Days Verified:** {(datetime.now() - verified_dt).days} days

**Active Benefits:**
🚀 Auto-approval sa Filipino groups
⚡ Instant access sa communities
🛡️ Trusted member status
🎯 Priority support access
📢 VIP treatment sa channels

**Account Status:**
✅ **Active** - All benefits working
🔒 **Secure** - Phone verified
🇵🇭 **Filipino** - Community member

_All systems operational! Enjoy your benefits._
                """
            else:
                stats_msg = "❌ Error retrieving your verification data."
        else:
            stats_msg = f"""
📊 **Your Account Stats**

**Personal Info:**
👤 **Name:** {user.first_name}
📧 **Username:** @{user.username or 'Not set'}
🆔 **User ID:** `{user.id}`

**Verification Status:**
❌ **Not Verified** - Missing benefits

**Missing Benefits:**
🚫 Manual approval lang sa groups
⏳ May waiting time sa join requests
❌ Hindi trusted member status
📱 Walang priority access

**Action Needed:**
Para ma-enjoy ang lahat ng benefits:
1\. Type `/start` para mag-verify
2\. Share Philippine phone number
3\. Get instant verification!

**Estimated Time:** 2 minutes lang
**Benefits:** Lifetime access sa auto-approval

_Ready to verify? Type /start now!_
            """
        
        await update.message.reply_text(stats_msg, parse_mode=ParseMode.MARKDOWN)
    
    async def pause_reminders_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Pause reminder notifications for user"""
        user = update.effective_user
        self.db.update_user_activity(user.id)
        
        # Pause reminders
        self.db.pause_reminders(user.id)
        
        pause_msg = f"""
🔕 **Reminders Paused**

Hi {user.first_name}! 

✅ **Status:** Reminder notifications stopped
🔕 **Action:** No more automatic reminders
⏸️ **Duration:** Permanently paused

**What this means:**
• Hindi ka na makakatanggap ng verification reminders
• Manual join request approvals pa rin
• Pwede mo pa rin i-verify sarili mo anytime

**Para mag-verify pa rin:**
• Type `/start` anytime
• Benefits pa rin available
• One-time verification lang

**Para ma-reactivate reminders:**
Contact admin kung gusto mo ulit ma-receive ang helpful reminders.

**Commands available:**
• /help - Show help
• /start - Manual verification
• /stats - Account info

_Salamat sa understanding! 🙏_
        """
        
        await update.message.reply_text(pause_msg, parse_mode=ParseMode.MARKDOWN)
        
        # Notify admin
        try:
            admin_msg = f"""
🔕 **User Paused Reminders**

**User:** {user.first_name} (@{user.username or 'no_username'})
**ID:** `{user.id}`
**Action:** Paused reminder notifications
**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

**Status:** User will not receive automated reminders
            """
            await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Failed to notify admin about paused reminders: {e}")
    
    def setup_handlers(self, app: Application):
        """Setup command and message handlers"""
        # Command handlers
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("stats", self.stats_command))
        app.add_handler(CommandHandler("pause_reminders", self.pause_reminders_command))
        
        # Contact handler for phone verification
        app.add_handler(MessageHandler(filters.CONTACT, self.handle_contact))
        
        # Join request handler
        app.add_handler(ChatJoinRequestHandler(self.handle_join_request))
        
        # Admin commands (if needed)
        if ADMIN_ID:
            app.add_handler(CommandHandler("admin_stats", self.admin_stats_command))
            app.add_handler(CommandHandler("ban_user", self.ban_user_command))
    
    async def admin_stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin command to view bot statistics"""
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("❌ Admin command lang.")
            return
        
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # Get verification stats
        cursor.execute('SELECT COUNT(*) FROM verified_users WHERE is_banned = FALSE')
        verified_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM verified_users WHERE is_banned = TRUE')
        banned_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM join_requests WHERE status = "pending"')
        pending_requests = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM reminder_notifications')
        total_reminders = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM verification_attempts WHERE success = FALSE')
        failed_attempts = cursor.fetchone()[0]
        
        conn.close()
        
        admin_stats = f"""
📊 **Admin Bot Statistics**

**User Verification:**
✅ **Verified Users:** {verified_count}
🚫 **Banned Users:** {banned_count}
⏳ **Pending Join Requests:** {pending_requests}

**Reminder System:**
📨 **Total Reminders Sent:** {total_reminders}
❌ **Failed Verification Attempts:** {failed_attempts}

**System Status:**
🤖 **Bot Status:** Online & Running
🔄 **Reminder Scheduler:** Active (every 6 hours)
💾 **Database:** Connected
🛡️ **Security:** All systems operational

**Commands Available:**
• `/admin_stats` - This statistics
• `/ban_user <user_id>` - Ban a user
• Regular user commands work din

**Last Updated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        await update.message.reply_text(admin_stats, parse_mode=ParseMode.MARKDOWN)
    
    async def ban_user_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin command to ban a user"""
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("❌ Admin command lang.")
            return
        
        if not context.args:
            await update.message.reply_text(
                "❌ Usage: `/ban_user <user_id>`\n\nExample: `/ban_user 123456789`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        try:
            user_id_to_ban = int(context.args[0])
            
            if user_id_to_ban == ADMIN_ID:
                await update.message.reply_text("❌ Hindi pwedeng i-ban ang admin.")
                return
            
            # Ban user
            self.db.ban_user(user_id_to_ban)
            
            ban_msg = f"""
🚫 **User Banned Successfully**

**Banned User ID:** `{user_id_to_ban}`
**Banned By:** Admin
**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

**Effects:**
• User verification revoked
• No auto-approval access
• Manual approval required for groups
• Can still receive reminders (but won't work)

**Note:** User can still use bot commands but verification benefits are disabled.
            """
            
            await update.message.reply_text(ban_msg, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"🚫 Admin banned user {user_id_to_ban}")
            
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID. Dapat number lang.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error banning user: {str(e)}")
            logger.error(f"Error in ban_user_command: {e}")
    
    async def start_reminder_scheduler_async(self):
        """Start the reminder scheduler task - Fixed async version"""
        try:
            self.reminder_task = asyncio.create_task(self._reminder_scheduler_loop())
            logger.info("🔔 Reminder scheduler started successfully")
        except Exception as e:
            logger.error(f"Failed to start reminder scheduler: {e}")
    
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
1\. I-click ang /start
2\. I-share ang Philippine phone number mo
3\. Tapos na! 🎉

**Bakit mo kailangan?**
• Mas mabilis na group approvals
• Trusted member status
• One-time lang 'to
• Walang hassle sa future join requests

**Hindi ka na makakakuha ng maraming reminders - 1 pa lang after nito.**

👇 **I-click para tapusin ngayon:**
/start

---
_Automatic reminder lang 'to - hindi mo kailangan mag-reply_
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
1\. I-click ang /start
2\. I-share ang Philippine phone number
3\. Verified ka na! 🎉
4\. Tapos na lahat ng reminders!

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
_Huling reminder na 'to. Salamat sa pagintindi! 🇵🇭_
_Para ma-stop ang reminders, i-type lang ang /pause_reminders_
        """
    
    def run_bot(self):
        """Main method to run the bot - FIXED VERSION"""
        try:
            # Create application - Updated to use ApplicationBuilder for timeouts
            app = ApplicationBuilder().token(BOT_TOKEN).get_updates_read_timeout(30).get_updates_write_timeout(30).build()
            
            # Store bot reference for reminder system
            self.bot = app.bot
            
            # Setup handlers
            self.setup_handlers(app)
            
            logger.info("🤖 Starting Filipino Verification Bot...")
            logger.info(f"🔧 Admin ID: {ADMIN_ID}")
            
            # Start the bot with run_polling which handles the event loop
            async def post_init(application: Application) -> None:
                """Called after the bot starts - perfect place to start scheduler"""
                await self.start_reminder_scheduler_async()
                logger.info("✅ Bot and scheduler started successfully!")
            
            # Add post_init to application
            app.post_init = post_init
            
            # Run the bot (this creates and manages the event loop)
            app.run_polling(
                poll_interval=1,
                bootstrap_retries=-1, # Keep this if needed
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to start bot: {e}")
            raise

def main():
    """Main function"""
    try:
        if not BOT_TOKEN:
            logger.error("❌ BOT_TOKEN environment variable not set!")
            return
        
        if not ADMIN_ID:
            logger.error("❌ ADMIN_ID environment variable not set!")
            return
        
        bot_manager = FilipinoBotManager()
        bot_manager.run_bot()
        
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        raise

if __name__ == "__main__":
    main()
```
