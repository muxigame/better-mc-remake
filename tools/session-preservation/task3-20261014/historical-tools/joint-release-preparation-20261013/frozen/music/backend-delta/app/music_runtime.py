"""One explicit deployment owner injects an existing managed OSS Bucket/provider.

Routes are always present but uploads stay disabled until configure_storage is
called. This module never reads a cloud secret, constructs a RAM identity, or
changes cloud permissions.
"""
from threading import RLock
from .music_audio import AudioNormalizer
from .music_receipt import BurnReceipts
from .music_upload import MusicUploads


class MusicRuntime:
    def __init__(self, database, signer, issuer):
        self.lock = RLock()
        self.service = MusicUploads(database, None, normalizer=AudioNormalizer(),
                                    receipts=BurnReceipts(signer, issuer))

    def get(self):
        return self.service

    def configure_storage(self, storage):
        # Called only by a reviewed deployment provider; no HTTP setter.
        with self.lock:
            if self.service.storage is not None and self.service.storage is not storage:
                raise RuntimeError('music_storage_owner_exists')
            self.service.storage = storage
