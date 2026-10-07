import requests
import urllib3
import time
import json
import shutil
import base64
import mimetypes
import traceback
import re
from datetime import datetime
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from settings import require, pick, work_dir

# ================== ЛОГГЕР ==================
LOG_LEVEL = "INFO"          # DEBUG | INFO | WARN | ERROR
LOG_FILE  = None            # задаётся ниже, после WORK_DIR

_log_fh = None

def _log_write(level, msg):
    global _log_fh
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level:5}] {msg}"
    # В консоль — WARN/ERROR, плюс INFO если явно разрешено
    lvl_order = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
    if lvl_order[level] >= lvl_order.get(LOG_LEVEL, 20):
        print(line)
    if _log_fh:
        try:
            _log_fh.write(line + "\n")
            _log_fh.flush()
        except Exception:
            pass

def log_d(msg): _log_write("DEBUG", msg)
def log_i(msg): _log_write("INFO",  msg)
def log_w(msg): _log_write("WARN",  msg)
def log_e(msg): _log_write("ERROR", msg)

# ================== НАСТРОЙКИ ==================
API_VERSION = "3.5"
BOT_MARK = "\u200b"   # невидимый маркер своих сообщений (zero-width space) то что бот отправил в чат он должен знать что  это его сообщение а не пользователя. Что бы не отправить его на обработку в DeepSeek API
# Сюда подставляется базовый URL сервера Rocket.Chat, без слэша на конце.
# Значение берётся из ROCKET_URL в файле .env.
ROCKET_URL = require("ROCKET_URL", "базовый URL Rocket.Chat без слэша на конце, например https://chat.example.com")
# Сюда подставляется личный токен Rocket.Chat (Personal Access Token).
# Это заголовок X-Auth-Token. Берётся из ROCKET_AUTH_TOKEN в .env, не из кода.
AUTH_TOKEN = require("ROCKET_AUTH_TOKEN", "личный токен доступа Rocket.Chat: аватар, My Account, Personal Access Tokens. Это заголовок X-Auth-Token")
# Сюда подставляется id пользователя, которому выдан токен.
# Поле _id ответа GET /api/v1/me. Берётся из ROCKET_USER_ID в .env.
USER_ID = require("ROCKET_USER_ID", "id пользователя Rocket.Chat (поле _id в GET /api/v1/me). Это заголовок X-User-Id")

# Канал управления (команды). История не хранится.
# Сюда подставляется id комнаты команд (!new, !list, !help).
# Берётся DP_CTRL_ID из .env, а если его нет — DP_IN_ID.
DP_CTRL_ID = pick("DP_CTRL_ID", "DP_IN_ID", hint="id комнаты команд. Если отдельного имени нет, укажите тот же id в DP_IN_ID")
# Канал работы (вопросы пользователя + ответы бота)
# Сюда подставляется id комнаты вопросов и ответов.
# Берётся DP_CHAT_ID из .env, а если его нет — DP_OUT_ID.
DP_CHAT_ID = pick("DP_CHAT_ID", "DP_OUT_ID", hint="id комнаты, куда пишут вопросы и куда бот отвечает. Если отдельного имени нет, укажите тот же id в DP_OUT_ID")

# Сюда подставляется секретный ключ API DeepSeek.
# Это заголовок Authorization: Bearer. Берётся из DEEPSEEK_API_KEY в .env.
DEEPSEEK_API_KEY = require("DEEPSEEK_API_KEY", "секретный ключ API DeepSeek со страницы platform.deepseek.com/api_keys")
DEFAULT_MODEL    = "deepseek-v4-pro"

# Сюда подставляется каталог чатов, архива и логов.
# Берётся WORK_DIR из .env. Если переменная пустая — папка с программой.
WORK_DIR = work_dir()
CHATS_DIR  = WORK_DIR / "chats"
ARCH_DIR   = WORK_DIR / "archives"
STATE_FILE = WORK_DIR / "state.json"
# Открываем файл лога (при старте)
WORK_DIR.mkdir(parents=True, exist_ok=True)
try:
    _log_fh = open(WORK_DIR / "bridge.log", "a", encoding="utf-8")
