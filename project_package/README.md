# 📦 Project Package

## Состав архива
- `business_logic.md` — бизнес-логика (что делает приложение и зачем).
- `technical_logic.md` — техническая логика (как реализовать приложение).
- `progpedia.md` — краткий справочник по модулям.
- `data_flow.md` — схема обмена данными между частями системы.

## Как использовать
1. Сначала прочитайте **business_logic.md**, чтобы понять цели и правила работы приложения.
2. Затем откройте **technical_logic.md** — он описывает архитектуру, структуру модулей и лучшие практики разработки.
3. По мере развития проекта сверяйтесь с **progpedia.md** и **data_flow.md**, чтобы выдерживать стиль и формат обмена данными.
4. Передавайте эти файлы агенту (например, GPT-Codex) для поэтапной генерации кода:
   - Сначала реализуйте базу данных.
   - Потом веб-интерфейс (Flask).
   - Далее — интеграцию с Outlook и Telegram.
   - В конце — автоматизацию проверок и тестирование.

## Режимы запуска
- **Веб-интерфейс (разработка)**: `python -m project_package.project.app`. Запускает встроенный сервер Flask на `http://127.0.0.1:5000`; перед дачей доступа пользователям обновите `SECRET_KEY` и отключите `debug` в `app.py`.
- **Веб-интерфейс (продакшн)**: запустите WSGI-сервер, например `gunicorn --bind 0.0.0.0:8000 project_package.project.app:app`. Для Windows подойдёт `waitress-serve --listen=0.0.0.0:8000 project_package.project.app:app`. Повесьте nginx/Apache/IIS как обратный прокси и настройте автозапуск (systemd, Task Scheduler).
- **Проверка почты вручную**: `python -m project_package.project.mail_checker` (опция `--fake` подставит тестовые письма).
- **Проверка задержек вручную**: `python -m project_package.project.notifier --minutes 60` (добавьте `--dry-run`, если нужно увидеть текст без отправки в Telegram).
## Автоматизация проверок
Вместо встроенного планировщика используются отдельные CLI-команды — их удобно запускать через cron (Linux) или Task Scheduler (Windows).

- Проверка почты и обновление статусов:
  ```bash
  python -m project_package.project.mail_checker
  ```
  Опция `--fake` включает встроенные тестовые письма (для отладки без Outlook).

- Напоминания о задержках:
  ```bash
  python -m project_package.project.notifier --minutes 60
  ```
  Опция `--dry-run` печатает сообщения без отправки в Telegram.

Примеры расписания:
- Linux (cron, каждые 10 минут):
  ```cron
  */10 * * * * /usr/bin/python -m project_package.project.mail_checker >> /var/log/omis-mail.log 2>&1
  */10 * * * * /usr/bin/python -m project_package.project.notifier >> /var/log/omis-notifier.log 2>&1
  ```
- Windows Task Scheduler: создайте два задания с триггером “каждые 10 минут” и действием
  `python -m project_package.project.mail_checker` и `python -m project_package.project.notifier`.

## Общие рекомендации
- Развивайте проект по шагам: сначала простая версия (MVP), потом улучшения.
- Храните все секретные данные (токены, пароли) только в `config.py` или переменных окружения.
- Всегда комментируйте код и используйте `logging` для отслеживания работы.
- Начинайте тестирование на тестовых письмах и тестовом Telegram-чате, прежде чем подключать боевую систему.
- Держите проект структурированным: один модуль — одна ответственность.
- По мере роста нагрузки можно заменить SQLite на PostgreSQL.

---
Удачи в разработке! 🚀

