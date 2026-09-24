"""崩溃日志：启动器上传、玩家中心查看、只给本人和管理员看。"""
import io
import json
import tempfile
import time
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.crash_reports import (KEEP_PER_PLAYER, UPLOADS_PER_HOUR, VIEW_LIMIT_BYTES, CrashReportError,
                               CrashReportStore, inspect_bundle)
from app.oidc import WebsiteAccount


def bundle(files=None, summary=None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if summary is not False:
            archive.writestr("report.json", json.dumps(summary or {
                "kind": "crash", "exitCode": -1, "reason": "内存不足", "launcherVersion": "1.1.35",
                "packVersion": "1.3.34", "environmentIncluded": True, "secret": "drop me"}))
        for name, text in (files or {"latest.log": "boom\n", "crash-report.txt": "Description: test\n"}).items():
            archive.writestr(name, text)
    return buffer.getvalue()


class InspectBundleTests(unittest.TestCase):
    def test_summary_keeps_known_fields_only(self):
        summary, files = inspect_bundle(bundle())
        self.assertEqual("crash", summary["kind"])
        self.assertEqual(-1, summary["exitCode"])
        self.assertTrue(summary["environmentIncluded"])
        self.assertNotIn("secret", summary)
        self.assertEqual(["crash-report.txt", "latest.log", "report.json"], [f["name"] for f in files])

    def test_rejects_bad_bundles(self):
        for data in (b"not a zip", bundle(summary=False), bundle(files={"../evil.txt": "x"}),
                     bundle(files={"dir/latest.log": "x"})):
            with self.assertRaises(CrashReportError):
                inspect_bundle(data)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("report.json", "[]")
        with self.assertRaises(CrashReportError):
            inspect_bundle(buffer.getvalue())


class CrashReportApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bmc-crash-")
        self.store = CrashReportStore(Path(self.tmp.name) / "bmc.db", Path(self.tmp.name) / "reports")
        store_patch = patch.object(main, "crash_store", self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        player = WebsiteAccount(subject="sub-10000", uid=10000, username="MuxiUser", nickname="洛可",
                                game_name="10000", email="a@example.com", email_verified=True, role="player")
        self.accounts = {
            "alice": player,
            "bob": replace(player, subject="sub-10001", uid=10001, username="Bob"),
            "admin": replace(player, subject="sub-10002", uid=10002, username="Admin", role="admin"),
        }
        userinfo = patch.object(main.oidc_client, "userinfo", side_effect=self.userinfo)
        userinfo.start()
        self.addCleanup(userinfo.stop)
        self.client = TestClient(main.app)

    def tearDown(self):
        self.tmp.cleanup()

    def userinfo(self, token):
        if token not in self.accounts:
            raise RuntimeError("bad token")
        return self.accounts[token]

    @staticmethod
    def auth(who):
        return {"Authorization": "Bearer " + who}

    def upload(self, who="alice", data=None):
        return self.client.post("/api/v1/player/crash-reports", content=data or bundle(),
                                headers={**self.auth(who), "Content-Type": "application/zip"})

    def test_upload_list_view_download_delete(self):
        self.assertEqual(401, self.client.post("/api/v1/player/crash-reports", content=bundle()).status_code)
        uploaded = self.upload()
        self.assertEqual(200, uploaded.status_code, uploaded.text)
        report = uploaded.json()["report"]
        self.assertRegex(report["id"], r"^[2-9A-HJKMNP-Z]{10}$")
        self.assertEqual("内存不足", report["reason"])

        listed = self.client.get("/api/v1/player/crash-reports", headers=self.auth("alice")).json()["reports"]
        self.assertEqual([report["id"]], [r["id"] for r in listed])
        self.assertNotIn("files", listed[0])

        detail = self.client.get(f"/api/v1/player/crash-reports/{report['id']}", headers=self.auth("alice")).json()
        self.assertIn("latest.log", [f["name"] for f in detail["report"]["files"]])
        text = self.client.get(f"/api/v1/player/crash-reports/{report['id']}/files/crash-report.txt",
                               headers=self.auth("alice"))
        self.assertEqual("Description: test\n", text.text)
        self.assertIn("no-store", text.headers["cache-control"])
        download = self.client.get(f"/api/v1/player/crash-reports/{report['id']}/download", headers=self.auth("alice"))
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(download.content)))
        self.assertIn("attachment", download.headers["content-disposition"])

        self.assertEqual(200, self.client.delete(f"/api/v1/player/crash-reports/{report['id']}",
                                                 headers=self.auth("alice")).status_code)
        self.assertEqual([], self.client.get("/api/v1/player/crash-reports", headers=self.auth("alice")).json()["reports"])
        self.assertEqual([], list(self.store.root.iterdir()))

    def test_only_owner_or_admin_can_read(self):
        report_id = self.upload().json()["report"]["id"]
        for path in (f"/api/v1/player/crash-reports/{report_id}",
                     f"/api/v1/player/crash-reports/{report_id}/files/latest.log",
                     f"/api/v1/player/crash-reports/{report_id}/download"):
            self.assertEqual(404, self.client.get(path, headers=self.auth("bob")).status_code, path)
            self.assertEqual(200, self.client.get(path, headers=self.auth("admin")).status_code, path)
        self.assertEqual([], self.client.get("/api/v1/player/crash-reports", headers=self.auth("bob")).json()["reports"])
        # 管理员能看，但不能替玩家删；别人更不能删
        self.assertEqual(404, self.client.delete(f"/api/v1/player/crash-reports/{report_id}",
                                                 headers=self.auth("bob")).status_code)
        self.assertEqual(404, self.client.delete(f"/api/v1/player/crash-reports/{report_id}",
                                                 headers=self.auth("admin")).status_code)

    def test_feedback_with_text_only(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("report.json", json.dumps({
                "kind": "feedback", "message": "进服就卡在加载地形" + "很" * 3000,
                "environmentIncluded": False, "logsIncluded": False}, ensure_ascii=False))
        report = self.upload(data=buffer.getvalue()).json()["report"]
        self.assertEqual("feedback", report["kind"])
        self.assertTrue(report["message"].startswith("进服就卡在加载地形"))
        self.assertEqual(2000, len(report["message"]))
        self.assertFalse(report["logsIncluded"])
        self.assertEqual(["report.json"], [f["name"] for f in report["files"]])

    def test_admin_sees_everyones_recent_reports(self):
        self.upload("alice")
        self.upload("bob")
        sessions = {"admin-cookie": self.accounts["admin"], "alice-cookie": self.accounts["alice"]}
        with patch.object(main.web_auth_store, "session", side_effect=lambda raw: sessions.get(raw)):
            self.assertEqual(401, self.client.get("/api/v1/admin/crash-reports").status_code)
            self.client.cookies.set("bmc_session", "alice-cookie")
            self.assertEqual(403, self.client.get("/api/v1/admin/crash-reports").status_code)
            self.client.cookies.set("bmc_session", "admin-cookie")
            reports = self.client.get("/api/v1/admin/crash-reports").json()["reports"]
            self.client.cookies.clear()
        self.assertEqual({10000, 10001}, {r["uid"] for r in reports})

    def test_long_logs_show_their_tail(self):
        big = "x" * VIEW_LIMIT_BYTES + "\nTHE END\n"
        report_id = self.upload(data=bundle(files={"latest.log": big})).json()["report"]["id"]
        response = self.client.get(f"/api/v1/player/crash-reports/{report_id}/files/latest.log",
                                   headers=self.auth("alice"))
        self.assertEqual("1", response.headers["x-truncated"])
        self.assertTrue(response.text.endswith("THE END\n"))
        self.assertLessEqual(len(response.content), VIEW_LIMIT_BYTES)

    def test_rejections(self):
        bad = self.upload(data=b"garbage")
        self.assertEqual(400, bad.status_code)
        self.assertIn("格式", bad.json()["detail"])
        huge = self.client.post("/api/v1/player/crash-reports", content=b"x",
                                headers={**self.auth("alice"), "Content-Length": str(30 * 1024 * 1024)})
        self.assertIn(huge.status_code, (400, 413))

    def test_rate_limit_and_retention(self):
        now = time.time()
        for i in range(UPLOADS_PER_HOUR):
            self.store.add(10000, bundle(), now=now)
        with self.assertRaisesRegex(CrashReportError, "频繁"):
            self.store.add(10000, bundle(), now=now)
        # 一小时以前的不算进频率，但保留份数有上限
        total = KEEP_PER_PLAYER + 5
        for i in range(total):
            self.store.add(10001, bundle(), now=now - 7200 - (total - i) * 4000)
        kept = self.store.list(10001)
        self.assertEqual(KEEP_PER_PLAYER, len(kept))
        self.assertEqual(KEEP_PER_PLAYER + UPLOADS_PER_HOUR, len(list(self.store.root.glob("*.zip"))))


if __name__ == "__main__":
    unittest.main()
