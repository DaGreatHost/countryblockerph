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
    ChatMemberUpdated, ChatPermissions, KeyboardButton, ReplyKeyboardMarkup
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

# Filipino language patterns
FILIPINO_PATTERNS = [
    r'\b(ako|ikaw|siya|kami|kayo|sila)\b',
    r'\b(ang|ng|sa|si|ni|kay)\b',
    r'\b(mga|mga|naman|lang|din|rin)\b',
    r'\b(kumusta|salamat|oo|hindi|hindi|opo)\b',
    r'\b(magandang|umaga|hapon|gabi)\b',
    r'\b(pano|paano|saan|kelan|bakit)\b',
    r'\b(tayo|natin|namin|ninyo|nila)\b',
    r'\b(pwede|pwedi|kaya|siguro|baka)\b',
    r'\b(yung|nung|dun|dito|dyan)\b',
    r'\b(pre|bro|kuya|ate|tito|tita)\b'
]

FILIPINO_REGEX = re.compile('|'.join(FILIPINO_PATTERNS), re.IGNORECASE)

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
                language_score INTEGER DEFAULT 0,
                timezone_score INTEGER DEFAULT 0,
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
                banned_by INTEGER,
                appeal_count INTEGER DEFAULT 0
            )
        ''')
        
        # Chat settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                strict_mode BOOLEAN DEFAULT TRUE,
                grace_period INTEGER DEFAULT 48,
                max_strikes INTEGER DEFAULT 3,
                auto_ban BOOLEAN DEFAULT TRUE
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

class FilipinoVerifier:
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
                'confidence': 0.9 if is_ph else 0.1
            }
        except NumberParseException:
            return {
                'is_filipino': False,
                'country_code': None,
                'region': None,
                'confidence': 0.0
            }
    
    @staticmethod
    def analyze_language(text: str) -> Dict[str, any]:
        """Analyze text for Filipino language patterns"""
        if not text:
            return {'is_filipino': False, 'confidence': 0.0, 'matched_patterns': []}
        
        text_lower = text.lower()
        matches = FILIPINO_REGEX.findall(text_lower)
        
        # Calculate confidence based on Filipino word density
        words = re.findall(r'\b\w+\b', text_lower)
        filipino_words = len(matches)
        total_words = len(words)
        
        if total_words == 0:
            confidence = 0.0
        else:
            confidence = min(filipino_words / total_words * 2, 1.0)  # Scale to max 1.0
        
        return {
            'is_filipino': confidence > 0.3,
            'confidence': confidence,
            'matched_patterns': matches,
            'filipino_word_count': filipino_words,
            'total_words': total_words
        }
    
    @staticmethod
    def analyze_timezone_activity(user_id: int, db: DatabaseManager) -> Dict[str, any]:
        """Analyze user activity patterns for Philippine timezone"""
        # This is a simplified version - in production you'd track activity over time
        current_hour = datetime.now().hour
        
        # Philippine time is UTC+8, so active hours would be different
        ph_active_hours = list(range(6, 24))  # 6 AM to 11 PM PH time
        
        is_ph_timezone = current_hour in ph_active_hours
        
        return {
            'is_filipino_timezone': is_ph_timezone,
            'confidence': 0.6 if is_ph_timezone else 0.3,
            'current_hour': current_hour
        }

class FilipinoBotManager:
    def __init__(self):
        if not BOT_TOKEN:
            raise ValueError("BOT_TOKEN environment variable is required!")
        if not ADMIN_ID:
            raise ValueError("ADMIN_ID environment variable is required!")
            
        self.db = DatabaseManager()
        self.verifier = FilipinoVerifier()
        self.pending_verifications = {}  # Store users in grace period
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        
        welcome_message = f"""
🇵🇭 *Maligayang pagdating sa Filipino Verification Bot!*

Ako ay tumutulong na ma-verify ang mga tunay na Filipino users para sa secure na community.

*Mga Commands:*
• `/verify` - Manual verification request
• `/status` - Check verification status
• `/appeal` - Appeal a ban (if banned)
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
• `/verify` - Request manual verification
• `/status` - Check your verification status
• `/appeal` - Appeal a ban

*How Verification Works:*
1. Phone number verification (PH +63)
2. Language analysis (Filipino/Tagalog)
3. Activity pattern analysis
4. Manual review if needed

*Admin Commands:*
• `/stats` - View bot statistics
• `/whitelist <user_id>` - Add to whitelist
• `/ban <user_id> <reason>` - Manual ban
• `/unban <user_id>` - Remove ban
• `/logs` - View activity logs

Para sa mga tanong, makipag-ugnayan sa admin.
        """
        
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def verify_new_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Verify new chat members"""
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
                    f"❌ User {new_member.first_name} is banned from this community."
                )
                return
            except Exception as e:
                logger.error(f"Error banning user {new_member.id}: {e}")
        
        # Start verification process
        await self.start_verification_process(new_member, chat_id, context)
    
    async def start_verification_process(self, user, chat_id, context):
        """Start the verification process for a new user"""
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
        self.db.log_activity(user.id, "joined", f"Joined chat {chat_id}")
        
        # Send verification message
        keyboard = [
            [InlineKeyboardButton("📱 Verify Phone Number", callback_data=f"verify_phone_{user.id}")],
            [InlineKeyboardButton("💬 Filipino Language Test", callback_data=f"verify_lang_{user.id}")],
            [InlineKeyboardButton("ℹ️ Manual Review", callback_data=f"manual_review_{user.id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_msg = f"""
🇵🇭 *Kumusta {user.first_name}!*

Para ma-verify ka sa community na ito, kailangan namin i-confirm na Filipino user ka.

*Mga paraan ng verification:*
1. 📱 Phone number verification (PH +63)
2. 💬 Filipino language test
3. ℹ️ Manual review by admin

Piliin ang isa sa mga options sa baba:
        """
        
        try:
            await context.bot.send_message(
                chat_id, 
                welcome_msg, 
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error sending verification message: {e}")
    
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback queries from inline keyboards"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
        if data.startswith("verify_phone_"):
            target_user_id = int(data.split("_")[2])
            if user_id != target_user_id:
                await query.edit_message_text("❌ You can only verify yourself!")
                return
            
            # Create contact sharing keyboard
            contact_keyboard = [[KeyboardButton("📱 Share My Phone Number", request_contact=True)]]
            contact_markup = ReplyKeyboardMarkup(
                contact_keyboard, 
                one_time_keyboard=True, 
                resize_keyboard=True
            )
            
            await query.edit_message_text(
                "📱 *Phone Number Verification*\n\n"
                "Para ma-verify ang inyong Philippine phone number, i-click ang button sa baba. "
                "Ang Telegram ay automatic na mag-s-send ng inyong real phone number.\n\n"
                "🔒 **Security Note:** Hindi pwedeng mag-fake ng number dahil direct galing sa Telegram account mo.\n\n"
                "👇 *Click the button below to share your contact:*",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Send separate message with contact button
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="👇 *I-click ang button para ma-send ang inyong phone number:*",
                reply_markup=contact_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        
        elif data.startswith("verify_lang_"):
            target_user_id = int(data.split("_")[2])
            if user_id != target_user_id:
                await query.edit_message_text("❌ You can only verify yourself!")
                return
            
            await self.start_language_test(query, target_user_id, context)
        
        elif data.startswith("manual_review_"):
            target_user_id = int(data.split("_")[2])
            if user_id != target_user_id:
                await query.edit_message_text("❌ You can only verify yourself!")
                return
            
            await self.request_manual_review(query, target_user_id, context)
        
        elif data.startswith("lang_answer_"):
            # Handle language test answers
            parts = data.split("_")
            target_user_id = int(parts[2])
            question_index = int(parts[3])
            selected_answer = int(parts[4])
            
            if user_id != target_user_id:
                await query.edit_message_text("❌ You can only answer your own test!")
                return
            
            await self.handle_language_answer(query, target_user_id, question_index, selected_answer, context)
    
    async def start_language_test(self, query, user_id, context):
        """Start Filipino language test"""
        questions = [
            {
                "question": "Ano ang tawag sa umaga sa Filipino?",
                "options": ["Morning", "Umaga", "Gabi", "Hapon"],
                "correct": 1
            },
            {
                "question": "Kumusta ka?",
                "options": ["I'm fine", "Okay lang", "Good", "Nice"],
                "correct": 1
            },
            {
                "question": "Salamat' means:",
                "options": ["Hello", "Goodbye", "Thank you", "Sorry"],
                "correct": 2
            }
        ]
        
        # Store test data
        self.pending_verifications[user_id] = {
            "type": "language_test",
            "questions": questions,
            "current_question": 0,
            "score": 0,
            "start_time": datetime.now()
        }
        
        await self.send_language_question(query, user_id, 0)
    
    async def send_language_question(self, query, user_id, question_index):
        """Send a language test question"""
        test_data = self.pending_verifications.get(user_id)
        if not test_data or question_index >= len(test_data["questions"]):
            return
        
        question = test_data["questions"][question_index]
        
        keyboard = []
        for i, option in enumerate(question["options"]):
            keyboard.append([InlineKeyboardButton(
                option, 
                callback_data=f"lang_answer_{user_id}_{question_index}_{i}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        question_text = f"""
🇵🇭 *Filipino Language Test*

**Question {question_index + 1}/3:**
{question["question"]}

Piliin ang tamang sagot:
        """
        
        await query.edit_message_text(
            question_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    async def request_manual_review(self, query, user_id, context):
        """Request manual review from admin"""
        await query.edit_message_text(
            "ℹ️ *Manual Review Requested*\n\n"
            "Ang inyong request ay naipadala na sa admin para sa manual review. "
            "Maghintay lang ng approval.\n\n"
            "Please wait for admin approval.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Notify admin
        try:
            user = await context.bot.get_chat(user_id)
            admin_msg = f"""
🔍 *Manual Review Request*

**User Details:**
• Name: {user.first_name} {user.last_name or ''}
• Username: @{user.username or 'None'}
• User ID: `{user_id}`

**Actions:**
• `/approve {user_id}` - Approve user
• `/reject {user_id}` - Reject user
• `/whitelist {user_id}` - Add to whitelist
            """
            
            await context.bot.send_message(
                ADMIN_ID,
                admin_msg,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Error notifying admin: {e}")
    
    async def handle_language_answer(self, query, user_id, question_index, selected_answer, context):
        """Handle language test answers"""
        test_data = self.pending_verifications.get(user_id)
        if not test_data or test_data["type"] != "language_test":
            await query.edit_message_text("❌ Test session expired. Please start again.")
            return
        
        # Check if answer is correct
        question = test_data["questions"][question_index]
        is_correct = selected_answer == question["correct"]
        
        if is_correct:
            test_data["score"] += 1
        
        # Move to next question or finish test
        next_question = question_index + 1
        test_data["current_question"] = next_question
        
        if next_question < len(test_data["questions"]):
            # Show next question
            await self.send_language_question(query, user_id, next_question)
        else:
            # Test completed
            await self.complete_language_test(query, user_id, test_data, context)
    
    async def complete_language_test(self, query, user_id, test_data, context):
        """Complete language test and show results"""
        score = test_data["score"]
        total = len(test_data["questions"])
        percentage = (score / total) * 100
        
        # Remove from pending
        if user_id in self.pending_verifications:
            del self.pending_verifications[user_id]
        
        if percentage >= 66:  # Need at least 2/3 correct
            # Update user status
            user_data = self.db.get_user(user_id)
            if user_data:
                user_data.verification_status = "verified"
                self.db.add_user(user_data)
                
                result_msg = f"""
✅ *Language Test Passed!*

**Results:**
• Score: {score}/{total} ({percentage:.0f}%)
• Status: **VERIFIED** 🇵🇭

Congratulations! Welcome to the community!

Hindi mo na kailangan mag-verify ulit. You're now a verified Filipino user!
                """
                
                self.db.log_activity(user_id, "verified", f"Language test passed: {score}/{total}")
        else:
            # Failed test - add strike
            self.db.add_strike(user_id)
            user_data = self.db.get_user(user_id)
            strikes = user_data.strike_count if user_data else 1
            
            result_msg = f"""
❌ *Language Test Failed*

**Results:**
• Score: {score}/{total} ({percentage:.0f}%)
• Required: 66% (2/3 correct)

**Strike Added:** {strikes}/3

{'⚠️ **Warning:** One more strike will result in automatic ban!' if strikes == 2 else ''}
{'🚫 **BANNED:** Too many failed attempts!' if strikes >= 3 else ''}

You can try other verification methods or request manual review.
            """
            
            self.db.log_activity(user_id, "strike", f"Language test failed: {score}/{total}")
            
            # Auto-ban if too many strikes
            if strikes >= 3:
                await self.auto_ban_user(user_id, "Too many verification failures", context)
        
        await query.edit_message_text(result_msg, parse_mode=ParseMode.MARKDOWN)
    
    async def auto_ban_user(self, user_id, reason, context):
        """Automatically ban user and notify admin"""
        user_data = self.db.get_user(user_id)
        username = user_data.username if user_data else "Unknown"
        
        self.db.ban_user(user_id, username, reason, 0)  # 0 = system ban
        
        # Notify admin
        try:
            admin_msg = f"""
🚫 *Automatic Ban Executed*

**User:** {username} (ID: `{user_id}`)
**Reason:** {reason}
**Action:** Automatic system ban

User has been banned from all protected chats.
            """
            
            await context.bot.send_message(
                ADMIN_ID,
                admin_msg,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Error notifying admin about auto-ban: {e}")
    
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
                "You can only verify your own phone number! "
                "Hindi pwedeng mag-verify ng number ng iba.\n\n"
                "Please share your own contact.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Remove keyboard after contact sharing
        from telegram import ReplyKeyboardRemove
        await update.message.reply_text(
            "📱 Processing your phone number...",
            reply_markup=ReplyKeyboardRemove()
        )
        
        # Verify phone number
        phone_result = self.verifier.verify_phone_number(contact.phone_number)
        
        if phone_result['is_filipino']:
            # Update user data
            user_data = self.db.get_user(user_id)
            if user_data:
                user_data.phone_number = contact.phone_number
                user_data.verification_status = "verified"
                self.db.add_user(user_data)
                
                success_msg = f"""
✅ **Phone Number Verified Successfully!**

🇵🇭 **Philippine Number Confirmed**
• Number: {contact.phone_number}
• Country: Philippines (+63)
• Status: **VERIFIED**

**Welcome to the community!** 
Hindi mo na kailangan mag-verify ulit sa ibang groups/channels na may same bot.

Salamat sa pagiging verified Filipino user! 🚀
                """
                
                await update.message.reply_text(success_msg, parse_mode=ParseMode.MARKDOWN)
                self.db.log_activity(user_id, "verified", f"PH phone verified: {contact.phone_number}")
        else:
            # Failed verification - add strike
            self.db.add_strike(user_id)
            user_data = self.db.get_user(user_id)
            strikes = user_data.strike_count if user_data else 1
            
            fail_msg = f"""
❌ **Phone Number Verification Failed**

**Issue Detected:**
• Number: {contact.phone_number}
• Country: {phone_result.get('region', 'Unknown')}
• Expected: Philippines (+63)

**Strike Added:** {strikes}/3

{'⚠️ **Warning:** Dalawang strikes na lang, automatic ban na!' if strikes == 2 else ''}
{'🚫 **BANNED:** Too many failed verification attempts!' if strikes >= 3 else ''}

**What you can do:**
• Use your real Philippine number
• Try language verification
• Request manual review
            """
            
            await update.message.reply_text(fail_msg, parse_mode=ParseMode.MARKDOWN)
            self.db.log_activity(user_id, "strike", f"Non-PH phone: {contact.phone_number} ({phone_result.get('region', 'Unknown')})")
            
            # Auto-ban if too many strikes
            if strikes >= 3:
                await self.auto_ban_user(user_id, "Multiple non-PH phone number attempts", context)
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        user_id = update.effective_user.id
        user_data = self.db.get_user(user_id)
        
        if not user_data:
            await update.message.reply_text("❌ No verification data found. Please join a protected chat first.")
            return
        
        status_msg = f"""
📊 *Your Verification Status*

**User Info:**
• Name: {user_data.first_name}
• Status: {user_data.verification_status.upper()}
• Strikes: {user_data.strike_count}/3
• Whitelisted: {'Yes' if user_data.is_whitelisted else 'No'}
• Join Date: {user_data.join_date.strftime('%Y-%m-%d %H:%M')}

**Phone:** {user_data.phone_number or 'Not provided'}
        """
        
        await update.message.reply_text(status_msg, parse_mode=ParseMode.MARKDOWN)
    
    # Admin Commands
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command (admin only)"""
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("❌ Admin access required!")
            return
        
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # Get statistics
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE verification_status = 'verified'")
        verified_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE verification_status = 'pending'")
        pending_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM banned_users")
        banned_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_whitelisted = 1")
        whitelisted_users = cursor.fetchone()[0]
        
        conn.close()
        
        stats_msg = f"""
📈 *Bot Statistics*

**Users:**
• Total: {total_users}
• Verified: {verified_users}
• Pending: {pending_users}
• Banned: {banned_users}
• Whitelisted: {whitelisted_users}

**Verification Rate:** {(verified_users/total_users*100):.1f}% if total_users > 0 else 0
        """
        
        await update.message.reply_text(stats_msg, parse_mode=ParseMode.MARKDOWN)

def main():
    """Main function to run the bot"""
    # Validate environment variables
    if not BOT_TOKEN:
        print("❌ Error: BOT_TOKEN environment variable is required!")
        print("Set it in Railway: BOT_TOKEN=your_bot_token_here")
        return
    
    if not ADMIN_ID:
        print("❌ Error: ADMIN_ID environment variable is required!")
        print("Set it in Railway: ADMIN_ID=your_admin_user_id")
        return
    
    # Create bot manager
    bot_manager = FilipinoBotManager()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", bot_manager.start_command))
    application.add_handler(CommandHandler("help", bot_manager.help_command))
    application.add_handler(CommandHandler("status", bot_manager.status_command))
    application.add_handler(CommandHandler("stats", bot_manager.stats_command))
    
    # Chat member handler for new joins
    application.add_handler(ChatMemberHandler(
        bot_manager.verify_new_member, 
        ChatMemberHandler.CHAT_MEMBER
    ))
    
    # Callback query handler
    application.add_handler(CallbackQueryHandler(bot_manager.handle_callback_query))
    
    # Contact message handler
    application.add_handler(MessageHandler(
        filters.CONTACT, 
        bot_manager.handle_contact_message
    ))
    
    # Start the bot
    print("🤖 Filipino Verification Bot starting...")
    print(f"👤 Admin ID: {ADMIN_ID}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
