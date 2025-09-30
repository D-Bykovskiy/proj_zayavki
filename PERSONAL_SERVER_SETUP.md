# Руководство по развёртыванию на персональной рабочей станции

Это руководство описывает, как разместить OMIS на одном рабочем компьютере внутри корпоративной сети. Эта же машина будет опрашивать почтовый ящик, обновлять статусы заявок и отдавать веб-интерфейс коллегам.

## Шаг 0. Предварительные условия
- Windows 10/11 Pro. Желательно иметь локальные права администратора для сервисной учётной записи; если их нет, заранее согласуйте действия с ИТ (особенно firewall и службы).
- Стабильное сетевое подключение и фиксированный hostname либо резервирование DHCP, чтобы коллеги могли подключаться по одному адресу.
- Установленные Python 3.11 ("для всех пользователей", чтобы работал лаунчер `py`) и Git for Windows.
- Данные для интеграций: учётка Outlook (client ID/secret/tenant, адрес сервисного почтового ящика) и, при необходимости, токен Telegram-бота и ID чата.
- Разрешение ИТ на входящие соединения по выбранному TCP-порту (по умолчанию 8000) внутри периметра компании.

## Шаг 1. Подготовьте рабочую станцию
1. Создайте или выделите отдельную локальную учётную запись (например, `omis-service`), которая будет запускать процессы. Добавьте ей право "Log on as a batch job", чтобы Планировщик задач мог стартовать фоновые задания.
2. Выполните вход под этой учётной записью и создайте рабочую папку, например `C:\omis` (убедитесь, что она пустая).
3. Настройте энергосбережение так, чтобы компьютер не засыпал и не переходил в гибернацию при работе от сети.

## Шаг 2. Установите окружение Python
1. Откройте PowerShell от имени сервисной учётной записи и перейдите в папку:
   ```powershell
   cd C:\omis
   ```
2. Клонируйте репозиторий в текущую директорию:
   ```powershell
   git clone https://github.com/talyu/omis.git .
   ```
3. Создайте виртуальное окружение и активируйте его:
   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
4. Обновите `pip` и установите рабочие зависимости:
   ```powershell
   python -m pip install --upgrade pip
   python -m pip install flask requests python-dotenv exchangelib waitress
   ```

## Шаг 3. Настройте переменные окружения
1. Создайте папку для конфигурации:
   ```powershell
   New-Item -ItemType Directory -Path .\conf -Force
   ```
2. Скопируйте пример `.env` и отредактируйте его:
   ```powershell
   Copy-Item .\ops\systemd\omis-runner.env.example .\conf\omis.env
   notepad .\conf\omis.env
   ```
3. Заполните значения (пример):
   ```
   OMIS_ROOT=C:\omis
   OMIS_PYTHON=C:\omis\.venv\Scripts\python.exe
   OMIS_OUTLOOK_EMAIL=service@company.local
   OMIS_OUTLOOK_CLIENT_ID=<azure-app-client-id>
   OMIS_OUTLOOK_CLIENT_SECRET=<azure-app-secret>
   OMIS_OUTLOOK_TENANT_ID=<azure-tenant-id>
   OMIS_OUTLOOK_FOLDER=Inbox/OMIS
   OMIS_OUTLOOK_LOOKBACK_MINUTES=120
   OMIS_TELEGRAM_TOKEN=<bot-token>
   OMIS_TELEGRAM_CHAT_ID=<chat-id>
   ```
   Держите файл вне Git и ограничьте права NTFS только сервисной учётной записи.
4. Загружайте переменные при запуске приложения и заданий. В PowerShell добавьте в стартовые скрипты:
   ```powershell
   Get-Content C:\omis\conf\omis.env | ForEach-Object {
       if ($_ -match '^(?<key>[^#=]+)=(?<value>.+)$') {
           [System.Environment]::SetEnvironmentVariable($matches.key.Trim(), $matches.value.Trim())
       }
   }
   ```

## Шаг 4. Ручная проверка приложения
1. При необходимости снова активируйте виртуальное окружение:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
2. Запустите раннер в "сухом" режиме, чтобы проверить базу и интеграции:
   ```powershell
   python -m project_package.runner --fake-mail --dry-run --log-level DEBUG
   ```
   Убедитесь, что обрабатываются тестовые письма и нет необработанных исключений.
