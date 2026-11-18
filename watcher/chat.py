"""Live chat readers. Each yields dicts:
  {"kind": "msg", "t": epoch_float, "user": str, "text": str}
  {"kind": "event", "t": epoch_float, "type": "raid"|"subgift"|...}
"""
import asyncio
import json
import random
import ssl
import time

SUPPRESS_TYPES = {"raid", "subgift", "submysterygift", "anonsubgift", "anonsubmysterygift", "giftpaidupgrade"}


def _parse_tags(raw: str) -> dict:
    tags = {}
    for part in raw.split(";"):
        k, _, v = part.partition("=")
        tags[k] = v
    return tags


async def twitch_chat(channel: str, log):
    """Anonymous read-only Twitch IRC over TLS (no account or token needed)."""
    channel = channel.lower().lstrip("#")
    backoff = 2
    while True:
        try:
            ctx = ssl.create_default_context()
            reader, writer = await asyncio.open_connection("irc.chat.twitch.tv", 6697, ssl=ctx)
            nick = f"justinfan{random.randint(10000, 99999)}"
            for line in ("CAP REQ :twitch.tv/tags twitch.tv/commands", "PASS SCHMOOPIIE", f"NICK {nick}", f"JOIN #{channel}"):
                writer.write((line + "\r\n").encode())
            await writer.drain()
            log(f"[chat] connected to #{channel}")
            backoff = 2
            while True:
                raw = await asyncio.wait_for(reader.readline(), timeout=400)
                if not raw:
                    raise ConnectionError("eof")
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("PING"):
                    writer.write(("PONG" + line[4:] + "\r\n").encode())
                    await writer.drain()
                    continue
                tags = {}
                if line.startswith("@"):
                    tagstr, _, line = line.partition(" ")
                    tags = _parse_tags(tagstr[1:])
                if " PRIVMSG " in line:
                    prefix, _, rest = line.partition(" PRIVMSG ")
                    user = prefix.lstrip(":").split("!")[0]
                    text = rest.partition(" :")[2]
                    yield {"kind": "msg", "t": time.time(), "user": user.lower(), "text": text}
                elif " USERNOTICE " in line:
                    mid = tags.get("msg-id", "")
                    if mid in SUPPRESS_TYPES:
                        yield {"kind": "event", "t": time.time(), "type": mid}
                elif " RECONNECT" in line:
                    raise ConnectionError("server asked to reconnect")
        except Exception as e:  # noqa: BLE001
            log(f"[chat] #{channel} disconnected ({e}); retry in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def kick_chat(chatroom_id: int, log):
    """EXPERIMENTAL: Kick's unofficial Pusher websocket. May break without notice."""
    import websockets  # pip install websockets

    url = "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0&flash=false"
    backoff = 2
    while True:
        try:
            async with websockets.connect(url, ping_interval=None) as ws:
                await ws.send(json.dumps({"event": "pusher:subscribe",
                                          "data": {"auth": "", "channel": f"chatrooms.{chatroom_id}.v2"}}))
                log(f"[chat] kick chatroom {chatroom_id} connected")
                backoff = 2
                async for raw in ws:
                    msg = json.loads(raw)
                    ev = msg.get("event", "")
                    if ev == "pusher:ping":
                        await ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                    elif ev.endswith("ChatMessageEvent"):
                        data = json.loads(msg["data"])
                        user = (data.get("sender") or {}).get("username", "?").lower()
                        yield {"kind": "msg", "t": time.time(), "user": user, "text": data.get("content", "")}
                    elif ev.endswith(("GiftedSubscriptionsEvent", "StreamHostEvent")):
                        yield {"kind": "event", "t": time.time(), "type": "subgift"}
        except Exception as e:  # noqa: BLE001
            log(f"[chat] kick {chatroom_id} disconnected ({e}); retry in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def message_weight(text: str, keywords: list[str]) -> float:
    t = text.strip()
    low = t.lower()
    w = 1.0
    if any(k in low for k in keywords):
        w += 0.75
    letters = [c for c in t if c.isalpha()]
    if len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        w += 0.25
    if t.count("?") >= 3 or t.count("!") >= 3:
        w += 0.25
    return w


DEMO_LINES = ["kkkkkkk", "KKKKKKKKKK", "CLIPA", "MDS", "???", "nossa", "LUL", "KEKW", "caralho kkkk", "OMEGALUL"]
CALM_LINES = ["salve", "boa noite", "qual o carro?", "vai na missão", "eae", "gta 6 lindo", "kk", "bora"]


async def demo_chat(t0_getter, period: int, log):
    """Synthetic chat for the demo streamer: calm chatter plus a flood ~6 s after each burst."""
    log("[chat] demo chat started")
    rng = random.Random()
    while True:
        await asyncio.sleep(1)
        t0 = t0_getter()
        now = time.time()
        phase = (now - t0) % period if t0 else -1
        flood = 6 <= phase <= 16
        n = rng.randint(12, 26) if flood else rng.choice([0, 1, 1, 2, 3])
        for _ in range(n):
            user = f"viewer{rng.randint(1, 400)}"
            text = rng.choice(DEMO_LINES if flood else CALM_LINES)
            yield {"kind": "msg", "t": now + rng.random() * 0.9, "user": user, "text": text}
