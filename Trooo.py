import json
import os
import time
import logging
from datetime import datetime
from typing import List, Tuple, Dict, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "8776746131:AAFWZw8s4vlT8j6cRcAKDhpDjoJwNIMtU3o"
ADMIN_ID = 8311790903
BOT_USERNAME = None

BASE_DIR = "bot_data"
os.makedirs(BASE_DIR, exist_ok=True)

USERS_FILE = os.path.join(BASE_DIR, "users.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
CHANNELS_FILE = os.path.join(BASE_DIR, "channels.txt")
PROTECTED_GROUPS_FILE = os.path.join(BASE_DIR, "protected_groups.json")
ADMIN_ACTIONS_FILE = os.path.join(BASE_DIR, "admin_actions.json")

STEP_NONE = 0
STEP_BROADCAST_TEXT = 1
STEP_BROADCAST_FORWARD = 2
STEP_BROADCAST_MEDIA = 3
STEP_ADD_CHANNEL = 4
STEP_ADD_ADMIN = 5
STEP_PROMOTE_USER = 6
STEP_DEMOTE_USER = 7
STEP_ADD_GROUP = 8

flood_cache: Dict[int, float] = {}

def is_spam(user_id: int) -> bool:
    now = time.time()
    if user_id in flood_cache and now - flood_cache[user_id] < 0.5:
        return True
    flood_cache[user_id] = now
    return False

class DataManager:
    @staticmethod
    def load_json(filepath: str, default: dict = None) -> dict:
        if default is None:
            default = {}
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return default
    
    @staticmethod
    def save_json(filepath: str, data: dict):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    @staticmethod
    def load_list(filepath: str) -> List[str]:
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    return [line.strip() for line in f.read().splitlines() if line.strip()]
        except Exception:
            pass
        return []
    
    @staticmethod
    def save_list(filepath: str, data: List[str]):
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("\n".join(data))

class UserManager:
    def __init__(self):
        self.users_file = USERS_FILE
        self.data = DataManager.load_json(self.users_file, {})
    
    def add_user(self, user_id: int, username: str, first_name: str) -> bool:
        user_id_str = str(user_id)
        if user_id_str not in self.data:
            self.data[user_id_str] = {
                'username': username,
                'first_name': first_name,
                'join_date': datetime.now().isoformat(),
                'id': user_id
            }
            self.save()
            return True
        return False
    
    def get_count(self) -> int:
        return len(self.data)
    
    def get_all_ids(self) -> List[int]:
        return [int(uid) for uid in self.data.keys()]
    
    def save(self):
        DataManager.save_json(self.users_file, self.data)

class SettingsManager:
    def __init__(self):
        self.file = SETTINGS_FILE
        self.data = DataManager.load_json(self.file, {
            'forwarding': False,
            'notifications': False,
            'mandatory_channels': []
        })
    
    def get(self, key: str, default=None):
        return self.data.get(key, default)
    
    def set(self, key: str, value):
        self.data[key] = value
        self.save()
    
    def toggle(self, key: str) -> bool:
        current = self.data.get(key, False)
        self.data[key] = not current
        self.save()
        return self.data[key]
    
    def save(self):
        DataManager.save_json(self.file, self.data)

class ProtectedGroupsManager:
    def __init__(self):
        self.file = PROTECTED_GROUPS_FILE
        self.data = DataManager.load_json(self.file, {})
    
    def add_group(self, user_id: int, chat_id: int, chat_title: str) -> bool:
        user_id_str = str(user_id)
        if user_id_str not in self.data:
            self.data[user_id_str] = []
        
        exists = any(g['chat_id'] == chat_id for g in self.data[user_id_str])
        if not exists:
            self.data[user_id_str].append({
                'chat_id': chat_id,
                'title': chat_title,
                'added_at': datetime.now().isoformat()
            })
            self.save()
            return True
        return False
    
    def get_user_groups(self, user_id: int) -> List[dict]:
        return self.data.get(str(user_id), [])
    
    def is_creator(self, user_id: int, chat_id: int) -> bool:
        groups = self.get_user_groups(user_id)
        return any(g['chat_id'] == chat_id for g in groups)
    
    def save(self):
        DataManager.save_json(self.file, self.data)

class AdminActions:
    def __init__(self):
        self.file = ADMIN_ACTIONS_FILE
        self.data = DataManager.load_json(self.file, {})
    
    def set_step(self, user_id: int, step: int, extra_data: dict = None):
        self.data[str(user_id)] = {
            'step': step,
            'data': extra_data or {},
            'timestamp': time.time()
        }
        self.save()
    
    def get_step(self, user_id: int) -> Tuple[int, dict]:
        info = self.data.get(str(user_id), {})
        return info.get('step', STEP_NONE), info.get('data', {})
    
    def clear_step(self, user_id: int):
        if str(user_id) in self.data:
            del self.data[str(user_id)]
            self.save()
    
    def save(self):
        DataManager.save_json(self.file, self.data)

user_manager = UserManager()
settings = SettingsManager()
protected_groups = ProtectedGroupsManager()
admin_actions = AdminActions()

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

async def check_channel_membership(context: ContextTypes.DEFAULT_TYPE, user_id: int, channel: str) -> bool:
    try:
        member = await context.bot.get_chat_member(f"@{channel}", user_id)
        return member.status not in ['left', 'kicked']
    except Exception:
        return False

async def check_all_channels(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Tuple[bool, Optional[str]]:
    channels = settings.get('mandatory_channels', [])
    for ch in channels:
        if not await check_channel_membership(context, user_id, ch):
            return False, ch
    return True, None

def get_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    if is_admin(user_id):
        keyboard = [
            [InlineKeyboardButton("🛠 الإذاعة", callback_data='broadcast_menu')],
            [InlineKeyboardButton("➕ إضافة قناة اشتراك", callback_data='add_mandatory_channel'),
             InlineKeyboardButton("➖ حذف قناة", callback_data='del_mandatory_channel')],
            [InlineKeyboardButton("📝 عرض القنوات", callback_data='list_mandatory_channels')],
            [InlineKeyboardButton("🔄 فتح/قفل التوجيه", callback_data='toggle_forwarding'),
             InlineKeyboardButton("🔔 تفعيل/تعطيل التنبيه", callback_data='toggle_notifications')],
            [InlineKeyboardButton("💾 نسخة احتياطية", callback_data='backup'),
             InlineKeyboardButton("📤 استعادة", callback_data='restore')],
            [InlineKeyboardButton("📊 الإحصائيات", callback_data='stats')]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("~ قنواتي ومجموعاتي ~", callback_data="my_groups")],
            [InlineKeyboardButton("~ اضف قناة او مجموعة ~", callback_data="add_group")],
            [InlineKeyboardButton("~ تعليمات البوت ~", callback_data="help")]
        ]
    return InlineKeyboardMarkup(keyboard)

def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="back_menu")]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_spam(user.id):
        return
        
    global BOT_USERNAME
    if not BOT_USERNAME:
        BOT_USERNAME = context.bot.username
    
    new_user = user_manager.add_user(user.id, user.username, user.first_name)
    
    if is_admin(user.id) and settings.get('notifications', False) and new_user:
        count = user_manager.get_count()
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📝 *تم دخول شخص جديد*\n\n*الاسم:* {user.first_name}\n*المعرف:* @{user.username or 'غير معروف'}\n*الايدي:* {user.id}\n*عدد الأعضاء:* {count}",
            parse_mode='Markdown'
        )
    
    if not is_admin(user.id):
        ok, ch = await check_all_channels(context, user.id)
        if not ok:
            await update.message.reply_text(
                f"🚫 *يجب عليك الاشتراك في قناة @{ch} لاستخدام البوت.*",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"اشتراك @{ch}", url=f"https://t.me/{ch}")]])
            )
            return
    
    welcome_text = """
• اهلا بك عزيزي في بوت حماية PRO 
• في بوت حماية القنوات والمجموعات من التفليش بالازالة من الادمنيه وفضحه في القناه والخاص 
- ارفع البوت ادمن في القناه واعطه كل الصلاحيات وسيعمل بشكل جيد المطور @PRO_YEM
"""
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_menu_keyboard(user.id)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    if is_spam(user_id):
        await query.answer("يرجى عدم تكرار الضغط.", show_alert=True)
        return

    await query.answer()
    
    if not is_admin(user_id):
        ok, ch = await check_all_channels(context, user_id)
        if not ok:
            await query.edit_message_text(f"🚫 *يجب عليك الاشتراك في قناة @{ch} أولاً.*", parse_mode='Markdown')
            return
    
    if data == "my_groups":
        await show_user_groups(query, user_id, context)
    elif data == "add_group":
        await prompt_add_group(query, user_id)
    elif data == "help":
        await show_help(query)
    elif data == "back_menu":
        await query.edit_message_text(
            "• اهلا بك عزيزي \n• في بوت حماية القنوات والمجموعات",
            reply_markup=get_main_menu_keyboard(user_id)
        )
    elif data == "broadcast_menu":
        await show_broadcast_menu(query)
    elif data == "broadcast_text":
        admin_actions.set_step(user_id, STEP_BROADCAST_TEXT)
        await query.edit_message_text("✉️ أرسل النص الذي تريد إذاعته:", reply_markup=back_button())
    elif data == "broadcast_forward":
        admin_actions.set_step(user_id, STEP_BROADCAST_FORWARD)
        await query.edit_message_text("📤 قم بتوجيه الرسالة التي تريد إذاعتها:", reply_markup=back_button())
    elif data == "broadcast_media":
        admin_actions.set_step(user_id, STEP_BROADCAST_MEDIA)
        await query.edit_message_text("🎬 أرسل الوسائط (صورة/فيديو/ملف) مع النص:", reply_markup=back_button())
    elif data == "add_mandatory_channel":
        admin_actions.set_step(user_id, STEP_ADD_CHANNEL)
        await query.edit_message_text("📢 أرسل معرف القناة (بدون @):", reply_markup=back_button())
    elif data == "list_mandatory_channels":
        await list_channels(query)
    elif data == "toggle_forwarding":
        status = settings.toggle('forwarding')
        await query.edit_message_text(
            f"{'✅ تم فتح' if status else '🚫 تم قفل'} التوجيه",
            reply_markup=back_button()
        )
    elif data == "toggle_notifications":
        status = settings.toggle('notifications')
        await query.edit_message_text(
            f"{'✅ تم تفعيل' if status else '🚫 تم تعطيل'} تنبيه الأعضاء الجدد",
            reply_markup=back_button()
        )
    elif data == "stats":
        await show_stats(query)
    elif data == "backup":
        await send_backup(query, context)
    elif data == "restore":
        await prompt_restore(query, user_id)
    elif data.startswith("manage_group#"):
        group_id = int(data.split("#")[1])
        await show_group_management(query, user_id, group_id, context)
    elif data.startswith("promote#"):
        group_id = int(data.split("#")[1])
        await prompt_promote(query, user_id, group_id)
    elif data.startswith("demote#"):
        group_id = int(data.split("#")[1])
        await prompt_demote(query, user_id, group_id)
    elif data.startswith("reup#"):
        parts = data.replace("reup#", "").split("&")
        if len(parts) == 2:
            await re_promote_admin(query, context, int(parts[0]), int(parts[1]))

