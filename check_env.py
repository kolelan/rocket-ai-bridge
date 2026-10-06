# check_env.py
import json
import sys
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from settings import require, work_dir

# ================== НАСТРОЙКИ ==================
# Сюда подставляется базовый URL сервера Rocket.Chat, без слэша на конце.
# Значение берётся из ROCKET_URL в файле .env.
ROCKET_URL = require("ROCKET_URL", "базовый URL Rocket.Chat без слэша на конце, например https://chat.example.com")
# Сюда подставляется секретный ключ API DeepSeek.
# Это заголовок Authorization: Bearer. Берётся из DEEPSEEK_API_KEY в .env.
DEEPSEEK_API_KEY = require("DEEPSEEK_API_KEY", "секретный ключ API DeepSeek со страницы platform.deepseek.com/api_keys")

# Сюда подставляется каталог чатов, архива и логов.
# Берётся WORK_DIR из .env. Если переменная пустая — папка с программой.
WORK_DIR = work_dir()
CHATS_DIR = WORK_DIR / "chats"

# Текущий лимит из bridge.py — для сравнения
MAX_CONTEXT_TOKENS = 40000
# ================================================

# ---------- Токенизаторы ----------

_tiktoken_enc = None
_tokenizer_kind = "heuristic"

def init_tokenizer():
    """Пытаемся поднять tiktoken (cl100k_base — универсальный, хорошо приближает
    DeepSeek для ru/en/кода). Если не установлен — остаёмся на эвристике."""
    global _tiktoken_enc, _tokenizer_kind
    try:
        import tiktoken
        _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        _tokenizer_kind = "tiktoken/cl100k_base"
        return
    except ImportError:
        pass
    try:
        # запасной вариант: transformers + любой подходящий токенизатор
        from transformers import AutoTokenizer
        _tiktoken_enc = AutoTokenizer.from_pretrained(
            "deepseek-ai/DeepSeek-V2-Lite", trust_remote_code=True
        )
        _tokenizer_kind = "transformers/DeepSeek"
        return
    except Exception:
        _tokenizer_kind = "heuristic (len/3)"


def count_tokens(text):
    """Точный подсчёт, если доступен токенизатор; иначе эвристика."""
    if not text:
        return 0
    if _tiktoken_enc is None:
        return len(text) // 3 + 1
    if _tokenizer_kind == "tiktoken/cl100k_base":
        return len(_tiktoken_enc.encode(text, disallowed_special=()))
    # transformers
    return len(_tiktoken_enc.encode(text, add_special_tokens=False))


def count_heuristic(text):
    return 0 if not text else len(text) // 3 + 1


