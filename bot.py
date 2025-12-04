import logging
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
TOKEN = "YOUR_BOT_TOKEN_HERE"
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
            "🎄 Ho ho ho! I've been added to the group!\n\n"
            "To start organizing your Secret Santa event, an admin should type:\n\n"
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
        await update.message.reply_text("🚫 Please run this command in the Group where you want to hold Secret Santa.")
        return

    # --- FIX START: Get the bot's username explicitly ---
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    # --- FIX END ---

    # Initialize Game Data
    games[chat_id] = {
        'admin_id': user_id,
        'status': 'open',
        'config': {'budget': 'Not set','rules': 'Not set' 'deadline': 'Not set'},
        'users': {}
    }
    
    # Deep Links
    join_link = f"https://t.me/{bot_username}?start=join_{chat_id}"
    setup_link = f"https://t.me/{bot_username}?start=setup_{chat_id}"

    # Buttons
    keyboard = [
        [InlineKeyboardButton("🎅 Join Secret Santa", url=join_link)],
        [InlineKeyboardButton("⚙️ Setup Rules (Admin Only)", url=setup_link)],
        [InlineKeyboardButton("📋 Status", callback_data=f"status_{chat_id}"),
         InlineKeyboardButton("🎲 Shuffle", callback_data=f"shuffle_{chat_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # HTML Message with Invisible Image Link (The "Main Message" Trick)
    # The <a href> tag adds the image preview, but the text allows 4096 chars.
    text_content = (
        f"<a href='{IMAGE_URL}'>&#8205;</a>"
        f"<b>🎄 Secret Santa Event Started! 🎄</b>\n\n"
        f"<b>Rules: Not set</b>\n"
        f"💰 Budget: Not set\n"
        f"📅 Deadline: Not set\n\n"
        f"<b>Participants: 0</b>\n"
        f"<i>Click 'Join' to set your wishlist privately!</i>"
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
        await query.edit_message_text("❌ Event expired or data lost (bot restarted).")
        return

    game = games[group_id]
    participants = game['users']
    config = game['config']
    
    # Sanitize names to prevent HTML errors
    if not participants:
        names_list = "<i>No participants yet</i>"
    else:
        names_list = "\n".join([f"- {html.escape(p['name'])}" for p in participants.values()])

    # Re-build the message with the image
    text_content = (
        f"<a href='{IMAGE_URL}'>&#8205;</a>"
        f"<b>🎄 Secret Santa Status 🎄</b>\n\n"
        f"<b>Rules:</b>\n"
        f"💰 Budget: {html.escape(config['budget'])}\n"
        f"📅 Deadline: {html.escape(config['deadline'])}\n\n"
        f"<b>Participants ({len(participants)}):</b>\n"
        f"{names_list}\n\n"
        f"<i>Waiting for Admin to shuffle...</i>"
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
        await query.answer("❌ Game not found.", show_alert=True)
        return

    # --- SECURITY CHECK ---
    # Check if the clicker is the one who started the game OR is a group admin
    clicker_id = update.effective_user.id
    admin_id = games[group_id]['admin_id']
    
    # Ideally, we also check Telegram Admin status, but for simplicity, we check game creator
    if clicker_id != admin_id:
        # This sends a "Toast" notification only to the user who clicked
        await query.answer("🚫 Only the Event Creator can start the shuffle!", show_alert=True)
        return

    # If Admin, proceed...
    await query.answer() # Close loading animation
    
    users = list(games[group_id]['users'].keys())
    if len(users) < 2:
        await context.bot.send_message(chat_id=group_id, text="⚠️ Need at least 2 people to shuffle!")
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
            f"🎅 **SECRET SANTA REVEAL** 🎅\n\n"
            f"You are gifting to: **{receiver_data['name']}**\n"
            f"📝 **Their Wishlist:**\n_{receiver_data['wishlist']}_\n\n"
            f"💰 **Rules:** {config['rules']}\n"
            f"💰 **Budget:** {config['budget']}\n"
            f"📅 **Deadline:** {config['deadline']}"
        )

        try:
            await context.bot.send_message(chat_id=giver_id, text=msg, parse_mode='Markdown')
        except Exception:
            blocked_users.append(games[group_id]['users'][giver_id]['name'])

    # Final Group Announcement
    if blocked_users:
        await context.bot.send_message(chat_id=group_id, text=f"✅ Shuffle Done! But I couldn't DM these people (Bot blocked?): {', '.join(blocked_users)}")
    else:
        await context.bot.send_message(chat_id=group_id, text="✅ **Shuffle Complete!** Check your private messages!")
    
    games[group_id]['status'] = 'closed'

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
        await update.message.reply_text("👋 Hi! Use the buttons in your Group Chat to join a Secret Santa.")
        return

    try:
        group_id = int(args[0].split("_")[1])
    except ValueError:
        return

    if group_id not in games:
        await update.message.reply_text("❌ This event doesn't exist.")
        return

    if games[group_id]['status'] == 'closed':
        await update.message.reply_text("❌ This event has already started/finished.")
        return

    # Register User
    user = update.effective_user
    games[group_id]['users'][user.id] = {
        'name': user.full_name,
        'username': user.username,
        'wishlist': 'No wishlist provided yet.'
    }
    
    # Save context for the next text message
    context.user_data['active_group_id'] = group_id

    await update.message.reply_text(
        f"✅ You joined the Secret Santa for Group ID `{group_id}`!\n\n"
        "**Please reply to this message with your WISHLIST.**\n"
        "(What do you want? What do you hate?)",
        parse_mode='Markdown'
    )

async def handle_wishlist_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Captures text in private chat as the wishlist."""
    if update.effective_chat.type != 'private':
        return

    group_id = context.user_data.get('active_group_id')
    
    if not group_id or group_id not in games:
        # If user chats with bot randomly without joining
        await update.message.reply_text("I don't know which event you are referring to. Please click 'Join' in your group again.")
        return

    text = update.message.text
    user_id = update.effective_user.id

    if user_id in games[group_id]['users']:
        games[group_id]['users'][user_id]['wishlist'] = text
        await update.message.reply_text("💾 **Wishlist Saved!** (You can send another message to overwrite it).")
    else:
        await update.message.reply_text("You aren't registered. Go back to the group and click Join.")

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
        await update.message.reply_text("🚫 You are not the admin of this event.")
        return ConversationHandler.END

    context.user_data['config_group_id'] = group_id
    
    await update.message.reply_text(
        f"⚙️ **Admin Setup for Group {group_id}**\n\n"
        "1️⃣ Please enter the **Budget** (e.g., '$20', 'Handmade'):"
    )
    return BUDGET

async def set_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = context.user_data['config_group_id']
    games[group_id]['config']['budget'] = update.message.text
    
    await update.message.reply_text("✅ Budget set.\n\n2️⃣ Now, please enter the **Rules** :")
    return RULES

async def set_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = context.user_data['config_group_id']
    games[group_id]['config']['rules'] = update.message.text
    
    await update.message.reply_text("✅ Rules set.\n\n2️⃣ Now, please enter the **Deadline** (e.g., 'Dec 24th'):")
    return DEADLINE

async def set_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = context.user_data['config_group_id']
    games[group_id]['config']['deadline'] = update.message.text
    
    await update.message.reply_text(
        "✅ **Configuration Complete!**\n\n"
        "I have updated the settings. You can go back to the group and click 'Status' to see changes."
    )
    return ConversationHandler.END

async def cancel_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Setup canceled.")
    return ConversationHandler.END

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

if __name__ == '__main__':
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

    print("🤖 Bot is running...")
    app.run_polling()