"""Настройки из файла .env.

В репозитории нет токенов, паролей и id комнат. Скопируйте .env.example
в .env и подставьте свои значения: подсказка к каждой переменной лежит
в образце и в сообщении, которое скрипт печатает, если переменной нет.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_dotenv(path=None):
    """Читает KEY=VALUE. Уже заданные переменные окружения не перезаписывает."""
    env_path = Path(path) if path else ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


def _missing(shown, hint):
    raise SystemExit(
        f"Не задано {shown}.\n"
        f"Что сюда подставить: {hint}\n"
        f"Добавьте переменную в файл .env (образец — .env.example) и запустите скрипт снова."
    )


def require(name, hint):
    value = os.environ.get(name, "").strip()
    if not value:
        _missing(name, hint)
    return value


def optional(name, default=""):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def pick(*names, hint):
    """Первая непустая переменная из списка. Так старые и новые имена комнат взаимозаменяемы."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    _missing(" или ".join(names), hint)


def work_dir():
    """Каталог чатов, архива и логов. Пустой WORK_DIR — папка с программой."""
    raw = optional("WORK_DIR")
    return Path(raw).expanduser() if raw else ROOT
