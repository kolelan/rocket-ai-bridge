import requests
import urllib3
import json

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

# Сюда подставляется логин создаваемого пользователя. Берётся из BOT_USERNAME в .env.
BOT_USERNAME = require("BOT_USERNAME", "логин новой учётной записи Rocket.Chat, латиницей")
# Сюда подставляется отображаемое имя бота. Берётся из BOT_NAME в .env.
BOT_NAME = require("BOT_NAME", "имя бота, которое видно в чате")
# Сюда подставляется почта создаваемого пользователя. Берётся из BOT_EMAIL в .env.
BOT_EMAIL = require("BOT_EMAIL", "почта новой учётной записи Rocket.Chat")
# Сюда подставляется пароль создаваемого пользователя, не токен API.
# Берётся из BOT_PASSWORD в .env.
BOT_PASSWORD = require("BOT_PASSWORD", "длинный случайный пароль новой учётной записи Rocket.Chat")

headers = {
    "X-Auth-Token": AUTH_TOKEN,
    "X-User-Id": USER_ID,
    "Content-Type": "application/json",
}

payload = {
    "username": BOT_USERNAME,
    "name": BOT_NAME,
    "email": BOT_EMAIL,
    "password": BOT_PASSWORD,
    "roles": ["user"],
    "verified": True,
}

r = requests.post(
    f"{ROCKET_URL}/api/v1/users.create",
    headers=headers,
    json=payload,
    verify=False,
    timeout=15,
)

print(f"HTTP {r.status_code}")
print(json.dumps(r.json(), ensure_ascii=False, indent=2))