async def show_user_groups(query, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    groups = protected_groups.get_user_groups(user_id)
    if not groups:
        await query.edit_message_text(
            "• *لم تقم باضافة قنوات او مجموعات *",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("• رجوع", callback_data="back_menu")]])
        )
        return
    
    keyboard = []
    for group in groups:
        try:
            chat = await context.bot.get_chat(group['chat_id'])
            title = chat.title
        except:
            title = group['title']
        
        keyboard.append([
            InlineKeyboardButton(title, callback_data=f"manage_group#{group['chat_id']}")
        ])
    keyboard.append([InlineKeyboardButton("• رجوع", callback_data="back_menu")])
    await query.edit_message_text("• اهلا بك في قائمه قنواتك", reply_markup=InlineKeyboardMarkup(keyboard))

async def prompt_add_group(query, user_id: int):
    admin_actions.set_step(user_id, STEP_ADD_GROUP)
    await query.edit_message_text(
        "• ارسل معرف القناة/المجموعة @ او الايدي -100\n• تأكد من رفع البوت ادمن أولاً",
        reply_markup=back_button()
    )

async def show_help(query):
    text = """اهلا بك البوت يقوم بحفظ قناتك او مجموعتك من التفليش من قبل المشرفين بالازالة .
ملاحظات مهمة:
- يجب رفع المشرفين من خلال البوت
- قم بمنح البوت كل الصلاحيات
- سيتم تنزيل أي مشرف يقوم بطرد عضو
"""
    await query.edit_message_text(text, reply_markup=back_button())

