import logging
import requests
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from config import TELEGRAM_TOKEN, BITRIX_WEBHOOK, MANAGER_CHAT_ID, ASSISTANTS, ANTHROPIC_API_KEY

logging.basicConfig(level=logging.INFO)
user_sessions = {}

def ask_claude(system_prompt, messages):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=system_prompt,
        messages=messages
    )
    return response.content[0].text

def create_lead_in_bitrix(name, phone, topic, dialog):
    url = BITRIX_WEBHOOK + "crm.lead.add.json"
    data = {"fields": {"TITLE": f"[Telegram] {topic} — {name}", "NAME": name, "PHONE": [{"VALUE": phone, "VALUE_TYPE": "WORK"}], "COMMENTS": dialog, "SOURCE_ID": "WEB"}}
    try:
        r = requests.post(url, json=data)
        return r.json().get("result")
    except:
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(f"{a['emoji']} {a['name']}", callback_data=f"assistant_{k}")] for k, a in ASSISTANTS.items()]
    await update.message.reply_text("👋 Добро пожаловать!\n\nЯ AI-ассистент компании по внедрению технологий.\nВыберите направление:", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_assistant_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.replace("assistant_", "")
    assistant = ASSISTANTS.get(key)
    if not assistant:
        return
    user_sessions[query.from_user.id] = {"assistant": key, "messages": [], "step": "name", "name": None, "phone": None}
    await query.edit_message_text(f"{assistant['emoji']} Вы выбрали: *{assistant['name']}*\n\nМеня зовут Алекс. Как вас зовут?", parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    if user_id not in user_sessions:
        await start(update, context)
        return
    session = user_sessions[user_id]
    if session["step"] == "name":
        session["name"] = text
        session["step"] = "phone"
        await update.message.reply_text(f"Приятно познакомиться, {text}! 👋\n\nУкажите ваш номер телефона:")
        return
    if session["step"] == "phone":
        session["phone"] = text
        session["step"] = "dialog"
        session["messages"].append({"role": "user", "content": f"Привет, меня зовут {session['name']}, мой телефон {session['phone']}"})
        reply = ask_claude(ASSISTANTS[session["assistant"]]["prompt"], session["messages"])
        session["messages"].append({"role": "assistant", "content": reply})
        await update.message.reply_text(reply)
        return
    session["messages"].append({"role": "user", "content": text})
    prompt = ASSISTANTS[session["assistant"]]["prompt"] + "\n\nЕсли собрал достаточно информации (имя, телефон, компания, задача, бюджет) — напиши ЛИД_ГОТОВ в конце. Иначе задавай вопросы по одному."
    reply = ask_claude(prompt, session["messages"])
    session["messages"].append({"role": "assistant", "content": reply})
    if "ЛИД_ГОТОВ" in reply:
        reply = reply.replace("ЛИД_ГОТОВ", "").strip()
        dialog = "\n".join([f"{m['role']}: {m['content']}" for m in session["messages"]])
        lead_id = create_lead_in_bitrix(session["name"], session["phone"], ASSISTANTS[session["assistant"]]["name"], dialog)
        await update.message.reply_text(reply + "\n\n✅ Заявка принята! Менеджер свяжется с вами. 😊")
        del user_sessions[user_id]
    else:
        await update.message.reply_text(reply)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_assistant_choice, pattern="^assistant_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
