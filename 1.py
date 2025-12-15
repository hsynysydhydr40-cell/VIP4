import requests
import time
import telebot
import threading
import re
import random
from telebot import types
from itertools import cycle

TELEGRAM_BOT_TOKEN = "7652688910:AAE1QVvBblt-LQG1Xi6vdqId-eY3wF7seHQ"
OWNER_ID = 6806810777

API_KEYS = [
    "NN5B8GQagI0ljfDiXIWV4I1kkedO3ex6v7axPSSu94wGW4xxo8Da3CNQtQkO",
    "zOkx4DNgqk1uRszRn7cEqtUtnRrZul0NIP57ftu2pYLM17cxLYpponr6t4oB",
    "lnczgRbvsFBxOBc8175HiyGM19VQvtlfkEAUNJMiIxP8shhmsixvxVOT39OV",
    "UwPCv93tpvyjfg022vC3DRjNAEQboDger9bMmxmizS36AofuvQRour1RrEiV"
]

POSSIBLE_ENDPOINTS = [
    "https://fastsmm-online.ru/api",
    "https://fastsmm-online.ru/api/v2",
    "https://fastsmm-online.ru/api.php",
]

SERVICE_IDS = {
    "positive": 865,
    "negative": 866
}

user_states = {}

MAINTENANCE_MODE = False
DAILY_LIMITS = {}
VIP_USERS = {}
DAILY_REQUEST_LIMIT = 3
VIP_1M_TIER = {}
FREE_LIMITS = {"min": 1000, "max": 2000}
VIP_LIMITS = {
    "positive": {"min": 5000, "max": 10000},
    "negative": {"min": 5000, "max": 10000}
}

MONITORED_CHANNELS = {}
FUTURE_REACTION_QTY = 1000

REFERRAL_ATTEMPTS_PER_ENTRY = 3
REFERRAL_ATTEMPTS = {}
REFERRAL_LOG = {}
MANDATORY_CHANNELS_INFO = {}

# متغير جديد لتتبع وإيقاف عمليات رشق 1M الجارية
RUNNING_1M_ORDERS = {} 

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def is_owner(user_id):
    return user_id == OWNER_ID

def is_vip(user_id):
    if is_owner(user_id):
        return True
    
    if user_id in VIP_USERS:
        return VIP_USERS[user_id] > time.time()
    return False

def check_mandatory_subscriptions(user_id):
    if not MANDATORY_CHANNELS_INFO:
        return True
        
    for chat_id in MANDATORY_CHANNELS_INFO.keys():
        try:
            member = bot.get_chat_member(chat_id, user_id)
            if member.status in ['left', 'kicked']:
                return False
        except Exception:
            continue 
    return True

def get_join_channels_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    if not MANDATORY_CHANNELS_INFO:
        return None
        
    for chat_id in MANDATORY_CHANNELS_INFO.keys():
        try:
            chat_info = bot.get_chat(chat_id)
            if chat_info.invite_link:
                invite_link = chat_info.invite_link
            elif chat_info.username:
                invite_link = f"https://t.me/{chat_info.username}"
            else:
                invite_link = "https://t.me/"

            markup.add(types.InlineKeyboardButton(f"اشترك في {MANDATORY_CHANNELS_INFO[chat_id]}", url=invite_link))
        except Exception:
            continue
    markup.add(types.InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_subscription"))
    return markup

def check_daily_limit(user_id):
    if is_vip(user_id):
        return True
    
    if REFERRAL_ATTEMPTS.get(user_id, 0) > 0:
        return True 
    
    DAILY_LIMITS.setdefault(user_id, 0)
    if DAILY_LIMITS[user_id] < DAILY_REQUEST_LIMIT:
        return True
    return False

def increase_daily_count(user_id):
    if is_vip(user_id):
        return
    
    if REFERRAL_ATTEMPTS.get(user_id, 0) > 0:
        REFERRAL_ATTEMPTS[user_id] -= 1
        return
        
    DAILY_LIMITS.setdefault(user_id, 0)
    DAILY_LIMITS[user_id] += 1

def get_next_key():
    if API_KEYS:
        return random.choice(API_KEYS)
    return None

def send_api_request(params):
    key = get_next_key()
    if not key:
        return {"error": "لا يوجد مفاتيح API متاحة."}
        
    params["key"] = key
    
    for url in POSSIBLE_ENDPOINTS:
        try:
            r = requests.post(url, data=params, timeout=10)
            return r.json()
        except requests.RequestException:
            continue
    return {"error": "فشل الاتصال بـ API أو انتهاء مهلة الاتصال."}

def parse_channel_link(link):
    link = link.strip()
    match = re.search(r'(?:t\.me/|@)([\w]+)', link)
    if match:
        return f"@{match.group(1)}"
    
    if link.startswith('https://t.me/c/'):
        return link
        
    return None

def check_admin_and_get_info(channel_link):
    try:
        channel_identifier = parse_channel_link(channel_link)
        if not channel_identifier:
            return None, "الرابط غير صالح."
            
        member = bot.get_chat_member(channel_identifier, bot.get_me().id)
        
        if member.status in ['administrator', 'creator']:
            chat = bot.get_chat(channel_identifier)
            return chat.id, None 
            
        return None, "البوت ليس مسؤولاً في القناة."
        
    except telebot.apihelper.ApiTelegramException as e:
        if "chat not found" in str(e) or "user not found" in str(e) or "A_MEMBER_NOT_FOUND" in str(e):
            return None, "القناة غير موجودة، تأكد من إرسال الرابط/المعرف الصحيح (@username)."
        return None, f"حدث خطأ في التحقق من القناة: {e}"
    except Exception as e:
        return None, f"خطأ غير متوقع أثناء التحقق: {e}"

# دالة لإنشاء زر الإيقاف لعملية 1M
def create_stop_markup(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    # نمرر chat_id في callback_data لنتعرف على العملية التي يجب إيقافها
    markup.add(types.InlineKeyboardButton("🛑 إيقاف الرشق", callback_data=f"stop_1m_order_{chat_id}"))
    return markup


# تعديل دالة process_1m_order
def process_1m_order(chat_id, link, service_id, message_id):
    TARGET_QTY = 1000000
    CHUNK_SIZE = 5000 
    total_sent = 0
    
    # التأكد من أن الحالة محددة للتشغيل قبل البدء
    if chat_id not in RUNNING_1M_ORDERS or not RUNNING_1M_ORDERS[chat_id]:
        # في حال تم الإلغاء قبل البدء، نخرج
        return

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text="🔥 جاري بدء عملية رشق 1M مشاهدة. سيتم إرسال طلبات متكررة (قد تستغرق وقتاً)."
    )
    
    # حلقة العمل مع فحص حالة الإيقاف
    while total_sent < TARGET_QTY and RUNNING_1M_ORDERS.get(chat_id, False): 
        
        # تحقق من حالة الإيقاف في بداية كل تكرار
        if not RUNNING_1M_ORDERS.get(chat_id):
            break 
            
        quantity = min(CHUNK_SIZE, TARGET_QTY - total_sent)
        
        params = {
            "action": "add",
            "service": service_id,
            "link": link,
            "quantity": quantity
        }
        
        res = send_api_request(params)
        
        if 'order' in res:
            total_sent += quantity
            increase_daily_count(chat_id)
            
            progress_text = (
                f"✅ تم إرسال طلب جديد.\n"
                f"الإجمالي المرسل: {total_sent:,}/{TARGET_QTY:,}"
            )
            try:
                # إضافة زر الإيقاف للرسالة
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=progress_text,
                    parse_mode='HTML',
                    reply_markup=create_stop_markup(chat_id) 
                )
            except Exception:
                pass 

        else:
            error_msg = res.get('error', 'خطأ غير معروف.')
            bot.send_message(chat_id, f"⚠️ توقف الرشق لخطأ في API: {error_msg}")
            break
            
        time.sleep(5) 
    
    # تنظيف الحالة وإرسال الرسالة النهائية
    is_stopped_manually = not RUNNING_1M_ORDERS.get(chat_id, False)

    if chat_id in RUNNING_1M_ORDERS:
        del RUNNING_1M_ORDERS[chat_id]

    if is_stopped_manually:
        final_text = f"🛑 تم إيقاف عملية رشق 1M مشاهدة يدوياً.\nتم إرسال إجمالي: {total_sent:,} مشاهدة قبل الإيقاف."
    else:
        final_text = f"🎉 اكتملت عملية رشق 1M مشاهدة للمنشور:\nالرابط: {link}\nتم إرسال {total_sent:,} مشاهدة."
        
    try:
        # تعديل الرسالة لإزالة زر الإيقاف وعرض الحالة النهائية
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=final_text,
            parse_mode='HTML',
            reply_markup=create_main_menu(chat_id)
        )
    except Exception:
        # إذا لم نتمكن من تعديل الرسالة، نرسل رسالة جديدة
        bot.send_message(chat_id, final_text, reply_markup=create_main_menu(chat_id), parse_mode='HTML')


