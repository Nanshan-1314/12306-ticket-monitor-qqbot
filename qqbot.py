"""获取 token 私聊发送 openid捕获。"""
import asyncio
import json
import urllib.error
import urllib.request

import aiohttp

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
INTENT = 1 << 25  # GROUP_AND_C2C_EVENT


def _post(url, body, headers=None, timeout=30):
    headers = headers or {"Content-Type": "application/json"}
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")


def _get(url, headers, timeout=30):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")


def get_access_token(appid, secret):
    r = _post(TOKEN_URL, {"appId": appid, "clientSecret": secret})
    token = r.get("access_token")
    if not token:
        raise RuntimeError(f"获取 access_token 失败: {r}")
    return token


def send_c2c(token, openid, text, api_base):
    url = f"{api_base}/v2/users/{openid}/messages"
    return _post(url, {"content": text, "msg_type": 0},
                 {"Authorization": f"QQBot {token}", "Content-Type": "application/json"})


def get_gateway(token, api_base):
    r = _get(f"{api_base}/gateway", {"Authorization": f"QQBot {token}"})
    url = r.get("url")
    if not url:
        raise RuntimeError(f"获取网关失败: {r}")
    return url


async def capture_openid(token, api_base, timeout=300):
    """连 QQ 网关，等用户给机器人发一条消息，返回其 openid。"""
    gateway = get_gateway(token, api_base)
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(gateway, autoping=True) as ws:
            state = {"seq": None, "hb": 45000}
            hb_task = None

            async def heartbeat():
                await asyncio.sleep(state["hb"] / 1000)
                while True:
                    try:
                        await ws.send_json({"op": 1, "d": state["seq"]})
                    except Exception:
                        return
                    await asyncio.sleep(state["hb"] / 1000)

            result = None
            try:
                async with asyncio.timeout(timeout):
                    async for m in ws:
                        if m.type != aiohttp.WSMsgType.TEXT:
                            continue
                        msg = json.loads(m.data)
                        op = msg.get("op")
                        if msg.get("s") is not None:
                            state["seq"] = msg["s"]
                        if op == 10:
                            state["hb"] = (msg.get("d") or {}).get("heartbeat_interval", 45000)
                            await ws.send_json({
                                "op": 2,
                                "d": {"token": f"QQBot {token}", "intents": INTENT, "shard": [0, 1]},
                            })
                            hb_task = asyncio.create_task(heartbeat())
                        elif op == 0 and msg.get("t") == "C2C_MESSAGE_CREATE":
                            author = (msg.get("d") or {}).get("author", {})
                            openid = author.get("user_openid") or author.get("id")
                            if openid:
                                result = openid
                                break
            except asyncio.TimeoutError:
                pass
            finally:
                if hb_task:
                    hb_task.cancel()
                    try:
                        await hb_task
                    except (asyncio.CancelledError, Exception):
                        pass
            return result
