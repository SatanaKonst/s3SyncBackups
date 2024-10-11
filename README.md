# Синхронизация бэкапов PROXMOX с s3 хранилищем

## ! Только для бэкапов PROXMOX

### Требуется Python 3

```
Формат наименованиия бэкапов 
vzdump-qemu-100-2024_02_30-15_42_45.vma.zst
```

Скрипт работает с использованием Rclone

Проверяет разницу между локальной дирректорией и удаленной.<br>
Если есть разница, то выгружает файлы.<br>
По окончанию загрузки проверяет удаленную папку на заданное кол-во копий и удаляет самую старую если кол-во превышает
заданый предел.

## Запуск

1. Скопировать env в .env
2. Заполнить переменные
3. Установить зависимости ```pip install -r requirements.txt```
4. Запустить ```python3.12 backupsSync.py```

# Параметры запуска
```
--dry-run - режим эмитации
```

# Переменные env
```
LOG_FILE - название файла лога
REMOTE_NAME - название подключения к s3
BACKUP_CONTAINER_NAME - название контейнера
BACKUP_LOCAL_DIR - путь до локальной дирректории с бэкапами
BACKUP_SAVE_COUNT - кол-во бэкапов в облаке
SELECTED_BACKUP_REGEX - регулярка для выборки бэкапов
GROUP_BACKUP_REGEX - регулярка для группировки (используется для расчета кол-ва бэкапов)
SEND_TELEGRAM - отправить сообщение в Telegramm
TELEGRAM_TOKEN - токен телеги
TELEGRAM_BOT_CHAT_ID - чат в телеге
TELEGRAM_MESSAGE_HEADER - Заголовок уведомлениия
ADD_NOTES_TO_BACKUP_NAME - добавляет md5 от файла .notes (для возможности синхронизировать бэкапы из разных расписаний)
BWLIMIT -  ограничение полосы канала
```