def monitor_channels():
    threading.Timer(30, monitor_channels).start()
    
    if not MONITORED_CHANNELS:
        return
    
    for channel_id, data in list(MONITORED_CHANNELS.items()):
        try:
            updates = bot.get_chat_history(channel_id, limit=1)
            if updates and updates.messages:
                latest_message = updates.messages[0]
                latest_message_id = latest_message.message_id
                
                if latest_message_id != data.get('last_checked_msg_id'):
                    
                    user_id = data['user_id']
                    
                    if not is_vip(user_id):
                        bot.send_message(user_id, f"⚠️ تم إيقاف الرشق التلقائي للقناة {data['link']} لأن اشتراكك VIP انتهى.")
                        del MONITORED_CHANNELS[channel_id]
                        continue
                    
                    link = f"{data['link']}/{latest_message_id}"
                    
                    params = {
                        "action": "add",
                        "service": data['reaction_service_id'],
                        "link": link,
                        "quantity": FUTURE_REACTION_QTY
                    }
                    
                    res = send_api_request(params)
                    
                    if 'order' in res:
                        bot.send_message(user_id, f"✅ تم رشق تفاعلات مستقبلية للمنشور الجديد (ID: {latest_message_id}).\nالكمية: {FUTURE_REACTION_QTY}\nرقم الطلب: <code>{res['order']}</code>", parse_mode='HTML')
                        data['last_checked_msg_id'] = latest_message_id
                    else:
                        error_msg = res.get('error', 'خطأ API.')
                        bot.send_message(user_id, f"❌ فشل رشق التفاعلات التلقائية للمنشور الجديد (ID: {latest_message_id}).\nالسبب: {error_msg}")
        
        except Exception:
            continue


def create_main_menu(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    if is_owner(user_id):
        markup.add(types.InlineKeyboardButton("📊 لوحة التحكم", callback_data="admin_panel"))
    
    markup.add(
        types.InlineKeyboardButton("➕ تفاعلات إيجابية", callback_data="order_service_865"),
        types.InlineKeyboardButton("➖ تفاعلات سلبية", callback_data="order_service_866")
    )
    
    markup.add(types.InlineKeyboardButton("🔗 نظام تجميع المحاولات (الإحالة)", callback_data="show_referral_panel"))
    
    markup.add(types.InlineKeyboardButton("⭐ خدمات VIP", callback_data="show_vip_info"))
    
    markup.add(types.InlineKeyboardButton("🔄 تفاعلات مستقبلية", callback_data="order_service_future"))
    
    markup.add(types.InlineKeyboardButton("🔥 1M مشاهدة", callback_data="order_service_1m"))
    
    markup.add(types.InlineKeyboardButton("👤 المالك", url="https://t.me/BBBBYB2"))
    
    return markup

def create_admin_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    maintenance_status = "✅" if MAINTENANCE_MODE else "❌"
    
    markup.add(
        types.InlineKeyboardButton(f"{maintenance_status} وضع الصيانة", callback_data="admin_toggle_maintenance"),
        types.InlineKeyboardButton("🔑 إضافة مفتاح", callback_data="admin_add_key")
    )
    markup.add(
        types.InlineKeyboardButton("⭐ تفعيل VIP", callback_data="admin_activate_vip"),
        types.InlineKeyboardButton("🎯 تفاعلات مجانية", callback_data="admin_free_limits")
    )
    markup.add(
        types.InlineKeyboardButton("⚙️ حد طلبات الرشق", callback_data="admin_set_limit"),
        types.InlineKeyboardButton("🔗 محاولات الإحالة", callback_data="admin_set_referral_limit")
    )
    markup.add(
        types.InlineKeyboardButton("📜 إدارة قنوات الاشتراك", callback_data="admin_manage_mandatory_channels"),
        types.InlineKeyboardButton("📢 إذاعة (برودكاست)", callback_data="admin_start_broadcast")
    )
    markup.add(
        types.InlineKeyboardButton("👑 حدود تفاعلات VIP", callback_data="admin_vip_limits_menu")
    )
    markup.add(
        types.InlineKeyboardButton("🏠 الرئيسية", callback_data="cancel")
    )
    return markup

def create_mandatory_channels_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ إضافة قناة", callback_data="admin_add_mandatory_channel"),
        types.InlineKeyboardButton("➖ حذف قناة", callback_data="admin_delete_mandatory_channel")
    )
    markup.add(types.InlineKeyboardButton("↩️ رجوع", callback_data="admin_panel"))
    return markup


