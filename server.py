# Minecraft Bedrock WebSocket bridge
# In-world command: /connect ws://127.0.0.1:3000
import asyncio, json, re, uuid
from aiohttp import web
import websockets
from websockets.legacy.server import serve

HTTP_HOST, HTTP_PORT = "127.0.0.1", 3001
WS_HOST, WS_PORT = "127.0.0.1", 3000
minecraft_ws = None
send_lock = asyncio.Lock()
pending = {}   # requestId -> command(失敗ログ用)
stats = {"ok": 0, "ng": 0}
ALLOWED = {"/time", "/weather", "/setblock", "/fill", "/summon", "/tp", "/give"}
BAD_CHARS = re.compile(r"[\r\n\x00]")

def validate_command(value):
    command = str(value or "").strip()
    if not command.startswith("/") or BAD_CHARS.search(command):
        return False, "コマンド形式が正しくありません"
    parts = command.split()
    name = parts[0].lower()
    if name not in ALLOWED:
        return False, f"許可されていないコマンド: {name}"
    if name == "/setblock":
        if len(parts) < 5:
            return False, "/setblockの引数が足りません"
        try: [int(float(v)) for v in parts[1:4]]
        except ValueError: return False, "setblockは数値の絶対座標を指定してください"
    if name == "/fill":
        if len(parts) < 8:
            return False, "/fillの引数が足りません"
        try: c = [int(float(v)) for v in parts[1:7]]
        except ValueError: return False, "fillは数値の絶対座標を指定してください"
        volume = (abs(c[0]-c[3])+1)*(abs(c[1]-c[4])+1)*(abs(c[2]-c[5])+1)
        if volume > 5000: return False, f"fill範囲が大きすぎます（上限5000、今回{volume}）"
    return True, command

async def send_command(command, wait=False):
    if minecraft_ws is None:
        raise RuntimeError("Minecraft WebSocket未接続。ワールド内で /connect ws://127.0.0.1:3000 を実行してください")
    request_id = str(uuid.uuid4())
    fut = asyncio.get_running_loop().create_future() if wait else None
    pending[request_id] = (command, fut)
    if len(pending) > 500:
        pending.pop(next(iter(pending)))
    packet = {
        "header": {"version": 1, "requestId": request_id,
                   "messageType": "commandRequest", "messagePurpose": "commandRequest"},
        "body": {"version": 1, "commandLine": command.lstrip("/"),
                 "origin": {"type": "player"}}
    }
    async with send_lock:
        await minecraft_ws.send(json.dumps(packet))
    if fut is None:
        return None
    try:
        return await asyncio.wait_for(fut, timeout=5)
    except asyncio.TimeoutError:
        pending.pop(request_id, None)
        return {"statusCode": None, "statusMessage": "マイクラからの応答なし(タイムアウト)"}

async def ws_handler(ws):
    global minecraft_ws
    minecraft_ws = ws
    print("🎉 MinecraftとWebSocket接続成功！")
    try:
        packet = {"header": {"version": 1, "requestId": str(uuid.uuid4()),
                             "messageType": "commandRequest", "messagePurpose": "subscribe"},
                  "body": {"eventName": "PlayerMessage"}}
        await ws.send(json.dumps(packet))
        print("📡 PlayerMessage購読を登録しました")
        async for raw in ws:
            try: msg = json.loads(raw)
            except json.JSONDecodeError: continue
            h = msg.get("header", {})
            if h.get("messagePurpose") == "commandResponse":
                cmd, fut = pending.pop(h.get("requestId"), ("?", None))
                b = msg.get("body", {})
                if fut is not None and not fut.done():
                    fut.set_result(b)
                code = b.get("statusCode", 0)
                if code != 0:
                    stats["ng"] += 1
                    print(f"❌ マイクラ側で失敗({code}): {cmd[:90]} → {b.get('statusMessage', '')}")
                else:
                    stats["ok"] += 1
                continue
            if h.get("messagePurpose") == "event" and h.get("eventName") == "PlayerMessage":
                b = msg.get("body", {})
                if b.get("message"):
                    print(f"💬 ワールドチャット: {b.get('sender', 'プレイヤー')}: {b['message']}")
    except websockets.ConnectionClosed:
        pass
    except Exception as e:
        print("WebSocketエラー:", e)
    finally:
        if minecraft_ws is ws: minecraft_ws = None
        print("🔌 Minecraft WebSocket切断。必要ならワールド内で再接続してください")

def clean_text(value):
    # /say は [ ] や @ をコマンドの構文として解釈して失敗することがあるので、無害な文字に置き換える
    text = BAD_CHARS.sub(" ", str(value or "").strip())
    text = text.replace("[", "(").replace("]", ")").replace("@", "＠")
    return text[:240]

async def say_handler(request):
    try: data = await request.json()
    except Exception: return web.json_response({"ok": False, "error": "JSONが正しくありません"}, status=400)
    text = clean_text(data.get("text"))
    if not text: return web.json_response({"ok": False, "error": "発言内容が空です"}, status=400)
    try:
        await send_command("say " + text)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=503)

async def command_handler(request):
    try: data = await request.json()
    except Exception: return web.json_response({"ok": False, "error": "JSONが正しくありません"}, status=400)
    ok, result = validate_command(data.get("command"))
    if not ok: return web.json_response({"ok": False, "error": result}, status=400)
    try:
        res = await send_command(result, wait=True)
        code = (res or {}).get("statusCode")
        if code not in (0, None):
            msg = (res or {}).get("statusMessage", "")
            return web.json_response({"ok": False, "error": f"マイクラ側で失敗({code}): {msg}"}, status=422)
        return web.json_response({"ok": True, "command": result})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=503)

async def query_player_position():
    """ワールドに接続しているプレイヤーの現在位置を querytarget で取得する"""
    last_err = "プレイヤー位置を取得できませんでした"
    for sel in ("@s", "@p"):
        res = await send_command(f"querytarget {sel}", wait=True) or {}
        details = res.get("details")
        if res.get("statusCode") in (0, None) and details:
            try:
                arr = json.loads(details) if isinstance(details, str) else details
                pos = arr[0]["position"]
                return {"x": float(pos["x"]), "y": float(pos["y"]), "z": float(pos["z"]),
                        "dimension": arr[0].get("dimension")}
            except Exception as e:
                last_err = f"位置の読み取りに失敗: {e}"
        else:
            last_err = res.get("statusMessage") or last_err
    raise RuntimeError(last_err)

async def player_position_handler(_request):
    try:
        pos = await query_player_position()
        return web.json_response({"ok": True, **pos})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=503)

async def health_handler(_request):
    return web.json_response({"ok": True, "minecraft_connected": minecraft_ws is not None,
                              "mc_success": stats["ok"], "mc_failed": stats["ng"],
                              "allowed_commands": sorted(ALLOWED)})

async def main():
    app = web.Application()
    app.router.add_post("/say", say_handler)
    app.router.add_post("/command", command_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/player_position", player_position_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, HTTP_HOST, HTTP_PORT).start()
    print(f"🌐 bot.js連携受付: http://{HTTP_HOST}:{HTTP_PORT}")
    print(f"🔌 Minecraft WebSocket: ws://{WS_HOST}:{WS_PORT}")
    print("Minecraftワールド内で /connect ws://127.0.0.1:3000 を実行してください")
    try:
        async with serve(ws_handler, WS_HOST, WS_PORT, max_size=2**20):
            await asyncio.Future()
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: print("\n終了しました")