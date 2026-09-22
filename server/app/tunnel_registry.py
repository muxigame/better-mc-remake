"""隧道控制面：服务端侧工具上报入口，客户端来取。

为什么必须有这一层：Minecraft 所在的家宽机器拿到的 IPv6 是临时地址，隐私扩展
开着就会定期轮换，RA 前缀还可能因为运营商重拨整体更换。把地址写死在整合包清单
里，今天能连，过几小时就全员失联。所以地址只能由那台机器自己上报，客户端每次
启动来取最新的。

同时这里也是 P2P 打洞的信令点：两边都能从这里拿到对方的外部地址，以及自己在
公网上被看到的样子（NAT 映射后的地址），否则谁也不知道该往哪打。

注册必须鉴权。这个接口决定了玩家会被指到哪台机器上，不设防等于把玩家流量交给
任何一个知道 URL 的人。
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import time
from dataclasses import dataclass, field
from threading import Lock

from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(prefix="/api/v1/tunnel", tags=["tunnel"])

#: 超过这个时间没再上报就认为那台机器掉线了，不再发给客户端。
REGISTRATION_TTL = 90.0
#: 打洞请求的保留时间，够服务端侧轮询取走即可。
PUNCH_TTL = 30.0
#: 允许的时间戳偏移，防重放。
CLOCK_SKEW = 120.0

#: 打洞配额：同一出口 IP 在窗口内最多发起这么多次。
#:
#: 客户端凭据是公开领取的，没有配额的话，循环调 /punch 就能让服务端侧的打洞
#: socket 不停重绑——每次打洞后那个端口都要让出去——真玩家永远排不上号。
#: 给到 4 次是为了不误伤同一个 NAT 后面同时开游戏的几个人。
PUNCH_QUOTA = 4
PUNCH_QUOTA_WINDOW = 30.0

_punch_quota: dict[str, list[float]] = {}


def _punch_allowed(ip: str, now: float) -> bool:
    """按出口 IP 限流。调用方已持 _lock。"""
    if len(_punch_quota) > 4096:
        for key, stamps in list(_punch_quota.items()):
            if not stamps or now - stamps[-1] > PUNCH_QUOTA_WINDOW:
                _punch_quota.pop(key, None)
    recent = [t for t in _punch_quota.get(ip, []) if now - t < PUNCH_QUOTA_WINDOW]
    if len(recent) >= PUNCH_QUOTA:
        _punch_quota[ip] = recent
        return False
    recent.append(now)
    _punch_quota[ip] = recent
    return True


RELAY_FALLBACK_HOST = os.getenv("BMC_TUNNEL_RELAY_HOST", "minecraft.muxigame.com")
RELAY_FALLBACK_PORT = int(os.getenv("BMC_TUNNEL_RELAY_PORT", "25565"))

#: 直连候选的数量上限。客户端是并发探测的，候选太多只会拖慢选路，
#: 而且上报端一旦把虚拟网卡也算进来，数量会失控。
MAX_DIRECT_CANDIDATES = 8

#: 会和玩家本机撞车的地址段，一律不下发。
#: 198.18.0.0/15 是 clash/mihomo TUN 的默认段，100.64.0.0/10 是运营商级 NAT。
_POISON_V4 = ipaddress.ip_network("198.18.0.0/15")


def _is_poison(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address.version != 4:
        return False
    return address in _POISON_V4 or address in ipaddress.ip_network("100.64.0.0/10")


#: 密钥文件优先于环境变量。放文件是为了能在不重建容器的前提下投放和轮换密钥
#: ——这套服务的镜像来自已经注销的旧 registry，`compose up -d` 拉不回来，
#: 重建容器等于把站点弄挂。
TOKEN_FILE = os.getenv("BMC_TUNNEL_TOKEN_FILE", "/app/server/data/tunnel-token")


def _secret() -> bytes:
    token = ""
    try:
        with open(TOKEN_FILE, encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError:
        pass
    if not token:
        token = os.getenv("MUXI_TUNNEL_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail=f"服务端未配置隧道密钥：写入 {TOKEN_FILE} 或设置 MUXI_TUNNEL_TOKEN",
        )
    # 和 C# 侧 TunnelProtocol.DeriveSecret 必须完全一致
    return hashlib.sha256(("muxi-tunnel-v1:" + token).encode("utf-8")).digest()


@dataclass
class Registration:
    server_id: str
    tunnel_port: int
    #: Minecraft 自己的端口。候选下发的是这个而不是隧道端口——没配隧道密钥的
    #: 客户端会直接拿候选去做 MC 握手，给它隧道端口必然失败；而走隧道时客户端
    #: 只用候选的地址，端口由本地设置覆盖，所以给 MC 端口两种情况都对。
    mc_port: int
    endpoints: list[str]
    observed_ip: str
    #: 服务端侧从反射器探到的全部 UDP 出口候选，供客户端打洞时逐个尝试。
    udp_candidates: list[str] = field(default_factory=list)
    updated: float = field(default_factory=time.time)

    @property
    def alive(self) -> bool:
        return time.time() - self.updated < REGISTRATION_TTL


@dataclass
class PunchRequest:
    """
    一次打洞会合请求。

    candidates 是客户端从反射器探到的**全部**出口地址，不是一个。这条线路的
    运营商 NAT 有多个出口 IP 并按流分配，只报一个的话对端有约一半概率打错
    地方；把全部候选交出去、对端逐个打，这个问题就没了。
    """
    session_id: str
    candidates: list[str]
    observed_ip: str
    punch_at: float
    created: float = field(default_factory=time.time)

    @property
    def alive(self) -> bool:
        return time.time() - self.created < PUNCH_TTL


_lock = Lock()
_registrations: dict[str, Registration] = {}
_punches: dict[str, list[PunchRequest]] = {}


def client_ip(request: Request) -> str:
    """
    取客户端真实地址。

    实际转发链是「客户端 → 宿主 nginx → docker-proxy → web 容器 nginx → 本服务」，
    所以到这里时 X-Forwarded-For 形如 `<真实客户端>, 172.18.0.1`：最后一项是
    Docker 网桥，不是玩家。直接取尾项会永远拿到 172.18.0.1。

    做法是从右往左跳过内网/回环地址（那些都是我们自己的跳板），第一个公网地址
    就是真实客户端。这样也顺带防住伪造：客户端自己塞进来的条目只会出现在真实
    地址的左边，越不过我们自己追加的那一跳。
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    for raw in reversed([part.strip() for part in forwarded.split(",") if part.strip()]):
        try:
            parsed = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if parsed.is_private or parsed.is_loopback or parsed.is_link_local:
            continue
        return raw

    # 全是内网（同机自测、或者将来链路里多了一跳）时退回 X-Real-IP，它由最外层
    # nginx 直接写成 $remote_addr，没有拼接语义。
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        try:
            ipaddress.ip_address(real_ip)
            return real_ip
        except ValueError:
            pass
    return request.client.host if request.client else "0.0.0.0"


