# Скрипт работает через rclone
import sys
import time
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

    logger = logging.getLogger(__name__)
    logging.basicConfig(filename=LOG_FILE, encoding='utf-8', level=logging.DEBUG)

    # Проверям на режим имитации
    isDryRun = False
    if len(sys.argv) > 1:
        isDryRun = sys.argv[1] == '--dry-run'

    # Имя удаленного хранилища
    REMOTE_NAME = getenv('REMOTE_NAME')

    # Пути расположения бэкапов
    BACKUP_CONTAINER_NAME = getenv('BACKUP_CONTAINER_NAME')
    BACKUP_LOCAL_DIR = getenv('BACKUP_LOCAL_DIR')

    if not REMOTE_NAME or not BACKUP_CONTAINER_NAME or not BACKUP_LOCAL_DIR:
        print('Не заполнены настройки для выгрузки файлов')
        exit()

    # Кол-во хранимых бэкапов
    BACKUP_SAVE_COUNT = int(getenv('BACKUP_SAVE_COUNT'))
    if not BACKUP_SAVE_COUNT:
        BACKUP_SAVE_COUNT = 1

    localBackups = functions.getLocalBackups(BACKUP_LOCAL_DIR)
    remoteBackups = functions.getRemoteBackups(REMOTE_NAME, BACKUP_CONTAINER_NAME)

    if len(remoteBackups) == 0:
        logger.info('Remote backups empty')
        if len(localBackups) > 0:
            logger.info('▶ Start upload backups')
            # Получим список локальных бэкапов
            for localBackupFileName in localBackups:
                if not isDryRun:
                    uploadStatus = functions.uploadBackup(
                        REMOTE_NAME,
                        BACKUP_CONTAINER_NAME,
                        BACKUP_LOCAL_DIR + localBackupFileName,
                        LOG_FILE
                    )
                else:
                    uploadStatus = True

                if not uploadStatus:
                    logger.error('❌ Error upload backup ' + localBackupFileName)
                else:
                    logger.info('✅ Success upload ' + localBackupFileName)
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
                for uploadBackupFilePath in localBackupsDiff:
                    if not isDryRun:
                        uploadStatus = functions.uploadBackup(
                            REMOTE_NAME,
                            BACKUP_CONTAINER_NAME,
                            BACKUP_LOCAL_DIR + uploadBackupFilePath,
                            LOG_FILE
                        )
                    else:
                        uploadStatus = True

                    if not uploadStatus:
                        logger.error("❌ Error upload backup: " + uploadBackupFilePath)
                    else:
                        logger.info("✅ Success upload: " + uploadBackupFilePath)
                        # Удаляем локальный бэкап если указано в настройках
                        if getenv('REMOVE_LOCAL_BACKUP', 'N') == 'Y':
                            if functions.isAddNotesToBackupName():
                                uploadBackupFilePath = re.sub(r"_notes.*", '', uploadBackupFilePath)

                            if not isDryRun:
                                unlink(BACKUP_LOCAL_DIR + uploadBackupFilePath)
                                logger.info("🗑✅ Remove local backup: " + uploadBackupFilePath)

        # Чистим облако от старых бэкапов
        if not isDryRun:
            errors = functions.clearRemoteBackups(BACKUP_SAVE_COUNT, REMOTE_NAME, BACKUP_CONTAINER_NAME)
            if len(errors) > 0:
                logger.info("\n♻ Clear remote storage\n" + "\n".join(errors))

    logger.info('⏹ End sync backups')

    # Печатаем лог если режим эмитации
    if isDryRun:
        print(
            Path(LOG_FILE).read_text()
        )

    # Отправляем сообщение в телеграмм (кроме dry-run и без заполненного токена)
    if not isDryRun and getenv('SEND_TELEGRAM') == 'Y' and getenv('TELEGRAM_TOKEN'):
        telegramArgs = (
            getenv('TELEGRAM_TOKEN'),
            getenv('TELEGRAM_BOT_CHAT_ID'),
        )
        # 1. Информация о синхронизации (лог: загрузки, удаления)
        functions.telegram_bot_sendtext(
            *telegramArgs,
            getenv('TELEGRAM_MESSAGE_HEADER') + "\n\n" +
            "🔆Complete Sync backups.\n\n" +
            Path(LOG_FILE).read_text()
        )
        # 2. Файлы в бакете (актуальное состояние после синка и чистки)
        remoteFiles = functions.getRemoteBackups(REMOTE_NAME, BACKUP_CONTAINER_NAME)
        functions.telegram_bot_sendtext(
            *telegramArgs,
            "📦 Backups in bucket:\n" + "\n".join(remoteFiles)
        )
        unlink(LOG_FILE)

    print('End sync backups')
finally:
    functions.unlockProcess()
