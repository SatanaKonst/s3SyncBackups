import hashlib
import json
import re
import subprocess
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
    filterRegex = "{}".format(getenv('SELECTED_BACKUP_REGEX', r"vzdump-qemu.*zst($|\n)"))
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
        command = 'rclone lsf ' + REMOTE_NAME + ':' + BACKUP_CONTAINER_NAME
        remoteBackupsTmp = subprocess.check_output(
            command,
            shell=True,
            executable="/bin/bash",
            stderr=subprocess.STDOUT
        )
        remoteBackupsTmp = remoteBackupsTmp.decode("utf-8").strip().split('\n')
        remoteBackups = []
        for backup in remoteBackupsTmp:
            file = re.findall(
                "{}".format(
                    getenv('SELECTED_BACKUP_REGEX', r"vzdump-qemu.*zst($|\n)")
                ),
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

    command = 'rclone -v' + transfers + bwLimit + logFile + ' copyto ' + originalFile + ' ' + remoteName + ':' + containerName + '/' + fileName

    try:
        p = subprocess.Popen(command, shell=True, executable="/bin/bash", stderr=subprocess.STDOUT)
        (output, err) = p.communicate()
        p_status = p.wait()
        return p_status == 0
    except subprocess.CalledProcessError as cpe:
        return False

    return False


def deleteBackup(remoteName, containerName, fileName):
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


# Отправка сообщения в телеграм
def telegram_bot_sendtext(bot_token, bot_chatID, bot_message):
    url = 'https://api.telegram.org/bot' + bot_token + '/sendMessage'
    headers = {'Content-type': 'application/json', 'Accept': 'text/plain'}
    data = {
        "chat_id": bot_chatID,
        "parse_mode": "Markdown",
        "text": bot_message
    }
    response = requests.post(
        url,
        data=json.dumps(data),
        headers=headers
    )
    return response.json()


# Проверка что скрипт уже запущен
def checkRunningScript(processName):
    p = subprocess.Popen('ps aux | grep ' + str(processName), shell=True, executable="/bin/bash",
                         stdout=subprocess.PIPE)
    (output, err) = p.communicate()
    p_status = p.wait()
    if p_status == 0:
        result = output.splitlines('\n')
        resultFilter = []
        for item in result:
            if str(item).find('grep') == -1:
                resultFilter.append(item)
        return len(resultFilter) > 1

    return False
