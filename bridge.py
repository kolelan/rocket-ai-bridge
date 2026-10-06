import requests
import urllib3
import time
import json
from datetime import datetime
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from settings import require, pick, work_dir

# ================== НАСТРОЙКИ ==================
# Сюда подставляется базовый URL сервера Rocket.Chat, без слэша на конце.
# Значение берётся из ROCKET_URL в файле .env.
ROCKET_URL = require("ROCKET_URL", "базовый URL Rocket.Chat без слэша на конце, например https://chat.example.com")
# Сюда подставляется личный токен Rocket.Chat (Personal Access Token).
# Это заголовок X-Auth-Token. Берётся из ROCKET_AUTH_TOKEN в .env, не из кода.
AUTH_TOKEN = require("ROCKET_AUTH_TOKEN", "личный токен доступа Rocket.Chat: аватар, My Account, Personal Access Tokens. Это заголовок X-Auth-Token")
# Сюда подставляется id пользователя, которому выдан токен.
# Поле _id ответа GET /api/v1/me. Берётся из ROCKET_USER_ID в .env.
USER_ID = require("ROCKET_USER_ID", "id пользователя Rocket.Chat (поле _id в GET /api/v1/me). Это заголовок X-User-Id")

# Сюда подставляется id комнаты вопросов (v1 и v2, канал dp_in).
# Берётся DP_IN_ID из .env, а если его нет — DP_CTRL_ID.
DP_IN_ID = pick("DP_IN_ID", "DP_CTRL_ID", hint="id комнаты, куда в v1 и v2 пишут вопросы. Id печатает python get_rooms.py")
# Сюда подставляется id комнаты ответов (v1 и v2, канал dp_out).
# Берётся DP_OUT_ID из .env, а если его нет — DP_CHAT_ID.
DP_OUT_ID = pick("DP_OUT_ID", "DP_CHAT_ID", hint="id комнаты, куда в v1 и v2 бот пишет ответы. Id печатает python get_rooms.py")

# Сюда подставляется секретный ключ API DeepSeek.
# Это заголовок Authorization: Bearer. Берётся из DEEPSEEK_API_KEY в .env.
DEEPSEEK_API_KEY = require("DEEPSEEK_API_KEY", "секретный ключ API DeepSeek со страницы platform.deepseek.com/api_keys")
DEFAULT_MODEL    = "deepseek-v4-pro"

# Сюда подставляется каталог чатов, архива и логов.
# Берётся WORK_DIR из .env. Если переменная пустая — папка с программой.
WORK_DIR = work_dir()
CHATS_DIR  = WORK_DIR / "chats"
STATE_FILE = WORK_DIR / "state.json"

POLL_INTERVAL   = 2.0
UPDATE_INTERVAL = 0.7
# ================================================

rc_headers = {
    "X-Auth-Token": AUTH_TOKEN,
    "X-User-Id": USER_ID,
    "Content-Type": "application/json",
}
ds_headers = {
    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    "Content-Type": "application/json",
}

HELP_TEXT = """📖 **Команды моста DeepSeek**

`!new <имя>` — создать новый чат (имя латиницей/цифрами/дефисами).
   Пример: `!new ssh-setup` → файл `2026-10-06-ssh-setup`
`!list` — список активных чатов (из папки `chats\\`).
`!use <chat_id>` — переключиться на существующий чат.
`!current` — показать активный чат и модель.
`!model flash` — `deepseek-flash` (быстрая, поддерживает изображения).
`!model pro` — `deepseek-v4-pro` (умная, только текст).
`!help` — эта справка.

**Рабочий процесс:**
1. `!new mytask` — создаём контекст.
2. Пишете вопросы обычным текстом — DeepSeek помнит их все.
3. Хотите другую тему — `!new other-task`.
4. Вернуться — `!use 2026-10-06-mytask`.

**Архивация:** перенесите ненужный файл из `chats\\` в `arch2026-XX\\` —
он исчезнет из `!list`, но останется на диске.

**Если писать без `!new`** сообщение идёт в последний активный чат.
Если активного нет — автоматически создастся `YYYY-MM-DD-default`."""


# ---------- Файловое хранилище ----------

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"active_chat": None, "model": DEFAULT_MODEL}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_chats():
    if not CHATS_DIR.exists():
        return []
    return sorted(p.stem for p in CHATS_DIR.glob("*.json"))


def chat_path(chat_id):
    return CHATS_DIR / f"{chat_id}.json"


