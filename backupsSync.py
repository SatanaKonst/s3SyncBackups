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

    # Имя удаленного хранилища
    REMOTE_NAME = getenv('REMOTE_NAME')

    # Пути расположения бэкапов
    BACKUP_CONTAINER_NAME = getenv('BACKUP_CONTAINER_NAME')
    BACKUP_LOCAL_DIR = getenv('BACKUP_LOCAL_DIR')

    if not REMOTE_NAME or not BACKUP_CONTAINER_NAME or not BACKUP_LOCAL_DIR:
        print('Не заполнены настройки для выгрузки файлов')
        exit()

    # Кол-во хранимых бэкапов
    BACKUP_SAVE_COUNT = int(getenv('BACKUP_SAVE_COUNT', 1))
    if not BACKUP_SAVE_COUNT:
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
        localBackupsGroup = functions.groupBackups(localBackups)

        # Бежим по бэкапам локальным и смотрим есть ли в удаленных
        logger.info('▶ Start sync backups')
        for vmId in localBackupsGroup:
            if not vmId in remoteBackupsGroup:
                remoteBackupsGroup[vmId] = []

            localBackupsDiff = list(set(localBackupsGroup[vmId]) - set(remoteBackupsGroup[vmId]))

            # Загружаем бэкапы которых нет в облаке
            if len(localBackupsDiff) > 0:
                processUploads(localBackupsDiff)

        # Чистим облако от старых бэкапов
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

    # Отправляем сообщение в телеграмм (кроме dry-run и без заполненного токена)
    if not isDryRun and getenv('SEND_TELEGRAM') == 'Y' and getenv('TELEGRAM_TOKEN'):
        message = getenv('TELEGRAM_MESSAGE_HEADER') + "\n\n"
        if uploadedFiles:
            message += "✅ Uploaded:\n" + "\n".join(uploadedFiles) + "\n\n"
        if deletedFiles:
            message += "🗑 Deleted from bucket:\n" + "\n".join(deletedFiles) + "\n\n"
        if deleteErrors:
            message += "⚠️ Delete errors:\n" + "\n".join(deleteErrors)
        if uploadedFiles or deletedFiles or deleteErrors:
            functions.telegram_bot_sendtext(
                getenv('TELEGRAM_TOKEN'),
                getenv('TELEGRAM_BOT_CHAT_ID'),
                message
            )
        # Файлы в бакете (актуальное состояние после синка и чистки)
        try:
            remoteFiles = functions.getRemoteBackups(REMOTE_NAME, BACKUP_CONTAINER_NAME)
            bucketMessage = "📦 Backups in bucket:\n" + ("\n".join(remoteFiles) or "(пусто)")
        except RuntimeError as e:
            bucketMessage = "📦 Backups in bucket: ⚠️ ошибка получения списка\n" + str(e)
        functions.telegram_bot_sendtext(
            getenv('TELEGRAM_TOKEN'),
            getenv('TELEGRAM_BOT_CHAT_ID'),
            bucketMessage
        )
        unlink(LOG_FILE)

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