async def show_broadcast_menu(query):
    keyboard = [
        [InlineKeyboardButton("إذاعة نصية", callback_data="broadcast_text")],
        [InlineKeyboardButton("إذاعة بالتوجيه", callback_data="broadcast_forward")],
        [InlineKeyboardButton("إذاعة بالوسائط", callback_data="broadcast_media")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="back_menu")]
    ]
    await query.edit_message_text("✉️ اختر نوع الإذاعة:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_stats(query):
    count = user_manager.get_count()
    await query.edit_message_text(
        f"📊 *الإحصائيات*\n\n• عدد المستخدمين: {count}",
        parse_mode='Markdown',
        reply_markup=back_button()
    )

async def list_channels(query):
    channels = settings.get('mandatory_channels', [])
    if not channels:
        await query.edit_message_text("لا توجد قنوات مفروضة حالياً.", reply_markup=back_button())
        return
    text = "القنوات الحالية:\n" + "\n".join([f"@{ch}" for ch in channels])
    await query.edit_message_text(text, reply_markup=back_button())

async def send_backup(query, context: ContextTypes.DEFAULT_TYPE):
    try:
        with open(USERS_FILE, 'rb') as f:
            await query.message.reply_document(f, caption="📂 نسخة احتياطية من بيانات الأعضاء")
        await query.edit_message_text("✅ تم إرسال النسخة الاحتياطية", reply_markup=back_button())
    except Exception:
        await query.edit_message_text("❌ حدث خطأ أثناء تجهيز النسخة الاحتياطية", reply_markup=back_button())

async def prompt_restore(query, user_id: int):
    admin_actions.set_step(user_id, STEP_NONE, {'waiting_file': True})
    await query.edit_message_text("📤 أرسل ملف users.json للاستعادة", reply_markup=back_button())

async def show_group_management(query, user_id: int, group_id: int, context: ContextTypes.DEFAULT_TYPE):
    if not protected_groups.is_creator(user_id, group_id):
        await query.edit_message_text("❌ ليس لديك صلاحية", reply_markup=back_button())
        return
    
    try:
        admins = await context.bot.get_chat_administrators(group_id)
        creator = None
        admin_list = []
        
        for admin in admins:
            if admin.status == 'creator':
                creator = admin.user
            elif admin.status == 'administrator':
                admin_list.append(admin.user)
        
        text = f"*المالك:* {creator.first_name if creator else 'غير معروف'}\n\n*المشرفون:*\n"
        for admin in admin_list:
            text += f"• {admin.first_name} (`{admin.id}`)\n"
        
        keyboard = [
            [InlineKeyboardButton("رفع مشرف", callback_data=f"promote#{group_id}"),
             InlineKeyboardButton("تنزيل مشرف", callback_data=f"demote#{group_id}")],
            [InlineKeyboardButton("• رجوع", callback_data="my_groups")]
        ]
        
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        await query.edit_message_text("❌ خطأ في جلب المعلومات، تأكد أن البوت مشرف", reply_markup=back_button())

async def prompt_promote(query, user_id: int, group_id: int):
    admin_actions.set_step(user_id, STEP_PROMOTE_USER, {'group_id': group_id})
    await query.edit_message_text("• ارسل ايدي المشرف لرفعه:", reply_markup=back_button())

async def prompt_demote(query, user_id: int, group_id: int):
    admin_actions.set_step(user_id, STEP_DEMOTE_USER, {'group_id': group_id})
    await query.edit_message_text("• ارسل ايدي المشرف لتنزيله:", reply_markup=back_button())

async def re_promote_admin(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int):
    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            can_change_info=True,
            can_delete_messages=True,
            can_invite_users=True,
            can_restrict_members=True,
            can_pin_messages=True,
            can_manage_voice_chats=True,
            can_manage_chat=True
        )
        await query.edit_message_text("✅ تم إعادة رفع المشرف بنجاح", reply_markup=back_button())
    except Exception:
        await query.edit_message_text("❌ فشل في إعادة الرفع", reply_markup=back_button())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_spam(user_id):
        return
        
    text = update.message.text
    step, data = admin_actions.get_step(user_id)
    
    if is_admin(user_id):
        if step == STEP_BROADCAST_TEXT:
            await perform_broadcast_text(update, context, text)
            admin_actions.clear_step(user_id)
            return
        elif step == STEP_ADD_CHANNEL:
            await add_mandatory_channel(update, context, text)
            admin_actions.clear_step(user_id)
            return
            
    if step == STEP_ADD_GROUP:
        await handle_add_group(update, context, text)
        admin_actions.clear_step(user_id)
        return
    elif step == STEP_PROMOTE_USER and 'group_id' in data:
        await handle_promote(update, context, data['group_id'], text)
        admin_actions.clear_step(user_id)
        return
    elif step == STEP_DEMOTE_USER and 'group_id' in data:
        await handle_demote(update, context, data['group_id'], text)
        admin_actions.clear_step(user_id)
        return
    
    if text == "رفع مشرف" and update.message.reply_to_message:
        await handle_quick_promote(update, context)
    elif text == "تنزيل مشرف" and update.message.reply_to_message:
        await handle_quick_demote(update, context)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    
    step, data = admin_actions.get_step(user_id)
    if step == STEP_NONE and data.get('waiting_file'):
        file = update.message.document
        if file.file_name == "users.json":
            file_obj = await context.bot.get_file(file.file_id)
            await file_obj.download_to_drive(custom_path=USERS_FILE)
            user_manager.data = DataManager.load_json(USERS_FILE, {})
            await update.message.reply_text("✅ تم استعادة النسخة الاحتياطية بنجاح")
            admin_actions.clear_step(user_id)
        else:
            await update.message.reply_text("❌ الملف يجب أن يكون باسم users.json")

