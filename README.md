открывать app.py

Веб-интерфейс сайта доступен в папке `website`.

Новые страницы:

- `/support` — Информация о поддержке, контактах и FAQ.
- `/feedback` — Форма обратной связи; сообщения сохраняются в `data_base/bot_database.db` и отправляются на указанный email.
- `/admin/feedbacks` — Админ-панель для просмотра и управления сообщениями обратной связи (требуется логин администратора сайта).

Установка зависимостей для веба:

```powershell
pip install -r website\web_requirements.txt
```

Запуск:

```powershell
python website\web.py

Требования для конвертации:
- Для конвертации аудио/видео требуется ffmpeg в PATH (для moviepy и pydub). Установите ffmpeg и добавьте в переменную среды PATH.
- Для конвертации текстовых форматов pypandoc требует установленный Pandoc (https://pandoc.org/).

Пример установки на Windows (PowerShell):

```powershell
choco install ffmpeg
choco install pandoc

Если на Windows при импорте pydub вы видите ошибку, похожую на:

```
Кроме того, код теперь включает резервный вариант на основе FFmpeg: если `pydub` или `moviepy` недоступны, сайт попытается использовать `ffmpeg` напрямую для конвертации аудио/видео. Это позволяет избежать зависимости от `audioop`/`pyaudioop`, если вы предпочитаете не устанавливать его. Убедитесь, что `ffmpeg` установлен и находится в PATH.

Вы также можете запустить вспомогательный скрипт, который устанавливает системные инструменты и требования Python (запустите из повышенной PowerShell):

```powershell
.\website\scripts\install_audio_tools.ps1
```
ModuleNotFoundError: No module named 'audioop'
```

То установите пакет-заменитель `pyaudioop`:

```powershell
pip install pyaudioop
```

Это решит проблему на некоторых сборках Python для Windows, где модуль `audioop` отсутствует.
```
```
