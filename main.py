import os
import logging
import requests
import anthropic
import threading
from flask import Flask, request, jsonify, send_from_directory, make_response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
BITRIX_WEBHOOK = os.environ.get("BITRIX_WEBHOOK")
MANAGER_CHAT_ID = os.environ.get("MANAGER_CHAT_ID")
PORT = int(os.environ.get("PORT", 8080))

CHANNEL_ID = "@axentra_it"
ADMIN_IDS = [656696027]

DIRECTION_NAMES = {
    "ai": "ИИ и автоматизация",
    "crm": "CRM и ERP системы",
    "security": "Кибербезопасность",
    "iot": "IoT и цифровые двойники",
    "bi": "Аналитика данных и BI",
}

SYSTEM_PROMPTS = {
    "ai": """Ты — Алекс, старший консультант Axentra IT. Специализация: ИИ и автоматизация.
КЛИЕНТ: {name}, {company} ({size} чел.), задача: {goal}. Контакт: {contact}
ПРАВИЛА: макс 120 слов, один вопрос в конце, используй цифры и ROI.
После 3-го ответа предложи встречу: «Предлагаю 30-минутную встречу — разберём вашу задачу и покажу расчёт ROI. Бесплатно. Когда удобно?»
ИНСТРУМЕНТЫ: n8n, Make, Zapier, GPT-4o, Claude, UiPath.
КЕЙСЫ: Металлопром (180 чел.) — договоры 3дня→4часа, −2.1M₽/год; Медклиника — −70% no-show.""",

    "crm": """Ты — Алекс, CRM/ERP консультант Axentra IT.
КЛИЕНТ: {name}, {company} ({size} чел.), задача: {goal}. Контакт: {contact}
ПРАВИЛА: макс 120 слов, называй системы с ценами, после 3-го ответа — встреча.
ПРОДУКТЫ: amoCRM от 60K₽/год, Bitrix24 от 120K₽/год, 1C:ERP от 500K₽.""",

    "security": """Ты — Алекс, эксперт ИБ Axentra IT. Специализация: 152-ФЗ, пентест.
КЛИЕНТ: {name}, {company} ({size} чел.), задача: {goal}. Контакт: {contact}
ПРАВИЛА: макс 120 слов, уточняй про ПД и 152-ФЗ, называй конкретные угрозы.
ПРАЙС: Аудит ИБ от 80K₽, Пентест от 150K₽, SIEM от 300K₽, 152-ФЗ от 120K₽.""",

    "iot": """Ты — Алекс, архитектор IoT-решений Axentra IT.
КЛИЕНТ: {name}, {company} ({size} чел.), задача: {goal}. Контакт: {contact}
ПРАВИЛА: макс 120 слов, уточняй оборудование и SCADA, предлагай пилот.
ПЛАТФОРМЫ: AWS IoT, Azure IoT Hub, ThingsBoard.
КЕЙС: Завод (240 раб.) — 80 датчиков, −40% простоев, ROI 14 мес.""",

    "bi": """Ты — Алекс, Lead Data Analyst Axentra IT.
КЛИЕНТ: {name}, {company} ({size} чел.), задача: {goal}. Контакт: {contact}
ПРАВИЛА: макс 120 слов, спрашивай про источники данных (1C, CRM, Excel).
ПРАЙС: Power BI (5 отчётов) от 80K₽ за 3-4 нед., BI от 300K₽, DWH от 500K₽.
АРГУМЕНТ: «>8 часов/неделю на отчёты? BI окупается за 4-6 месяцев гарантированно.»""",
}

app = Flask(__name__, static_folder="webapp")
tg_sessions = {}
notified_users = set()
pending_posts = {}  # user_id → {"text": ..., "topic": ...}

# ── Промпт для генерации постов ──────────────────────────────────
POST_SYSTEM_PROMPT = """Ты — контент-менеджер IT-компании Axentra IT (Telegram-канал @axentra_it).

Пишешь экспертные посты для руководителей и собственников бизнеса (50-500 сотрудников).

СТИЛЬ:
- Живой, уверенный, без воды и шаблонных фраз
- Начинай с цепляющего факта, цифры или вопроса — не с «Привет» или «Сегодня поговорим»
- Структура: крючок → проблема → решение → призыв
- Длина: 150-250 слов
- Используй 1-2 эмодзи максимум, только по делу
- В конце — чёткий призыв к действию (написать в @Alex_AXENTRA или ссылка на бот)

КОМПАНИЯ:
- Axentra IT — цифровая трансформация: ИИ, CRM/ERP, кибербезопасность, IoT, BI
- Кейсы: Металлопром (договоры 3дня→4часа, -2.1M₽/год), МедКлиника (-70% no-show)
- Бесплатный аудит бизнес-процессов (стоимость 30 000₽) — первым 10 заявкам

ЗАПРЕЩЕНО: корпоративный канцелярит, «в современном мире», «не секрет что», списки из 10 пунктов.

Верни ТОЛЬКО текст поста, без пояснений и кавычек."""


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return response


