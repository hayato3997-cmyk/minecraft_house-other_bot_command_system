# minecraft_house-other_bot_command_system
Minecraft AI Bot（プレイヤー参加＋Groq(OpenAI互換API)＋server.py連携）

ファイル
- bot.js: bedrock-protocolでプレイヤーとして接続し、チャットをGroq(OpenAI互換API)へ送信
- server.py: WebSocket経由でMinecraftの/sayと許可されたコマンドを実行
- bot.jsはAPIキーをソースに保存しません

最初に
APIキーをチャットに貼り付けてしまった場合は、Groq(OpenAI互換API) Platformでそのキーを無効化し、新しいキーを作成してください。

1. Node.jsプロジェクトのフォルダーで:
   npm install bedrock-protocol 

2. Python 3.9で:
   py -3.9 -m pip install aiohttp websockets

3. PowerShellウィンドウ1:
   py -3.9 server.py

4. PowerShellウィンドウ2:
   $env:LLM_API_KEY="新しいAPIキー"
   node bot.js

5. Minecraftワールド内で:
   /connect ws://127.0.0.1:3000

6. bot.js側に「AIボットがワールドに参加しました」と出たら、チャットで:
   AI、やっほー
   AI、近くに石の小さな壁を作って
   などと話しかけます。

任意の設定（bot.jsを起動するPowerShellで設定）
   $env:MC_HOST="127.0.0.1"
   $env:MC_PORT="19132"
   $env:LLM_MODEL="llama-3.3-70b-versatile"

注意
- 127.0.0.1:19132は、bot.jsを実行しているPCから接続できるMinecraft Bedrockワールドのアドレスとポートである必要があります。
- WebSocket接続成功は、bot.jsのプレイヤー参加成功とは別です。bot.js側でspawnログを確認してください。
- 通常のWindows版Minecraftのローカルワールドでは、外部クライアント接続やMicrosoft認証の条件によってbotが参加できない場合があります。
- AIコマンドは許可リストで制限し、/fillは最大5000ブロックに制限しています。
