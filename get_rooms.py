import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from settings import require

# Сюда подставляется базовый URL сервера Rocket.Chat, без слэша на конце.
# Значение берётся из ROCKET_URL в файле .env.
ROCKET_URL = require("ROCKET_URL", "базовый URL Rocket.Chat без слэша на конце, например https://chat.example.com")
# Сюда подставляется личный токен Rocket.Chat (Personal Access Token).
# Это заголовок X-Auth-Token. Берётся из ROCKET_AUTH_TOKEN в .env, не из кода.
AUTH_TOKEN = require("ROCKET_AUTH_TOKEN", "личный токен доступа Rocket.Chat: аватар, My Account, Personal Access Tokens. Это заголовок X-Auth-Token")
# Сюда подставляется id пользователя, которому выдан токен.
# Поле _id ответа GET /api/v1/me. Берётся из ROCKET_USER_ID в .env.
USER_ID = require("ROCKET_USER_ID", "id пользователя Rocket.Chat (поле _id в GET /api/v1/me). Это заголовок X-User-Id")

headers = {
    "X-Auth-Token": AUTH_TOKEN,
    "X-User-Id": USER_ID,
}

r = requests.get(f"{ROCKET_URL}/api/v1/rooms.get", headers=headers, verify=False)

if r.status_code == 200:
    rooms = r.json().get("update", [])
    print(f"Найдено каналов: {len(rooms)}\n")
    for room in rooms:
        print(f"ID: {room['_id']}  |  Имя: {room.get('name', '(личный)')}")
else:
    print(f"Ошибка: HTTP {r.status_code} — {r.text}")