except Exception as e:
    print(f"[logger] не удалось открыть bridge.log: {e}")
# --- Адаптивный опрос ---
POLL_INTERVAL_MIN = 5.0     # когда идёт активность были (3.0, 5.0)
POLL_INTERVAL_MAX = 90.0    # когда в каналах тихо были (10.0, 15.0, 60.0)
POLL_INTERVAL_STEP = 8.0    # шаг увеличения паузы при пустом опросе были (1.5, 5.0)
POLL_INTERVAL = POLL_INTERVAL_MIN   # текущее значение (меняется в цикле)

# --- Обновление стрима в чате ---
UPDATE_INTERVAL = 3.0              # мин. пауза между chat.update
MIN_DELTA_FOR_UPDATE = 40          # мин. прирост символов, чтобы обновлять
MAX_SILENCE_BEFORE_UPDATE = 5.0    # принудительное обновление, если давно не было

# Приблизительный лимит токенов в context.json
MAX_CONTEXT_TOKENS = 40000

# --- throttle для Rocket.Chat ---
RC_MIN_SEND_INTERVAL =  5.0      # сек между сообщениями (было 1.5, 3.0)
RC_RATE_LIMIT_BACKOFF = 61.0    # сек паузы после 429
_last_send_ts = 0.0
_rate_limited_until = 0.0
_rate_limit_just_resumed = False    # выставляется один раз, когда пауза 429 истекла
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

HELP_TEXT = """📖 **Команды моста DeepSeek** (канал управления)

`!new <имя>` — создать новый чат (папку `chats/<id>/`) и сделать его активным.
   Пример: `!new ssh-setup` → `chats/2026-10-06-ssh-setup/`
`!list` — список активных чатов (папки в `chats\\`).
`!use <chat_id>` — переключиться на существующий чат.
`!current` — показать активный чат и модель.
`!model flash` — `deepseek-flash` (быстрая, умеет изображения).
`!model pro` — `deepseek-v4-pro` (умная, только текст).
`!arch [chat_id]` — отправить чат в архив (без имени — активный).
`!help` — эта справка.

**Как работать:**
1. Сюда пишете только команды (`!new`, `!list`, …).
2. Вопросы DeepSeek — в канал работы.
3. DeepSeek помнит весь диалог в рамках активного чата.
4. Хотите другую тему — `!new other-task`, потом вопросы в канал работы.
5. Вернуться — `!use 2026-10-06-mytask` здесь же.

**Структура папки чата:**
- `context.json` — активная история (идёт в DeepSeek).
- `context-NNN.json` — архивные срезы (при переполнении токенов).
- `tracking.json` — состояние обработки (курсор по каналу работы).
- `media/` — файлы, отправленные пользователем.

**Архивация:** `!arch` переносит папку в `archives\\` — из `!list` чат исчезнет,
но останется на диске."""


# ---------- Утилиты ----------

def approx_tokens(text):
    """Очень грубая оценка: ~1 токен на 3 символа (смесь ru/en)."""
    if not text:
        return 0
    return len(text) // 3 + 1


def count_messages_tokens(messages):
    total = 0
    for m in messages:
        # роль + обвязка сообщения в API ≈ 4 токена
        total += 4
        c = m.get("content", "")
        if isinstance(c, str):
            total += approx_tokens(c)
        elif isinstance(c, list):
            for part in c:
                if not isinstance(part, dict):
                    continue
                t = part.get("type")
                if t == "text":
                    total += approx_tokens(part.get("text", ""))
                elif t in ("image_path", "image_url"):
                    total += 500
    return total


# ---------- Файловое хранилище ----------

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"active_chat": None, "model": DEFAULT_MODEL}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def chat_dir(chat_id):        return CHATS_DIR / chat_id
def context_path(chat_id):    return chat_dir(chat_id) / "context.json"
def tracking_path(chat_id):   return chat_dir(chat_id) / "tracking.json"
def media_dir(chat_id):       return chat_dir(chat_id) / "media"


