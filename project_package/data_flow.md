# Data Flow Architecture

## Назначение документа
Этот файл описывает, как модули OMIS обмениваются данными. Перед тем как добавлять новый канал передачи данных или расширять существующий, сверяйтесь с этими правилами, чтобы сохранять единый формат.

## Основные сущности
- **Запись заявки** — словарь, который возвращают функции `database.get_requests()` и `database.get_delayed_requests()`:
  - `id`: int, первичный ключ в SQLite;
  - `request_number`: str, номер заявки (обязателен);
  - `position_number`: str, номер позиции (обязателен);
  - `comment`: str | None, последний комментарий;
  - `comment_author`: str | None, имя автора комментария;
  - `status`: str, один из статусов из `business_logic.md`;
  - `created_at`: str, UTC в ISO-8601 без микросекунд;
  - `status_updated_at`: str, UTC в ISO-8601 без микросекунд.
- **Телеграм-уведомление** — текстовая строка, которую формирует `_format_delay_message()` в `notifier.py`. В боевом режиме доставляется через `send_message()` с передачей `chat_id` и `text` в Telegram Bot API.
- **Почтовое сообщение подрядчика** — объект `ContractorMessage` из `mail_checker.py` (dataclass) с полями `request_number`, `position_number`, `status`, `comment`, `sent_at`, `author`.

## Модули и их ответственность
- `project_package/project/config.py`
  - Читает путь к БД и креды Telegram из переменных окружения.
  - Ничего не хранит в себе, только отдаёт константы другим модулям.
- `project_package/project/database.py`
  - Принимает простые типы (str/int) и возвращает словари (`dict`).
  - Все временные метки пишет в UTC (`_utc_now()`).
  - Содержит единую точку доступа к SQLite: другие модули не открывают соединения напрямую.
- `project_package/project/app.py`
  - Получает данные формы, вызывает `database.add_request()`, затем читает заявки через `database.get_requests()`.
  - Передаёт словари в шаблоны, без дополнительной трансформации.
- `project_package/project/mail_checker.py`
  - Анализирует письма, приводит данные к `ContractorMessage`.
  - Для каждой записи вызывает `database.update_status()` и `database.update_comment()`.
  - Не обращается к Telegram напрямую; только пишет в БД.
- `project_package/project/notifier.py`
  - Вызывает `database.get_delayed_requests()` и преобразует словари заявок в текстовые уведомления.
  - Доставляет уведомления в Telegram при наличии токена и chat_id.
- *(планируется)* `project_package/project/scheduler.py`
  - Должен вызывать `mail_checker.process_mailbox()` и `notifier.notify_delays()` по расписанию, не вмешиваясь в структуру данных.

## Потоки данных
1. **Создание заявки (UI → БД)**
   1. Пользователь отправляет форму в `app.py` (`POST /add_request`).
   2. Модуль вызывает `database.add_request(request_number, position_number, comment, author)`.
   3. Функция сохраняет запись с начальным статусом «заявка отправлена», устанавливает `created_at` и `status_updated_at` и возвращает числовой `request_id`.
   4. Интерфейс запрашивает актуальный список через `database.get_requests()` и выводит словари в шаблоне.

2. **Обновление статуса (Почта → mail_checker → БД)**
   1. `mail_checker.fetch_contractor_messages()` получает письма (сейчас — из тестового набора).
   2. `process_mailbox()` создаёт `ContractorMessage` и вызывает:
      - `database.update_status(request_number, new_status, position_number)`;
      - `database.update_comment(request_number, comment, position_number, author)`.
   3. Каждая функция БД возвращает `bool`, указывая, была ли найдена запись.
   4. `process_mailbox()` формирует отчёт из результата (строки для логирования/UI).

3. **Уведомления о задержках (БД → notifier → Telegram)**
   1. `notifier.notify_delays(minutes, send=True)` обращается к `database.get_delayed_requests(minutes)`.
   2. Для каждой заявки `_format_delay_message()` формирует текст.
   3. `send_message()` отправляет текст в Telegram Bot API, используя токен и chat_id из `config.py`.
   4. При отсутствии настроек запись попадает в лог `[FAKE TELEGRAM]` и на этом поток завершается.

## Правила и соглашения
- Все временные метки хранятся в UTC, формат ISO-8601 без микросекунд (`YYYY-MM-DDTHH:MM:SS`).
- Поля `request_number` и `position_number` всегда строковые, чтобы избежать ошибок приведения типов.
- Статусы должны соответствовать перечню из `project_package/business_logic.md`.
- Любые новые модули, которым нужна информация о заявках, должны использовать публичные функции `database.py`, а не работать с SQLite напрямую.
- Если требуется добавить новый канал оповещений, он должен принимать на вход словари заявок такого же вида, как возвращает `get_delayed_requests()`.

## Как использовать документ
- Перед добавлением нового обмена данными убедитесь, что используете существующие структуры (`dict` с полями, перечисленными выше, или `ContractorMessage`).
- Если поток данных отклоняется от описанных сценариев, сначала обновите этот документ, затем вносите изменения в код.
- При появлении новых сущностей (например, комментарии к уведомлениям) добавьте раздел в **Основные сущности** и опишите, какие модули их создают и потребляют.
