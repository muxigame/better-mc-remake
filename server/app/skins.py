"""玩家自定义皮肤（原版 64×64 皮肤）。

游戏服务端是 offline-mode，原版客户端只认 Mojang 签过名、放在 Mojang 域名下的皮肤，
服务端往玩家档案里塞贴图行不通。所以整合包带 CustomSkinLoader（CSL），每个客户端
按玩家登录名（就是平台 UID）来这里取皮肤。接口按 CSL 的 CustomSkinAPI 约定：

    GET {root}{登录名}.json    -> {"username": ..., "skins": {"default"|"slim": <hash>}}
    GET {root}textures/{hash}  -> PNG

root 是 /api/v1/skins/csl/。整合包里 CSL 的 loadlist 只配这一个源——保留它默认的
Mojang 源的话，UID 叫 10000 的玩家会被套上正版同名账号的皮肤。

玩家通过启动器上传（带 muxi 账户 access token），一人一份，按 UID 存。
贴图重新组装成只含关键块的 PNG（丢掉文本、时间等元数据），按内容 SHA-256 命名。

没上传过的玩家一律是 Steve（经典手臂）。原版对离线玩家是按 UUID 在 9 个默认角色、
宽窄两种手臂里挑一个，看着像随机分配。Steve 的贴图取自 Minecraft 1.21.1 官方客户端 jar
（sha1 30c73b1c5da787909b2f73340419fdf13b9def88）里的
assets/minecraft/textures/entity/player/wide/steve.png，原样放在 assets/steve.png。

支持原版的全部皮肤格式：64×64（1.8 起，带第二层）、64×32（1.8 以前），经典/纤细两种手臂。
不收高清皮肤（128×128 起）：CSL 能渲染，但整合包里的 3D 皮肤层等模组按 64 像素取样。
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import struct
import threading
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MODELS = ("default", "slim")
MAX_BYTES = 64 * 1024
DEFAULT_SKIN = Path(__file__).with_name("assets") / "steve.png"
DEFAULT_MODEL = "default"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
# 游戏登录名就是平台 UID（见 game_identity.uid_login_name），其它名字一律没有皮肤
LOGIN_NAME_PATTERN = re.compile(r"^[0-9]{5,16}$")

# 颜色类型 -> 每像素通道数
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
# 只保留解码必需的块；tRNS 是调色板图的透明度，丢了透明部分会变成实色
_KEEP_CHUNKS = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"}


class SkinError(ValueError):
    """给玩家看的校验失败原因。"""


def normalize_png(data: bytes) -> bytes:
    """校验这是一张原版能用的皮肤，返回去掉元数据后的 PNG。"""
    if len(data) > MAX_BYTES:
        raise SkinError("皮肤文件太大（上限 64 KB）")
    if not data.startswith(PNG_SIGNATURE):
        raise SkinError("不是 PNG 图片")

    chunks: list[tuple[bytes, bytes]] = []
    offset = len(PNG_SIGNATURE)
    while offset < len(data):
        if offset + 8 > len(data):
            raise SkinError("PNG 文件不完整")
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        body = data[offset + 8:offset + 8 + length]
        crc = data[offset + 8 + length:offset + 12 + length]
        if len(body) != length or len(crc) != 4:
            raise SkinError("PNG 文件不完整")
        if struct.unpack(">I", crc)[0] != zlib.crc32(kind + body) & 0xFFFFFFFF:
            raise SkinError("PNG 文件已损坏")
        chunks.append((kind, body))
        offset += 12 + length
        if kind == b"IEND":
            break
    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise SkinError("PNG 文件已损坏")
    if chunks[-1][0] != b"IEND":
        raise SkinError("PNG 文件不完整")

    width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    if width != 64 or height not in (64, 32):
        raise SkinError(f"皮肤尺寸必须是 64×64（或旧版 64×32），这张是 {width}×{height}")
    if color not in _CHANNELS or depth not in (1, 2, 4, 8, 16) or compression or filtering:
        raise SkinError("不支持这种 PNG 格式，用画图工具另存为普通 PNG 再试")
    if interlace:
        raise SkinError("不支持隔行扫描的 PNG，用画图工具另存为普通 PNG 再试")

    # 解压一遍确认像素数据完整。按理论大小封顶，压缩炸弹解不出更多东西。
    expected = height * (1 + (width * _CHANNELS[color] * depth + 7) // 8)
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"), expected + 1)
    except zlib.error as error:
        raise SkinError("PNG 像素数据已损坏") from error
    if len(raw) != expected or not inflater.eof:
        raise SkinError("PNG 像素数据已损坏")

    out = bytearray(PNG_SIGNATURE)
    for kind, body in chunks:
        if kind in _KEEP_CHUNKS:
            out += struct.pack(">I", len(body)) + kind + body
            out += struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return bytes(out)


class SkinStore:
    def __init__(self, database: Path, textures: Path, default_skin: Path = DEFAULT_SKIN):
        self.database = database
        self.textures = textures
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.textures.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # 默认皮肤也放进贴图目录，CSL 按同一个 textures/{hash} 地址取
        self.default_hash = self._store_texture(normalize_png(default_skin.read_bytes()))
        with self._lock, self.connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS player_skins (
                    uid INTEGER PRIMARY KEY,
                    hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def get(self, uid: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT hash, model, updated_at FROM player_skins WHERE uid=?", (uid,)).fetchone()
        if row is None:
            return None
        return {"hash": str(row["hash"]), "model": str(row["model"]), "updatedAt": int(row["updated_at"])}

    def texture_path(self, digest: str) -> Path | None:
        if not HASH_PATTERN.match(digest):
            return None
        path = self.textures / f"{digest}.png"
        return path if path.is_file() else None

    def texture(self, digest: str) -> bytes | None:
        path = self.texture_path(digest)
        return path.read_bytes() if path else None

    def default(self) -> dict:
        return {"hash": self.default_hash, "model": DEFAULT_MODEL}

    def _store_texture(self, clean: bytes) -> str:
        digest = hashlib.sha256(clean).hexdigest()
        target = self.textures / f"{digest}.png"
        if not target.is_file():
            temp = target.with_suffix(".part")
            temp.write_bytes(clean)
            temp.replace(target)
        return digest

    def save(self, uid: int, model: str, png: bytes | None) -> dict:
        if model not in MODELS:
            raise SkinError("模型只能是 default（经典）或 slim（纤细）")
        with self._lock:
            current = self.get(uid)
            if png is None:
                if current is None:
                    raise SkinError("还没有上传皮肤")
                digest = current["hash"]
            else:
                digest = self._store_texture(normalize_png(png))
            with self.connect() as db:
                db.execute(
                    """
                    INSERT INTO player_skins (uid, hash, model, updated_at) VALUES (?, ?, ?, ?)
                    ON CONFLICT(uid) DO UPDATE SET hash=excluded.hash, model=excluded.model,
                                                  updated_at=excluded.updated_at
                    """,
                    (uid, digest, model, int(time.time())),
                )
                if current is not None and current["hash"] != digest:
                    self._drop_if_unused(db, current["hash"])
        return self.get(uid) or {}

    def delete(self, uid: int) -> None:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT hash FROM player_skins WHERE uid=?", (uid,)).fetchone()
            db.execute("DELETE FROM player_skins WHERE uid=?", (uid,))
            if row is not None:
                self._drop_if_unused(db, str(row["hash"]))

    def _drop_if_unused(self, db: sqlite3.Connection, digest: str) -> None:
        # 贴图按内容命名，可能几个人共用一张，没人用了才删。不删的话反复上传能把盘填满；
        # 删了之后磁盘占用封顶在"玩家数 × 64 KB"。默认皮肤永远留着。
        if digest == self.default_hash:
            return
        if db.execute("SELECT 1 FROM player_skins WHERE hash=? LIMIT 1", (digest,)).fetchone() is None:
            (self.textures / f"{digest}.png").unlink(missing_ok=True)

    def csl_profile(self, login_name: str) -> dict | None:
        # 不是 UID 的名字（NPC 之类）不归我们管，返回 None 让 CSL 用原版的默认皮肤
        if not LOGIN_NAME_PATTERN.match(login_name):
            return None
        skin = self.get(int(login_name)) or self.default()
        return {"username": login_name, "skins": {skin["model"]: skin["hash"]}}
