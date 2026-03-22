from flask import Flask, request, jsonify, send_from_directory
import anthropic
import requests
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import ANTHROPIC_API_KEY, BITRIX_WEBHOOK, ASSISTANTS

app = Flask(__name__, static_folder="webapp")

user_data = {}

def ask_claude(system_prompt, messages):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=system_prompt,
        messages=messages
    )
    return response.content[0].text

def create_lead(name, phone, topic, dialog):
    url = BITRIX_WEBHOOK + "crm.lead.add.json"
    data = {"fields": {"TITLE": f"[WebApp] {topic} — {name}", "NAME": name, "PHONE": [{"VALUE": phone, "VALUE_TYPE": "WORK"}], "COMMENTS": dialog, "SOURCE_ID": "WEB"}}
    try:
        r = requests.post(url, json=data)
        return r.json().get("result")
    except:
        return None

@app.route("/")
def index():
    return send_from_directory("webapp", "index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_id = str(data.get("user_id", "0"))
    assistant_key = data.get("assistant", "ai")
    messages = data.get("messages", [])
    user_name = data.get("user_name", "Гость")

    if user_id not in user_data:
        user_data[user_id] = {"name": None, "phone": None, "step": "name"}

    session = user_data[user_id]
    last_msg = messages[-1]["content"] if messages else ""

    if not session["name"]:
        session["name"] = last_msg
        return jsonify({"reply": f"Приятно познакомиться, {last_msg}! Укажите ваш номер телефона:"})

    if not session["phone"]:
        session["phone"] = last_msg
        assistant = ASSISTANTS[assistant_key]
        prompt = assistant["prompt"]
        reply = ask_claude(prompt, [{"role": "user", "content": f"Меня зовут {session['name']}, телефон {session['phone']}"}])
        return jsonify({"reply": reply})

    assistant = ASSISTANTS[assistant_key]
    prompt = assistant["prompt"] + "\n\nЕсли собрал имя, телефон, компанию, задачу и бюджет — напиши ЛИД_ГОТОВ в конце."
    reply = ask_claude(prompt, messages)

    if "ЛИД_ГОТОВ" in reply:
        reply = reply.replace("ЛИД_ГОТОВ", "").strip()
        dialog = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        create_lead(session["name"], session["phone"], assistant["name"], dialog)
        del user_data[user_id]
        reply += "\n\n✅ Заявка принята! Менеджер свяжется с вами."

    return jsonify({"reply": reply})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
