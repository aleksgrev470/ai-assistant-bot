import os
import logging
import requests
import anthropic
from flask import Flask, request, jsonify, send_from_directory

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
BITRIX_WEBHOOK = os.environ.get("BITRIX_WEBHOOK")
MANAGER_CHAT_ID = os.environ.get("MANAGER_CHAT_ID")
PORT = int(os.environ.get("PORT", 8080))

ASSISTANTS = {
    "ai": {"name": "ИИ и автоматизация", "emoji": "🤖", "prompt": "Ты эксперт по внедрению ИИ и автоматизации бизнеса. Квалифицируй лида, узнай: название компании, какие процессы хотят автоматизировать, текущие боли, бюджет и сроки."},
    "crm": {"name": "CRM и ERP", "emoji": "📊", "prompt": "Ты эксперт по внедрению CRM и ERP систем. Квалифицируй лида, узнай: название компании, сферу бизнеса, сколько сотрудников, какая CRM сейчас используется, какие проблемы хотят решить, бюджет и сроки."},
    "cyber": {"name": "Кибербезопасность", "emoji": "🔒", "prompt": "Ты эксперт по кибербезопасности и 152-ФЗ. Квалифицируй лида, узнай: название компании, сферу, количество сотрудников, какие данные обрабатывают, есть ли уже аудит, бюджет."},
    "iot": {"name": "IoT и цифровые двойники", "emoji": "🏭", "prompt": "Ты эксперт по IoT и цифровым двойникам. Квалифицируй лида, узнай: название компании, отрасль, какое оборудование используют, что хотят мониторить, бюджет."}
}

app = Flask(__name__, static_folder="webapp")
user_data = {}

def ask_claude(system_prompt, messages):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(model="claude-sonnet-4-5", max_tokens=1024, system=system_prompt, messages=messages)
    return response.content[0].text

def create_lead(name, phone, topic, dialog):
    url = BITRIX_WEBHOOK + "crm.lead.add.json"
    data = {"fields": {"TITLE": f"[WebApp] {topic} - {name}", "NAME": name, "PHONE": [{"VALUE": phone, "VALUE_TYPE": "WORK"}], "COMMENTS": dialog, "SOURCE_ID": "WEB"}}
    try:
        r = requests.post(url, json=data)
        return r.json().get("result")
    except:
        return None

@app.route("/")
def index():
    resp = send_from_directory("webapp", "index.html")
    resp.headers["ngrok-skip-browser-warning"] = "true"
    return resp

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_id = str(data.get("user_id", "0"))
    assistant_key = data.get("assistant", "ai")
    messages = data.get("messages", [])

    if user_id not in user_data:
        user_data[user_id] = {"name": None, "phone": None}

    session = user_data[user_id]
    last_msg = messages[-1]["content"] if messages else ""

    if not session["name"]:
        session["name"] = last_msg
        return jsonify({"reply": f"Приятно познакомиться, {last_msg}! Укажите ваш номер телефона:"})

    if not session["phone"]:
        session["phone"] = last_msg
        reply = ask_claude(ASSISTANTS[assistant_key]["prompt"], [{"role": "user", "content": f"Меня зовут {session['name']}, телефон {session['phone']}"}])
        return jsonify({"reply": reply})

    prompt = ASSISTANTS[assistant_key]["prompt"] + "\n\nЕсли собрал имя, телефон, компанию, задачу и бюджет — напиши ЛИД_ГОТОВ в конце."
    reply = ask_claude(prompt, messages)

    if "ЛИД_ГОТОВ" in reply:
        reply = reply.replace("ЛИД_ГОТОВ", "").strip()
        dialog = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        create_lead(session["name"], session["phone"], ASSISTANTS[assistant_key]["name"], dialog)
        del user_data[user_id]
        reply += "\n\n✅ Заявка принята! Менеджер свяжется с вами."

    return jsonify({"reply": reply})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
