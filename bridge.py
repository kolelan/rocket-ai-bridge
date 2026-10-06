import requests
import urllib3
import time
import json
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from settings import require, optional, pick

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
# Сюда подставляется имя модели. Это не секрет.
# Берётся DEEPSEEK_MODEL из .env, иначе deepseek-v4-pro.
DEEPSEEK_MODEL = optional("DEEPSEEK_MODEL", "deepseek-v4-pro")

POLL_INTERVAL   = 2.0   # как часто проверять dp_in (сек)
UPDATE_INTERVAL = 0.7   # как часто обновлять сообщение в dp_out (сек)
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


def rc_get_history(room_id, count=20, oldest=None):
    """Пробуем channels.history, потом groups.history, потом im.history."""
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


def call_deepseek_stream(prompt):
    """Генератор: выдаёт текстовые куски ответа от DeepSeek."""
    data = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
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


def process_question(question, sender):
    print(f"[{datetime.now():%H:%M:%S}] Вопрос от {sender}: {question[:80]!r}")

    # Создаём сообщение-заглушку в dp_out
    placeholder = f"**{sender}** спросил:\n> {question[:500]}\n\n⏳ Думаю..."
    resp = rc_send_message(DP_OUT_ID, placeholder)
    if not resp.get("success"):
        print(f"[RC] Не удалось создать сообщение: {resp}")
        return
    msg_id = resp["message"]["_id"]

    # Стримим ответ и обновляем сообщение
    full_answer = ""
    last_update = 0.0
    for chunk in call_deepseek_stream(question):
        full_answer += chunk
        now = time.time()
        if now - last_update > UPDATE_INTERVAL:
            rc_update_message(DP_OUT_ID, msg_id, full_answer)
            last_update = now

    # Финальное обновление
    if full_answer:
        rc_update_message(DP_OUT_ID, msg_id, full_answer)
    else:
        rc_update_message(DP_OUT_ID, msg_id, "[Пустой ответ от DeepSeek]")
    print(f"[{datetime.now():%H:%M:%S}] Ответ отправлен ({len(full_answer)} симв.)")


def main():
    print(f"Мост запущен. Слушаем dp_in={DP_IN_ID}, пишем в dp_out={DP_OUT_ID}")
    print(f"Модель: {DEEPSEEK_MODEL}. Ctrl+C для остановки.\n")

    # На старте берём метку времени последнего сообщения, чтобы не обрабатывать старое
    history = rc_get_history(DP_IN_ID, count=1)
    last_ts = history[0]["ts"] if history else None
    if last_ts:
        print(f"Начинаем отслеживание с ts={last_ts}")
    else:
        print("Канал dp_in пуст, ждём сообщений...")

    while True:
        try:
            history = rc_get_history(DP_IN_ID, count=20, oldest=last_ts)
            # Обрабатываем от старых к новым
            for msg in reversed(history):
                ts = msg["ts"]
                if last_ts and ts <= last_ts:
                    continue
                last_ts = ts

                text = (msg.get("msg") or "").strip()
                if not text:
                    continue

                sender = msg.get("u", {}).get("username", "unknown")
                process_question(text, sender)
        except KeyboardInterrupt:
            print("\nОстановлено по Ctrl+C")
            return
        except Exception as e:
            print(f"[main] Ошибка в цикле: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