async def perform_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    users = user_manager.get_all_ids()
    count = 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            count += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ تم الإذاعة لـ {count} مستخدم", reply_markup=get_main_menu_keyboard(ADMIN_ID))

async def add_mandatory_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    ch = text.replace("@", "").strip()
    channels = settings.get('mandatory_channels', [])
    if ch not in channels:
        channels.append(ch)
        settings.set('mandatory_channels', channels)
        await update.message.reply_text(f"✅ تم إضافة @{ch}", reply_markup=get_main_menu_keyboard(ADMIN_ID))
    else:
        await update.message.reply_text("⚠️ القناة موجودة بالفعل")

async def handle_add_group(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id
    try:
        if text.startswith("@"):
            chat_id = text
        elif text.startswith("-100"):
            chat_id = int(text)
        else:
            await update.message.reply_text("❌ صيغة غير صحيحة")
            return
        
        chat = await context.bot.get_chat(chat_id)
        member = await context.bot.get_chat_member(chat.id, user_id)
        
        if member.status != 'creator':
            await update.message.reply_text("❌ يجب أن تكون مالك القناة أو المجموعة")
            return
        
        success = protected_groups.add_group(user_id, chat.id, chat.title)
        if success:
            await update.message.reply_text("✅ تم الحفظ بنجاح", reply_markup=get_main_menu_keyboard(user_id))
        else:
            await update.message.reply_text("⚠️ المجموعة مضافة مسبقاً")
            
    except Exception:
        await update.message.reply_text("❌ خطأ في الوصول للقناة. تأكد من رفع البوت مشرفاً")

async def handle_promote(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: int, user_id_text: str):
    try:
        target_id = int(user_id_text)
        await context.bot.promote_chat_member(
            chat_id=group_id,
            user_id=target_id,
            can_change_info=True,
            can_delete_messages=True,
            can_invite_users=True,
            can_restrict_members=True,
            can_pin_messages=True,
            can_manage_voice_chats=True,
            can_manage_chat=True,
            can_post_messages=True
        )
        await update.message.reply_text(f"✅ تم رفع المستخدم {target_id} مشرفاً")
    except Exception:
        await update.message.reply_text("❌ فشل في الرفع. تأكد من صلاحيات البوت")

async def handle_demote(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: int, user_id_text: str):
    try:
        target_id = int(user_id_text)
        await context.bot.promote_chat_member(
            chat_id=group_id,
            user_id=target_id,
            can_change_info=False,
            can_delete_messages=False,
            can_invite_users=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_manage_voice_chats=False,
            can_manage_chat=False,
            can_post_messages=False
        )
        await update.message.reply_text(f"✅ تم تنزيل المستخدم {target_id}")
    except Exception:
        await update.message.reply_text("❌ فشل في التنزيل")

async def handle_quick_promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    target = update.message.reply_to_message.from_user
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ['creator', 'administrator']:
            return
        
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if not bot_member.can_promote_members:
            await update.message.reply_text("❌ لا أملك صلاحية رفع مشرفين")
            return
        
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            can_change_info=True,
            can_delete_messages=True,
            can_invite_users=True,
            can_restrict_members=True,
            can_pin_messages=True
        )
        await update.message.reply_text(f"✅ تم رفع {target.first_name} مشرفاً")
    except Exception:
        await update.message.reply_text("❌ خطأ")

