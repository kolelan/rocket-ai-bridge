import requests
import urllib3

# Отключаем предупреждения о самоподписанных сертификатах (частая ситуация с корпоративными серверами)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from settings import require

# --- Проверка доступа к Rocket.Chat ---
# Сюда подставляется базовый URL сервера Rocket.Chat, без слэша на конце.
# Значение берётся из ROCKET_URL в файле .env.
ROCKET_URL = require("ROCKET_URL", "базовый URL Rocket.Chat без слэша на конце, например https://chat.example.com")

print(f"Проверяем Rocket.Chat: {ROCKET_URL}")
try:
    r = requests.get(ROCKET_URL, timeout=10, verify=False)
    print(f"[Rocket.Chat] HTTP {r.status_code} — сервер доступен")
except Exception as e:
    print(f"[Rocket.Chat] Ошибка: {e}")

# --- Проверка доступа к DeepSeek ---
# Сюда подставляется секретный ключ API DeepSeek.
# Это заголовок Authorization: Bearer. Берётся из DEEPSEEK_API_KEY в .env.
DEEPSEEK_API_KEY = require("DEEPSEEK_API_KEY", "секретный ключ API DeepSeek со страницы platform.deepseek.com/api_keys")

print("\nПроверяем DeepSeek API...")
try:
    r = requests.get(
        "https://api.deepseek.com/models",
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
        timeout=10
    )
    if r.status_code == 200:
        print(f"[DeepSeek] HTTP 200 — API доступен, ключ работает")
        print(f"[DeepSeek] Доступные модели: {r.json()}")
    else:
        print(f"[DeepSeek] HTTP {r.status_code} — {r.text}")
except Exception as e:
    print(f"[DeepSeek] Ошибка: {e}")