def create_vip_duration_menu(user_id_to_activate):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("⏳ بالساعات", callback_data=f"admin_vip_set_h_{user_id_to_activate}"),
        types.InlineKeyboardButton("📅 بالأيام", callback_data=f"admin_vip_set_d_{user_id_to_activate}")
    )
    markup.add(
        types.InlineKeyboardButton("🔥 ترقية 1M", callback_data=f"admin_vip_set_1m_{user_id_to_activate}"),
        types.InlineKeyboardButton("❌ إلغاء", callback_data="cancel")
    )
    return markup

def create_confirmation_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ موافق", callback_data="confirm_order"),
        types.InlineKeyboardButton("❌ إلغاء", callback_data="cancel")
    )
    return markup

def create_free_limits_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 تعيين الأدنى", callback_data="admin_set_min_free"),
        types.InlineKeyboardButton("📈 تعيين الأقصى", callback_data="admin_set_max_free")
    )
    markup.add(types.InlineKeyboardButton("🏠 الرئيسية", callback_data="cancel"))
    return markup

def create_vip_limits_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    pos_min = VIP_LIMITS['positive']['min']
    pos_max = VIP_LIMITS['positive']['max']
    neg_min = VIP_LIMITS['negative']['min']
    neg_max = VIP_LIMITS['negative']['max']

    markup.add(
        types.InlineKeyboardButton(f"إيجابية (Min: {pos_min})", callback_data="admin_set_vip_pos_min"),
        types.InlineKeyboardButton(f"إيجابية (Max: {pos_max})", callback_data="admin_set_vip_pos_max")
    )
    markup.add(
        types.InlineKeyboardButton(f"سلبية (Min: {neg_min})", callback_data="admin_set_vip_neg_min"),
        types.InlineKeyboardButton(f"سلبية (Max: {neg_max})", callback_data="admin_set_vip_neg_max")
    )
    markup.add(types.InlineKeyboardButton("↩️ رجوع", callback_data="admin_panel"))
    return markup

def handle_start(message):
    chat_id = message.chat.id
    
    if message.text and message.text.startswith('/start ref_'):
        try:
            referrer_id = int(message.text.split('_')[1])
            
            if referrer_id == chat_id:
                bot.send_message(chat_id, "لا يمكنك إحالة نفسك!.")
                return
            
            if chat_id in REFERRAL_LOG:
                bot.send_message(chat_id, "لقد تم تسجيل إحالتك مسبقاً.")
                return
            
            if not check_mandatory_subscriptions(chat_id):
                bot.send_message(chat_id, "لإكمال عملية الإحالة وكسب محاولات للطرف الآخر، يجب عليك الاشتراك في جميع القنوات الإجبارية أولاً:", reply_markup=get_join_channels_markup())
                user_states[chat_id] = {'step': 'checking_referral_subscription', 'referrer_id': referrer_id}
                return
            
            REFERRAL_LOG[chat_id] = referrer_id
            
            REFERRAL_ATTEMPTS.setdefault(referrer_id, 0)
            REFERRAL_ATTEMPTS[referrer_id] += REFERRAL_ATTEMPTS_PER_ENTRY
            
            attempts_left = REFERRAL_ATTEMPTS[referrer_id]
            
            try:
                bot.send_message(referrer_id, f"لقد حصلت من رابط احالتك ع عدد محاولات رقم {REFERRAL_ATTEMPTS_PER_ENTRY}. رصيدك الحالي: {attempts_left}", parse_mode='HTML')
            except Exception:
                pass
            
            bot.send_message(chat_id, f"✅ تم تسجيلك بنجاح عن طريق المستخدم <code>{referrer_id}</code>. شكراً لك.", parse_mode='HTML')
            
        except (IndexError, ValueError):
            bot.send_message(chat_id, "رابط الإحالة غير صالح.")
            
    if not check_mandatory_subscriptions(chat_id):
        bot.send_message(chat_id, "يرجى الاشتراك في القنوات الإجبارية أولاً للمتابعة:", reply_markup=get_join_channels_markup())
        return

    attempts = REFERRAL_ATTEMPTS.get(chat_id, 0)
    display_name = message.from_user.first_name
    mention = f"<a href='tg://user?id={chat_id}'>{display_name}</a>"
    
    welcome_text = (
        f"مرحبا بيك {mention}\n"
        f"عدد محاولات الرشق: {attempts}\n"
        "اختر من الازرار"
    )
            
    bot.send_message(
        chat_id,
        welcome_text,
        reply_markup=create_main_menu(chat_id),
        parse_mode='HTML'
    )

@bot.message_handler(commands=['start', 'admin'])
def command_handler(message):
    chat_id = message.chat.id
    if message.text.startswith('/admin'):
        if is_owner(chat_id):
            user_states.pop(chat_id, None)
            bot.send_message(chat_id, "أهلاً بك في لوحة تحكم المالك:", reply_markup=create_admin_menu())
        else:
            bot.send_message(chat_id, "عذرا، هذه الوظيفة مخصصة للمالك فقط.")
    else:
        handle_start(message)

