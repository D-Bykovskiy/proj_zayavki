# Руководство по запуску OMIS на новой машине

## 1. Клонирование проекта
Используйте, когда розвертываете OMIS на чистом окружении или обновляете локальную копию.
```bash
git clone https://github.com/talyu/omis.git
cd omis
```

## 2. Виртуальное окружение
Создайте отдельный Python-интерпретатор, чтобы не мешать системным пакетам. Обязательно делайте на всех средах (dev/prod).
Windows:
```powershell
python -m venv .venv
.\.venv\Scripts\activate
```
Linux/macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 3. Установка зависимостей
Нужна при первом запуске и после обновления `requirements.txt`.
```bash
pip install --upgrade pip
pip install -r requirements.txt
```
Для COM-бэкенда на Windows дополнительно установите pywin32 (только там, где Outlook установлен и потребуется локальный доступ):
```bash
pip install pywin32
```

## 4. Подготовка файла `.env`
Храните все чувствительные переменные здесь для разработки или стендов. На продакшене переносите значения в системные переменные.
```dotenv
OMIS_OUTLOOK_BACKEND=com              # com — когда работаете через установленный Outlook; oauth — при использовании Azure OAuth2; fake — для тестов
OMIS_OUTLOOK_FOLDER=Входящие/OMIS     # задайте подпапку, если почта сортируется не в корень Inbox
OMIS_OUTLOOK_LOOKBACK_MINUTES=120     # ограничение по давности писем; уменьшайте на медленных машинах
OMIS_OUTLOOK_MAX_MESSAGES=25          # верхняя граница количества писем за проход
OMIS_OUTLOOK_EMAIL=user@example.com   # e-mail почтового ящика для OAuth2
OMIS_OUTLOOK_CLIENT_ID=...            # клиент Azure AD (только для oauth)
OMIS_OUTLOOK_CLIENT_SECRET=...        # секрет Azure AD (только для oauth)
OMIS_OUTLOOK_TENANT_ID=...            # tenant Azure AD (только для oauth)
OMIS_TELEGRAM_TOKEN=...               # токен бота для уведомлений
OMIS_TELEGRAM_CHAT_ID=...             # ID чата/канала, куда шлём напоминания
OMIS_DB_FILE=project_package/project/omis.sqlite3  # путь к базе: меняйте, если нужно хранить вне репозитория
```

## 5. Подхват настроек
`python-dotenv` автоматически считывает `.env`, если запускаете скрипты из корня проекта. Для служб/планировщиков пропишите переменные вручную:
- **Windows Task Scheduler**: укажите переменные в настройке задачи или вызовите `.bat`, где они экспортируются перед запуском.
- **systemd**: используйте `EnvironmentFile=/path/to/.env` (или создайте отдельный `*.env` без секретов в репозитории).

## 6. Инициализация базы
Запускайте один раз после развёртывания или когда очищаете БД. Проверяет схему и создаёт таблицы.
```bash
python -m project_package.project.app            # поднимает Flask и инициализирует БД
```
или, если нужен пакетный режим без веб-интерфейса:
```bash
python -m project_package.runner --fake-mail --dry-run
```

## 7. Основные команды
Используйте согласно выбранному backend почты и требованиям к запуску.
```bash
python -m project_package.project.mail_checker --backend com      # принудительно COM (иначе сработает auto)
python -m project_package.project.notifier --minutes 60            # напоминания о задержках
python -m project_package.runner --mail-backend com --minutes 60   # связка почты и уведомлений
```
Для тестов без реального Outlook/Telegram:
```bash
python -m project_package.project.mail_checker --fake
python -m project_package.runner --fake-mail --dry-run
```

## 8. Планировщики
Настраивайте, когда нужно автоматическое выполнение.
- **Windows Task Scheduler**: действие `python -m project_package.runner --mail-backend com`, рабочая папка — корень проекта, предварительно активируйте `.venv` в скрипте или укажите путь к `python.exe` внутри `.venv`.
- **Linux systemd/cron**: используйте `project_package/project/server_setup.py` как шаблон; убедитесь, что переменные окружения доступны сервису.

## 9. Обновления
Выполняйте после `git pull` или при смене ветки.
```bash
git pull
pip install -r requirements.txt
```
Если меняли backend или получали новые секреты, обновите `.env` перед следующим запуском.

## 10. Диагностика
- Логи (`logging`) выводятся в консоль или журнал службы.
- COM-режим: убедитесь, что Outlook установлен, пользователь хотя бы раз открывал программу, `pywin32` стоит, а задача запускается от того же пользователя.
- OAuth2: проверьте корректность `OMIS_OUTLOOK_*`. При ошибке авторизации скрипт предупредит в логах; используйте тестовые письма (`--fake`), чтобы отделить проблемы интеграции от логики.