3. Запустите веб-сервер локально:
   ```powershell
   waitress-serve --listen=0.0.0.0:8000 project_package.project.app:app
   ```
4. С другого компьютера в сети откройте `http://<ip-станции>:8000/` и проверьте доступность интерфейса.
5. Остановите сервер сочетанием `Ctrl+C`, когда проверка завершена.

## Шаг 5. Создайте вспомогательные скрипты
Сохраните два скрипта в `C:\omis\scripts` (создайте папку при необходимости), чтобы упростить автоматизацию.

`start-web.ps1`:
```powershell
$envPath = 'C:\omis\conf\omis.env'
Get-Content $envPath | ForEach-Object {
    if ($_ -match '^(?<key>[^#=]+)=(?<value>.+)$') {
        [System.Environment]::SetEnvironmentVariable($matches.key.Trim(), $matches.value.Trim())
    }
}
& 'C:\omis\.venv\Scripts\waitress-serve.exe' --listen=0.0.0.0:8000 project_package.project.app:app
```

`run-runner.ps1`:
```powershell
$envPath = 'C:\omis\conf\omis.env'
Get-Content $envPath | ForEach-Object {
    if ($_ -match '^(?<key>[^#=]+)=(?<value>.+)$') {
        [System.Environment]::SetEnvironmentVariable($matches.key.Trim(), $matches.value.Trim())
    }
}
& 'C:\omis\.venv\Scripts\python.exe' -m project_package.runner --log-level INFO
```

## Шаг 6. Настройте фоновые задания
1. Откройте Планировщик задач и создайте задачу **OMIS Web UI**:
   - Триггер: «При входе» для сервисной учётной записи (или «При запуске системы», если политика допускает).
   - Действие: `powershell.exe -File C:\omis\scripts\start-web.ps1`.
   - Включите режим для Windows 10 или новее, запрашивайте наивысшие права. При необходимости добавьте правило остановки и перезапуска.
2. Создайте задачу **OMIS Runner**:
   - Триггер: «Ежедневно» с повторением каждые 10 минут без даты окончания.
   - Действие: `powershell.exe -File C:\omis\scripts\run-runner.ps1`.
   - Отметьте «Выполнять, даже если пользователь не вошёл в систему», чтобы раннер работал в фоне.
3. Запустите обе задачи вручную и проверьте вкладку «Журнал» на наличие ошибок.

## Шаг 7. Откройте порт в брандмауэре
1. Запустите PowerShell от имени администратора (если прав нет — обратитесь в ИТ или попросите выполнить шаг).
2. Разрешите входящие соединения к порту 8000 из внутренней сети:
   ```powershell
   New-NetFirewallRule -DisplayName "OMIS Web" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Domain
   ```
3. Зафиксируйте выбранный порт и сообщите его команде.

## Шаг 8. Контроль непрерывной работы
- Через 15 минут проверьте статус в Планировщике задач (`OMIS Runner > Последний результат выполнения`).
- Посмотрите «Просмотр событий > Журналы приложений и служб > Microsoft > Windows > TaskScheduler» на предмет сбоев.
- Откройте `http://<имя-станции>:8000/` (или `/health`, если реализовано) и убедитесь, что Flask-приложение отвечает.
- Анализируйте логи (добавьте `Out-File` или редирект в скриптах), чтобы вовремя замечать ошибки синхронизации Outlook.

## Шаг 9. Обслуживание
- Ежедневно делайте резервную копию `project_package\project\omis.sqlite3` и храните её на защищённом сетевом ресурсе.
- Периодически ротируйте секреты (client secret, токен Telegram) и обновляйте `conf\omis.env`.
- Раз в месяц обновляйте приложение:
  ```powershell
  cd C:\omis
  git pull --ff-only
  .\.venv\Scripts\Activate.ps1
  python -m pip install --upgrade flask requests python-dotenv exchangelib waitress
  ```
  После обновления перезапустите фоновые задачи или перезагрузите компьютер.
- Следите за свободным местом: SQLite растёт по мере накопления истории.

## Шаг 10. Передача коллегам
- Сообщите имя/адрес станции и порт, а также короткую инструкцию по работе с веб-интерфейсом.
- Сохраните файл с секретами (`conf\omis.env`) в корпоративном менеджере паролей, чтобы другой сотрудник смог восстановить конфигурацию.
- Храните копию этого документа и фиксируйте локальные изменения, сделанные при развёртывании.