@bot.callback_query_handler(func=lambda call: call.data == 'check_subscription')
def handle_check_subscription(call):
    chat_id = call.message.chat.id
    state = user_states.get(chat_id)
    
    if check_mandatory_subscriptions(chat_id):
        if state and state.get('step') == 'checking_referral_subscription':
            referrer_id = state['referrer_id']
            
            REFERRAL_LOG[chat_id] = referrer_id
            REFERRAL_ATTEMPTS.setdefault(referrer_id, 0)
            REFERRAL_ATTEMPTS[referrer_id] += REFERRAL_ATTEMPTS_PER_ENTRY
            
            attempts_left = REFERRAL_ATTEMPTS[referrer_id]
            
            try:
                bot.send_message(referrer_id, f"لقد حصلت من رابط احالتك ع عدد محاولات رقم {REFERRAL_ATTEMPTS_PER_ENTRY}. رصيدك الحالي: {attempts_left}", parse_mode='HTML')
            except Exception:
                pass 
                
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=call.message.message_id,
                text="✅ تم تأكيد اشتراكك ونجحت عملية الإحالة!.",
                reply_markup=create_main_menu(chat_id)
            )
            if chat_id in user_states:
                del user_states[chat_id]
        else:
             bot.edit_message_text(
                chat_id=chat_id,
                message_id=call.message.message_id,
                text="✅ تم تأكيد اشتراكك في جميع القنوات.",
                reply_markup=create_main_menu(chat_id)
            )
    else:
        bot.answer_callback_query(call.id, "❌ لم تكتمل عملية الاشتراك بعد. الرجاء الانضمام لجميع القنوات.", show_alert=True)
        bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=get_join_channels_markup()
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'show_referral_panel')
def handle_referral_panel(call):
    chat_id = call.message.chat.id
    
    attempts = REFERRAL_ATTEMPTS.get(chat_id, 0)
    
    referral_link = f"https://t.me/{bot.get_me().username}?start=ref_{chat_id}"
    
    text = (
        "🔗 نظام تجميع المحاولات (الإحالة)\n\n"
        f"رابط الإحالة الخاص بك:\n<code>{referral_link}</code>\n\n"
        f"رصيدك الحالي من محاولات الرشق المجانية (العادية): **{attempts} محاولة**\n"
    )
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🏠 الرئيسية", callback_data="cancel"))
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=text,
        parse_mode='HTML',
        reply_markup=markup
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == 'admin_panel')
def handle_admin_panel(call):
    chat_id = call.message.chat.id
    if is_owner(chat_id):
        user_states.pop(chat_id, None)
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="لوحة تحكم المالك:",
            reply_markup=create_admin_menu()
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def handle_admin_actions(call):
    chat_id = call.message.chat.id
    data = call.data
    
    if not is_owner(chat_id):
        bot.answer_callback_query(call.id, "ليس لديك صلاحية الوصول.", show_alert=True)
        return

    if data == 'admin_toggle_maintenance':
        global MAINTENANCE_MODE
        MAINTENANCE_MODE = not MAINTENANCE_MODE
        status = "مفعل" if MAINTENANCE_MODE else "معطل"
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"تم تغيير وضع الصيانة بنجاح. الوضع الحالي: {status}",
            reply_markup=create_admin_menu()
        )
    
    elif data == 'admin_add_key':
        user_states[chat_id] = {'step': 'admin_waiting_for_key'}
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="الرجاء إرسال المفتاح الجديد (نص) أو ملف نصي (TXT) يحتوي على مفاتيح (كل مفتاح في سطر):",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("إلغاء", callback_data="cancel"))
        )
    
    elif data == 'admin_activate_vip':
        user_states[chat_id] = {'step': 'admin_waiting_for_vip_id'}
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="الرجاء إرسال معرف المستخدم (ID) الذي تود تفعيل VIP له:",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("إلغاء", callback_data="cancel"))
        )
    
    elif data == 'admin_set_limit':
        user_states[chat_id] = {'step': 'admin_waiting_for_daily_limit'}
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"الحد اليومي الحالي: {DAILY_REQUEST_LIMIT}.\nالرجاء إرسال الحد اليومي الجديد (أرقام فقط):",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("إلغاء", callback_data="cancel"))
        )
    
    elif data == 'admin_set_referral_limit':
        user_states[chat_id] = {'step': 'admin_waiting_for_ref_limit'}
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"عدد محاولات الرشق الممنوحة لكل إحالة حالياً هو: {REFERRAL_ATTEMPTS_PER_ENTRY}.\nالرجاء إرسال العدد الجديد:",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("إلغاء", callback_data="cancel"))
        )
    
    elif data == 'admin_manage_mandatory_channels':
        channels_list = '\n'.join([f"{v} (ID: {k})" for k, v in MANDATORY_CHANNELS_INFO.items()]) if MANDATORY_CHANNELS_INFO else 'لا توجد قنوات إجبارية حالياً.'
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"قنوات الاشتراك الإجباري:\n\n{channels_list}",
            reply_markup=create_mandatory_channels_menu()
        )
    
    elif data == 'admin_add_mandatory_channel':
        user_states[chat_id] = {'step': 'admin_waiting_for_channel_to_add'}
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="الرجاء إرسال رابط القناة (مثل: @username) أو توجيه منشور منها لإضافتها والتحقق من صلاحيات البوت كمسؤول:",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("إلغاء", callback_data="admin_manage_mandatory_channels"))
        )
    
    elif data == 'admin_delete_mandatory_channel':
        if not MANDATORY_CHANNELS_INFO:
            bot.answer_callback_query(call.id, "لا توجد قنوات لحذفها.", show_alert=True)
            return
            
        markup = types.InlineKeyboardMarkup(row_width=1)
        for chat_id_key, display_link in MANDATORY_CHANNELS_INFO.items():
            markup.add(types.InlineKeyboardButton(f"حذف: {display_link} (ID: {chat_id_key})", callback_data=f"delete_channel_{chat_id_key}"))
        markup.add(types.InlineKeyboardButton("❌ إلغاء", callback_data="admin_manage_mandatory_channels"))
        
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="اختر القناة التي تريد حذفها:",
            reply_markup=markup
        )
    
    elif data.startswith('delete_channel_'):
        try:
            channel_id_to_delete = int(data.split('delete_channel_')[1])
        except ValueError:
            bot.answer_callback_query(call.id, "خطأ في المعرف.", show_alert=True)
            return
            
        if channel_id_to_delete in MANDATORY_CHANNELS_INFO:
            deleted_link = MANDATORY_CHANNELS_INFO.pop(channel_id_to_delete)
            
            channels_list = '\n'.join([f"{v} (ID: {k})" for k, v in MANDATORY_CHANNELS_INFO.items()]) if MANDATORY_CHANNELS_INFO else 'لا توجد قنوات إجبارية حالياً.'

            bot.edit_message_text(
                chat_id=chat_id,
                message_id=call.message.message_id,
                text=f"✅ تم حذف القناة {deleted_link} بنجاح.\n\nقنوات الاشتراك الإجباري:\n\n{channels_list}",
                reply_markup=create_mandatory_channels_menu()
            )
        else:
            bot.answer_callback_query(call.id, "القناة غير موجودة.", show_alert=True)
            
    elif data == 'admin_free_limits':
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"الحدود الحالية للتفاعلات المجانية:\n\nالحد الأدنى: {FREE_LIMITS['min']}\nالحد الأقصى: {FREE_LIMITS['max']}",
            reply_markup=create_free_limits_menu()
        )
    
    elif data == 'admin_set_min_free':
        user_states[chat_id] = {'step': 'admin_waiting_min_free'}
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="الرجاء إرسال الحد الأدنى الجديد (أرقام فقط):",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("إلغاء", callback_data="cancel"))
        )
    
    elif data == 'admin_set_max_free':
        user_states[chat_id] = {'step': 'admin_waiting_max_free'}
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="الرجاء إرسال الحد الأقصى الجديد (أرقام فقط):",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("إلغاء", callback_data="cancel"))
        )

    elif data == 'admin_start_broadcast':
        user_states[chat_id] = {'step': 'admin_waiting_for_broadcast_message'}
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="الرجاء إرسال الكليشة التي تريد إذاعتها (سيتم إرسالها لجميع القنوات، المجموعات، والمستخدمين الذين تفاعلوا مع البوت):",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("إلغاء", callback_data="cancel"))
        )
    
    elif data == 'admin_vip_limits_menu':
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="تعيين الحدود الدنيا والقصوى لتفاعلات VIP:",
            reply_markup=create_vip_limits_menu()
        )
    
    elif data.startswith('admin_set_vip_'):
        parts = data.split('_')
        reaction_type = parts[3] 
        limit_type = parts[4] 

        key = 'positive' if reaction_type == 'pos' else 'negative'
        limit = 'min' if limit_type == 'min' else 'max'
        
        user_states[chat_id] = {'step': 'admin_waiting_vip_limit_value', 'key': key, 'limit': limit}
        
        display_text = "الإيجابية" if key == 'positive' else "السلبية"
        display_limit = "الأدنى" if limit == 'min' else "الأقصى"

        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"الرجاء إرسال القيمة الجديدة للحد {display_limit} للتفاعلات {display_text} لـ VIP (رقم فقط):",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("إلغاء", callback_data="admin_vip_limits_menu"))
        )
    
    elif data.startswith('admin_vip_set_'):
        parts = data.split('_')
        action = parts[3]
        target_id = int(parts[4])
        
        if action == '1m':
            VIP_1M_TIER[target_id] = True
            bot.send_message(
                chat_id,
                f"تم ترقية المستخدم {target_id} لـ VIP 2 (1M مشاهدة).",
                reply_markup=create_admin_menu()
            )
            if chat_id in user_states:
                del user_states[chat_id]
        else:
            user_states[chat_id] = {'step': 'admin_waiting_for_duration', 'target_id': target_id, 'unit': action}
            unit_text = "بالساعات" if action == 'h' else "بالأيام"
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=call.message.message_id,
                text=f"تم اختيار التفعيل {unit_text}. الرجاء إرسال مدة التفعيل (رقم صحيح):",
                reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("إلغاء", callback_data="cancel"))
            )
    
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'show_vip_info')
def handle_show_vip_info(call):
    vip_text = (
        "مواصفات خدمات VIP:\n\n"
        "1. رشق بدون حدود: للمشتركين الـ VIP فقط، لا يتم تطبيق الحد اليومي للطلبات.\n"
        "2. رشق تفاعلات مستقبلية: يسمح للبوت بالرشق التلقائي لأي منشور جديد يتم نشره في القناة (تتطلب رفع البوت كأدمن).\n"
        "3. خدمة رشق 1M: خدمة خاصة لرشق مليون مشاهدة وتفاعل للمنشور الواحد.\n"
        "\nللاشتراك، تواصل مع المالك."
    )
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🏠 الرئيسية", callback_data="cancel"))
    
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=vip_text,
        reply_markup=markup
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('order_service_'))
def handle_service_selection(call):
    chat_id = call.message.chat.id
    service_key = call.data.split('_')[-1]
    
    if MAINTENANCE_MODE and not is_owner(chat_id):
        bot.answer_callback_query(call.id, "الخدمة في وضع الصيانة حاليا. الرجاء المحاولة لاحقا.", show_alert=True)
        return
        
    if not check_daily_limit(chat_id):
        display_name = call.from_user.first_name
        limit_msg = f"عذراً {display_name}، لقد وصلت للحد الأقصى لعدد الطلبات اليومية ({DAILY_REQUEST_LIMIT}). انتهت محاولاتك عليك تجميع من رابط احاله"
        bot.answer_callback_query(call.id, limit_msg, show_alert=True)
        return
    
    if service_key == 'future':
        if not is_vip(chat_id):
             bot.answer_callback_query(call.id, "هذه الخدمة مخصصة لـ VIP فقط.", show_alert=True)
             return
        
        service_id = SERVICE_IDS['positive'] 
        user_states[chat_id] = {'step': 'waiting_for_channel_link', 'service_id': service_id, 'is_future': True}
        text = "للتفعيل، يرجى رفع البوت كـ **مسؤول** في القناة.\nثم أرسل رابط القناة (مثل: @channel_username) ليتم التحقق:"
        
    elif service_key == '1m':
        if not (is_owner(chat_id) or (chat_id in VIP_1M_TIER and VIP_1M_TIER.get(chat_id))):
             bot.answer_callback_query(call.id, "هذه الخدمة مخصصة لـ VIP الدرجة الثانية فقط.", show_alert=True)
             return
        
        service_id = SERVICE_IDS['positive']
        user_states[chat_id] = {'step': 'waiting_for_link', 'service_id': service_id, 'is_1m': True}
        text = "خدمة رشق 1M (مشاهدات وتفاعلات).\n\nالرجاء إرسال رابط المنشور:"
        
    else:
        service_id = int(service_key)
        user_states[chat_id] = {'step': 'waiting_for_link', 'service_id': service_id}
        service_type = "تفاعلات إيجابية" if service_id == SERVICE_IDS['positive'] else "تفاعلات سلبية"
        
        if not is_vip(chat_id) and REFERRAL_ATTEMPTS.get(chat_id, 0) > 0:
            attempts_msg = f" (سيتم خصمها من رصيد محاولات الإحالة: {REFERRAL_ATTEMPTS[chat_id]} محاولة متبقية)"
        else:
            attempts_msg = ""
            
        text = f"اخترت خدمة: {service_type}{attempts_msg}\nالرجاء إرسال رابط المنشور المراد رشقه:"

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=text,
        reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="cancel"))
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'confirm_order')
def handle_confirm_order(call):
    chat_id = call.message.chat.id
    state = user_states.get(chat_id)
    
    if not state:
        bot.answer_callback_query(call.id, "انتهت الجلسة، يرجى البدء من جديد.")
        return
    
    link = state.get('link')
    quantity = state.get('quantity')
    service_id = state.get('service_id')
    
    if not link or not quantity or not service_id:
        bot.answer_callback_query(call.id, "بيانات غير مكتملة.")
        return
    
    if not check_daily_limit(chat_id):
        display_name = call.from_user.first_name
        limit_msg = f"عذراً {display_name}، الحد اليومي أو محاولات الإحالة نفذت."
        bot.answer_callback_query(call.id, limit_msg, show_alert=True)
        return
    
    if state.get('is_1m'):
        # تهيئة حالة التشغيل قبل بدء الثريد
        global RUNNING_1M_ORDERS
        RUNNING_1M_ORDERS[chat_id] = True 
        
        threading.Thread(
            target=process_1m_order, 
            args=(chat_id, link, service_id, call.message.message_id)
        ).start()
        
        if chat_id in user_states:
            del user_states[chat_id]
        bot.answer_callback_query(call.id)
        return 
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text="جاري إرسال الطلب، الرجاء الانتظار..."
    )
    
    params = {
        "action": "add",
        "service": service_id,
        "link": link,
        "quantity": quantity
    }
    
    res = send_api_request(params)
    
    if 'order' in res:
        increase_daily_count(chat_id)
        
        attempts_left = REFERRAL_ATTEMPTS.get(chat_id, 0)
        attempts_msg = f"\n(محاولات الإحالة المتبقية: {attempts_left})" if attempts_left > 0 else ""
        
        response_text = f"✅ تم إرسال الطلب بنجاح!{attempts_msg}\n\nرقم الطلب: <code>{res['order']}</code>\nالرابط: {link}\nالكمية: {quantity}"
    else:
        error_msg = res.get('error', 'خطأ غير معروف أثناء تنفيذ الطلب.')
        response_text = f"❌ فشل إرسال الطلب.\nالسبب: {error_msg}"
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=response_text,
        parse_mode='HTML',
        reply_markup=create_main_menu(chat_id)
    )
    
    if chat_id in user_states:
        del user_states[chat_id]
    
    bot.answer_callback_query(call.id)

