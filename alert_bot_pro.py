import json
import os
import sqlite3
import logging
from datetime import datetime
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler

# --- CONFIGURATION ---
# Replace with your actual Bot Token from @BotFather
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
# Replace with your actual Channel ID (e.g., -100...)
PUBLIC_CHANNEL_ID = "-1002361138833" 

# File Paths
DATA_FILE = "/var/www/app.x1galaxy.io/all_validator_data.json"
DB_FILE = "/root/xenobi_website/bot_users.db"

# Settings & Thresholds
WHALE_THRESHOLD = 50000 
LAMPORTS = 1_000_000_000
FOOTER = "\n\n📊 <i>More data at <a href='https://x1galaxy.io'>x1galaxy.io</a></i>"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- DATABASE LOGIC ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions 
                 (user_id TEXT, identity TEXT, last_state TEXT, skip_limit INTEGER DEFAULT 1, 
                 UNIQUE(user_id, identity))''')
    c.execute('''CREATE TABLE IF NOT EXISTS network_state (key TEXT PRIMARY KEY, value TEXT)''')
    conn.commit()
    conn.close()

def get_net_state(key, default=None):
    conn = sqlite3.connect(DB_FILE)
    res = conn.execute("SELECT value FROM network_state WHERE key=?", (key,)).fetchone()
    conn.close()
    return res[0] if res else default

def set_net_state(key, value):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR REPLACE INTO network_state (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

# --- DATA HELPERS ---
def load_data():
    if not os.path.exists(DATA_FILE): return {}
    try:
        with open(DATA_FILE, 'r') as f: return json.load(f)
    except: return {}

def find_validator_smart(query, validators):
    """
    Returns (best_match_dict, list_of_suggestion_dicts)
    """
    query = query.strip().lower()
    if not query: return None, []

    # 1. Exact Identity match
    for v in validators:
        if query == v.get('identity', '').lower():
            return v, []

    # 2. Exact Name match
    for v in validators:
        if query == (v.get('name') or "").lower():
            return v, []

    # 3. Partial Name Matches
    suggestions = []
    for v in validators:
        name = (v.get('name') or "").lower()
        if query in name:
            suggestions.append(v)
    
    # If exactly one partial match, treat as best match
    if len(suggestions) == 1:
        return suggestions[0], []
    
    return None, suggestions

def format_xnt(lamports):
    if lamports is None: return "0"
    return f"{int(lamports / LAMPORTS):,}"

# --- BOT COMMANDS ---

async def post_init(application):
    commands = [
        BotCommand("start", "Help & Instructions"),
        BotCommand("stats", "Snapshot: /stats <name/id>"),
        BotCommand("calc", "ROI: /calc <amount> <name>"),
        BotCommand("set_limit", "Alert Limit: /set_limit <num>"),
        BotCommand("all_nodes_rewards", "Rewards table (DM Only)"),
        BotCommand("top", "Stake Leaderboard"),
        BotCommand("subscribe", "Get Private Alerts"),
        BotCommand("list", "Your Subscriptions"),
        BotCommand("unsubscribe", "Stop Alerts")
    ]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🛰 <b>X1Galaxy Bot: Network Intelligence</b>\n\n"
        "<b>Commands:</b>\n"
        "• /stats <code>[name/id]</code> - Live performance\n"
        "• /set_limit <code>[number]</code> - Custom skip alert\n"
        "• /subscribe <code>[id]</code> - Get private pings\n\n"
        "<i>If you provide a partial name, I will show you matching options.</i>" + FOOTER
    )
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Handle being called by command (/stats name) or by callback (button click)
    query = " ".join(context.args) if context.args else ""
    
    if not query:
        await update.message.reply_text("❓ Usage: /stats <code>[name or identity]</code>", parse_mode='HTML')
        return
    
    data = load_data()
    validators = data.get('validators', [])
    best_match, suggestions = find_validator_smart(query, validators)

    # If no exact match but multiple suggestions found -> Show Buttons
    if not best_match and suggestions:
        buttons = []
        for s in suggestions[:8]: # Limit to 8 buttons
            name = s.get('name') or f"ID: {s['identity'][:8]}"
            buttons.append([InlineKeyboardButton(f"📊 {name}", callback_data=f"stats:{name}")])
        
        reply_markup = InlineKeyboardMarkup(buttons)
        await update.message.reply_text(
            f"🔍 I found multiple matches for '<b>{query}</b>'. Which one did you mean?",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return

    if not best_match:
        await update.message.reply_text("❌ Validator not found. Please check the spelling or use the identity address.")
        return

    # Process Stats for the Best Match
    sorted_vals = sorted(validators, key=lambda x: x.get('activatedStake', 0), reverse=True)
    rank = next((i for i, val in enumerate(sorted_vals) if val['identity'] == best_match['identity']), 0) + 1
    balance = best_match.get('voteBalanceLamports', 0) / LAMPORTS

    text = (
        f"🛰 <b>Validator Snapshot</b>\n"
        f"<b>Name:</b> {best_match.get('name', 'Unnamed Node')}\n"
        f"<b>ID:</b> <code>{best_match['identity']}</code>\n"
        f"----------------------------------\n"
        f"🏆 <b>Rank:</b> #{rank} / {len(sorted_vals)}\n"
        f"🚦 <b>Status:</b> {'🟢 Active' if best_match['status'] == 'Active' else '🔴 Delinquent'}\n"
        f"💰 <b>Stake:</b> {format_xnt(best_match.get('activatedStake', 0))} XNT\n"
        f"🏦 <b>Vote Balance:</b> {balance:,.2f} XNT\n"
        f"⚖️ <b>Comm:</b> {best_match.get('commission', '?')}%\n"
        f"📊 <b>Skips (Epoch):</b> {best_match.get('skipped_slots_1_epochs', 0)}\n"
        f"🎁 <b>Recent Rewards:</b> +{best_match.get('rewards_last_1_epochs_xnt', 0):.2f} XNT"
        + FOOTER
    )
    
    # If this was called via a button, we edit the original message. If not, we send a new one.
    if update.callback_query:
        await update.callback_query.message.edit_text(text, parse_mode='HTML', disable_web_page_preview=True)
    else:
        await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes button clicks."""
    query = update.callback_query
    await query.answer() # Removes the loading icon on the button
    
    if query.data.startswith("stats:"):
        validator_name = query.data.split(":", 1)[1]
        context.args = [validator_name]
        await stats_cmd(update, context)

async def calc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❓ Usage: /calc [amount] [name]", parse_mode='HTML')
        return
    try: amount = float(context.args[0].replace(',', ''))
    except: await update.message.reply_text("❌ Invalid amount."); return
    
    query = " ".join(context.args[1:])
    data = load_data()
    best_match, _ = find_validator_smart(query, data.get('validators', []))
    
    if not best_match:
        await update.message.reply_text("❌ Validator not found. Please use the exact name.")
        return

    comm = best_match.get('commission', 10)
    est_apr = 0.07 * (1 - (comm/100))
    text = (f"💰 <b>ROI Estimate: {best_match.get('name', 'Node')}</b>\n"
            f"Principle: {amount:,.0f} XNT\n"
            f"----------------------------------\n"
            f"💎 <b>Per Epoch:</b> ~{(amount * est_apr / 182):.4f} XNT\n"
            f"📈 <b>Net APY:</b> {(est_apr * 100):.2f}%" + FOOTER)
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage: /set_limit <number>")
        return
    try:
        limit = int(context.args[0])
        conn = sqlite3.connect(DB_FILE)
        conn.execute("UPDATE subscriptions SET skip_limit = ? WHERE user_id = ?", (limit, user_id))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Skip alert threshold set to <b>{limit} blocks</b>.", parse_mode='HTML')
    except: await update.message.reply_text("❌ Invalid number.")

async def all_nodes_rewards_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        await update.message.reply_text("⚠️ Use this command in private DM.", parse_mode='HTML')
        return
    data = load_data()
    active_nodes = [v for v in data.get("validators", []) if v.get("status") == "Active"]
    active_nodes.sort(key=lambda x: x.get("activatedStake", 0), reverse=True)
    header = "🛰 <b>Last Epoch Rewards</b>\n<code> # |  Rew  | Name           | ID    </code>\n<code>---|-------|----------------|-------</code>\n"
    rows = [f"<code>{str(i+1).ljust(2)} | {str(round(v.get('rewards_last_1_epochs_xnt',0),1)).rjust(5)} | {v.get('name','?').ljust(14)[:14]} | {v['identity'][:4]}..{v['identity'][-2:]}</code>" for i,v in enumerate(active_nodes)]
    for i in range(0, len(rows), 30):
        await update.message.reply_text((header if i==0 else "") + "\n".join(rows[i:i+30]) + (FOOTER if i+30>=len(rows) else ""), parse_mode='HTML')

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    sorted_vals = sorted(data.get('validators', []), key=lambda x: x.get('activatedStake', 0), reverse=True)[:10]
    text = "🏆 <b>Stake Leaderboard</b>\n\n"
    for i, v in enumerate(sorted_vals):
        text += f"{i+1}. <b>{v.get('name') or v['identity'][:8]}</b> - {format_xnt(v.get('activatedStake', 0))} XNT\n"
    await update.message.reply_text(text + FOOTER, parse_mode='HTML', disable_web_page_preview=True)

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args: return
    identity = context.args[0]
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR IGNORE INTO subscriptions (user_id, identity, last_state) VALUES (?, ?, ?)", (user_id, identity, "{}"))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Alerts active for <code>{identity[:8]}...</code>", parse_mode='HTML')

async def list_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    conn = sqlite3.connect(DB_FILE)
    subs = conn.execute("SELECT identity FROM subscriptions WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    if not subs: await update.message.reply_text("No active subscriptions."); return
    await update.message.reply_text("<b>Your Subscriptions:</b>\n" + "\n".join([f"• <code>{s[0]}</code>" for s in subs]), parse_mode='HTML')

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args: return
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM subscriptions WHERE user_id = ? AND identity = ?", (user_id, context.args[0]))
    conn.commit()
    conn.close()
    await update.message.reply_text("❌ Unsubscribed.", parse_mode='HTML')

# --- BACKGROUND ENGINE ---
async def check_data_job(context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data: return
    validators = {v['identity']: v for v in data.get('validators', [])}
    curr_epoch = 0
    if validators:
        sample = next(iter(validators.values()))
        if sample.get('epochCreditsFull'): curr_epoch = sample['epochCreditsFull'][-1][0]

    # Whale Alerts
    prev_stakes = json.loads(get_net_state("stake_map", "{}"))
    curr_stakes = {idn: v.get('activatedStake', 0) for idn, v in validators.items()}
    for idn, stake in curr_stakes.items():
        if idn in prev_stakes:
            diff = (stake - prev_stakes[idn]) / LAMPORTS
            if abs(diff) >= WHALE_THRESHOLD:
                name = validators[idn].get('name') or f"<code>{idn[:8]}</code>"
                alert = f"{'🐋' if diff > 0 else '📉'} <b>WHALE:</b> {abs(diff):,.0f} XNT {'to' if diff > 0 else 'from'} <b>{name}</b>"
                await context.bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=alert + FOOTER, parse_mode='HTML')
    set_net_state("stake_map", json.dumps(curr_stakes))

    # User Pings
    conn = sqlite3.connect(DB_FILE)
    subscriptions = conn.execute("SELECT user_id, identity, last_state, skip_limit FROM subscriptions").fetchall()
    for user_id, identity, last_state_json, skip_limit in subscriptions:
        if identity not in validators: continue
        curr, prev = validators[identity], json.loads(last_state_json)
        pings = []
        if prev.get('status') and curr['status'] != prev['status']: pings.append(f"🚦 Status: {curr['status']}")
        
        last_notified_skip = prev.get('notified_skip', 0)
        if curr_epoch > prev.get('epoch', 0): last_notified_skip = 0
        if curr.get('skipped_slots_1_epochs',0) >= skip_limit and curr.get('skipped_slots_1_epochs',0) > last_notified_skip:
            pings.append(f"⚠️ High Skips: {curr['skipped_slots_1_epochs']} blocks")
            last_notified_skip = curr['skipped_slots_1_epochs']

        if pings:
            try: await context.bot.send_message(chat_id=user_id, text=f"🛰 <b>Alert: {curr.get('name', identity[:8])}</b>\n" + "\n".join(pings) + FOOTER, parse_mode='HTML')
            except: pass
        new_state = json.dumps({"status": curr['status'], "comm": curr['commission'], "notified_skip": last_notified_skip, "epoch": curr_epoch})
        conn.execute("UPDATE subscriptions SET last_state = ? WHERE user_id = ? AND identity = ?", (new_state, user_id, identity))
    conn.commit(); conn.close()

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("calc", calc_cmd))
    app.add_handler(CommandHandler("set_limit", set_limit))
    app.add_handler(CommandHandler("all_nodes_rewards", all_nodes_rewards_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("list", list_subs))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    
    # THE BUTTON HANDLER
    app.add_handler(CallbackQueryHandler(button_handler))
    
    app.job_queue.run_repeating(check_data_job, interval=180, first=10)
    app.run_polling()
