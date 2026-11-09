import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import requests
from os import walk, getenv, path


# Сгруппировать бэкапы по номеру виртуальной машины
def groupBackups(backups):
    vmBackupGroups = dict()
    # Сгруппируем бэкапы по номеру виртуальной машины
    for remoteBackup in backups:
        remoteBackup = remoteBackup.strip()
        # Получим номер ВМ
        groupRegex = getenv('GROUP_BACKUP_REGEX', r"-\d{3}-")
        if not groupRegex:
            groupRegex = r"-\d{3}-"

        groupRegex = "{}".format(groupRegex)
        vmNumber = re.findall(groupRegex, remoteBackup)
        if len(vmNumber) > 0:
            vmNumber = vmNumber[0].strip('-')
            if not str(vmNumber) in vmBackupGroups:
                vmBackupGroups[str(vmNumber)] = []
                vmBackupGroups[str(vmNumber)].append(remoteBackup)
            else:
                vmBackupGroups[str(vmNumber)].append(remoteBackup)
        else:
            if not 'noVmFiles' in vmBackupGroups:
                vmBackupGroups['noVmFiles'] = []
                vmBackupGroups['noVmFiles'].append(remoteBackup)
            else:
                vmBackupGroups['noVmFiles'].append(remoteBackup)

    return vmBackupGroups


def getLocalBackups(dir):
    localBackupsTmp = next(walk(dir), (None, None, []))[2]  # [] if no file
    localBackups = []

    regex = getenv('SELECTED_BACKUP_REGEX', r"vzdump-qemu.*zst($|\n)")
    if not regex:
        regex = r"vzdump-qemu.*zst($|\n)"

    filterRegex = "{}".format(regex)
    for backup in localBackupsTmp:
        file = re.findall(filterRegex, str(backup))
        if (len(file) > 0):
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
    try:
        if BACKUP_CONTAINER_NAME[-1] != '/':
            BACKUP_CONTAINER_NAME += '/'

        command = 'rclone lsf ' + REMOTE_NAME + ':' + BACKUP_CONTAINER_NAME
        remoteBackupsTmp = subprocess.check_output(
            command,
            shell=True,
            executable="/bin/bash",
            stderr=subprocess.STDOUT
        )
        remoteBackupsTmp = remoteBackupsTmp.decode("utf-8").strip().split('\n')
        remoteBackups = []
        regex = getenv('SELECTED_BACKUP_REGEX', r"vzdump-qemu.*zst(_notes:.+)?($|\n)")
        if not regex:
            regex =  r"vzdump-qemu.*zst(_notes:.+)?($|\n)"

        for backup in remoteBackupsTmp:
            file = re.findall(
                "{}".format(regex),
                str(backup)
            )
            if (len(file) > 0):
                remoteBackups.append(str(backup))
    except subprocess.CalledProcessError as cpe:
        print(cpe)
        remoteBackups = []

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

    bwLimit = getenv('BWLIMIT', '')
    if bwLimit != '':
        bwLimit = ' --bwlimit ' + bwLimit

    # Кол-во потоков для загрузки
    transfers = getenv('TRANSFERS', '')
    if transfers:
        transfers = ' --transfers ' + str(transfers)

    # Добавляем вывод в файл
    if logFile != '':
        logFile = ' --log-file ' + logFile

    command = 'rclone -v' + transfers + bwLimit + logFile + ' copyto ' + originalFile + ' ' + remoteName + ':' + containerName + fileName

    try:
        p = subprocess.Popen(command, shell=True, executable="/bin/bash", stderr=subprocess.STDOUT)
        (output, err) = p.communicate()
        p_status = p.wait()
        return p_status == 0
    except subprocess.CalledProcessError as cpe:
        return False

    return False


def deleteBackup(remoteName, containerName, fileName):
    if containerName[-1] != '/':
        containerName += '/'
    command = str('rclone deletefile ' + remoteName + ':' + containerName + fileName)
    try:
        p = subprocess.Popen(command, shell=True, executable="/bin/bash", stderr=subprocess.STDOUT)
        (output, err) = p.communicate()
        p_status = p.wait()
        if p_status == 0:
            return True
        else:
            return False
    except subprocess.CalledProcessError as cpe:
        return False

    return False


# Очистить облако до кол-ва хранимых бэкапов
def clearRemoteBackups(BACKUP_SAVE_COUNT, REMOTE_NAME, BACKUP_CONTAINER_NAME):
    remoteBackups = getRemoteBackups(REMOTE_NAME, BACKUP_CONTAINER_NAME)
    remoteBackupsGroup = groupBackups(remoteBackups)
    errors = []
    for vmId in remoteBackupsGroup:
        if (len(remoteBackupsGroup[vmId]) > BACKUP_SAVE_COUNT):
            backups = sorted(remoteBackupsGroup[vmId], reverse=True)
            backupsForRemove = backups[BACKUP_SAVE_COUNT::]
            for backupForRemove in backupsForRemove:
                result = deleteBackup(REMOTE_NAME, BACKUP_CONTAINER_NAME, backupForRemove)
                if result == False:
                    errors.append('🗑❗Error delete file ' + backupForRemove)
                else:
                    errors.append('🗑✅Success remove remote backup ' + backupForRemove)

    return errors


# Отправка сообщения в телеграм, длинные сообщения дробятся на части (лимит Telegram 4096 символов)
def telegram_bot_sendtext(bot_token, bot_chatID, bot_message):
    tgBaseUrl = getenv('TG_BASE_URL','https://api.telegram.org').rstrip('/')
    url = f'{tgBaseUrl}/bot{bot_token}/sendMessage'
    headers = {'Content-type': 'application/json', 'Accept': 'text/plain'}
    responses = []
    for i in range(0, len(bot_message), 4000):
        data = {
            "chat_id": bot_chatID,
            "parse_mode": "Markdown",
            "text": bot_message[i:i + 4000]
        }
        response = requests.post(
            url,
            data=json.dumps(data),
            headers=headers
        )
        responses.append(response.json())
    return responses


# Проверка что скрипт уже запущен
def checkRunningScript():
    lock_file = str(Path(__file__).parent.resolve()) + '/backup_sync.lock'
    # Проверяем, существует ли файл блокировки
    if Path(lock_file).exists():
        print('Синхронизация уже работает')
        exit()

    # Создаем файл блокировки
    with open(lock_file, 'w') as lock:
        lock.write(str(os.getpid()))  # записываем PID процесса


def unlockProcess():
    lock_file = str(Path(__file__).parent.resolve()) + '/backup_sync.lock'
    if (Path(lock_file).exists()):
        os.unlink(lock_file)
