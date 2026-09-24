"""隧道控制面：长轮询取件、按 agent 状态定提前量、只下发外面真能看到的候选。"""
import asyncio
import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import tunnel_registry as registry

TOKEN = "selftest-tunnel-token"


def _signed(body: bytes = b"") -> dict:
    secret = hashlib.sha256(("muxi-tunnel-v1:" + TOKEN).encode()).digest()
    stamp = repr(time.time())
    signature = hmac.new(secret, stamp.encode("ascii") + body, hashlib.sha256).hexdigest()
    return {"X-Muxi-Signature": signature, "X-Muxi-Timestamp": stamp}


class TunnelRegistryTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"MUXI_TUNNEL_TOKEN": TOKEN})
        env.start()
        self.addCleanup(env.stop)
        token_file = patch.object(registry, "TOKEN_FILE", os.path.join(os.path.dirname(__file__), "no-such-token"))
        token_file.start()
        self.addCleanup(token_file.stop)
        for table in (registry._registrations, registry._punches, registry._punch_quota,
                      registry._punch_events, registry._agents_parked, registry._agent_last_poll):
            table.clear()
        self.app = FastAPI()
        self.app.include_router(registry.router)

    def run_async(self, scenario):
        async def wrapped():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await scenario(client)
        return asyncio.run(wrapped())

    async def register(self, client, udp, mapped=None, observed="36.22.40.30"):
        body = json.dumps({
            "serverId": "t", "tunnelPort": 25540, "mcPort": 25565, "endpoints": [],
            "udpCandidates": udp, "mappedEndpoint": mapped,
        }).encode()
        headers = {**_signed(body), "Content-Type": "application/json", "X-Forwarded-For": observed}
        response = await client.post("/api/v1/tunnel/register", content=body, headers=headers)
        self.assertEqual(200, response.status_code, response.text)

    async def punch(self, client, session="00000000000000aa"):
        response = await client.post(
            "/api/v1/tunnel/punch?server_id=t",
            json={"sessionId": session, "candidates": ["122.245.210.60:4000"]},
            headers={"X-Forwarded-For": "122.245.210.60"})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_long_poll_is_woken_by_punch(self):
        async def scenario(client):
            await self.register(client, ["36.22.40.30:1234"])
            started = time.monotonic()
            waiter = asyncio.create_task(client.get(
                "/api/v1/tunnel/punch/pending?server_id=t&wait=8", headers=_signed()))
            await asyncio.sleep(0.2)
            reply = await self.punch(client)
            pending = await asyncio.wait_for(waiter, 3)
            return reply, pending.json(), time.monotonic() - started
        reply, pending, elapsed = self.run_async(scenario)
        self.assertEqual(1, len(pending["punchRequests"]), pending)
        self.assertEqual(8.0, pending["longPoll"])
        self.assertLess(elapsed, 2.0, "长轮询应该被 /punch 立即唤醒，而不是等满 8 秒")
        # agent 挂在长轮询上时提前量用快档。
        self.assertLess(reply["punchInMs"], 2000)

    def test_lead_falls_back_without_attentive_agent(self):
        async def scenario(client):
            await self.register(client, ["36.22.40.30:1234"])
            return await self.punch(client)
        reply = self.run_async(scenario)
        self.assertGreater(reply["punchInMs"], 3500, "没有 agent 在长轮询时仍要给轮询周期留足余量")

    def test_short_poll_stays_compatible(self):
        async def scenario(client):
            await self.register(client, ["36.22.40.30:1234"])
            await self.punch(client)
            return (await client.get("/api/v1/tunnel/punch/pending?server_id=t", headers=_signed())).json()
        pending = self.run_async(scenario)
        self.assertEqual(1, len(pending["punchRequests"]))
        self.assertEqual(0.0, pending["longPoll"])

    def test_candidates_outside_real_exits_are_not_published(self):
        async def scenario(client):
            # 映射在内层 NAT 上、控制面看到的是代理出口：两条都不该下发。
            await self.register(client, ["36.22.40.30:1234", "a:36.22.40.30:1240"],
                                mapped="115.215.43.38:25541", observed="199.7.140.141")
            hidden = (await client.get("/api/v1/tunnel/endpoints?server_id=t")).json()
            # 控制面看到的就是真出口时照旧下发。
            await self.register(client, ["36.22.40.30:1234"], observed="36.22.40.30")
            shown = (await client.get("/api/v1/tunnel/endpoints?server_id=t")).json()
            return hidden, shown
        hidden, shown = self.run_async(scenario)
        hidden_ids = {c["id"] for c in hidden["candidates"]}
        self.assertNotIn("v4-mapped", hidden_ids)
        self.assertNotIn("v4-observed", hidden_ids)
        self.assertIn("relay-fallback", hidden_ids)
        self.assertIn("v4-observed", {c["id"] for c in shown["candidates"]})

    def test_old_agent_without_candidates_keeps_old_behaviour(self):
        async def scenario(client):
            await self.register(client, [], mapped="115.215.43.38:25541", observed="199.7.140.141")
            return (await client.get("/api/v1/tunnel/endpoints?server_id=t")).json()
        ids = {c["id"] for c in self.run_async(scenario)["candidates"]}
        self.assertIn("v4-mapped", ids)
        self.assertIn("v4-observed", ids)


if __name__ == "__main__":
    unittest.main()
