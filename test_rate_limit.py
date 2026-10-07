import requests, urllib3, time
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

headers = {"X-Auth-Token": AUTH_TOKEN, "X-User-Id": USER_ID}

print("GET /api/v1/me — endpoint без параметров, 30 попыток с интервалом 2 сек")
print("Если лимит 500/мин — увидим все 30 OK.\n")

ok = 0
for i in range(1, 31):
    r = requests.get(f"{ROCKET_URL}/api/v1/me", headers=headers,
                     verify=False, timeout=10)
    if r.status_code == 200:
        ok += 1
        print(f"#{i:2d} 200 OK (всего успешных: {ok})")
    elif r.status_code == 429:
        print(f"#{i:2d} 429 ← ЛИМИТ. Успешных до бана: {ok}")
        break
    else:
        print(f"#{i:2d} HTTP {r.status_code}: {r.text[:100]}")
    time.sleep(2)  # 30 запросов за 60 секунд = ровно 30 rpm

print("Готово.")