def calculate_lead_score(lead):
    score = 0
    size_scores = {"200+": 30, "51-200": 25, "16-50": 15, "1-15": 5}
    score += size_scores.get(lead.get("company_size", ""), 0)
    if lead.get("email"): score += 10
    if lead.get("phone"): score += 10
    direction_scores = {"iot": 20, "bi": 18, "crm": 16, "security": 14, "ai": 12}
    score += direction_scores.get(lead.get("direction", ""), 0)
    if lead.get("name") and lead.get("company") and lead.get("goal"):
        score += 10
    if score >= 75:   grade = "A"
    elif score >= 50: grade = "B"
    elif score >= 25: grade = "C"
    else:             grade = "D"
    return score, grade


def ask_claude(system_prompt, messages):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=400,
        system=system_prompt,
        messages=messages[-10:]
    )
    return response.content[0].text


def create_lead(lead, dialog):
    if not BITRIX_WEBHOOK:
        return None
    score, grade = calculate_lead_score(lead)
    title = f"[{grade}{score}] {DIRECTION_NAMES.get(lead.get('direction','ai'))} — {lead.get('name','?')}"
    comments = (
        f"Грейд: {grade} | Score: {score}/100\n"
        f"Компания: {lead.get('company','?')}\nРазмер: {lead.get('company_size','?')}\n"
        f"Задача: {lead.get('goal','?')}\n\nДиалог:\n{dialog}"
    )
    data = {"fields": {
        "TITLE": title,
        "NAME": lead.get("name", ""),
        "PHONE": [{"VALUE": lead.get("phone", ""), "VALUE_TYPE": "WORK"}] if lead.get("phone") else [],
        "EMAIL": [{"VALUE": lead.get("email", ""), "VALUE_TYPE": "WORK"}] if lead.get("email") else [],
        "COMMENTS": comments,
        "SOURCE_ID": "WEB",
    }}
    try:
        r = requests.post(BITRIX_WEBHOOK + "crm.lead.add.json", json=data, timeout=10)
        return r.json().get("result")
    except Exception as e:
        logging.error(f"Bitrix error: {e}")
        return None


