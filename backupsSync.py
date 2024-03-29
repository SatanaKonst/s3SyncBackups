# Скрипт работает через rclone
import sys
from os import unlink, getenv
from dotenv import load_dotenv
from pathlib import Path
import logging
import functions

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(filename=getenv('LOG_FILE'), encoding='utf-8', level=logging.DEBUG)

# Имя удаленного хранилища
REMOTE_NAME = getenv('REMOTE_NAME')

# Пути расположения бэкапов
BACKUP_CONTAINER_NAME = getenv('BACKUP_CONTAINER_NAME')
BACKUP_LOCAL_DIR = getenv('BACKUP_LOCAL_DIR')

# Кол-во хранимых бэкапов
BACKUP_SAVE_COUNT = int(getenv('BACKUP_SAVE_COUNT'))

localBackups = functions.getLocalBackups(BACKUP_LOCAL_DIR)
remoteBackups = functions.getRemoteBackups(REMOTE_NAME, BACKUP_CONTAINER_NAME)

if not remoteBackups:
    logger.info('Remote backups empty')
    if len(localBackups) > 0:
        logger.info('▶ Start upload backups')
        # Получим список локальных бэкапов
        for localBackupFileName in localBackups:
            uploadStatus = functions.uploadBackup(REMOTE_NAME, BACKUP_CONTAINER_NAME,
                                                  BACKUP_LOCAL_DIR + localBackupFileName)
            if uploadStatus == False:
                logger.error('❌ Error upload backup ' + localBackupFileName)
            else:
                logger.info('✅ Success upload ' + localBackupFileName)

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
                uploadStatus = functions.uploadBackup(REMOTE_NAME, BACKUP_CONTAINER_NAME,
                                                      BACKUP_LOCAL_DIR + uploadBackupFilePath)
                if uploadStatus == False:
                    logger.error("❌ Error upload backup: " + uploadBackupFilePath)
                else:
                    logger.info("✅ Success upload: " + uploadBackupFilePath)
                    # Удаляем локальный бэкап если указано в настройках
                    if getenv('REMOVE_LOCAL_BACKUP', 'N') == 'Y':
                        unlink(BACKUP_LOCAL_DIR + uploadBackupFilePath)
                        logger.info("🗑✅ Remove local backup: " + uploadBackupFilePath)

    # Чистим облако от старых бэкапов
    errors = functions.clearRemoteBackups(BACKUP_SAVE_COUNT, REMOTE_NAME, BACKUP_CONTAINER_NAME)
    if len(errors) > 0:
        logger.info("♻ Clear remote storage\n" + ", ".join(errors))

logger.info('⏹ End sync backups')
if getenv('SEND_TELEGRAM') == 'Y':
    sendResponse = functions.telegram_bot_sendtext(
        getenv('TELEGRAM_TOKEN'),
        getenv('TELEGRAM_BOT_CHAT_ID'),
        getenv('TELEGRAM_MESSAGE_HEADER') + "\n\n" +
        "🔆Complete Sync backups.\n\n" + Path(getenv('LOG_FILE')).read_text()
    )
    print(sendResponse)
    unlink(getenv('LOG_FILE'))

print('End sync backups')