# معالج الاستدعاء لزر الإيقاف
@bot.callback_query_handler(func=lambda call: call.data.startswith('stop_1m_order_'))
def handle_stop_order(call):
    chat_id = call.message.chat.id
    
    # تعيين علامة الإيقاف إلى False
    global RUNNING_1M_ORDERS
    if chat_id in RUNNING_1M_ORDERS:
        RUNNING_1M_ORDERS[chat_id] = False
        bot.answer_callback_query(call.id, "✅ تم طلب إيقاف عملية الرشق. سيتم التوقف قريباً.", show_alert=True)
        # إزالة زر الإيقاف فوراً لمنع الضغط عليه مرة أخرى
        try:
            bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
        except Exception:
            pass 
    else:
        bot.answer_callback_query(call.id, "⚠️ لا توجد عملية رشق 1M قيد التنفيذ حاليًا.", show_alert=True)
        
@bot.callback_query_handler(func=lambda call: call.data == 'cancel')
def handle_cancel(call):
    chat_id = call.message.chat.id
    if chat_id in user_states:
        del user_states[chat_id]
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text="تم إلغاء العملية.",
        reply_markup=create_main_menu(chat_id)
    )
    bot.answer_callback_query(call.id)

# (التعديل 1): إضافة دالة جديدة لمعالجة ملفات المستندات
@bot.message_handler(content_types=['document'])
def handle_document(message):
    chat_id = message.chat.id
    state = user_states.get(chat_id)

    # التحقق من أن المستخدم هو المالك وفي حالة انتظار المفتاح
    if not is_owner(chat_id) or not (state and state.get('step') == 'admin_waiting_for_key'):
        return

    # التحقق من أن الملف هو TXT (text/plain)
    if message.document and message.document.mime_type == 'text/plain':
        try:
            # تنزيل الملف
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)

            # قراءة المفاتيح من الملف (كل مفتاح في سطر)
            file_content = downloaded_file.decode('utf-8')
            new_keys = [key.strip() for key in file_content.split('\n') if key.strip()]

            if new_keys:
                global API_KEYS
                API_KEYS.extend(new_keys)
                
                # إرسال رسالة نجاح والعودة إلى لوحة التحكم
                bot.send_message(
                    chat_id,
                    f"✅ تم إضافة {len(new_keys)} مفتاح جديد بنجاح من ملف TXT.\nإجمالي المفاتيح: {len(API_KEYS)}",
                    reply_markup=create_admin_menu()
                )
                del user_states[chat_id]
                
            else:
                bot.send_message(chat_id, "⚠️ الملف المُرسل لا يحتوي على أي مفاتيح صالحة.")
                
        except Exception as e:
            bot.send_message(chat_id, f"❌ حدث خطأ أثناء معالجة الملف: {e}")
    
    return