async def handle_quick_demote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    target = update.message.reply_to_message.from_user
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status != 'creator':
            await update.message.reply_text("❌ يجب أن تكون مالكاً")
            return
        
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            permissions=ChatPermissions(can_send_messages=True)
        )
        await update.message.reply_text(f"✅ تم تنزيل {target.first_name}")
    except Exception:
        await update.message.reply_text("❌ خطأ")

async def handle_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        if settings.get('forwarding', False) and update.effective_user.id != ADMIN_ID:
            try:
                await context.bot.forward_message(
                    chat_id=ADMIN_ID,
                    from_chat_id=update.effective_chat.id,
                    message_id=update.message.message_id
                )
            except Exception:
                pass

async def handle_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.chat_member:
            new_status = update.chat_member.new_chat_member.status if update.chat_member.new_chat_member else None
            
            if new_status in ['kicked', 'left']:
                chat_id = update.chat_member.chat.id
                perpetrator = update.chat_member.from_user
                victim = update.chat_member.old_chat_member.user if update.chat_member.old_chat_member else None
                
                if not victim or perpetrator.id == victim.id:
                    return
                
                try:
                    admins = await context.bot.get_chat_administrators(chat_id)
                    creator = None
                    for admin in admins:
                        if admin.status == 'creator':
                            creator = admin.user
                            break
                    
                    if creator and perpetrator.id != creator.id:
                        await context.bot.promote_chat_member(
                            chat_id=chat_id,
                            user_id=perpetrator.id,
                            can_change_info=False,
                            can_delete_messages=False,
                            can_invite_users=False,
                            can_restrict_members=False,
                            can_pin_messages=False,
                            can_promote_members=False,
                            can_manage_voice_chats=False,
                            can_manage_chat=False,
                            can_post_messages=False
                        )
                        
                        warning_text = f"""
اهلا بك عزيزي مالك القناة/المجموعة
----------------------------
• القناة/المجموعة: {update.chat_member.chat.title}
• المشرف المخالف: {perpetrator.first_name}
• ايديه: {perpetrator.id}
• المعرف: @{perpetrator.username or 'بدون'}
----------------------------
• قام بطرد/حظر عضو: {victim.first_name}
• تم تنزيله تلقائياً وانذاره 🤚
"""
                        
                        await context.bot.send_message(
                            chat_id=creator.id,
                            text=warning_text,
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("• اعادة رفع ~ ⚙", callback_data=f"reup#{perpetrator.id}&{chat_id}")
                            ]])
                        )
                        
                        await context.bot.send_message(chat_id=chat_id, text=warning_text)
                        
                except Exception as e:
                    logger.error(f"Protection Error: {e}")
                    
    except Exception as e:
        logger.error(f"Chat Member Update Error: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

async def backup_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        with open(USERS_FILE, 'rb') as f:
            await context.bot.send_document(chat_id=ADMIN_ID, document=f, caption="🔄 نسخة احتياطية مجدولة")
    except Exception:
        pass

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.FORWARDED, handle_forward))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    
    app.add_error_handler(error_handler)
    
    job_queue = app.job_queue
    job_queue.run_daily(backup_job, time=datetime.strptime('00:00', '%H:%M').time())
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