def list_chats():
    if not CHATS_DIR.exists():
        return []
    return sorted(p.name for p in CHATS_DIR.iterdir() if p.is_dir())


def load_context(chat_id):
    p = context_path(chat_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_context(chat_id, chat):
    chat_dir(chat_id).mkdir(parents=True, exist_ok=True)
    context_path(chat_id).write_text(
        json.dumps(chat, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_tracking(chat_id):
    p = tracking_path(chat_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_processed_ts": None}


def save_tracking(chat_id, tracking):
    chat_dir(chat_id).mkdir(parents=True, exist_ok=True)
    tracking_path(chat_id).write_text(
        json.dumps(tracking, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ensure_chat_dir(chat_id):
    """Если папки чата нет (удалена/перемещена вручную) — создать заново.
    Возвращает True, если папка была пересоздана."""
    d = chat_dir(chat_id)
    if d.exists():
        return False
    d.mkdir(parents=True, exist_ok=True)
    media_dir(chat_id).mkdir(exist_ok=True)
    save_context(chat_id, {
        "id": chat_id,
        "created": datetime.now().isoformat(timespec="seconds"),
        "messages": [],
    })
    save_tracking(chat_id, {"last_processed_ts": None})
    return True


def create_chat(name):
    date_prefix = datetime.now().strftime("%Y-%m-%d")
    safe = "".join(
        c if (c.isalnum() or c in "-_") else "-" for c in name
    ).strip("-") or "chat"
    chat_id = f"{date_prefix}-{safe}"
    base = chat_id
    counter = 1
    while chat_dir(chat_id).exists():
        counter += 1
        chat_id = f"{base}-{counter}"
    chat_dir(chat_id).mkdir(parents=True, exist_ok=True)
    media_dir(chat_id).mkdir(exist_ok=True)
    save_context(chat_id, {
        "id": chat_id,
        "created": datetime.now().isoformat(timespec="seconds"),
        "messages": [],
    })
    save_tracking(chat_id, {"last_processed_ts": None})
    return chat_id


def truncate_context_if_needed(chat_id):
    """Если контекст превышает лимит — сохраняем срез как context-NNN.json,
    а в context.json оставляем только хвост в пределах лимита."""
    chat = load_context(chat_id)
    if not chat:
        return
    msgs = chat.get("messages", [])
    total = count_messages_tokens(msgs)
    if total <= MAX_CONTEXT_TOKENS:
        return

    existing = sorted(chat_dir(chat_id).glob("context-*.json"))
    next_num = 1
    nums = []
    for p in existing:
        try:
            nums.append(int(p.stem.split("-")[-1]))
        except Exception:
            pass
    if nums:
        next_num = max(nums) + 1
    archive_name = f"context-{next_num:03d}.json"

    (chat_dir(chat_id) / archive_name).write_text(
        json.dumps(chat, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    kept, tokens = [], 0
    for m in reversed(msgs):
        t = count_messages_tokens([m])
        if tokens + t > MAX_CONTEXT_TOKENS:
            break
        kept.append(m)
        tokens += t
    kept.reverse()

    chat["messages"] = kept
    save_context(chat_id, chat)
    log_i(f"[{chat_id}] Контекст усечён ({total}→{tokens} токенов), "
          f"старый срез → {archive_name}")


def archive_chat(chat_id):
    src = chat_dir(chat_id)
    if not src.exists():
        return None
    ARCH_DIR.mkdir(parents=True, exist_ok=True)
    dst = ARCH_DIR / chat_id
    if dst.exists():
        n = 1
        while (ARCH_DIR / f"{chat_id}-{n}").exists():
            n += 1
        dst = ARCH_DIR / f"{chat_id}-{n}"
    shutil.move(str(src), str(dst))
    return dst.name


# ---------- Rocket.Chat ----------

# Кэш: {room_id: "groups.history" | "channels.history" | "im.history"}
_history_endpoint_cache = {}

def _remember_endpoint(room_id, ep):
    if _history_endpoint_cache.get(room_id) != ep:
        _history_endpoint_cache[room_id] = ep
        log_i(f"history endpoint для {room_id[:12]}… = {ep}")

def rc_get_history(room_id, count=20, oldest=None):
    """Универсальный history с кэшем рабочего endpoint."""
    global _rate_limited_until, _rate_limit_just_resumed

    now = time.time()
    if now < _rate_limited_until:
        return []

    # Только что вышли из паузы 429 → один раз ставим флаг
    if _rate_limited_until > 0:
        _rate_limit_just_resumed = True
        _rate_limited_until = 0.0

    params = {"roomId": room_id, "count": count}
    if oldest:
        params["oldest"] = oldest

    cached = _history_endpoint_cache.get(room_id)
    candidates = [cached] if cached else ["groups.history", "channels.history", "im.history"]

    for ep in candidates:
        try:
            r = requests.get(
                f"{ROCKET_URL}/api/v1/{ep}",
                headers=rc_headers, params=params,
                verify=False, timeout=15,
            )
        except Exception as e:
            log_w(f"{ep} exception: {e}")
            continue

        if r.status_code == 200:
            data = r.json()
            if data.get("success"):
                _remember_endpoint(room_id, ep)
                return data.get("messages", [])
            # success:false — не тот тип, пробуем дальше (без логирования)
            continue

        if r.status_code == 429:
            wait_s = _parse_retry_after(r)
            _rate_limited_until = time.time() + wait_s
            log_w(f"429 на {ep}: пауза {wait_s:.0f}с "
                  f"(до {datetime.fromtimestamp(_rate_limited_until):%H:%M:%S})")
            return []

        if r.status_code == 404:
            # endpoint отсутствует на этом сервере — запомним и пропустим
            log_d(f"{ep} → 404, пропускаем")
            continue

        if r.status_code == 400:
            # invalid-channel — не тот тип комнаты, пропускаем тихо
            continue

        log_w(f"{ep} HTTP {r.status_code}: {r.text[:120]}")

    # Если кэш не сработал — сбрасываем его и запомним на след. раз
    if cached:
        log_w(f"кэш endpoint для {room_id[:12]}… не сработал, сброс")
        _history_endpoint_cache.pop(room_id, None)
    return []


def _parse_retry_after(resp):
    hdr = resp.headers.get("Retry-After")
    if hdr:
        try:
            return max(float(hdr), 1.0) + 1.0
        except Exception:
            pass
    try:
        m = re.search(r"wait\s+(\d+)\s+second", resp.text, re.IGNORECASE)
        if m:
            return float(m.group(1)) + 1.0
    except Exception:
        pass
    return RC_RATE_LIMIT_BACKOFF


def rc_get_latest_ts(room_id):
    h = rc_get_history(room_id, count=1)
    return h[0]["ts"] if h else None


def _rc_throttle_wait():
    """Ждём, если мы в бэкоффе после 429, и держим минимальный интервал."""
    global _last_send_ts
    now = time.time()
    if now < _rate_limited_until:
        wait = _rate_limited_until - now
        log_d(f"пауза после 429: {wait:.1f}с")
        time.sleep(wait)
        now = time.time()
    delta = now - _last_send_ts
    if delta < RC_MIN_SEND_INTERVAL:
        time.sleep(RC_MIN_SEND_INTERVAL - delta)


def rc_send_message(room_id, text, own_msg_ids):
    global _last_send_ts, _rate_limited_until
    _rc_throttle_wait()
    try:
        r = requests.post(
            f"{ROCKET_URL}/api/v1/chat.postMessage",
            headers=rc_headers, json={"roomId": room_id, "text": BOT_MARK + text},
            verify=False, timeout=15,
        )
        resp = r.json()
        if not resp.get("success"):
            err = str(resp.get("error", "")).lower()
            if "too many requests" in err:
                wait_s = _parse_retry_after(r)
                _rate_limited_until = time.time() + wait_s
                log_i(f"429 → пауза {wait_s:.0f}с")
            else:
                log_w(f"postMessage error: {resp}")
            return resp
        own_msg_ids.add(resp["message"]["_id"])
        _last_send_ts = time.time()
        return resp
    except Exception as e:
        log_w(f"postMessage exception: {e}")
        return {}

def rc_update_message(room_id, msg_id, text):
    global _last_send_ts, _rate_limited_until
    _rc_throttle_wait()
    try:
        r = requests.post(
            f"{ROCKET_URL}/api/v1/chat.update",
            headers=rc_headers,
            json={"roomId": room_id, "msgId": msg_id, "text":  BOT_MARK + text},
            verify=False, timeout=15,
        )
        resp = r.json()
        if not resp.get("success"):
            err = str(resp.get("error", "")).lower()
            if "too many requests" in err:
                wait_s = _parse_retry_after(r)
                _rate_limited_until = time.time() + wait_s
                log_i(f"429 → пауза {wait_s:.0f}с")
        _last_send_ts = time.time()
        return resp
    except Exception as e:
        log_w(f"update exception: {e}")
        return {}

def rc_download_file(file_obj, dest_dir):
    url = file_obj.get("url")
    if not url:
        fid = file_obj.get("_id")
        name = file_obj.get("name", "file")
        url = f"/file-upload/{fid}/{name}"
    full_url = url if url.startswith("http") else f"{ROCKET_URL}{url}"
    try:
        r = requests.get(
            full_url,
            headers={"X-Auth-Token": AUTH_TOKEN, "X-User-Id": USER_ID},
            verify=False, timeout=60, stream=True,
        )
        if r.status_code != 200:
            log_w(f"download {full_url} → {r.status_code}")
            return None
        fname = file_obj.get("name") or Path(url).name or "file"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / fname
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return dest
    except Exception as e:
        log_w(f"download exception: {e}")
        return None


def rc_get_me():
    try:
        r = requests.get(
            f"{ROCKET_URL}/api/v1/me",
            headers=rc_headers, verify=False, timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log_w(f"me exception: {e}")
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


# ---------- Работа с content в context.json ----------

def content_to_api(content, chat_id):
    """Разворачиваем image_path → image_url (data URL) для запроса в API."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            t = part.get("type")
            if t == "text":
                parts.append({"type": "text", "text": part.get("text", "")})
            elif t == "image_path":
                rel = part.get("path", "")
                p = chat_dir(chat_id) / rel
                if p.exists():
                    try:
                        parts.append({
                            "type": "image_url",
                            "image_url": {"url": encode_image_data_url(p)},
                        })
                    except Exception as e:
                        log_w(f"[{chat_id}] image encode: {e}")
        return parts if parts else "(пусто)"
    return str(content)


def build_api_messages(chat_id, chat, extra_user_msg):
    result = []
    for m in chat.get("messages", []):
        result.append({
            "role": m.get("role", "user"),
            "content": content_to_api(m.get("content", ""), chat_id),
        })
    result.append(extra_user_msg)
    return result


def encode_image_data_url(path):
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "application/octet-stream"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def is_image(path):
    mime, _ = mimetypes.guess_type(str(path))
    return (mime or "").startswith("image/")


# ---------- Обработка команд (канал управления) ----------

def handle_command(text, state, own_msg_ids):
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
        return (
            f"✅ Создан новый чат: **{chat_id}**\n"
            f"Все следующие вопросы в канале работы пойдут в него."
        )

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
        if not chat_dir(arg).exists():
            if (ARCH_DIR / arg).exists():
                return (f"❌ Чат `{arg}` находится в архиве. "
                        f"Переместите папку из `archives\\` обратно в `chats\\`.")
            return f"❌ Чат `{arg}` не найден. Посмотрите `!list`."
        state["active_chat"] = arg
        save_state(state)
        ctx = load_context(arg) or {"messages": []}
        n = len(ctx.get("messages", []))
        return f"✅ Переключились на **{arg}** ({n} сообщений в истории)."

    if cmd == "current":
        chat_id = state.get("active_chat")
        model = state.get("model", DEFAULT_MODEL)
        if not chat_id:
            return "ℹ️ Активный чат не выбран. Используйте `!new <имя>` или `!use <chat_id>`."
        ensure_chat_dir(chat_id)
        ctx = load_context(chat_id) or {"messages": []}
        tracking = load_tracking(chat_id)
        media = list(media_dir(chat_id).glob("*")) if media_dir(chat_id).exists() else []
        return (
            f"📌 Активный чат: **{chat_id}**\n"
            f"🤖 Модель: **{model}**\n"
            f"💬 Сообщений в контексте: **{len(ctx.get('messages', []))}**\n"
            f"📎 Файлов в media/: **{len(media)}**\n"
            f"🎯 Курсор: `{tracking.get('last_processed_ts') or '—'}`"
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

    if cmd == "arch":
        target = arg if arg else state.get("active_chat")
        if not target:
            return "❌ Не указан чат и нет активного. Использование: `!arch [chat_id]`"
        if not chat_dir(target).exists():
            if (ARCH_DIR / target).exists():
                return f"⚠️ Чат `{target}` уже находится в архиве."
            existing = list_chats()
            if existing:
                lines = [f"❌ Чат `{target}` не найден. Доступные чаты:"]
                lines += [f"  `{c}`" for c in existing]
                return "\n".join(lines)
            return f"❌ Чат `{target}` не найден, и список чатов пуст."
        moved = archive_chat(target)
        if state.get("active_chat") == target:
            state["active_chat"] = None
            save_state(state)
        return f"📦 Чат **{target}** перемещён в архив (`archives\\{moved}`)."

    return f"❌ Неизвестная команда: `!{cmd}`. Введите `!help`."


# ---------- Обработка вопроса (канал работы) ----------

def build_answer_message(sender, chat_id, model, question, answer, thinking=False):
    preview = (question or "").strip()
    if len(preview) > 500:
        preview = preview[:500] + "..."
    preview = preview.replace("\n", "\n> ") if preview else "(без текста)"
    suffix = " ▌" if thinking else ""
    return (
        f"**❓ {sender}** · чат `{chat_id}` · модель `{model}`\n\n"
        f"> {preview}\n\n"
        f"**🤖 Ответ:**\n\n{answer}{suffix}"
    )


def process_question(text, files, sender, state, own_msg_ids, chat_id):
    model = state.get("model", DEFAULT_MODEL)
    # Страховка: если по какой-то причине прилетело наше же сообщение — отсекаем
    if text.startswith(BOT_MARK):
        return

    # 1. Скачиваем вложения в media/
    saved_files = []
    if files:
        for f in files:
            p = rc_download_file(f, media_dir(chat_id))
            if p:
                saved_files.append(p)
        if saved_files:
            log_i(f"[{chat_id}] скачано файлов: {len(saved_files)}")

    images = [p for p in saved_files if is_image(p)]
    non_images = [p for p in saved_files if not is_image(p)]

    # 2. Проверяем совместимость модели и файлов
    if images and model != "deepseek-flash":
        rc_send_message(
            DP_CHAT_ID,
            f"⚠️ **{sender}**, модель `{model}` не умеет изображения. "
            f"Изображения сохранены в `media/`, но в API не ушли. "
            f"Переключитесь на `!model flash` для анализа картинок.",
            own_msg_ids,
        )
        images = []

    if non_images:
        rc_send_message(
            DP_CHAT_ID,
            f"⚠️ **{sender}**, не-изображения (`{', '.join(p.name for p in non_images)}`) "
            f"пока не поддерживаются API. Файлы сохранены в `media/`.",
            own_msg_ids,
        )

    # 3. Формируем user-content для context.json
    if images and model == "deepseek-flash":
        user_content_ctx = [{"type": "text", "text": text or "(см. изображения)"}]
        for p in images:
            rel = p.relative_to(chat_dir(chat_id)).as_posix()
            user_content_ctx.append({"type": "image_path", "path": rel})
        user_msg_api = {
            "role": "user",
            "content": content_to_api(user_content_ctx, chat_id),
        }
    else:
        user_content_ctx = text or "(пустое сообщение)"
        user_msg_api = {"role": "user", "content": user_content_ctx}

    # 4. Загружаем контекст
    chat = load_context(chat_id)
    if chat is None:
        chat = {"id": chat_id,
                "created": datetime.now().isoformat(timespec="seconds"),
                "messages": []}

    api_messages = build_api_messages(chat_id, chat, user_msg_api)

    # 5. Placeholder в канале работы
    resp = rc_send_message(
        DP_CHAT_ID,
        build_answer_message(sender, chat_id, model, text, "⏳ Думаю...", thinking=True),
        own_msg_ids,
    )
    if not resp.get("success"):
        return
    msg_id = resp["message"]["_id"]

    # 6. Стрим ответа
    full_answer = ""
    last_update = time.time()
    last_len_at_update = 0
    for chunk in call_deepseek_stream(api_messages, model):
        full_answer += chunk
        now = time.time()
        grew = len(full_answer) - last_len_at_update
        since_update = now - last_update
        should_update = (
            since_update > UPDATE_INTERVAL and grew >= MIN_DELTA_FOR_UPDATE
        ) or (since_update > MAX_SILENCE_BEFORE_UPDATE)
        if should_update:
            rc_update_message(
                DP_CHAT_ID, msg_id,
                build_answer_message(sender, chat_id, model, text,
                                     full_answer, thinking=True),
            )
            last_update = now
            last_len_at_update = len(full_answer)
            
    if not full_answer:
        full_answer = "[Пустой ответ от DeepSeek]"

    rc_update_message(
        DP_CHAT_ID, msg_id,
        build_answer_message(sender, chat_id, model, text, full_answer),
    )

    # 7. Сохраняем в контекст
    now_iso = datetime.now().isoformat(timespec="seconds")
    chat["messages"].append({
        "role": "user",
        "content": user_content_ctx,
        "ts": now_iso,
    })
    chat["messages"].append({
        "role": "assistant",
        "content": full_answer,
        "ts": now_iso,
    })
    save_context(chat_id, chat)
    truncate_context_if_needed(chat_id)

    log_i(f"[{datetime.now():%H:%M:%S}] [{chat_id}] {len(full_answer)} симв.")


# ---------- Главный цикл ----------

def main():
    global POLL_INTERVAL, _rate_limit_just_resumed
    CHATS_DIR.mkdir(parents=True, exist_ok=True)
    ARCH_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    own_msg_ids = set()

    me = rc_get_me()
    own_username = me.get("username")
    log_i(f"Мост v{API_VERSION} запущен.")
    log_i(f"Мой username:     {own_username}")
    log_i(f"Канал управления: {DP_CTRL_ID}")
    log_i(f"Канал работы:     {DP_CHAT_ID}")
    log_i(f"Активный чат:     {state.get('active_chat') or '—'}")
    log_i(f"Модель:           {state.get('model', DEFAULT_MODEL)}")
    log_i("Ctrl+C для остановки.")

    # Если активный чат пропал — пересоздаём
    active = state.get("active_chat")
    if active and ensure_chat_dir(active):
        log_w(f"Чат {active} отсутствовал в chats/ — пересоздан.")
        tracking = load_tracking(active)
        tracking["last_processed_ts"] = rc_get_latest_ts(DP_CHAT_ID) or \
            datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        save_tracking(active, tracking)

    # Позиция в канале управления — с текущего момента
    last_ts_in = rc_get_latest_ts(DP_CTRL_ID)
    if last_ts_in is None:
        # не смогли узнать — ставим отсечку на текущий момент,
        # чтобы не обрабатывать всю историю канала
        last_ts_in = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        log_w(f"last_ts_in не получен, стартуем с {last_ts_in}")
    log_i(f"last_ts_in = {last_ts_in}")
    log_i("=" * 40)
    while True:
        ctrl_msgs = []
        work_msgs = []
        try:
            # ---------- Канал управления ----------
            prev_active = state.get("active_chat")
            ctrl_msgs = rc_get_history(DP_CTRL_ID, count=20, oldest=last_ts_in)
            for msg in reversed(ctrl_msgs):
                ts = msg["ts"]
                if last_ts_in and ts <= last_ts_in:
                    continue
                last_ts_in = ts
                raw = msg.get("msg") or ""
                if raw.startswith(BOT_MARK):
                    continue
                if msg["_id"] in own_msg_ids:
                    continue
                if msg.get("files") or msg.get("attachments"):
                    continue
                text = (msg.get("msg") or "").strip()
                if not text:
                    continue
                sender = msg.get("u", {}).get("username", "unknown")

                if text.startswith("!"):
                    log_i(f"[{datetime.now():%H:%M:%S}] CTRL {sender}: {text}")
                    reply = handle_command(text, state, own_msg_ids)
                    rc_send_message(DP_CTRL_ID, reply, own_msg_ids)
                else:
                    rc_send_message(
                        DP_CTRL_ID,
                        f"⚠️ **{sender}**, это канал команд. Здесь пишите `!help`, "
                        f"`!new <имя>`, `!list`, `!use <chat_id>`, "
                        f"`!model flash|pro`, `!arch`.",
                        own_msg_ids,
                    )

            # Если активный чат сменился — ставим курсор на ts команды
            new_active = state.get("active_chat")
            if new_active != prev_active and new_active:
                ensure_chat_dir(new_active)
                tracking = load_tracking(new_active)
                tracking["last_processed_ts"] = last_ts_in
                save_tracking(new_active, tracking)
                log_i(f"  [switch] → {new_active}, курсор: {last_ts_in}")

            # ---------- Канал работы ----------
            active = state.get("active_chat")
            if not active:
                active = create_chat("default")
                state["active_chat"] = active
                save_state(state)
                tracking = load_tracking(active)
                tracking["last_processed_ts"] = rc_get_latest_ts(DP_CHAT_ID) or \
                    datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                save_tracking(active, tracking)
                log_i(f"  [auto] Активный чат не задан — создан {active}")

            ensure_chat_dir(active)
            tracking = load_tracking(active)
            cursor = tracking.get("last_processed_ts")

            # Защита: если курсора нет или его не удалось получить — ставим
            # отсечку на текущий момент, чтобы не читать всю историю канала.
            if not cursor:
                cursor = rc_get_latest_ts(DP_CHAT_ID) or \
                         datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                tracking["last_processed_ts"] = cursor
                save_tracking(active, tracking)
                log_i(f"Курсор рабочего канала не получен, стартуем с {cursor}")
            
            work_msgs = rc_get_history(DP_CHAT_ID, count=20, oldest=cursor)
            for msg in reversed(work_msgs):
                ts = msg["ts"]
                if cursor and ts <= cursor:
                    continue
                # Обновляем курсор сразу, чтобы не зациклиться при ошибке
                cursor = ts
                tracking["last_processed_ts"] = ts
                save_tracking(active, tracking)

                raw = msg.get("msg") or ""
                if raw.startswith(BOT_MARK):
                    continue
                if msg["_id"] in own_msg_ids:
                    continue

                text = (msg.get("msg") or "").strip()
                files = msg.get("files") or msg.get("attachments") or []
                if not text and not files:
                    continue

                sender = msg.get("u", {}).get("username", "unknown")
                process_question(text, files, sender, state, own_msg_ids, active)

        except KeyboardInterrupt:
            log_i("Остановлено по Ctrl+C")
            return
        except Exception as e:
            log_e(f"Ошибка в цикле: {e}\n{traceback.format_exc()}")

        got_something = bool(ctrl_msgs) or bool(work_msgs)

        if _rate_limit_just_resumed:
            log_i("вышли из паузы 429 → форс-опрос")
            POLL_INTERVAL = POLL_INTERVAL_MIN
            _rate_limit_just_resumed = False
        elif got_something:
            if POLL_INTERVAL != POLL_INTERVAL_MIN:
                log_d(f"активность → интервал {POLL_INTERVAL_MIN}с")
            POLL_INTERVAL = POLL_INTERVAL_MIN
        else:
            new_interval = min(POLL_INTERVAL + POLL_INTERVAL_STEP, POLL_INTERVAL_MAX)
            if int(new_interval) != int(POLL_INTERVAL):
                log_d(f"тишина → интервал {new_interval:.1f}с")
            POLL_INTERVAL = new_interval

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
