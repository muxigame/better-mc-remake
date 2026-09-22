import hashlib
import uuid


def uid_login_name(uid: int) -> str:
    if type(uid) is not int or not 10000 <= uid <= 9999999999999999:
        raise ValueError('Invalid Minecraft platform UID')
    return str(uid)


def offline_uuid(uid: int) -> str:
    digest = hashlib.md5(('OfflinePlayer:' + uid_login_name(uid)).encode('utf-8')).digest()
    return str(uuid.UUID(bytes=digest, version=3))
