import json
import os
import sqlite3
import logging
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- CONFIG ---
TOKEN = "8481030243:AAF3GlpzdfkbJwFjwHIYhm0NXJiX67Q9Fhs"
PUBLIC_CHANNEL_ID = "-1003615505475"
DATA_FILE = "/var/www/app.x1galaxy.io/all_validator_data.json"
DB_FILE = "/root/xenobi_website/bot_users.db"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions 
                 (user_id TEXT, identity TEXT, last_state TEXT)''')
    conn.commit()
    conn.close()

# --- HELPER: GET VALIDATOR NAME ---
def get_friendly_name(identity, validators_dict):
    """Returns the validator name if it exists, otherwise a shortened identity."""
    if identity in validators_dict:
        v = validators_dict[identity]
        return v.get('name') or f"{identity[:4]}...{identity[-4:]}"
    return f"{identity[:4]}...{identity[-4:]}"

# --- COMMANDS ---
async def post_init(application):
    """Sets the bot menu commands in the Telegram UI."""
    commands = [
        BotCommand("start", "Introduction and instructions"),
        BotCommand("subscribe", "Usage: /subscribe <IDENTITY>"),
        BotCommand("list", "View your current subscriptions"),
        BotCommand("unsubscribe", "Usage: /unsubscribe <IDENTITY>")
    ]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛰 <b>X1Galaxy Alert Bot</b>\n\n"
        "I monitor the X1 Blockchain and alert you to changes in your validator's status.\n\n"
        "<b>Available Commands:</b>\n"
        "/subscribe <code>[identity]</code> - Get private alerts for a node\n"
        "/list - See your active subscriptions\n"
        "/unsubscribe <code>[identity]</code> - Stop receiving alerts\n\n"
        "<b>What I monitor:</b>\n"
        "• State changes (Active/Delinquent)\n"
        "• Commission changes\n"
        "• Epoch Reward distributions\n"
        "• Performance spikes"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("❌ Please provide an identity. Example:\n<code>/subscribe HN4DDjs...</code>", parse_mode='HTML')
        return
    
    identity = context.args[0]
    
    # Try to find name for immediate confirmation
    name = "this address"
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            v_dict = {v['identity']: v for v in data.get('validators', [])}
            name = get_friendly_name(identity, v_dict)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO subscriptions (user_id, identity, last_state) VALUES (?, ?, ?)", (user_id, identity, "{}"))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Subscribed! You will now receive alerts for <b>{name}</b>.", parse_mode='HTML')

async def list_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    subs = c.execute("SELECT identity FROM subscriptions WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()

    if not subs:
        await update.message.reply_text("You have no active subscriptions.")
        return

    text = "<b>Your Subscriptions:</b>\n"
    for s in subs:
        text += f"• <code>{s[0]}</code>\n"
    await update.message.reply_text(text, parse_mode='HTML')

# --- THE BACKGROUND JOB ---
async def check_data_job(context: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(DATA_FILE): return
    
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)
    
    validators = {v['identity']: v for v in data.get('validators', [])}
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    subscriptions = c.execute("SELECT user_id, identity, last_state FROM subscriptions").fetchall()

    for user_id, identity, last_state_json in subscriptions:
        if identity not in validators: continue
        
        curr = validators[identity]
        prev = json.loads(last_state_json)
        msg_parts = []
        
        # Use our Friendly Name helper
        name = get_friendly_name(identity, validators)
        
        # 1. Status Change
        if prev.get('status') and curr['status'] != prev['status']:
            icon = "🟢" if curr['status'] == "Active" else "🔴"
            msg_parts.append(f"{icon} <b>Status:</b> {curr['status']}")

        # 2. Commission Change
        if prev.get('comm') is not None and curr['commission'] != prev['comm']:
            msg_parts.append(f"⚖️ <b>Comm:</b> {prev['comm']}% ➡️ {curr['commission']}%")

        # 3. Reward Distribution
        curr_rewards = curr.get('totalLifetimeRewards', 0)
        if prev.get('rewards') is not None and curr_rewards > prev['rewards']:
            gain = (curr_rewards - prev['rewards']) / 1_000_000_000
            msg_parts.append(f"💰 <b>Epoch Rewards:</b> +{gain:.4f} XNT")

        if msg_parts:
            text = f"🛰 <b>Alert: {name}</b>\n" + "\n".join(msg_parts)
            try:
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode='HTML')
            except Exception: pass # Handles users who blocked the bot
            
        new_state = json.dumps({
            "status": curr['status'],
            "comm": curr['commission'],
            "rewards": curr_rewards
        })
        c.execute("UPDATE subscriptions SET last_state = ? WHERE user_id = ? AND identity = ?", (new_state, user_id, identity))

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("list", list_subs))
    
    job_queue = app.job_queue
    job_queue.run_repeating(check_data_job, interval=180, first=10)
    
    app.run_polling()
