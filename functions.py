import re
import subprocess
import requests
from os import walk, getenv


# Сгруппировать бэкапы по номеру виртуальной машины
def groupBackups(backuos):
    vmBackupGroups = dict()
    # Сгруппируем бэкапы по номеру виртуальной машины
    for remoteBackup in backuos:
        # Получим номер ВМ
        vmNumber = re.findall(r"-\d{3}-", remoteBackup)
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
    filterRegex = getenv('SELECTED_BACKUP_REGEX', 'vzdump-qemu.*zst');
    for backup in localBackupsTmp:
        file = re.findall(filterRegex, str(backup))
        if (len(file) > 0):
            localBackups.append(file[0])
    return localBackups


# Получить список бэкапов в облаке
def getRemoteBackups(REMOTE_NAME, BACKUP_CONTAINER_NAME):
    try:
        command = 'rclone ls ' + REMOTE_NAME + ':' + BACKUP_CONTAINER_NAME;
        remoteBackupsTmp = subprocess.check_output(
            command,
            shell=True,
            executable="/bin/bash",
            stderr=subprocess.STDOUT
        )
        remoteBackupsTmp = remoteBackupsTmp.strip().splitlines();
        remoteBackups = []
        for backup in remoteBackupsTmp:
            file = re.findall(getenv('SELECTED_BACKUP_REGEX', 'vzdump-qemu.*zst'), str(backup))
            if (len(file) > 0):
                remoteBackups.append(file[0])
    except subprocess.CalledProcessError as cpe:
        remoteBackups = []

    return remoteBackups


# Загрузить бэкап в облако
def uploadBackup(remoteName, containerName, filePath):
    command = 'rclone copy ' + filePath + ' ' + remoteName + ':' + containerName
    try:
        result = subprocess.check_call(command, shell=True, executable="/bin/bash", stderr=subprocess.STDOUT)
        if result == 0:
            return True
        else:
            return False
    except subprocess.CalledProcessError as cpe:
        return False

    return False


def deleteBackup(remoteName, containerName, fileName):
    command = 'rclone deletefile ' + remoteName + ':' + containerName + '/' + fileName
    try:
        result = subprocess.check_call(command, shell=True, executable="/bin/bash", stderr=subprocess.STDOUT)
        if result == 0:
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
                    errors.append('Error delete file ' + backupForRemove)

    return errors


def telegram_bot_sendtext(bot_token, bot_chatID, bot_message):
    send_text = 'https://api.telegram.org/bot' + bot_token + '/sendMessage?chat_id=' + bot_chatID + '&parse_mode=Markdown&text=' + bot_message
    response = requests.get(send_text)
    return response.json()
