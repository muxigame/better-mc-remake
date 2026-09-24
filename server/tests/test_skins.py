"""自定义皮肤：PNG 校验、玩家上传接口、CustomSkinLoader 公开接口。"""
import base64
import hashlib
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.oidc import WebsiteAccount
from app.skins import MAX_BYTES, SkinError, SkinStore, normalize_png


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def png(width=64, height=64, extra=(), rows=None, color=6, interlace=0) -> bytes:
    channels = {0: 1, 2: 3, 6: 4}[color]
    raw = rows if rows is not None else b"".join(
        b"\x00" + bytes((x * 4 + y) % 256 for x in range(width * channels)) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, interlace))
            + b"".join(extra)
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


class NormalizePngTests(unittest.TestCase):
    def test_accepts_modern_and_legacy_sizes(self):
        self.assertTrue(normalize_png(png()).startswith(b"\x89PNG"))
        self.assertTrue(normalize_png(png(height=32)).startswith(b"\x89PNG"))

    def test_strips_metadata_so_same_pixels_dedupe(self):
        tagged = png(extra=[chunk(b"tEXt", b"Author\x00someone"), chunk(b"tIME", b"\x07\xea\x09\x18\x00\x00\x00")])
        self.assertEqual(normalize_png(png()), normalize_png(tagged))
        self.assertNotIn(b"someone", normalize_png(tagged))

    def test_rejects_wrong_size(self):
        with self.assertRaisesRegex(SkinError, "64×64"):
            normalize_png(png(width=128, height=128))

    def test_rejects_non_png_and_corruption(self):
        with self.assertRaises(SkinError):
            normalize_png(b"GIF89a" + b"\x00" * 64)
        broken = bytearray(png())
        broken[40] ^= 0xFF
        with self.assertRaises(SkinError):
            normalize_png(bytes(broken))
        with self.assertRaises(SkinError):
            normalize_png(png()[:-12])

    def test_rejects_short_or_bomb_pixel_data(self):
        with self.assertRaises(SkinError):
            normalize_png(png(rows=b"\x00" * 100))
        # 解压后远大于 64×64 应有的量：封顶解压，不会真去解几十 MB
        with self.assertRaises(SkinError):
            normalize_png(png(rows=b"\x00" * (20 * 1024 * 1024)))

    def test_rejects_interlaced_and_oversized_files(self):
        with self.assertRaises(SkinError):
            normalize_png(png(interlace=1))
        with self.assertRaisesRegex(SkinError, "64 KB"):
            normalize_png(png() + b"\x00" * MAX_BYTES)


class SkinApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bmc-skins-")
        self.store = SkinStore(Path(self.tmp.name) / "bmc.db", Path(self.tmp.name) / "skins")
        patcher = patch.object(main, "skin_store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.account = WebsiteAccount(subject="sub-10000", uid=10000, username="MuxiUser", nickname="洛可",
                                      game_name="10000", email="user@example.com", email_verified=True,
                                      role="player")
        userinfo = patch.object(main.oidc_client, "userinfo", side_effect=self.userinfo)
        userinfo.start()
        self.addCleanup(userinfo.stop)
        self.client = TestClient(main.app)
        self.auth = {"Authorization": "Bearer good-token"}

    def tearDown(self):
        self.tmp.cleanup()

    def userinfo(self, token):
        if token != "good-token":
            raise RuntimeError("bad token")
        return self.account

    def test_requires_login(self):
        self.assertEqual(401, self.client.get("/api/v1/player/skin").status_code)
        self.assertEqual(401, self.client.put("/api/v1/player/skin", json={"model": "slim"},
                                              headers={"Authorization": "Bearer nope"}).status_code)

    def test_players_without_upload_are_steve(self):
        steve = (Path(main.__file__).parent / "assets" / "steve.png").read_bytes()
        empty = self.client.get("/api/v1/player/skin", headers=self.auth).json()
        self.assertIsNone(empty["skin"])
        self.assertEqual("default", empty["default"]["model"])
        self.assertEqual(normalize_png(steve), base64.b64decode(empty["default"]["png"]))
        profile = self.client.get("/api/v1/skins/csl/10002.json").json()
        self.assertEqual({"username": "10002", "skins": {"default": empty["default"]["hash"]}}, profile)
        texture = self.client.get("/api/v1/skins/csl/textures/" + empty["default"]["hash"])
        self.assertEqual(200, texture.status_code)

    def test_default_texture_survives_cleanup(self):
        steve = (Path(main.__file__).parent / "assets" / "steve.png").read_bytes()
        # 有人上传了一模一样的 Steve，再恢复默认：不能顺手把默认贴图删了
        saved = self.store.save(10000, "default", steve)
        self.assertEqual(self.store.default_hash, saved["hash"])
        self.store.delete(10000)
        self.assertIsNotNone(self.store.texture_path(self.store.default_hash))

    def test_upload_switch_model_and_reset(self):
        self.assertIsNone(self.client.get("/api/v1/player/skin", headers=self.auth).json()["skin"])
        encoded = base64.b64encode(png()).decode()
        saved = self.client.put("/api/v1/player/skin", json={"model": "slim", "png": encoded}, headers=self.auth)
        self.assertEqual(200, saved.status_code, saved.text)
        skin = saved.json()["skin"]
        self.assertEqual("slim", skin["model"])
        self.assertEqual(hashlib.sha256(base64.b64decode(skin["png"])).hexdigest(), skin["hash"])
        self.assertIn("no-store", saved.headers["cache-control"])

        # 只改模型不重传图片
        switched = self.client.put("/api/v1/player/skin", json={"model": "default"}, headers=self.auth).json()
        self.assertEqual(("default", skin["hash"]), (switched["skin"]["model"], switched["skin"]["hash"]))

        profile = self.client.get("/api/v1/skins/csl/10000.json")
        self.assertEqual({"username": "10000", "skins": {"default": skin["hash"]}}, profile.json())
        texture = self.client.get(f"/api/v1/skins/csl/textures/{skin['hash']}")
        self.assertEqual(200, texture.status_code)
        self.assertEqual("image/png", texture.headers["content-type"])
        self.assertEqual(base64.b64decode(skin["png"]), texture.content)
        self.assertIn("immutable", texture.headers["cache-control"])

        reset = self.client.delete("/api/v1/player/skin", headers=self.auth).json()
        self.assertIsNone(reset["skin"])
        self.assertEqual({"username": "10000", "skins": {"default": self.store.default_hash}},
                         self.client.get("/api/v1/skins/csl/10000.json").json())

    def test_replaced_textures_are_removed_unless_shared(self):
        first = self.store.save(10000, "default", png())
        shared = self.store.save(10001, "default", png())
        self.assertEqual(first["hash"], shared["hash"])
        second = self.store.save(10000, "slim", png(height=32))
        # 10001 还在用第一张，不能删
        self.assertIsNotNone(self.store.texture_path(first["hash"]))
        self.store.delete(10001)
        self.assertIsNone(self.store.texture_path(first["hash"]))
        self.store.delete(10000)
        self.assertIsNone(self.store.texture_path(second["hash"]))
        self.assertEqual([self.store.default_hash + ".png"], [p.name for p in self.store.textures.iterdir()])

    def test_data_url_prefix_is_accepted(self):
        encoded = "data:image/png;base64," + base64.b64encode(png(height=32)).decode()
        saved = self.client.put("/api/v1/player/skin", json={"model": "default", "png": encoded}, headers=self.auth)
        self.assertEqual(200, saved.status_code, saved.text)

    def test_rejections_carry_a_readable_reason(self):
        bad = self.client.put("/api/v1/player/skin", json={"model": "default",
                                                           "png": base64.b64encode(png(width=32, height=32)).decode()},
                              headers=self.auth)
        self.assertEqual(400, bad.status_code)
        self.assertIn("64×64", bad.json()["detail"])
        self.assertEqual(400, self.client.put("/api/v1/player/skin", json={"model": "giant", "png": base64.b64encode(png()).decode()},
                                              headers=self.auth).status_code)
        self.assertEqual(400, self.client.put("/api/v1/player/skin", json={"model": "slim"},
                                              headers=self.auth).status_code)
        self.assertEqual(400, self.client.put("/api/v1/player/skin", json={"png": "***"},
                                              headers=self.auth).status_code)

    def test_public_lookup_ignores_names_that_are_not_uids(self):
        self.client.put("/api/v1/player/skin", json={"model": "default", "png": base64.b64encode(png()).decode()},
                        headers=self.auth)
        for name in ("Steve", "10000abc", "1234", "..%2F10000"):
            self.assertEqual(404, self.client.get(f"/api/v1/skins/csl/{name}.json").status_code, name)
        self.assertEqual(404, self.client.get("/api/v1/skins/csl/textures/" + "0" * 64).status_code)
        self.assertEqual(404, self.client.get("/api/v1/skins/csl/textures/..%2Fbmc.db").status_code)


if __name__ == "__main__":
    unittest.main()