@bot.message_handler(func=lambda message: message.chat.id in user_states)
def handle_input(message):
    chat_id = message.chat.id
    state = user_states.get(chat_id)
    
    if not state:
        return handle_start(message)
    
    step = state.get('step')
    
    if step == 'admin_waiting_for_key':
        if not is_owner(chat_id): return
        
        if message.text:
            new_key = message.text.strip()
            if new_key:
                API_KEYS.append(new_key)
                bot.send_message(chat_id, f"✅ تم إضافة المفتاح الجديد بنجاح.\nإجمالي المفاتيح: {len(API_KEYS)}", reply_markup=create_admin_menu())
                del user_states[chat_id]
            
    elif step == 'admin_waiting_for_daily_limit':
        if not is_owner(chat_id): return
        try:
            new_limit = int(message.text.strip())
            global DAILY_REQUEST_LIMIT
            DAILY_REQUEST_LIMIT = new_limit
            bot.send_message(chat_id, f"✅ تم تعيين الحد اليومي الجديد إلى {new_limit}.", reply_markup=create_admin_menu())
            del user_states[chat_id]
        except ValueError:
            bot.send_message(chat_id, "الرجاء إرسال رقم صحيح.")

    elif step == 'admin_waiting_for_ref_limit':
        if not is_owner(chat_id): return
        try:
            new_ref_limit = int(message.text.strip())
            global REFERRAL_ATTEMPTS_PER_ENTRY
            REFERRAL_ATTEMPTS_PER_ENTRY = new_ref_limit
            bot.send_message(chat_id, f"✅ تم تعيين محاولات الإحالة لكل مشترك إلى {new_ref_limit} محاولة.", reply_markup=create_admin_menu())
            del user_states[chat_id]
        except ValueError:
            bot.send_message(chat_id, "الرجاء إرسال رقم صحيح.")
            
    elif step == 'admin_waiting_for_channel_to_add':
        if not is_owner(chat_id): return
        
        target_chat_id = None
        display_link = None
        
        if message.forward_from_chat:
            target_chat_id = message.forward_from_chat.id
            display_link = message.forward_from_chat.title
        else:
            input_text = message.text.strip()
            if input_text.startswith('@'):
                display_link = input_text
                try:
                    chat_info = bot.get_chat(input_text)
                    target_chat_id = chat_info.id
                except telebot.apihelper.ApiTelegramException:
                    bot.send_message(chat_id, "❌ لم أتمكن من العثور على القناة. تأكد من صحة المعرف (@username).")
                    return
            else:
                bot.send_message(chat_id, "الرجاء إرسال معرف القناة (@username) أو توجيه منشور منها.")
                return

        if target_chat_id in MANDATORY_CHANNELS_INFO:
            bot.send_message(chat_id, "⚠️ هذه القناة مضافة مسبقاً.")
            del user_states[chat_id]
            return

        if target_chat_id:
            try:
                member = bot.get_chat_member(target_chat_id, bot.get_me().id)
                if member.status not in ['administrator', 'creator']:
                    bot.send_message(chat_id, "❌ يجب أن يكون البوت مسؤولاً في القناة قبل إضافتها.")
                    del user_states[chat_id]
                    return
                    
                MANDATORY_CHANNELS_INFO[target_chat_id] = display_link if display_link else str(target_chat_id)
                
                bot.send_message(chat_id, f"✅ تم إضافة القناة {display_link} للقنوات الإجبارية.", reply_markup=create_mandatory_channels_menu())
                del user_states[chat_id]
                
            except telebot.apihelper.ApiTelegramException as e:
                bot.send_message(chat_id, f"❌ حدث خطأ في التحقق من القناة: {e}. هل هي قناة عامة أو تمت إضافتي كأدمن؟")
                del user_states[chat_id]
            except Exception:
                bot.send_message(chat_id, "خطأ غير متوقع أثناء التحقق.")
                del user_states[chat_id]
            
    elif step == 'admin_waiting_for_vip_id':
        if not is_owner(chat_id): return
        try:
            target_id = int(message.text.strip())
            bot.send_message(
                chat_id,
                f"اختر مدة تفعيل VIP للمستخدم {target_id}:",
                reply_markup=create_vip_duration_menu(target_id)
            )
            del user_states[chat_id]
        except ValueError:
            bot.send_message(chat_id, "الرجاء إرسال معرف (ID) صحيح للمستخدم.")
    
    elif step == 'admin_waiting_for_duration':
        if not is_owner(chat_id): return
        try:
            duration = int(message.text.strip())
            target_id = state['target_id']
            unit = state['unit']
            
            if unit == 'h':
                expiry_time = time.time() + duration * 3600
                unit_text = f"{duration} ساعة"
            else:
                expiry_time = time.time() + duration * 86400
                unit_text = f"{duration} يوم"
                
            VIP_USERS[target_id] = expiry_time
            
            bot.send_message(
                chat_id,
                f"✅ تم تفعيل VIP للمستخدم {target_id} لمدة {unit_text}.",
                reply_markup=create_admin_menu()
            )
            del user_states[chat_id]
        except ValueError:
            bot.send_message(chat_id, "الرجاء إرسال مدة صحيحة (رقم).")
    
    elif step == 'admin_waiting_min_free':
        if not is_owner(chat_id): return
        try:
            min_limit = int(message.text.strip())
            FREE_LIMITS['min'] = min_limit
            bot.send_message(
                chat_id,
                f"✅ تم تعيين الحد الأدنى للتفاعلات المجانية إلى {min_limit}.",
                reply_markup=create_admin_menu()
            )
            del user_states[chat_id]
        except ValueError:
            bot.send_message(chat_id, "الرجاء إرسال رقم صحيح.")
    
    elif step == 'admin_waiting_max_free':
        if not is_owner(chat_id): return
        try:
            max_limit = int(message.text.strip())
            FREE_LIMITS['max'] = max_limit
            bot.send_message(
                chat_id,
                f"✅ تم تعيين الحد الأقصى للتفاعلات المجانية إلى {max_limit}.",
                reply_markup=create_admin_menu()
            )
            del user_states[chat_id]
        except ValueError:
            bot.send_message(chat_id, "الرجاء إرسال رقم صحيح.")
            
    elif step == 'admin_waiting_vip_limit_value':
        if not is_owner(chat_id): return
        try:
            value = int(message.text.strip())
            key = state['key']
            limit = state['limit']
            
            VIP_LIMITS[key][limit] = value
            
            display_text = "الإيجابية" if key == 'positive' else "السلبية"
            display_limit = "الأدنى" if limit == 'min' else "الأقصى"
            
            bot.send_message(
                chat_id,
                f"✅ تم تعيين الحد {display_limit} للتفاعلات {display_text} لـ VIP إلى {value}.",
                reply_markup=create_vip_limits_menu()
            )
            del user_states[chat_id]
        except ValueError:
            bot.send_message(chat_id, "الرجاء إرسال رقم صحيح.")

    elif step == 'admin_waiting_for_broadcast_message':
        if not is_owner(chat_id): return
        
        broadcast_text = message.text
        
        all_known_ids = set(DAILY_LIMITS.keys()) | set(VIP_USERS.keys()) | set(user_states.keys()) | {OWNER_ID}
        for channel_id in MONITORED_CHANNELS.keys():
            try:
                chat_info = bot.get_chat(channel_id)
                if chat_info.type in ['channel', 'group', 'supergroup']:
                    all_known_ids.add(channel_id)
            except Exception:
                pass
        
        success_count = 0
        
        bot.send_message(chat_id, f"جاري إرسال الإذاعة إلى {len(all_known_ids)} وجهة...")

        for target_id in list(all_known_ids):
            try:
                bot.send_message(target_id, broadcast_text)
                success_count += 1
            except telebot.apihelper.ApiTelegramException:
                pass 
            except Exception:
                pass

        bot.send_message(chat_id, f"✅ اكتملت الإذاعة. تم الإرسال بنجاح إلى {success_count} مستخدم/قناة.", reply_markup=create_admin_menu())
        del user_states[chat_id]
    
    elif step == 'waiting_for_channel_link':
        channel_link = message.text.strip()
        
        bot.send_message(chat_id, "جاري التحقق من صلاحيات البوت...")
        
        channel_id, error = check_admin_and_get_info(channel_link)
        
        if channel_id:
            if channel_id not in MONITORED_CHANNELS:
                MONITORED_CHANNELS[channel_id] = {
                    'user_id': chat_id,
                    'link': channel_link,
                    'last_checked_msg_id': None, 
                    'reaction_service_id': state['service_id']
                }
            
            bot.send_message(
                chat_id,
                f"✅ تم تأكيد صلاحيات البوت ({channel_link}). تم تفعيل الرشق التلقائي (الكمية: {FUTURE_REACTION_QTY}).\n\nالبوت سيبدأ برشق التفاعلات تلقائيًا لأي منشور جديد.",
                reply_markup=create_main_menu(chat_id)
            )
            del user_states[chat_id]
        else:
            bot.send_message(chat_id, f"❌ فشل التحقق من القناة:\n{error}\n\nالرجاء التأكد من رفع البوت كمسؤول ثم أعد إرسال الرابط.", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="cancel")))
    
    elif step == 'waiting_for_link':
        state['link'] = message.text
        
        if state.get('is_1m'):
            state['quantity'] = 1000000 
            state['step'] = 'confirmation'
            
            confirmation_text = f"📋 تأكيد الطلب\n\nالخدمة: 1M مشاهدة وتفاعلات\nالرابط: {message.text}\nالكمية: 1,000,000\n\nهل تريد تأكيد الطلب؟"
            
            bot.send_message(
                chat_id,
                confirmation_text,
                reply_markup=create_confirmation_menu()
            )
        else:
            state['step'] = 'waiting_for_quantity'
            service_type = "تفاعلات إيجابية" if state['service_id'] == SERVICE_IDS['positive'] else "تفاعلات سلبية"
        
            bot.send_message(
                chat_id,
                f"تم استلام الرابط بنجاح.\n\nالخدمة: {service_type}\nالرابط: {message.text}\n\nالرجاء إرسال الكمية المطلوبة (أرقام فقط):"
            )
    
    elif step == 'waiting_for_quantity':
        try:
            qty = int(message.text.strip())
            service_key = 'positive' if state['service_id'] == SERVICE_IDS['positive'] else 'negative'

            if is_vip(chat_id):
                vip_min = VIP_LIMITS[service_key]['min']
                vip_max = VIP_LIMITS[service_key]['max']
                
                if qty < vip_min:
                    bot.send_message(chat_id, f"الحد الأدنى المسموح لـ VIP هو {vip_min}. الرجاء إدخال كمية أكبر.")
                    return
                if qty > vip_max:
                    bot.send_message(chat_id, f"الحد الأقصى المسموح لـ VIP هو {vip_max}. الرجاء إدخال كمية أصغر.")
                    return
            else:
                if qty < FREE_LIMITS['min']:
                    bot.send_message(chat_id, f"الحد الأدنى المسموح هو {FREE_LIMITS['min']}. الرجاء إدخال كمية أكبر.")
                    return
                if qty > FREE_LIMITS['max']:
                    bot.send_message(chat_id, f"الحد الأقصى المسموح هو {FREE_LIMITS['max']}. الرجاء إدخال كمية أصغر.")
                    return
            
            state['quantity'] = qty
            state['step'] = 'confirmation'
            
            link = state['link']
            service_type = "تفاعلات إيجابية" if state['service_id'] == SERVICE_IDS['positive'] else "تفاعلات سلبية"
            
            attempts_left = REFERRAL_ATTEMPTS.get(chat_id, 0)
            
            confirmation_text = f"📋 تأكيد الطلب\n\n"
            confirmation_text += f"الخدمة: {service_type}\n"
            confirmation_text += f"الرابط: {link}\n"
            confirmation_text += f"الكمية: {qty}\n\n"
            
            if not is_vip(chat_id) and attempts_left > 0:
                 confirmation_text += f"ملاحظة: سيتم استهلاك 1 محاولة من رصيدك المتبقي ({attempts_left}).\n\n"
            
            confirmation_text += "هل تريد تأكيد الطلب؟"
            
            bot.send_message(
                chat_id,
                confirmation_text,
                reply_markup=create_confirmation_menu()
            )
            
        except ValueError:
            bot.send_message(chat_id, "الكمية يجب أن تكون رقماً صحيحاً. حاول مرة أخرى.")

if __name__ == '__main__':
    def reset_daily_limits():
        global DAILY_LIMITS
        DAILY_LIMITS = {}
        threading.Timer(86400, reset_daily_limits).start()
    
    reset_daily_limits()
    monitor_channels()
    bot.infinity_polling()
