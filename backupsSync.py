# Скрипт работает через rclone
import sys
import subprocess
from os import unlink, getenv, mkdir
from dotenv import load_dotenv
from pathlib import Path
import re
import datetime
import logging
import functions

# Запуск в фоне: скрипт перезапускает себя отсоединённым процессом и сразу завершается
if '--background' in sys.argv:
    args = [a for a in sys.argv[1:] if a != '--background']
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve())] + args,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    print('Запущено в фоне, результат придёт в Telegram')
    exit()

try:
    # Проверим что скрипт не работает
    functions.checkRunningScript()

    load_dotenv()

    LOG_DIR = str(Path(__file__).parent.resolve()) + '/logs/'
    if not Path(LOG_DIR).exists():
        mkdir(LOG_DIR)

    currentTime = str(datetime.datetime.now()).replace(' ', '_')
    LOG_FILE = LOG_DIR + getenv('LOG_FILE', 'backupsSync.log') + '_' + currentTime
    functions.cleanupOldLogs(LOG_DIR)

    logger = logging.getLogger(__name__)
    logging.basicConfig(filename=LOG_FILE, encoding='utf-8', level=logging.INFO)

    # Проверям на режим имитации
    isDryRun = '--dry-run' in sys.argv
    # --dump-tg: сообщение для телеграма пишем в файл вместо отправки
    isDumpTg = '--dump-tg' in sys.argv

    # Отправка в телеграм или сброс в файл логов (--dump-tg)
    def sendToTelegram(message):
        if isDumpTg:
            with open(LOG_DIR + 'telegram_' + currentTime + '.txt', 'a', encoding='utf-8') as dumpFile:
                dumpFile.write(message + '\n')
        else:
            functions.telegram_bot_sendtext(
                getenv('TELEGRAM_TOKEN'),
                getenv('TELEGRAM_BOT_CHAT_ID'),
                message
            )

    # Имя удаленного хранилища
    REMOTE_NAME = getenv('REMOTE_NAME')

    # Пути расположения бэкапов
    BACKUP_CONTAINER_NAME = getenv('BACKUP_CONTAINER_NAME')
    BACKUP_LOCAL_DIR = getenv('BACKUP_LOCAL_DIR')

    # Обязательные настройки: пустые или из пробелов считаем незаполненными
    missing = [
        key for key, value in {
            'REMOTE_NAME': REMOTE_NAME,
            'BACKUP_CONTAINER_NAME': BACKUP_CONTAINER_NAME,
            'BACKUP_LOCAL_DIR': BACKUP_LOCAL_DIR,
        }.items() if not value or not value.strip()
    ]
    if missing:
        print('Не заполнены настройки для выгрузки файлов: ' + ', '.join(missing))
        exit()

    # Склеиваем пути конкатенацией — нормализуем слэш на конце
    BACKUP_LOCAL_DIR = BACKUP_LOCAL_DIR.strip().rstrip('/') + '/'
    if not Path(BACKUP_LOCAL_DIR).is_dir():
        print('Директория бэкапов не существует: ' + BACKUP_LOCAL_DIR)
        exit()

    # Регексы должны компилироваться, иначе падение в середине работы
    try:
        for envKey in ('SELECTED_BACKUP_REGEX', 'GROUP_BACKUP_REGEX'):
            re.compile(getenv(envKey) or '')
    except re.error as e:
        print('Некорректный регекс в настройках: ' + str(e))
        exit()

    # Кол-во хранимых бэкапов
    try:
        BACKUP_SAVE_COUNT = int(getenv('BACKUP_SAVE_COUNT') or 1)
    except ValueError:
        print('BACKUP_SAVE_COUNT должно быть числом')
        exit()
    if BACKUP_SAVE_COUNT < 1:
        BACKUP_SAVE_COUNT = 1

    uploadedFiles = []
    deletedFiles = []
    deleteErrors = []

    # Загрузка одного бэкапа (в dry-run — имитация успеха)
    def uploadOne(fileName):
        if isDryRun:
            return True
        return functions.uploadBackup(
            REMOTE_NAME,
            BACKUP_CONTAINER_NAME,
            BACKUP_LOCAL_DIR + fileName,
            LOG_FILE
        )

    # Загрузка списка бэкапов: лог, отчёт, опциональное удаление локальной копии
    def processUploads(files):
        for fileName in files:
            if not uploadOne(fileName):
                logger.error('❌ Error upload backup: ' + fileName)
                continue
            logger.info('✅ Success upload: ' + fileName)
            uploadedFiles.append(fileName)
            # Удаляем локальный бэкап если указано в настройках
            if getenv('REMOVE_LOCAL_BACKUP', 'N') == 'Y' and not isDryRun:
                localName = re.sub(r"_notes.*", '', fileName) if functions.isAddNotesToBackupName() else fileName
                unlink(BACKUP_LOCAL_DIR + localName)
                logger.info("🗑✅ Remove local backup: " + localName)

    localBackups = functions.getLocalBackups(BACKUP_LOCAL_DIR)

    # Локальные бэкапы старше лимита не грузим: иначе карусель upload->delete на каждом прогоне
    localBackupsGroup = functions.groupBackups(localBackups)
    localBackupsGroup = {
        vmId: sorted(files, key=functions.backupDateKey, reverse=True)[:BACKUP_SAVE_COUNT]
        for vmId, files in localBackupsGroup.items()
    }
    localBackups = [b for files in localBackupsGroup.values() for b in files]

    remoteBackups = functions.getRemoteBackups(REMOTE_NAME, BACKUP_CONTAINER_NAME)

    if len(remoteBackups) == 0:
        logger.info('Remote backups empty')
        if len(localBackups) > 0:
            logger.info('▶ Start upload backups')
            processUploads(localBackups)
        else:
            logger.info('Local backups empty')
    else:
        remoteBackupsGroup = functions.groupBackups(remoteBackups)

        # Бежим по бэкапам локальным и смотрим есть ли в удаленных
        logger.info('▶ Start sync backups')
        for vmId in localBackupsGroup:
            if not vmId in remoteBackupsGroup:
                remoteBackupsGroup[vmId] = []

            # Окно ретеншена считаем по объединению локальных и облачных:
            # иначе локальный хвост вне лимита вечно ре-аплоадится и удаляется
            candidates = remoteBackupsGroup[vmId] + localBackupsGroup[vmId]
            keepSet = {
                functions.normalizeBackupName(b)
                for b in sorted(candidates, key=functions.backupDateKey, reverse=True)[:BACKUP_SAVE_COUNT]
            }

            # Сравниваем по нормализованным именам: дубли с _ и без не грузятся повторно
            remoteSet = {functions.normalizeBackupName(b) for b in remoteBackupsGroup[vmId]}
            localBackupsDiff = [
                b for b in localBackupsGroup[vmId]
                if functions.normalizeBackupName(b) in keepSet
                and functions.normalizeBackupName(b) not in remoteSet
            ]

            # Загружаем бэкапы которых нет в облаке
            if len(localBackupsDiff) > 0:
                processUploads(localBackupsDiff)

    # Чистим облако от старых бэкапов (после любой загрузки, иначе первый прогон нарушает лимит)
    if not isDryRun:
        deletedFiles, deleteErrors = functions.clearRemoteBackups(BACKUP_SAVE_COUNT, REMOTE_NAME, BACKUP_CONTAINER_NAME)
        if deletedFiles or deleteErrors:
            logger.info("\n♻ Clear remote storage\n" + "\n".join(deletedFiles + deleteErrors))

    logger.info('⏹ End sync backups')

    # Печатаем лог если режим эмитации
    if isDryRun:
        print(
            Path(LOG_FILE).read_text()
        )

    # Отправляем сообщение в телеграмм (кроме dry-run; --dump-tg пишет в файл, токен не нужен)
    if not isDryRun and (isDumpTg or (getenv('SEND_TELEGRAM') == 'Y' and getenv('TELEGRAM_TOKEN'))):
        message = getenv('TELEGRAM_MESSAGE_HEADER') + "\n\n"
        if uploadedFiles:
            message += "✅ Uploaded:\n" + "\n".join(uploadedFiles) + "\n\n"
        if deletedFiles:
            message += "🗑 Deleted from bucket:\n" + "\n".join(deletedFiles) + "\n\n"
        if deleteErrors:
            message += "⚠️ Delete errors:\n" + "\n".join(deleteErrors)
        if uploadedFiles or deletedFiles or deleteErrors:
            sendToTelegram(message)
        # Файлы в бакете (актуальное состояние после синка и чистки)
        try:
            remoteFiles = functions.getRemoteBackups(REMOTE_NAME, BACKUP_CONTAINER_NAME)
            bucketMessage = "📦 Backups in bucket:\n" + ("\n".join(remoteFiles) or "(пусто)")
        except RuntimeError as e:
            bucketMessage = "📦 Backups in bucket: ⚠️ ошибка получения списка\n" + str(e)
        sendToTelegram(bucketMessage)

    print('End sync backups')
except Exception as e:
    # Ошибка в лог-файл (важно для фонового запуска, где stderr потерян)
    try:
        logging.getLogger(__name__).exception('Fatal: %s', e)
    except Exception:
        print('Fatal: ' + str(e))
    raise
finally:
    functions.unlockProcess()
