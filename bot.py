import logging
import random
import os
import json
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)

# --- CONFIGURATION ---
TOKEN = os.environ.get("BOT_TOKEN")
DATA_FILE = os.environ.get("DATA_PATH", "data.json")
IMAGE_URL = "https://cdn-icons-png.flaticon.com/512/6231/6231458.png"

# --- STATES FOR CONFIGURATION CONVERSATION ---
BUDGET, RULES, DEADLINE = range(3)

# --- IN-MEMORY DATABASE ---
# Structure:
# games = {
#   group_chat_id: {
#       'admin_id': int,
#       'status': 'open' | 'closed',
#       'config': {'budget': str, 'deadline': str},
#       'users': {
#           user_id: {'name': str, 'username': str, 'wishlist': str}
#       }
#   }
# }
games = {}

# --- JSON PERSISTENCE ---
def load_games():
    """Load games data from JSON file."""
    global games
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Convert string keys back to integers (JSON doesn't support int keys)
                games = {}
                for group_id_str, game_data in data.items():
                    group_id = int(group_id_str)
                    games[group_id] = game_data
                    # Also convert user IDs back to integers
                    games[group_id]['users'] = {
                        int(user_id_str): user_data
                        for user_id_str, user_data in game_data.get('users', {}).items()
                    }
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Failed to load games data: {e}")
            games = {}

def save_games():
    """Save games data to JSON file."""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(games, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save games data: {e}")

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==============================================================================
# 1. NEW: BOT ADDED TO GROUP HANDLER
# ==============================================================================

async def bot_added_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggered when the bot is added to a new group.
    Sends a welcome message asking to start the event.
    """
    # Check if the bot itself was the one added
    bot_id = context.bot.id
    new_members = update.message.new_chat_members
    
    is_bot_added = any(member.id == bot_id for member in new_members)

    if is_bot_added:
        await update.message.reply_text(
            "🎄 Хо-хо-хо! Мене додали до групи!\n\n"
            "Щоб організувати Таємного Санту, адміністратор має написати:\n\n"
            "👉 **/santa**"
        )

# ==============================================================================
# 1. GROUP HANDLERS (Public)
# ==============================================================================

async def start_group_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggered by /santa in a Group.
    Initializes the game and shows the dashboard with buttons.
    """
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    user_id = update.effective_user.id

    if chat_type == 'private':
        await update.message.reply_text("🚫 Будь ласка, використовуйте цю команду в групі, де хочете провести Таємного Санту.")
        return

    # --- FIX START: Get the bot's username explicitly ---
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    # --- FIX END ---

    # Initialize Game Data
    games[chat_id] = {
        'admin_id': user_id,
        'status': 'open',
        'config': {'budget': 'Не вказано', 'rules': 'Не вказано', 'deadline': 'Не вказано'},
        'users': {}
    }
    save_games()

    # Deep Links
    join_link = f"https://t.me/{bot_username}?start=join_{chat_id}"
    setup_link = f"https://t.me/{bot_username}?start=setup_{chat_id}"

    # Buttons
    keyboard = [
        [InlineKeyboardButton("🎅 Приєднатися", url=join_link)],
        [InlineKeyboardButton("⚙️ Налаштування (Тільки адмін)", url=setup_link)],
        [InlineKeyboardButton("📋 Статус", callback_data=f"status_{chat_id}"),
         InlineKeyboardButton("🎲 Жеребкування", callback_data=f"shuffle_{chat_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # HTML Message with Invisible Image Link (The "Main Message" Trick)
    # The <a href> tag adds the image preview, but the text allows 4096 chars.
    text_content = (
        f"<a href='{IMAGE_URL}'>&#8205;</a>"
        f"<b>🎄 Таємний Санта розпочато! 🎄</b>\n\n"
        f"<b>Правила: Не вказано</b>\n"
        f"💰 Бюджет: Не вказано\n"
        f"📅 Дедлайн: Не вказано\n\n"
        f"<b>Учасники: 0</b>\n"
        f"<i>Натисніть 'Приєднатися', щоб вказати свій список бажань!</i>"
    )

    await update.message.reply_text(
        text=text_content,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def check_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refreshes the message in the group with latest participant list and rules."""
    query = update.callback_query
    await query.answer()
    
    try:
        group_id = int(query.data.split('_')[1])
    except (IndexError, ValueError):
        return

    if group_id not in games:
        await query.edit_message_text("❌ Подія застаріла або дані втрачено (бот перезавантажився).")
        return

    game = games[group_id]
    participants = game['users']
    config = game['config']
    
    # Sanitize names to prevent HTML errors
    if not participants:
        names_list = "<i>Ще немає учасників</i>"
    else:
        names_list = "\n".join([f"- {html.escape(p['name'])}" for p in participants.values()])

    # Re-build the message with the image
    text_content = (
        f"<a href='{IMAGE_URL}'>&#8205;</a>"
        f"<b>🎄 Статус Таємного Санти 🎄</b>\n\n"
        f"<b>Правила:</b> {html.escape(config['rules'])}\n"
        f"💰 Бюджет: {html.escape(config['budget'])}\n"
        f"📅 Дедлайн: {html.escape(config['deadline'])}\n\n"
        f"<b>Учасники ({len(participants)}):</b>\n"
        f"{names_list}\n\n"
        f"<i>Очікуємо жеребкування від адміна...</i>"
    )

    # Use edit_message_text (because we are using the Link Preview method)
    await query.edit_message_text(
        text=text_content,
        reply_markup=query.message.reply_markup,
        parse_mode=ParseMode.HTML
    )
    

async def protected_shuffle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggered by the Shuffle button. 
    Includes Security Check: Only Admin can execute.
    """
    query = update.callback_query
    group_id = int(query.data.split('_')[1])

    if group_id not in games:
        await query.answer("❌ Гру не знайдено.", show_alert=True)
        return

    # --- SECURITY CHECK ---
    # Check if the clicker is the one who started the game OR is a group admin
    clicker_id = update.effective_user.id
    admin_id = games[group_id]['admin_id']
    
    # Ideally, we also check Telegram Admin status, but for simplicity, we check game creator
    if clicker_id != admin_id:
        # This sends a "Toast" notification only to the user who clicked
        await query.answer("🚫 Тільки організатор може провести жеребкування!", show_alert=True)
        return

    # If Admin, proceed...
    await query.answer() # Close loading animation
    
    users = list(games[group_id]['users'].keys())
    if len(users) < 2:
        await context.bot.send_message(chat_id=group_id, text="⚠️ Потрібно мінімум 2 учасники для жеребкування!")
        return

    # --- DERANGEMENT LOGIC (Simple Rotation) ---
    random.shuffle(users)
    
    blocked_users = []
    
    for i in range(len(users)):
        giver_id = users[i]
        receiver_id = users[(i + 1) % len(users)] # The next person in list
        
        receiver_data = games[group_id]['users'][receiver_id]
        config = games[group_id]['config']

        msg = (
            f"🎅 **ТАЄМНИЙ САНТА** 🎅\n\n"
            f"Ти даруєш подарунок: **{receiver_data['name']}**\n"
            f"📝 **Список бажань:**\n_{receiver_data['wishlist']}_\n\n"
            f"📋 **Правила:** {config['rules']}\n"
            f"💰 **Бюджет:** {config['budget']}\n"
            f"📅 **Дедлайн:** {config['deadline']}"
        )

        try:
            await context.bot.send_message(chat_id=giver_id, text=msg, parse_mode='Markdown')
        except Exception:
            blocked_users.append(games[group_id]['users'][giver_id]['name'])

    # Final Group Announcement
    if blocked_users:
        await context.bot.send_message(chat_id=group_id, text=f"✅ Жеребкування завершено! Але я не зміг надіслати повідомлення цим людям (бот заблокований?): {', '.join(blocked_users)}")
    else:
        await context.bot.send_message(chat_id=group_id, text="✅ **Жеребкування завершено!** Перевірте особисті повідомлення!")

    games[group_id]['status'] = 'closed'
    save_games()

# ==============================================================================
# 2. PRIVATE HANDLERS (Join & Wishlist)
# ==============================================================================

async def handle_join_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggered by /start join_GROUPID in private.
    Registers the user and asks for wishlist.
    """
    args = context.args
    # Check if args exist and start with join_
    if not args or not args[0].startswith("join_"):
        await update.message.reply_text("👋 Привіт! Використовуй кнопки в груповому чаті, щоб приєднатися до Таємного Санти.")
        return

    try:
        group_id = int(args[0].split("_")[1])
    except ValueError:
        return

    if group_id not in games:
        await update.message.reply_text("❌ Ця подія не існує.")
        return

    if games[group_id]['status'] == 'closed':
        await update.message.reply_text("❌ Ця подія вже розпочалася або завершилася.")
        return

    # Register User
    user = update.effective_user
    games[group_id]['users'][user.id] = {
        'name': user.full_name,
        'username': user.username,
        'wishlist': 'Список бажань ще не вказано.'
    }
    save_games()

    # Save context for the next text message
    context.user_data['active_group_id'] = group_id

    await update.message.reply_text(
        f"✅ Ти приєднався до Таємного Санти!\n\n"
        "**Будь ласка, напиши свій СПИСОК БАЖАНЬ у відповідь.**\n"
        "(Що ти хочеш? Що тобі не подобається?)",
        parse_mode='Markdown'
    )

async def handle_wishlist_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Captures text in private chat as the wishlist."""
    if update.effective_chat.type != 'private':
        return

    group_id = context.user_data.get('active_group_id')
    
    if not group_id or group_id not in games:
        # If user chats with bot randomly without joining
        await update.message.reply_text("Я не знаю, до якої події ти звертаєшся. Натисни 'Приєднатися' в групі ще раз.")
        return

    text = update.message.text
    user_id = update.effective_user.id

    if user_id in games[group_id]['users']:
        games[group_id]['users'][user_id]['wishlist'] = text
        save_games()
        await update.message.reply_text("💾 **Список бажань збережено!** (Можеш надіслати інше повідомлення, щоб замінити його).", parse_mode='Markdown')
    else:
        await update.message.reply_text("Ти не зареєстрований. Поверніся в групу і натисни 'Приєднатися'.")

# ==============================================================================
# 3. ADMIN CONFIGURATION (Conversation Handler)
# ==============================================================================

async def start_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: /start setup_GROUPID"""
    args = context.args
    # Parse Group ID
    if not args or not args[0].startswith("setup_"):
        return ConversationHandler.END

    group_id = int(args[0].split("_")[1])
    
    # Security: Check if user is the admin stored in games
    if games.get(group_id, {}).get('admin_id') != update.effective_user.id:
        await update.message.reply_text("🚫 Ти не є адміністратором цієї події.")
        return ConversationHandler.END

    context.user_data['config_group_id'] = group_id
    
    await update.message.reply_text(
        f"⚙️ **Налаштування адміна**\n\n"
        "1️⃣ Введи **Бюджет** (наприклад, '500 грн', 'Handmade'):",
        parse_mode='Markdown'
    )
    return BUDGET

async def set_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = context.user_data['config_group_id']
    games[group_id]['config']['budget'] = update.message.text
    save_games()

    await update.message.reply_text("✅ Бюджет встановлено.\n\n2️⃣ Тепер введи **Правила**:")
    return RULES

async def set_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = context.user_data['config_group_id']
    games[group_id]['config']['rules'] = update.message.text
    save_games()

    await update.message.reply_text("✅ Правила встановлено.\n\n3️⃣ Тепер введи **Дедлайн** (наприклад, '24 грудня'):")
    return DEADLINE

async def set_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = context.user_data['config_group_id']
    games[group_id]['config']['deadline'] = update.message.text
    save_games()

    await update.message.reply_text(
        "✅ **Налаштування завершено!**\n\n"
        "Я оновив параметри. Можеш повернутися в групу і натиснути 'Статус', щоб побачити зміни.",
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def cancel_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Налаштування скасовано.")
    return ConversationHandler.END

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

if __name__ == '__main__':
    # Load existing games data from JSON file
    load_games()

    app = ApplicationBuilder().token(TOKEN).build()

    # 1. Conversation Handler (Needs to be higher priority to catch /start setup_...)
    # Note: We filter specifically for 'start' commands that contain 'setup_'
    config_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_config, filters.Regex('setup_'))],
        states={
            BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_budget)],
            RULES: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_rules)],
            DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_deadline)],
        },
        fallbacks=[CommandHandler('cancel', cancel_config)]
    )
    app.add_handler(config_handler)

    # 2. Group Commands
    app.add_handler(CommandHandler("santa", start_group_event))
    
    # 3. Button Callbacks
    app.add_handler(CallbackQueryHandler(check_status_callback, pattern=r"^status_"))
    app.add_handler(CallbackQueryHandler(protected_shuffle_callback, pattern=r"^shuffle_"))

    # 4. Private Join Handler (Matches /start join_...)
    # The regex filter ensures this only triggers for join links, not general /start
    app.add_handler(CommandHandler("start", handle_join_start, filters.Regex('join_')))
    
    # 5. Generic Start (If user just types /start with no payload)
    app.add_handler(CommandHandler("start", handle_join_start)) 

    # 6. Wishlist Message Capture (Must be last to avoid capturing commands)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_wishlist_text))

    print("🤖 Бот запущено...")
    app.run_polling()