def send_tg_notification(lead, last_message, msg_count):
    if not MANAGER_CHAT_ID or not TELEGRAM_TOKEN:
        return
    score, grade = calculate_lead_score(lead)
    grade_emoji = {"A": "🔥", "B": "✅", "C": "🟡", "D": "⚪️"}
    tg_link = f"t.me/{lead['tg_username']}" if lead.get("tg_username") else "—"
    text = (
        f"{grade_emoji.get(grade,'✅')} НОВЫЙ ЛИД — Axentra IT Bot\n\n"
        f"Грейд: {grade} | Score: {score}/100\n\n"
        f"👤 {lead.get('name','?')} — {lead.get('company','?')}\n"
        f"📊 {lead.get('company_size','?')} сотрудников\n"
        f"📍 {DIRECTION_NAMES.get(lead.get('direction',''), '?')}\n"
        f"🎯 {lead.get('goal','?')}\n"
        f"📧 {lead.get('email','—')} | 📱 {lead.get('phone','—')}\n\n"
        f"💬 «{last_message[:200]}»\n\n"
        f"⏱ {msg_count} сообщений\n"
        f"{tg_link}"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": MANAGER_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        logging.error(f"TG notification error: {e}")


@app.route("/")
def index():
    return send_from_directory("webapp", "index.html")


@app.route("/chat", methods=["POST", "OPTIONS"])
def chat():
    if request.method == "OPTIONS":
        return make_response("", 200)

    data = request.json
    user_id = str(data.get("user_id", "0"))
    direction = data.get("assistant", "ai")
    messages = data.get("messages", [])
    lead = data.get("lead", {})
    lead["direction"] = direction

    # Build system prompt with lead context
    sys_prompt = SYSTEM_PROMPTS.get(direction, SYSTEM_PROMPTS["ai"]).format(
        name=lead.get("name", "Клиент"),
        company=lead.get("company", "компания"),
        size=lead.get("company_size", "неизвестно"),
        goal=lead.get("goal", "цифровизация"),
        contact=lead.get("phone") or lead.get("email") or "не указан",
    )

    reply = ask_claude(sys_prompt, messages)
    bot_count = len([m for m in messages if m.get("role") == "assistant"]) + 1
    show_cta = bot_count >= 3

    # Send notification when contact provided (once per user)
    if (lead.get("phone") or lead.get("email")) and user_id not in notified_users:
        notified_users.add(user_id)
        last_msg = messages[-1]["content"] if messages else ""
        dialog = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        create_lead(lead, dialog)
        send_tg_notification(lead, last_msg, len([m for m in messages if m["role"] == "user"]))

    return jsonify({"reply": reply, "show_cta": show_cta})


# ── Telegram bot ─────────────────────────────────────────────────

async def generate_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /пост [тема] — генерирует пост через Claude и показывает превью."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return

    # Получаем тему из аргументов команды
    topic = " ".join(context.args) if context.args else ""
    if not topic:
        await update.message.reply_text(
            "📝 Укажите тему поста после команды.\n\n"
            "Примеры:\n"
            "/пост автоматизация договоров\n"
            "/пост почему CRM не внедряется\n"
            "/пост кибербезопасность для малого бизнеса\n"
            "/пост ROI от внедрения BI аналитики"
        )
        return

    msg = await update.message.reply_text("⏳ Генерирую пост...")

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            system=POST_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Напиши пост на тему: {topic}"}]
        )
        post_text = response.content[0].text.strip()

        # Сохраняем пост для публикации
        pending_posts[user_id] = {"text": post_text, "topic": topic}

        # Показываем превью с кнопками
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Опубликовать", callback_data="post_publish"),
                InlineKeyboardButton("🔄 Перегенерировать", callback_data="post_regen"),
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="post_cancel")],
        ])

        await msg.edit_text(
            f"📋 *Превью поста:*\n\n{post_text}\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 Символов: {len(post_text)} | Тема: _{topic}_",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка генерации: {e}")


async def post_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок Опубликовать / Перегенерировать / Отмена."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id not in ADMIN_IDS:
        return

    action = query.data  # post_publish / post_regen / post_cancel

    if action == "post_cancel":
        pending_posts.pop(user_id, None)
        await query.edit_message_text("❌ Публикация отменена.")
        return

    if action == "post_regen":
        pending = pending_posts.get(user_id)
        if not pending:
            await query.edit_message_text("❌ Пост не найден. Создайте новый через /пост")
            return

        await query.edit_message_text(f"⏳ Генерирую новый вариант на тему: _{pending['topic']}_...", parse_mode="Markdown")

        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=600,
                system=POST_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": f"Напиши пост на тему: {pending['topic']}"},
                    {"role": "assistant", "content": pending["text"]},
                    {"role": "user", "content": "Напиши другой вариант — с другим углом подачи и другим началом."},
                ]
            )
            post_text = response.content[0].text.strip()
            pending_posts[user_id]["text"] = post_text

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Опубликовать", callback_data="post_publish"),
                    InlineKeyboardButton("🔄 Перегенерировать", callback_data="post_regen"),
                ],
                [InlineKeyboardButton("❌ Отмена", callback_data="post_cancel")],
            ])

            await query.edit_message_text(
                f"📋 *Превью поста (новый вариант):*\n\n{post_text}\n\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📊 Символов: {len(post_text)} | Тема: _{pending['topic']}_",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}")
        return

    if action == "post_publish":
        pending = pending_posts.get(user_id)
        if not pending:
            await query.edit_message_text("❌ Пост не найден. Создайте новый через /пост")
            return

        try:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("⚡️ ПОЛУЧИТЬ КОНСУЛЬТАЦИЮ ИИ", url="https://t.me/ITAxentra_bot/app")
            ]])
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=pending["text"],
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            pending_posts.pop(user_id, None)
            await query.edit_message_text(
                f"✅ Пост опубликован в {CHANNEL_ID}!\n\n"
                f"📝 Тема: _{pending['topic']}_",
                parse_mode="Markdown",
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка публикации: {e}")


async def publish_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⚡️ ПОЛУЧИТЬ КОНСУЛЬТАЦИЮ ИИ", url="https://t.me/ITAxentra_bot/app")]])
    try:
        msg = update.message
        if msg.photo:
            await context.bot.send_photo(chat_id=CHANNEL_ID, photo=msg.photo[-1].file_id, caption=msg.caption or "", parse_mode="Markdown", reply_markup=keyboard)
            await msg.reply_text("✅ Пост с фото опубликован!")
        elif msg.video:
            await context.bot.send_video(chat_id=CHANNEL_ID, video=msg.video.file_id, caption=msg.caption or "", parse_mode="Markdown", reply_markup=keyboard)
            await msg.reply_text("✅ Пост с видео опубликован!")
        elif msg.text and not msg.text.startswith("/"):
            await context.bot.send_message(chat_id=CHANNEL_ID, text=msg.text, parse_mode="Markdown", reply_markup=keyboard)
            await msg.reply_text("✅ Пост опубликован!")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS:
        await update.message.reply_text(
            "👋 Привет, Алекс!\n\n"
            "📝 *Публикация постов:*\n"
            "• Отправь текст/фото/видео → опубликую в канал\n"
            "• /post [тема] → AI сгенерирует пост\n\n"
            "Примеры:\n"
            "`/post автоматизация договоров`\n"
            "`/post кибербезопасность для бизнеса`\n"
            "`/post ROI от внедрения CRM`",
            parse_mode="Markdown"
        )
        return
    icons = {'ai':'🤖','crm':'📊','security':'🔒','iot':'🏭','bi':'📈'}
    keyboard = [[InlineKeyboardButton(f"{icons.get(k,'•')} {v}", callback_data=f"dir_{k}")] for k, v in DIRECTION_NAMES.items()]
    await update.message.reply_text("Выберите направление:", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.replace("dir_", "")
    name = DIRECTION_NAMES.get(key, key)
    tg_sessions[query.from_user.id] = {
        "direction": key, "messages": [], "step": "name",
        "lead": {"direction": key, "tg_username": query.from_user.username or ""},
    }
    await query.edit_message_text(
        f"*{name}*\n\nПривет! Я Алекс — AI-консультант Axentra IT.\n\nКак вас зовут и из какой компании?",
        parse_mode="Markdown"
    )


async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if user_id in ADMIN_IDS:
        await publish_post(update, context)
        return

    if user_id not in tg_sessions:
        await start(update, context)
        return

    session = tg_sessions[user_id]
    lead = session["lead"]

    if session["step"] == "name":
        parts = text.split(",", 1)
        lead["name"] = parts[0].strip()
        lead["company"] = parts[1].strip() if len(parts) > 1 else "—"
        session["step"] = "phone"
        await update.message.reply_text(
            f"Приятно познакомиться, {lead['name']}! 👍\n\n📱 Оставьте номер телефона — пришлю персональные рекомендации:"
        )
        return

    if session["step"] == "phone":
        lead["phone"] = text
        session["step"] = "dialog"
        session["messages"].append({"role": "user", "content": f"Имя: {lead['name']}, компания: {lead['company']}, телефон: {text}"})
        sys_prompt = SYSTEM_PROMPTS.get(session["direction"], SYSTEM_PROMPTS["ai"]).format(
            name=lead.get("name", "Клиент"),
            company=lead.get("company", "?"),
            size=lead.get("company_size", "неизвестно"),
            goal=lead.get("goal", "цифровизация"),
            contact=lead.get("phone", "?"),
        )
        reply = ask_claude(sys_prompt, session["messages"])
        session["messages"].append({"role": "assistant", "content": reply})
        # Notify manager
        dialog = "\n".join([f"{m['role']}: {m['content']}" for m in session["messages"]])
        create_lead(lead, dialog)
        send_tg_notification(lead, text, 1)
        await update.message.reply_text(reply)
        return

    session["messages"].append({"role": "user", "content": text})
    sys_prompt = SYSTEM_PROMPTS.get(session["direction"], SYSTEM_PROMPTS["ai"]).format(
        name=lead.get("name", "Клиент"),
        company=lead.get("company", "?"),
        size=lead.get("company_size", "неизвестно"),
        goal=lead.get("goal", "цифровизация"),
        contact=lead.get("phone", "?"),
    )
    reply = ask_claude(sys_prompt, session["messages"])
    session["messages"].append({"role": "assistant", "content": reply})
    await update.message.reply_text(reply)


def run_flask():
    app.run(host="0.0.0.0", port=PORT)


def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"Flask started on port {PORT}")
    tg_app = Application.builder().token(TELEGRAM_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("post", generate_post))
    tg_app.add_handler(CallbackQueryHandler(post_action, pattern="^post_"))
    tg_app.add_handler(CallbackQueryHandler(handle_callback))
    tg_app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, publish_post))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    print("Bot started!")
    tg_app.run_polling()


if __name__ == "__main__":
    main()