def _verify(signature: str | None, timestamp: str | None, body: bytes) -> None:
    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="缺少签名")
    try:
        sent = float(timestamp)
    except ValueError as error:
        raise HTTPException(status_code=401, detail="时间戳非法") from error
    if abs(time.time() - sent) > CLOCK_SKEW:
        raise HTTPException(status_code=401, detail="时间戳超出允许范围")
    expected = hmac.new(_secret(), timestamp.encode("ascii") + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="签名不匹配")


#: 客户端 token 的换代周期。派生 token 泄露之后最多活这么久。
CLIENT_TOKEN_PERIOD = 86400.0


def client_token(now: float | None = None, offset: int = 0) -> str:
    """按周期从 master 派生出给客户端的 token。

    客户端和服务端侧 agent 不能共用一个密钥：master 同时是 agent 调 /register
    的凭据，谁拿到它就能把全体玩家的候选入口改指到自己机器上。派生 token 只够
    连那台机器上的 Minecraft——本来就是公开可进的——签不出 /register 的名。
    """
    bucket = int((now if now is not None else time.time()) // CLIENT_TOKEN_PERIOD) + offset
    return hmac.new(_secret(), f"muxi-client-v1|{bucket}".encode("ascii"),
                    hashlib.sha256).hexdigest()


def _format_host(host: str) -> str:
    """IPv6 放进 host:port 之前要加方括号。"""
    try:
        if ipaddress.ip_address(host).version == 6:
            return f"[{host}]"
    except ValueError:
        pass
    return host


def _candidates(registration: Registration) -> list[dict]:
    """
    把上报的地址翻译成客户端清单里的 routes[] 条目。

    顺序不在这里定——客户端按 RouteKind 分组排序，这里只负责标对类型。
    """
    items: list[dict] = []
    seen: set[str] = set()

    for index, host in enumerate(registration.endpoints):
        if len(items) >= MAX_DIRECT_CANDIDATES:
            break
        if host in seen:
            continue
        seen.add(host)
        try:
            parsed = ipaddress.ip_address(host)
        except ValueError:
            continue
        if parsed.is_loopback or parsed.is_link_local:
            continue
        # 这两段绝不能下发：玩家自己机器上极可能有同样的地址（clash/mihomo 的
        # TUN 默认就在 198.18.0.0/15），下发出去会让他连到自己的虚拟网卡上，
        # 而且按 LanDirect 排在最高优先级，直接把人坑死。
        if _is_poison(parsed):
            continue
        if parsed.version == 6:
            kind, prefix = "Ipv6Direct", "v6"
        elif parsed.is_private:
            # 家宽内网地址对外网玩家没用，但同局域网的玩家用得上
            kind, prefix = "LanDirect", "lan"
        else:
            kind, prefix = "Ipv4Direct", "v4"
        items.append({
            "id": f"{prefix}-{index}",
            "label": f"{kind} {host}",
            "kind": kind,
            "transport": "tcp",
            "host": host,
            "port": registration.mc_port,
            "priority": index,
            "probeTimeoutMs": 3000,
        })

    # 服务端机器在公网上被看到的 IPv4。多数家宽是 NAT 后的地址，直连多半不通，
    # 但标成 PortMapped 让客户端仍然试一次——有人确实做了端口转发。
    if registration.observed_ip and registration.observed_ip not in seen:
        try:
            parsed = ipaddress.ip_address(registration.observed_ip)
            if not parsed.is_private and parsed.version == 4 and not _is_poison(parsed):
                items.append({
                    "id": "v4-observed",
                    "label": f"公网 IPv4 {registration.observed_ip}",
                    "kind": "PortMapped",
                    "transport": "tcp",
                    "host": registration.observed_ip,
                    "port": registration.mc_port,
                    "priority": 0,
                    "probeTimeoutMs": 2000,
                })
        except ValueError:
            pass

    # 兜底：所有直连都建不起来时走中转。永远排在最后（Relay 组）。
    items.append({
        "id": "relay-fallback",
        "label": "中转兜底",
        "kind": "Relay",
        "transport": "tcp",
        "host": RELAY_FALLBACK_HOST,
        "port": RELAY_FALLBACK_PORT,
        "priority": 0,
        "probeTimeoutMs": 4000,
    })
    return items


@router.post("/register")
async def register(
    request: Request,
    x_muxi_signature: str | None = Header(default=None),
    x_muxi_timestamp: str | None = Header(default=None),
):
    """服务端侧工具定期调用，上报自己当前可被连到的地址。"""
    body = await request.body()
    _verify(x_muxi_signature, x_muxi_timestamp, body)

    import json
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON") from error

    server_id = str(payload.get("serverId") or "default")
    tunnel_port = int(payload.get("tunnelPort") or 25540)
    endpoints = [str(x) for x in (payload.get("endpoints") or [])]
    udp_candidates = [str(x).strip() for x in (payload.get("udpCandidates") or []) if str(x).strip()][:16]
    mc_port = int(payload.get("mcPort") or 25565)
    if not 1 <= mc_port <= 65535:
        raise HTTPException(status_code=400, detail="mcPort 非法")
    if not 1 <= tunnel_port <= 65535:
        raise HTTPException(status_code=400, detail="tunnelPort 非法")

    observed = client_ip(request)
    with _lock:
        _registrations[server_id] = Registration(
            server_id=server_id,
            tunnel_port=tunnel_port,
            mc_port=mc_port,
            endpoints=endpoints,
            observed_ip=observed,
            udp_candidates=udp_candidates,
        )
        pending = [p for p in _punches.get(server_id, []) if p.alive]
        _punches[server_id] = []

    return {
        "ok": True,
        "observedIp": observed,
        "ttl": REGISTRATION_TTL,
        # 客户端排队等着打洞的外部地址，服务端侧工具取走后同时回连
        "punchRequests": [
            {
                "sessionId": p.session_id,
                "candidates": p.candidates,
                "observedIp": p.observed_ip,
                "punchAt": p.punch_at,
            }
            for p in pending
        ],
    }


@router.get("/endpoints")
async def endpoints(request: Request, server_id: str = "default"):
    """客户端启动时调用，拿当前可用的入口列表。"""
    with _lock:
        registration = _registrations.get(server_id)
        fresh = registration if registration and registration.alive else None

    observed = client_ip(request)
    if fresh is None:
        # 服务端侧没上报（没开机、或者刚重启还没报上来），只能给兜底。
        return {
            "serverId": server_id,
            "online": False,
            "yourIp": observed,
            "candidates": [{
                "id": "relay-fallback",
                "label": "中转兜底",
                "kind": "Relay",
                "transport": "tcp",
                "host": RELAY_FALLBACK_HOST,
                "port": RELAY_FALLBACK_PORT,
                "priority": 0,
                "probeTimeoutMs": 4000,
            }],
        }

    return {
        "serverId": fresh.server_id,
        "online": True,
        "yourIp": observed,
        "tunnelPort": fresh.tunnel_port,
        "mcPort": fresh.mc_port,
        "ageSeconds": round(time.time() - fresh.updated, 1),
        "candidates": _candidates(fresh),
    }



@router.get("/client-token")
async def issue_client_token(server_id: str = "default"):
    """客户端启动时来取。不鉴权：它换来的能力和直接连公网 Minecraft 等价。"""
    now = time.time()
    period = CLIENT_TOKEN_PERIOD
    return {
        "serverId": server_id,
        "token": client_token(now),
        "expiresAt": round((int(now // period) + 1) * period, 3),
    }


#: 打洞约定时刻相对现在的提前量。两边都在这个时刻开始发包，NAT 映射才会
#: 在对方的包到达之前就已经建立。
#:
#: 必须大于服务端侧的轮询间隔，否则它取到请求时约定时刻已经过去了，两边错开发包
#: ——那就退化成单边发起，必然打不通。留出轮询 + 探测自身出口的时间。
PUNCH_LEAD_SECONDS = 4.0


@router.get("/punch/pending")
async def punch_pending(
    request: Request,
    server_id: str = "default",
    x_muxi_signature: str | None = Header(default=None),
    x_muxi_timestamp: str | None = Header(default=None),
):
    """
    服务端侧工具的快速轮询口。

    register 是 30 秒一次，对打洞来说太慢——客户端点开始游戏之后等不了那么久。
    这个口足够轻（空响应几十字节），可以每秒问一次。取走即清除，避免重复打洞。
    """
    _verify(x_muxi_signature, x_muxi_timestamp, await request.body())
    with _lock:
        pending = [p for p in _punches.get(server_id, []) if p.alive]
        _punches[server_id] = []
    return {
        "punchRequests": [
            {
                "sessionId": p.session_id,
                "candidates": p.candidates,
                "observedIp": p.observed_ip,
                "punchAt": p.punch_at,
            }
            for p in pending
        ],
    }


@router.post("/punch")
async def punch(request: Request, server_id: str = "default"):
    """
    客户端交出自己的全部出口候选，换回服务端侧的候选和一个约定打洞时刻。

    为什么必须双向同时发：NAT 只放行"自己先发过包的那个对端地址"回来的流量。
    单边发起的话，先到的那个包在对方 NAT 上没有对应映射，会被当成未知连接丢掉。
    所以两边都要在约定时刻朝对方猛发一阵，谁的包后到谁就能进去。
    """
    import json
    try:
        payload = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON") from error

    session_id = str(payload.get("sessionId") or "").strip()
    raw = payload.get("candidates")
    if isinstance(raw, str):
        raw = [raw]
    candidates = [str(x).strip() for x in (raw or []) if str(x).strip()]
    if not session_id or not candidates:
        raise HTTPException(status_code=400, detail="缺少 sessionId 或 candidates")
    if len(candidates) > 16:
        candidates = candidates[:16]

    observed = client_ip(request)
    now = time.time()
    punch_at = now + PUNCH_LEAD_SECONDS

    with _lock:
        if not _punch_allowed(observed, now):
            raise HTTPException(status_code=429, detail="打洞请求过于频繁，稍后再试")
        queue = [p for p in _punches.get(server_id, []) if p.alive and p.session_id != session_id]
        queue.append(PunchRequest(
            session_id=session_id,
            candidates=candidates,
            observed_ip=observed,
            punch_at=punch_at,
        ))
        _punches[server_id] = queue[-32:]
        registration = _registrations.get(server_id)
        peer = list(registration.udp_candidates) if registration and registration.alive else []

    return {
        "ok": True,
        "observedIp": observed,
        "punchAt": punch_at,
        "serverOnline": bool(peer),
        # 服务端侧上一次上报的出口候选。为空说明它还没探到（刚启动或反射器不可达），
        # 客户端这时应当直接退回中转，而不是对着空列表干打。
        "peerCandidates": peer,
    }