def load_chat(chat_id):
    p = chat_path(chat_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_chat(chat_id, chat):
    CHATS_DIR.mkdir(parents=True, exist_ok=True)
    chat_path(chat_id).write_text(
        json.dumps(chat, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def create_chat(name):
    date_prefix = datetime.now().strftime("%Y-%m-%d")
    safe = "".join(
        c if (c.isalnum() or c in "-_") else "-" for c in name
    ).strip("-") or "chat"
    chat_id = f"{date_prefix}-{safe}"
    base = chat_id
    counter = 1
    while chat_path(chat_id).exists():
        counter += 1
        chat_id = f"{base}-{counter}"
    chat = {
        "id": chat_id,
        "created": datetime.now().isoformat(timespec="seconds"),
        "messages": [],
    }
    save_chat(chat_id, chat)
    return chat_id


# ---------- Rocket.Chat ----------

def rc_get_history(room_id, count=20, oldest=None):
    params = {"roomId": room_id, "count": count}
    if oldest:
        params["oldest"] = oldest
    for method in ("channels.history", "groups.history", "im.history"):
        try:
            r = requests.get(
                f"{ROCKET_URL}/api/v1/{method}",
                headers=rc_headers, params=params,
                verify=False, timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("success"):
                    return data.get("messages", [])
        except Exception as e:
            print(f"[RC] {method} exception: {e}")
    return []


def rc_send_message(room_id, text):
    r = requests.post(
        f"{ROCKET_URL}/api/v1/chat.postMessage",
        headers=rc_headers, json={"roomId": room_id, "text": text},
        verify=False, timeout=15,
    )
    return r.json()


def rc_update_message(room_id, msg_id, text):
    try:
        r = requests.post(
            f"{ROCKET_URL}/api/v1/chat.update",
            headers=rc_headers,
            json={"roomId": room_id, "msgId": msg_id, "text": text},
            verify=False, timeout=15,
        )
        return r.json()
    except Exception as e:
        print(f"[RC] update exception: {e}")
        return {}


# ---------- DeepSeek ----------

def call_deepseek_stream(messages, model):
    data = {"model": model, "messages": messages, "stream": True}
    try:
        with requests.post(
            "https://api.deepseek.com/chat/completions",
            headers=ds_headers, json=data, stream=True, timeout=600,
        ) as r:
            if r.status_code != 200:
                yield f"[DeepSeek error {r.status_code}] {r.text}"
                return
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                line = line.strip()
                if line.startswith("data: "):
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                        delta = chunk["choices"][0].get("delta", {}).get("content", "")
                        if delta:
                            yield delta
                    except Exception:
                        continue
    except Exception as e:
        yield f"[DeepSeek exception] {e}"


# ---------- Обработка команд ----------

def handle_command(text, state):
    parts = text[1:].split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "help":
        return HELP_TEXT

    if cmd == "new":
        if not arg:
            return "❌ Использование: `!new <имя>`, например `!new ssh-setup`"
        chat_id = create_chat(arg)
        state["active_chat"] = chat_id
        save_state(state)
        return f"✅ Создан новый чат: **{chat_id}**\nВсе следующие вопросы пойдут в него."

    if cmd == "list":
        chats = list_chats()
        if not chats:
            return "📭 Список чатов пуст. Создайте первый: `!new <имя>`"
        lines = ["📂 **Доступные чаты:**"]
        for c in chats:
            marker = "▶️ " if c == state.get("active_chat") else "   "
            lines.append(f"{marker}`{c}`")
        lines.append(f"\nАктивный: `{state.get('active_chat') or '—'}`")
        return "\n".join(lines)

    if cmd == "use":
        if not arg:
            return "❌ Использование: `!use <chat_id>`"
        chat = load_chat(arg)
        if not chat:
            return f"❌ Чат `{arg}` не найден. Посмотрите `!list`."
        state["active_chat"] = arg
        save_state(state)
        n = len(chat.get("messages", []))
        return f"✅ Переключились на **{arg}** ({n} сообщений в истории)."

    if cmd == "current":
        chat_id = state.get("active_chat")
        model = state.get("model", DEFAULT_MODEL)
        if not chat_id:
            return "ℹ️ Активный чат не выбран. Используйте `!new <имя>` или `!use <chat_id>`."
        chat = load_chat(chat_id) or {"messages": []}
        return (
            f"📌 Активный чат: **{chat_id}**\n"
            f"🤖 Модель: **{model}**\n"
            f"💬 Сообщений в истории: **{len(chat.get('messages', []))}**"
        )

    if cmd == "model":
        a = arg.lower()
        if a == "flash":
            state["model"] = "deepseek-flash"
            save_state(state)
            return "✅ Модель: **deepseek-flash** (быстрая, умеет изображения)."
        if a == "pro":
            state["model"] = "deepseek-v4-pro"
            save_state(state)
            return "✅ Модель: **deepseek-v4-pro** (умная, только текст)."
        return f"❌ Неизвестная модель: `{arg}`. Доступно: `flash`, `pro`."

    return f"❌ Неизвестная команда: `!{cmd}`. Введите `!help`."


# ---------- Обработка вопроса ----------

def build_answer_message(sender, chat_id, model, question, answer, thinking=False):
    preview = question[:500] + ("..." if len(question) > 500 else "")
    preview = preview.replace("\n", "\n> ")
    suffix = " ▌" if thinking else ""
    return (
        f"**❓ {sender}** · чат `{chat_id}` · модель `{model}`\n\n"
        f"> {preview}\n\n"
        f"**🤖 Ответ:**\n\n{answer}{suffix}"
    )


def process_question(text, sender, state):
    chat_id = state.get("active_chat")
    if not chat_id:
        chat_id = create_chat("default")
        state["active_chat"] = chat_id
        save_state(state)
        print(f"[auto] Создан чат по умолчанию: {chat_id}")

    chat = load_chat(chat_id)
    if chat is None:
        chat = {"id": chat_id, "created": datetime.now().isoformat(timespec="seconds"), "messages": []}

    model = state.get("model", DEFAULT_MODEL)
    api_messages = list(chat.get("messages", [])) + [{"role": "user", "content": text}]

    resp = rc_send_message(
        DP_OUT_ID,
        build_answer_message(sender, chat_id, model, text, "⏳ Думаю...", thinking=True),
    )
    if not resp.get("success"):
        print(f"[RC] Не удалось отправить заглушку: {resp}")
        return
    msg_id = resp["message"]["_id"]

    full_answer = ""
    last_update = 0.0
    for chunk in call_deepseek_stream(api_messages, model):
        full_answer += chunk
        now = time.time()
        if now - last_update > UPDATE_INTERVAL:
            rc_update_message(
                DP_OUT_ID, msg_id,
                build_answer_message(sender, chat_id, model, text, full_answer, thinking=True),
            )
            last_update = now

    if not full_answer:
        full_answer = "[Пустой ответ от DeepSeek]"

    rc_update_message(
        DP_OUT_ID, msg_id,
        build_answer_message(sender, chat_id, model, text, full_answer),
    )

    chat["messages"].append({"role": "user", "content": text, "ts": datetime.now().isoformat(timespec="seconds")})
    chat["messages"].append({"role": "assistant", "content": full_answer, "ts": datetime.now().isoformat(timespec="seconds")})
    save_chat(chat_id, chat)

    print(f"[{datetime.now():%H:%M:%S}] [{chat_id}] {len(full_answer)} симв.")


# ---------- Главный цикл ----------

def main():
    CHATS_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()

    print(f"Мост v2 запущен.")
    print(f"  Активный чат: {state.get('active_chat') or '—'}")
    print(f"  Модель:       {state.get('model', DEFAULT_MODEL)}")
    print(f"  Каталог чатов: {CHATS_DIR}")
    print(f"  Ctrl+C для остановки.\n")

    history = rc_get_history(DP_IN_ID, count=1)
    last_ts = history[0]["ts"] if history else None
    if last_ts:
        print(f"Следим с ts={last_ts}\n")
    else:
        print("Канал dp_in пуст, ждём сообщений...\n")

    while True:
        try:
            history = rc_get_history(DP_IN_ID, count=20, oldest=last_ts)
            for msg in reversed(history):
                ts = msg["ts"]
                if last_ts and ts <= last_ts:
                    continue
                last_ts = ts

                text = (msg.get("msg") or "").strip()
                if not text:
                    continue

                sender = msg.get("u", {}).get("username", "unknown")

                if msg.get("files") or msg.get("attachments"):
                    rc_send_message(
                        DP_OUT_ID,
                        f"⚠️ **{sender}**, вложения пока не поддерживаются в v2. "
                        f"Будут в v2.1 (только для `!model flash`).",
                    )
                    continue

                if text.startswith("!"):
                    print(f"[{datetime.now():%H:%M:%S}] КОМАНДА от {sender}: {text}")
                    reply = handle_command(text, state)
                    rc_send_message(DP_OUT_ID, reply)
                    continue

                process_question(text, sender, state)
        except KeyboardInterrupt:
            print("\nОстановлено по Ctrl+C")
            return
        except Exception as e:
            print(f"[main] Ошибка в цикле: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
