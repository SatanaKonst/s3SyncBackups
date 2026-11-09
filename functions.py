import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests
from os import walk, getenv, path


# Проверка имени бэкапа по фильтру из настроек
def matchBackupName(name):
    regex = getenv('SELECTED_BACKUP_REGEX', r"vzdump-qemu.*zst(_notes:.+)?($|\n)")
    if not regex:
        regex = r"vzdump-qemu.*zst(_notes:.+)?($|\n)"
    return bool(re.search(regex, name))


# Сгруппировать бэкапы по номеру виртуальной машины
def groupBackups(backups):
    groupRegex = getenv('GROUP_BACKUP_REGEX', r"-\d{3}-") or r"-\d{3}-"
    vmBackupGroups = dict()
    for remoteBackup in backups:
        remoteBackup = remoteBackup.strip()
        vmNumber = re.search(groupRegex, remoteBackup)
        key = vmNumber.group(0).strip('-') if vmNumber else 'noVmFiles'
        vmBackupGroups.setdefault(key, []).append(remoteBackup)
    return vmBackupGroups


def getLocalBackups(dir):
    localBackupsTmp = next(walk(dir), (None, None, []))[2]  # [] if no file
    localBackups = []

    for backup in localBackupsTmp:
        if matchBackupName(backup):
            # берем файл описания
            backup = addNotesToBackupName(backup, dir)
            localBackups.append(backup)
    return localBackups


def addNotesToBackupName(backupName, dir):
    notesFilePath = dir + backupName + '.notes'
    if isAddNotesToBackupName() == True and path.isfile(notesFilePath):
        backupName = backupName + '_notes:' + md5(notesFilePath)
    return backupName


# Добавлять описание в название бэкапа
def isAddNotesToBackupName():
    return getenv('ADD_NOTES_TO_BACKUP_NAME', 'N') == 'Y'


# Получить md5 от файла
def md5(fname):
    hash_md5 = hashlib.md5()
    with open(fname, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


# Получить список бэкапов в облаке
def getRemoteBackups(REMOTE_NAME, BACKUP_CONTAINER_NAME):
    if BACKUP_CONTAINER_NAME[-1] != '/':
        BACKUP_CONTAINER_NAME += '/'

    try:
        remoteBackupsTmp = subprocess.check_output(
            ['rclone', 'lsf', REMOTE_NAME + ':' + BACKUP_CONTAINER_NAME],
            stderr=subprocess.STDOUT
        ).decode("utf-8").strip().split('\n')
    except subprocess.CalledProcessError as cpe:
        raise RuntimeError('rclone lsf failed: ' + cpe.output.decode('utf-8', 'replace').strip())

    remoteBackups = []
    for backup in remoteBackupsTmp:
        if matchBackupName(str(backup)):
            remoteBackups.append(str(backup))

    return remoteBackups


# Загрузить бэкап в облако
def uploadBackup(remoteName, containerName, filePath, logFile=''):
    if containerName[-1] != '/':
        containerName += '/'

    if isAddNotesToBackupName() == True:
        originalFile = re.sub(r"_notes.*", '', filePath)
    else:
        originalFile = filePath

    fileName = path.basename(filePath)

    command = ['rclone']
    bwLimit = getenv('BWLIMIT', '')
    if bwLimit:
        command += ['--bwlimit', bwLimit]

    # Кол-во потоков для загрузки
    transfers = getenv('RCLONE_TRANSFERS', getenv('TRANSFERS', ''))
    if transfers:
        command += ['--transfers', transfers]

    # Добавляем вывод в файл
    if logFile != '':
        command += ['--log-file', logFile]

    command += ['copyto', originalFile, remoteName + ':' + containerName + fileName]

    p = subprocess.Popen(command)
    return p.wait() == 0


def deleteBackup(remoteName, containerName, fileName):
    if containerName[-1] != '/':
        containerName += '/'
    p = subprocess.Popen(['rclone', 'deletefile', remoteName + ':' + containerName + fileName])
    return p.wait() == 0


# Очистить облако до кол-ва хранимых бэкапов
def clearRemoteBackups(BACKUP_SAVE_COUNT, REMOTE_NAME, BACKUP_CONTAINER_NAME):
    remoteBackups = getRemoteBackups(REMOTE_NAME, BACKUP_CONTAINER_NAME)
    remoteBackupsGroup = groupBackups(remoteBackups)
    deleted = []
    errors = []
    for vmId in remoteBackupsGroup:
        if (len(remoteBackupsGroup[vmId]) > BACKUP_SAVE_COUNT):
            backups = sorted(remoteBackupsGroup[vmId], reverse=True)
            backupsForRemove = backups[BACKUP_SAVE_COUNT::]
            for backupForRemove in backupsForRemove:
                result = deleteBackup(REMOTE_NAME, BACKUP_CONTAINER_NAME, backupForRemove)
                if result == False:
                    errors.append(backupForRemove)
                else:
                    deleted.append(backupForRemove)

    return deleted, errors


# Отправка сообщения в телеграм, длинные сообщения дробятся на части (лимит Telegram 4096 символов)
def telegram_bot_sendtext(bot_token, bot_chatID, bot_message):
    tgBaseUrl = getenv('TG_BASE_URL','https://api.telegram.org').rstrip('/')
    url = f'{tgBaseUrl}/bot{bot_token}/sendMessage'
    headers = {'Content-type': 'application/json', 'Accept': 'text/plain'}
    responses = []
    for i in range(0, len(bot_message), 4000):
        data = {
            "chat_id": bot_chatID,
            "text": bot_message[i:i + 4000]
        }
        try:
            response = requests.post(
                url,
                data=json.dumps(data),
                headers=headers,
                timeout=30
            )
            responses.append(response.json())
        except Exception as e:
            print('Telegram send error: ' + str(e))
    return responses


# Проверка что скрипт уже запущен (мёртвые lock-файлы убираем)
def checkRunningScript():
    lock_file = str(Path(__file__).parent.resolve()) + '/backup_sync.lock'
    if Path(lock_file).exists():
        running = False
        try:
            pid = int(Path(lock_file).read_text().strip())
            os.kill(pid, 0)
            running = True
        except ValueError:
            pass  # мусор в lock-файле
        except ProcessLookupError:
            pass  # процесс мёртв
        except PermissionError:
            running = True  # чужой живой процесс

        if running:
            print('Синхронизация уже работает')
            exit()
        os.unlink(lock_file)

    # Создаем файл блокировки
    with open(lock_file, 'w') as lock:
        lock.write(str(os.getpid()))  # записываем PID процесса


# Удалить логи старше N дней
def cleanupOldLogs(logDir, keepDays=14):
    prefix = getenv('LOG_FILE', 'backupsSync.log') + '_'
    cutoff = time.time() - keepDays * 86400
    for f in Path(logDir).glob(prefix + '*'):
        if f.stat().st_mtime < cutoff:
            f.unlink()


def unlockProcess():
    lock_file = str(Path(__file__).parent.resolve()) + '/backup_sync.lock'
    if (Path(lock_file).exists()):
        os.unlink(lock_file)
