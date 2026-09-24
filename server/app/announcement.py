"""服务端下发的公告。

文案放在 `site.json` 里、由接口下发，而不是写死在官网和启动器里，原因很实际：
改一句话如果要重新打包发客户端，玩家就得先更新才能看到公告——而公告往往正是为了
通知"现在要做什么"，等更新完黄花菜都凉了。放在这里，改完重新部署一次官网即可，
启动器那边 30 秒缓存过期就能看到。

公告会**自动过期**。"明天上午十点开服"这种话挂到下个月就成了笑话，而过期清理这种
事没人记得做。给一个 `expiresAt`，到点自动不再下发。
"""
from __future__ import annotations

from datetime import datetime, timezone


def _parse(value: str) -> datetime | None:
    """解析 ISO8601。带 Z 的要先换成 +00:00，fromisoformat 到 3.11 才认 Z。"""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # 不带时区的一律按 UTC 解释：宁可早一点下线，也不要因为服务器时区
    # 和写文案的人不一致，让公告多挂几个小时。
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def public_announcement(config: dict, now: datetime | None = None) -> dict | None:
    """把 site.json 里的 announcement 整理成可下发的形状；没有或已过期返回 None。"""
    raw = config.get("announcement")
    if not isinstance(raw, dict):
        return None

    title = str(raw.get("title") or "").strip()
    body = str(raw.get("body") or "").strip()
    if not title and not body:
        return None

    moment = now or datetime.now(timezone.utc)
    expires = _parse(raw.get("expiresAt", ""))
    if expires and moment >= expires:
        return None
    starts = _parse(raw.get("startsAt", ""))
    if starts and moment < starts:
        return None

    return {
        # id 用来让玩家关掉之后不再弹。改文案时记得换 id，否则关过的人看不到新公告。
        "id": str(raw.get("id") or "").strip() or "announcement",
        "title": title,
        "body": body,
        "level": str(raw.get("level") or "info").strip().lower(),
        "expiresAt": expires.isoformat() if expires else None,
    }
