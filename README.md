# Rocket AI Bridge

Мост из Rocket.Chat в DeepSeek. Токены, id комнат и пароль бота в код не вставляются: скопируйте `.env.example` в `.env` и заполните его. Что писать в каждую переменную, сказано в комментариях образца.

Снимок ветки: **v3.5-stream**. Ответ обновляется по мере генерации, свои сообщения помечены невидимым символом.

Полное описание команд и карта веток — в ветке `main`.

## Запуск

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python get_rooms.py
python bridge.py
```