def content_tokens(content):
    """content в context.json бывает строкой или списком частей."""
    if isinstance(content, str):
        return count_tokens(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            t = part.get("type")
            if t == "text":
                total += count_tokens(part.get("text", ""))
            elif t in ("image_path", "image_url"):
                total += 500  # фиксированная оценка на изображение
        return total
    return 0


def analyze_context(chat_id):
    """Возвращает словарь с метриками по одному чату."""
    p = CHATS_DIR / chat_id / "context.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}

    msgs = data.get("messages", [])
    by_role = {"user": 0, "assistant": 0, "other": 0}
    heur = 0
    imgs = 0
    for m in msgs:
        c = m.get("content", "")
        t = content_tokens(c)
        if isinstance(c, list):
            imgs += sum(1 for x in c if isinstance(x, dict)
                        and x.get("type") in ("image_path", "image_url"))
        role = m.get("role", "other")
        by_role[role if role in by_role else "other"] += t
        # эвристика для сравнения — по строкам текста
        if isinstance(c, str):
            heur += count_heuristic(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text":
                    heur += count_heuristic(part.get("text", ""))
                elif isinstance(part, dict) and part.get("type") in ("image_path", "image_url"):
                    heur += 500

    total = sum(by_role.values())
    return {
        "chat_id": chat_id,
        "messages": len(msgs),
        "tokens_total": total,
        "tokens_user": by_role["user"],
        "tokens_assistant": by_role["assistant"],
        "tokens_heuristic": heur,
        "images": imgs,
        "file_bytes": p.stat().st_size,
    }


# ---------- Проверка Rocket.Chat ----------

def check_rocket():
    print(f"Проверяем Rocket.Chat: {ROCKET_URL}")
    try:
        r = requests.get(ROCKET_URL, timeout=10, verify=False)
        print(f"[Rocket.Chat] HTTP {r.status_code} — сервер доступен\n")
        return True
    except Exception as e:
        print(f"[Rocket.Chat] Ошибка: {e}\n")
        return False


# ---------- Проверка DeepSeek ----------

def check_deepseek():
    print("Проверяем DeepSeek API...")
    try:
        r = requests.get(
            "https://api.deepseek.com/models",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            timeout=10,
        )
        if r.status_code == 200:
            print("[DeepSeek] HTTP 200 — API доступен, ключ работает")
            try:
                models = r.json().get("data", [])
                names = [m.get("id") for m in models if isinstance(m, dict)]
                if names:
                    print(f"[DeepSeek] Доступные модели: {', '.join(names)}")
            except Exception:
                print(f"[DeepSeek] Ответ: {r.text[:300]}")
            print()
            return True
        print(f"[DeepSeek] HTTP {r.status_code} — {r.text[:300]}\n")
        return False
    except Exception as e:
        print(f"[DeepSeek] Ошибка: {e}\n")
        return False


# ---------- Анализ чатов ----------

def analyze_all_chats():
    if not CHATS_DIR.exists():
        print(f"[chats] Папка не найдена: {CHATS_DIR}")
        return []

    chats = sorted(p.name for p in CHATS_DIR.iterdir() if p.is_dir())
    if not chats:
        print(f"[chats] В {CHATS_DIR} нет подпапок с чатами.")
        return []

    print(f"Токенизатор: {_tokenizer_kind}")
    print(f"Лимит MAX_CONTEXT_TOKENS = {MAX_CONTEXT_TOKENS}")
    print(f"Папка чатов: {CHATS_DIR}\n")

    results = []
    for cid in chats:
        info = analyze_context(cid)
        if info is None:
            print(f"  {cid}: нет context.json")
            continue
        if "error" in info:
            print(f"  {cid}: ошибка чтения — {info['error']}")
            continue
        results.append(info)

        over = "  ⚠️  ПРЕВЫШЕНИЕ" if info["tokens_total"] > MAX_CONTEXT_TOKENS else ""
        print(f"── {cid}{over}")
        print(f"     сообщений:      {info['messages']}")
        print(f"     токенов (API):  {info['tokens_total']:>8,}"
              f"   user={info['tokens_user']:,}  asst={info['tokens_assistant']:,}")
        print(f"     токенов (эвр.): {info['tokens_heuristic']:>8,}"
              f"   ({_tokenizer_kind})")
        print(f"     изображений:    {info['images']}")
        print(f"     размер файла:   {info['file_bytes']:,} байт")
        print(f"     доля лимита:    {info['tokens_total']/MAX_CONTEXT_TOKENS*100:.1f} %\n")

    return results


def print_summary(results):
    if not results:
        return
    print("=" * 64)
    print("СВОДКА")
    print("=" * 64)
    print(f"{'чат':<40} {'токенов':>10} {'% лимита':>10}")
    print("-" * 64)
    for r in sorted(results, key=lambda x: -x["tokens_total"]):
        pct = r["tokens_total"] / MAX_CONTEXT_TOKENS * 100
        flag = " ⚠️" if pct > 100 else (" !" if pct > 80 else "")
        print(f"{r['chat_id']:<40} {r['tokens_total']:>10,} {pct:>9.1f}%{flag}")
    print("-" * 64)

    total = sum(r["tokens_total"] for r in results)
    print(f"Всего чатов: {len(results)}, суммарно токенов: {total:,}")
    print()

    # Подсказка по лимиту
    mx = max(r["tokens_total"] for r in results)
    if mx > MAX_CONTEXT_TOKENS:
        print(f"⚠️  Самый тяжёлый чат ({mx:,} токенов) превышает лимит "
              f"{MAX_CONTEXT_TOKENS:,} — увеличьте MAX_CONTEXT_TOKENS "
              f"или уменьшите окно истории.")
    else:
        head = MAX_CONTEXT_TOKENS - mx
        print(f"✅  Запас до лимита: {head:,} токенов "
              f"({head/MAX_CONTEXT_TOKENS*100:.1f} %).")


def main():
    print("=== Диагностика окружения моста DeepSeek ===\n")
    init_tokenizer()
    check_rocket()
    check_deepseek()
    results = analyze_all_chats()
    print_summary(results)


if __name__ == "__main__":
    main()
