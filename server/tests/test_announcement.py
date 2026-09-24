import unittest
from datetime import datetime, timedelta, timezone

from app.announcement import public_announcement


def config(**overrides):
    body = {
        "id": "launch-2026-09-25",
        "title": "9 月 25 日 10:00 正式开服",
        "body": "感谢各位在测试期间的辛勤付出。",
        "level": "highlight",
        "expiresAt": "2026-09-26T02:00:00Z",
    }
    body.update(overrides)
    return {"announcement": body}


AT = datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc)   # 北京时间 25 日 00:00


class AnnouncementTests(unittest.TestCase):
    def test_live_announcement_is_published(self):
        result = public_announcement(config(), now=AT)
        self.assertEqual("launch-2026-09-25", result["id"])
        self.assertIn("正式开服", result["title"])
        self.assertEqual("highlight", result["level"])

    def test_expired_announcement_disappears_on_its_own(self):
        # 整个功能的重点：「明天上午十点开服」挂到下个月就成了笑话，
        # 而回来手动删这种事没人记得做。
        after = datetime(2026, 9, 26, 2, 0, tzinfo=timezone.utc)
        self.assertIsNone(public_announcement(config(), now=after))
        self.assertIsNone(public_announcement(config(), now=after + timedelta(days=30)))

    def test_announcement_can_be_scheduled_ahead(self):
        cfg = config(startsAt="2026-09-25T01:00:00Z")
        self.assertIsNone(public_announcement(cfg, now=AT))
        self.assertIsNotNone(public_announcement(cfg, now=datetime(2026, 9, 25, 1, 30, tzinfo=timezone.utc)))

    def test_naive_timestamp_is_read_as_utc(self):
        # 写文案的人不会带时区。按 UTC 解释会让公告早一点下线，
        # 这个方向的错比「多挂几个小时」安全。
        cfg = config(expiresAt="2026-09-26T02:00:00")
        self.assertIsNotNone(public_announcement(cfg, now=AT))
        self.assertIsNone(public_announcement(cfg, now=datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc)))

    def test_missing_or_empty_announcement_is_not_published(self):
        for cfg in ({}, {"announcement": None}, {"announcement": "开服了"},
                    {"announcement": {"title": "  ", "body": ""}}):
            self.assertIsNone(public_announcement(cfg, now=AT))

    def test_unparseable_expiry_does_not_hide_the_announcement(self):
        # 时间写错了就当没写：公告发不出去是静默失败，比多挂一会儿更糟。
        self.assertIsNotNone(public_announcement(config(expiresAt="明天"), now=AT))

    def test_title_only_announcement_is_allowed(self):
        result = public_announcement(config(body=""), now=AT)
        self.assertEqual("", result["body"])
        self.assertIn("正式开服", result["title"])

    def test_id_falls_back_so_dismissal_never_keys_on_empty(self):
        self.assertEqual("announcement", public_announcement(config(id=""), now=AT)["id"])


if __name__ == "__main__":
    unittest.main()
