// Install: npm install bedrock-protocol   (Node.js 18以上 / fetch標準搭載)
// Set LLM_API_KEY in the environment; never put the key in this file.
const bedrock = require('bedrock-protocol');
const { houseCommands, findFarFills } = require('./house');
const readline = require('readline');
const http = require('http');
const fs = require('fs');
const path = require('path');

const API_KEY = process.env.LLM_API_KEY;
if (!API_KEY) {
  console.error('LLM_API_KEY が未設定です。README.txt を確認してください。');
  process.exit(1);
}
const LLM_URL = process.env.LLM_URL || 'https://api.groq.com/openai/v1/chat/completions';
const MC_HOST = process.env.MC_HOST || '127.0.0.1';
const MC_PORT = Number(process.env.MC_PORT || 19132);
const MODEL = process.env.LLM_MODEL || 'openai/gpt-oss-120b';
const CHAT_HTTP_PORT = Number(process.env.BOT_CHAT_PORT || 3002);

// ===== ログ関連 =====
const LOGS_DIR = path.join(__dirname, 'logs');
if (!fs.existsSync(LOGS_DIR)) fs.mkdirSync(LOGS_DIR, { recursive: true });

function getDateStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function logChat(sender, message, type = 'user') {
  const timestamp = new Date().toISOString();
  const logFile = path.join(LOGS_DIR, `chat_${getDateStr()}.log`);
  const logEntry = `[${timestamp}] ${type.toUpperCase()}: ${sender}: ${message}\n`;
  fs.appendFileSync(logFile, logEntry, 'utf8');
}

function logAIReply(sender, message, reply, commands, build) {
  const timestamp = new Date().toISOString();
  const logFile = path.join(LOGS_DIR, `chat_${getDateStr()}.log`);
  const logEntry = `[${timestamp}] AI_REPLY: ${sender}: ${reply}\n`;
  fs.appendFileSync(logFile, logEntry, 'utf8');
  if (commands.length > 0) {
    fs.appendFileSync(logFile, `  COMMANDS: ${commands.join(' | ')}\n`, 'utf8');
  }
  if (build) {
    fs.appendFileSync(logFile, `  BUILD: ${JSON.stringify(build)}\n`, 'utf8');
  }
}

// ===== 会話履歴の永続化 =====
const MEMORY_FILE = path.join(LOGS_DIR, 'ai_memory.json');

function loadAIMemory() {
  if (!fs.existsSync(MEMORY_FILE)) return new Map();
  try {
    const data = JSON.parse(fs.readFileSync(MEMORY_FILE, 'utf8'));
    const map = new Map();
    for (const [sender, history] of Object.entries(data)) {
      map.set(sender, history || []);
    }
    console.log(`📚 AI記憶を読み込みました: ${map.size}プレイヤーの履歴`);
    return map;
  } catch (e) {
    console.warn('⚠️ AI記憶ファイルの読み込みに失敗:', e.message);
    return new Map();
  }
}

function saveAIMemory(chatHistory) {
  const data = {};
  for (const [sender, history] of chatHistory) {
    data[sender] = history;
  }
  try {
    fs.writeFileSync(MEMORY_FILE, JSON.stringify(data, null, 2), 'utf8');
  } catch (e) {
    console.warn('⚠️ AI記憶ファイルの保存に失敗:', e.message);
  }
}

let bot = null, connecting = false, reconnectTimer = null;
let lastRespawnAt = 0;

function post(path, payload) {
  return new Promise((resolve, reject) => {
    const body = Buffer.from(JSON.stringify(payload));
    const req = http.request({
      hostname: '127.0.0.1', port: 3001, path, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': body.length },
      timeout: 10000
    }, res => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        let parsed = {};
        try { parsed = JSON.parse(data); } catch {}
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(new Error(parsed.error || `server.py HTTP ${res.statusCode}`));
        } else resolve(parsed);
      });
    });
    req.on('timeout', () => req.destroy(new Error('server.py timeout')));
    req.on('error', reject);
    req.end(body);
  });
}

async function askLLM(prompt, history = []) {
  // 先頭の長いルール部分は system、プレイヤーの発言以降は user に分ける(会話履歴をその間に挟む)
  const cut = prompt.indexOf('プレイヤー名: ');
  const rules = cut > 0 ? prompt.slice(0, cut) : '';
  const turn = cut > 0 ? prompt.slice(cut) : prompt;
  const res = await fetch(LLM_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${API_KEY}` },
    body: JSON.stringify({
      model: MODEL,
      messages: [
        { role: 'system', content: rules + '\n必ずJSONオブジェクトのみを出力してください。' },
        ...history,
        { role: 'user', content: turn }
      ],
      response_format: { type: 'json_object' },
      temperature: 0.7,
      max_tokens: 4000,
      stream: false,
      ...(/gpt-oss/i.test(MODEL) ? { reasoning_effort: 'low' } : {})
    }),
    signal: AbortSignal.timeout(30000)
  });
  if (!res.ok) {
    const t = await res.text().catch(() => '');
    throw new Error(`LLM HTTP ${res.status}: ${t.slice(0, 200)}`);
  }
  const json = await res.json();
  const choice = json.choices?.[0];
  if (choice?.finish_reason === 'length') throw new Error('LLMの出力が長すぎて途中で切れました');
  return choice?.message?.content || '';
}

function parseJson(text) {
  let s = String(text || '').trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, '');
  const start = s.indexOf('{'), end = s.lastIndexOf('}');
  if (start >= 0 && end > start) s = s.slice(start, end + 1);
  return JSON.parse(s);
}
// 自分が/sayした文を覚えておき、チャットに流れてきても反応しない(無限ループ防止)
const ownTexts = [];
function normalizeText(s) {
  return String(s || '').replace(/§./g, '').replace(/\s+/g, ' ').trim();
}
function rememberOwn(text) {
  ownTexts.push({ t: normalizeText(text).slice(0, 40), at: Date.now() });
  while (ownTexts.length > 300) ownTexts.shift();
}
function isOwnMessage(message) {
  const m = normalizeText(message);
  if (m.includes('【AI】') || m.includes('[AI]') || m.includes('(AI)')) return true;
  const now = Date.now();
  return ownTexts.some(o => o.t && now - o.at < 10 * 60 * 1000 && m.includes(o.t));
}
const IGNORE_SENDERS = /^(外部|server|サーバー|rcon|console)$/i;
let lastBusyNoticeAt = 0;

function mentionsAI(s) {
  return /(^|\s)ai([、,！!?\s]|$)/i.test(s) || /^AI[、\s]/i.test(s);
}

function cleanMessage(value) {
  if (value == null) return '';
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === 'object') return cleanMessage(parsed.text || parsed.message || parsed.rawtext?.[0]?.text);
    } catch {}
    return value.trim();
  }
  if (Array.isArray(value)) return value.map(cleanMessage).filter(Boolean).join(' ').trim();
  if (typeof value === 'object') return cleanMessage(value.text || value.message || value.parameters || value.rawtext);
  return String(value).trim();
}

function handleTextPacket(packet) {
  if (!packet) return;
  const type = String(packet.type || '').toLowerCase();
  if (type && !['chat', 'whisper', 'announcement', 'raw', 'translation', 'json'].includes(type)) return;
  const sender = packet.source_name || packet.source || packet.sender || 'プレイヤー';
  if (bot && bot.username && sender === bot.username) return;
  if (IGNORE_SENDERS.test(normalizeText(packet.source_name || packet.source || packet.sender || ''))) return;
  const message = cleanMessage(packet.message || packet.text || packet.parameters);
  if (message) void handleChat(sender, message);
}

function requestRespawn(reason) {
  if (!bot || typeof bot.write !== 'function') return;
  const now = Date.now();
  if (now - lastRespawnAt < 3000) return;
  lastRespawnAt = now;
  const start = bot.startGameData || {};
  const position = start.player_position || { x: 0, y: 0, z: 0 };
  const runtime_entity_id = bot.entityId || start.runtime_entity_id || 0;
  setTimeout(() => {
    try {
      bot.write('respawn', { position, state: 2, runtime_entity_id });
      console.log(`🩹 リスポーン要求を送信しました: ${reason}`);
    } catch (e) {
      console.error('⚠️ リスポーン要求に失敗:', e.message || e);
    }
  }, 800);
}

const MAX_COMMANDS = 50;   // 1回の返答で実行する最大コマンド数
const MAX_ROUNDS = 5;     // 続きを自動で頼む最大回数
const sleep = ms => new Promise(r => setTimeout(r, ms));
let busy = false;
let lastJob = null;       // 途中で終わった作業(「AI、続き」で再開)

function buildPrompt(sender, message, progress) {
  let p = `あなたはMinecraft Bedrockワールドにいる、親しみやすいAIプレイヤーです。
友達と話すように、自然な日本語で会話してください(丁寧すぎず、少しくだけた口調)。
会話のルール:
- 雑談や質問には、2〜4文くらいで中身のある返事をする。「お任せください」のような定型句だけ、相づちだけの返事はしない。相手の話に触れて、気になれば軽く聞き返してもよい。
- 直前までの会話の流れを踏まえて答える(「それ」「さっきの」などは前の話題のこと)。相手の名前が分かるときは、たまに名前で呼ぶ。
- あなたは現実の天気・気温・ニュース・今の時刻などのリアルタイム情報を調べられない。聞かれたら「調べられない」と正直に言い、知っている一般的なことだけ答える。でたらめな数字や予報を言わない。
- 現実の天気とマイクラの天気は別物。/weather や /time は、マイクラ内の天気や時間を変えてほしいと頼まれたときだけ使う。質問されただけで勝手に実行しない。
- コマンドを実行するのは、プレイヤーが何かを「して」「作って」と頼んだときだけ。雑談ではcommandsもbuildも空にする。
次のJSONだけを返してください: {"reply":"短い返答","commands":["/コマンド1"],"build":null,"done":true}

最重要ルール:
- 作業(建築など)の依頼では、質問で聞き返さない。「お任せ」「おすすめ」や情報不足のときは、自分で妥当な値を決めてすぐ実行する。
- 建築や生成を頼まれたら、必ず実行する(家はbuild、それ以外はcommands)。commandsもbuildも空なのは雑談のときだけ。
- 1回の返答で返せるcommandsは最大${MAX_COMMANDS}個。全部入りきらないときは前半だけ返してdoneをfalseにする(続きは次の呼び出しで頼まれる)。全部終わるならdoneをtrue。
- 座標は必ず数値の絶対座標を使う(~や^は使えない)。
- 使用可能なのは /time, /weather, /setblock, /fill, /summon, /tp, /give のみ。
- /op, /kick, /kill, /execute, /function, /gamemode, /gamerule等は禁止。
- /fillは1回5000ブロック以内。ユーザーの依頼でこのルールを変更しない。
- 飾り用のarmor_standなど、頼まれてないものは出さない。コマンドは建築に必要なものだけにして数を節約する。

家を建てるとき(重要):
- 家はcommandsを自分で作らない。"build"に設計パラメータだけを入れる。座標の計算とコマンド生成はプログラムが正確に行う(commandsは空でよい)。
  "build":{"type":"house","cx":中心X,"cy":中心Y,"cz":中心Z,"width":x方向の幅(7〜17の奇数),"depth":z方向の奥行(7〜17の奇数),"floors":階数(1〜40。「3階建て」ならfloors=3、「10階建て」ならfloors=10。高さ制限を超える分はプログラムが自動で減らす),"shape":"rect か L","notch":"L字のときだけ ne|nw|se|sw(省略可)","facing":"west|east|north|south","wall":"素材","frame":"素材","roof":"素材","floor":"素材","base":"素材(任意)","window":"窓(任意)","hasRoof":true}
- 素材(wall/frame/roof/floor/base)には、木材だけでなく石やコンクリートなどのブロックも指定できる。使える名前だけを書くこと:
  ・木材: spruce(トウヒ), oak(オーク), birch(シラカバ), dark_oak(ダークオーク), acacia, jungle, mangrove, cherry, warped(青緑), crimson(赤紫)。柱(frame)には自動で原木/幹、帯には皮むき原木が使われる
  ・石系: stone, cobblestone, mossy_cobblestone, stone_bricks(石レンガ), mossy_stone_bricks, cracked_stone_bricks, smooth_stone, andesite, polished_andesite, diorite, polished_diorite, granite, polished_granite, deepslate_bricks, deepslate_tiles, cobbled_deepslate, polished_deepslate, tuff, calcite, brick_block(レンガ), mud_bricks, sandstone, smooth_sandstone, cut_sandstone, red_sandstone, quartz_block, smooth_quartz, quartz_bricks, quartz_pillar(石英の柱), purpur_block, prismarine_bricks, dark_prismarine, nether_brick, blackstone, polished_blackstone_bricks, end_bricks, copper_block, cut_copper, exposed_cut_copper, weathered_cut_copper, oxidized_cut_copper, bone_block, terracotta, clay
  ・色付き: 「色」_concrete(コンクリート), 「色」_terracotta(テラコッタ), 「色」_wool(羊毛)。色は white, orange, magenta, light_blue, yellow, lime, pink, gray, light_gray, cyan, purple, blue, brown, green, red, black
  ・日本語の依頼はこの名前に直す(例: 石レンガ→stone_bricks、レンガ→brick_block、白いコンクリート→white_concrete、石英→quartz_block)。上の一覧に無いブロックは使わず、近いものを選ぶ。
- window は glass_pane(既定), glass, iron_bars, 「色」_stained_glass_pane のどれか。base は壁の下の土台の素材(既定は cobblestone)。
- 屋根: 既定は屋根あり(hasRoof=true)。「屋根なし」「屋根はいらない」「豆腐建築」「フラット」「箱型」と言われたら hasRoof=false にする(平らな箱になる)。屋根ありのときだけ roof の素材が使われる。「屋根もつけて」「豆腐建築はNG」は屋根ありの意味。
- 柱(frame)は壁と違う素材にすると豆腐に見えにくい(例: 壁=white_concrete、柱=quartz_pillar か stone_bricks)。frameを省略すると壁と同じになる。
- 「L字」「L型」と言われたら shape を "L" にする(それ以外は "rect")。L字は width/depth を 11〜17 にし、notch(欠ける角)は省略すると裏側の角になる。L字で素材の指定が無いときは wall=warped, frame=crimson, roof=warped, floor=dark_oak, floors=2 を既定にする(青緑の家)。
- facing(正面の向き): xが-1の方向=west、xが+1の方向=east、zが-1の方向=north、zが+1の方向=south。指定がなければwest。
- cx,cy,cz はユーザーが言った中心座標(数値)。座標が全く無いときだけ、buildは使わず座標を教えてと一言返す。
- 半径N以内の指定があるときは、屋根の張り出し(+1)を含めて収まるようにwidth/depthを決める。指定がなければ 11x9 くらい。2階建てなら floors=2。
- 見た目は素材の組み合わせで決める。温かみ=wall:spruce, frame:dark_oak, roof:dark_oak, floor:spruce など。

プレイヤー名: ${sender}
依頼: ${message}`;
  if (progress && progress.executed.length) {
    const done = progress.executed.join('\n').slice(-6000);
    p += `\n\n--- 進行状況(ラウンド${progress.round}) ---\n実行済みコマンド:\n${done}`;
    if (progress.failedLast.length) {
      p += `\n\n直前に拒否・失敗したコマンド(原因を直して再送すること):\n${progress.failedLast.join('\n')}`;
    }
    p += `\n\n実行済みの分は繰り返さず、残りだけをcommandsで返してください。全部終わったらdoneをtrueにしてください。`;
  }
  return p;
}


// ---- 会話の記憶(プレイヤーごとに直近6往復・30分) ----
let chatHistory = loadAIMemory();
const HISTORY_MAX = 12, HISTORY_TTL = 30 * 60 * 1000;
function getHistory(sender) {
  const now = Date.now();
  const h = (chatHistory.get(sender) || []).filter(m => now - m.at < HISTORY_TTL);
  chatHistory.set(sender, h);
  return h.map(({ role, content }) => ({ role, content }));
}
function pushHistory(sender, userText, reply) {
  const h = chatHistory.get(sender) || [];
  const at = Date.now();
  h.push({ role: 'user', content: `プレイヤー名: ${sender}\n依頼: ${userText}`, at });
  h.push({ role: 'assistant', content: JSON.stringify({ reply, commands: [], build: null, done: true }), at });
  while (h.length > HISTORY_MAX) h.shift();
  chatHistory.set(sender, h);
  saveAIMemory(chatHistory);  // 保存
}
// 長い返事は文の区切りで分けて、複数行のチャットにする
function splitForChat(text, max = 100) {
  const sentences = String(text).match(/[^。！？!?\n]+[。！？!?]?/g) || [String(text)];
  const lines = [];
  let cur = '';
  for (const s of sentences.map(x => x.trim()).filter(Boolean)) {
    if (cur && (cur + s).length > max) { lines.push(cur); cur = s; }
    else cur += s;
  }
  if (cur) lines.push(cur);
  return lines.flatMap(l => l.length > 200 ? l.match(/.{1,200}/g) : [l]);
}
async function sayLong(text) {
  for (const line of splitForChat(text)) { await sayAI(line); await sleep(250); }
}

async function sayAI(text) {
  rememberOwn(text);
  try { await post('/say', { text: `【AI】 ${text}` }); }
  catch (e) { console.error('server.py接続エラー:', e.message || e); }
}

async function handleChat(sender, message) {
  if (isOwnMessage(message)) return;
  if (!mentionsAI(message)) return;
  sender = String(sender).replace(/§./g, '').trim() || 'プレイヤー';
  {   // 「[Owner] HAYATO3997: AI, こんにちは」→ 名前とメッセージに分ける
    const mm = normalizeText(message).match(/^(?:\[[^\]]*\]\s*)*([^:：\s][^:：]*?)\s*[:：]\s+(.+)$/);
    if (mm && mentionsAI(mm[2])) {
      if (sender === 'プレイヤー') sender = mm[1].trim();
      message = mm[2].trim();
    }
  }
  
  // ログに記録
  logChat(sender, message, 'user');
  
  if (busy) {
    if (Date.now() - lastBusyNoticeAt > 15000) {   // 15秒に1回だけ知らせる
      lastBusyNoticeAt = Date.now();
      await sayAI('いま作業中！終わるまで少し待ってね。');
    }
    return;
  }
  busy = true;

  // 「続き」なら前回の作業を再開
  let job;
  if (/続き|つづき/.test(message) && lastJob && !lastJob.finished) {
    job = lastJob;
    job.round = 0;
    await sayAI('さっきの続きをやるね！');
  } else {
    job = { sender, message, executed: [], failedLast: [], round: 0, finished: false, ok: 0, ng: 0 };
  }
  lastJob = job;

  try {
    console.log(`🧠 ${MODEL}: ${sender}: ${message}`);
    for (let round = 1; round <= MAX_ROUNDS; round++) {
      job.round = round;
      const text = await askLLM(buildPrompt(job.sender, job.message, job.executed.length ? job : null), getHistory(job.sender));
      const data = parseJson(text);
      const reply = typeof data.reply === 'string' ? data.reply.trim() : '';
      let commands = Array.isArray(data.commands) ? data.commands : [];
      if (typeof data.command === 'string' && data.command.trim()) commands.push(data.command);
      commands = commands.filter(c => typeof c === 'string').map(c => c.trim()).filter(Boolean);
      let isBuild = false;
      if (data.build && typeof data.build === 'object') {
        try {
          const built = houseCommands(data.build);
          commands = commands.concat(built);
          for (const w of built.warnings || []) await sayAI(w);
          isBuild = true;
          console.log(`🏠 家の設計: ${JSON.stringify(data.build)} → ${commands.length}コマンド`);
        } catch (e) {
          console.warn('⚠️ 家の設計エラー:', e.message || e);
          await sayAI(`家の設計がうまくいかなかった(${e.message || e})。座標を確認してもう一度言ってね。`);
        }
      }
      const limit = isBuild ? 2000 : MAX_COMMANDS;
      const cut = !isBuild && commands.length > limit;
      commands = commands.slice(0, limit);

      if (reply) { await sayLong(reply); console.log(`💬 返答をserver.pyへ送信: ${reply}`); }
      if (round === 1 && reply) {
        pushHistory(job.sender, job.message, reply);
        logAIReply(job.sender, job.message, reply, commands, data.build);
      }

      if (!(await confirmFarFills(commands, job))) { job.finished = true; job.cancelled = true; break; }

      job.failedLast = [];
      for (const command of commands) {
        if (!command.startsWith('/')) { job.failedLast.push(`${command} → 先頭が/ではありません`); job.ng++; continue; }
        try {
          await post('/command', { command });
          job.executed.push(command);
          job.ok++;
          console.log(`⚙️ コマンド受付: ${command}`);
        } catch (e) {
          job.failedLast.push(`${command} → ${e.message || e}`);
          job.ng++;
          console.warn(`⚠️ コマンド拒否: ${command} → ${e.message || e}`);
        }
        await sleep(120);
      }

      const wantsMore = !isBuild && (data.done === false || cut);
      const needsFix = !isBuild && job.failedLast.length > 0 && commands.length > 0;
      if (!commands.length || (!wantsMore && !needsFix)) { job.finished = true; break; }
      if (round < MAX_ROUNDS) await sayAI(`続きを作ってるよ…(${round}/${MAX_ROUNDS})`);
    }
    if (job.cancelled) {
      // 中止の連絡は済んでいるので、完了メッセージは出さない
    } else {
    if (job.failedLast.length) {
      for (const f of job.failedLast.slice(0, 3)) await sayAI(`失敗: ${f}`.slice(0, 200));
    }
    if (job.finished) {
      if (job.ok + job.ng > 0) await sayAI(`できた！ コマンド成功${job.ok}件${job.ng ? ` / 失敗${job.ng}件` : ''}`);
    } else {
      await sayAI(`途中まで進めたよ(成功${job.ok}件)。「AI、続きをやって」で再開できるよ！`);
    }
    }
  } catch (e) {
    console.error('❌ AI/連携エラー:', e.message || e);
    if (job.executed.length) await sayAI('途中でエラーが出ちゃった。「AI、続きをやって」でもう一回やれるよ！');
    else await sayAI('ごめん、うまく処理できなかった！もう一度言ってね。');
  } finally {
    busy = false;
  }
}


// ---- 安全機能: プレイヤーから50マス以上離れた /fill は、コンソールで yes と入力するまで実行しない ----
const FAR_LIMIT = 50;
const APPROVAL_TIMEOUT_MS = 120000;
let approvalResolver = null;
const consoleReader = readline.createInterface({ input: process.stdin });
consoleReader.on('line', line => {
  if (!approvalResolver) return;            // 確認待ちでないときの入力は無視
  const resolve = approvalResolver;
  approvalResolver = null;
  resolve(line.trim().toLowerCase());
});
consoleReader.on('close', () => { if (approvalResolver) { const r = approvalResolver; approvalResolver = null; r('closed'); } });

function askConsole(timeoutMs) {
  return new Promise(resolve => {
    const timer = setTimeout(() => { if (approvalResolver) { approvalResolver = null; resolve('timeout'); } }, timeoutMs);
    approvalResolver = answer => { clearTimeout(timer); resolve(answer); };
  });
}

function getJson(path) {
  return new Promise((resolve, reject) => {
    const req = http.get({ hostname: '127.0.0.1', port: 3001, path, timeout: 10000 }, res => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        let parsed = {};
        try { parsed = JSON.parse(data); } catch {}
        if (res.statusCode < 200 || res.statusCode >= 300) reject(new Error(parsed.error || `server.py HTTP ${res.statusCode}`));
        else resolve(parsed);
      });
    });
    req.on('timeout', () => req.destroy(new Error('server.py timeout')));
    req.on('error', reject);
  });
}

// true=実行してよい / false=中止
async function confirmFarFills(commands, job) {
  if (job.farApproved) return true;
  if (!commands.some(c => /^\/fill\s/i.test(c))) return true;
  let pos = null, posError = '';
  try { pos = await getJson('/player_position'); } catch (e) { posError = e.message || String(e); }
  const far = findFarFills(commands, pos, FAR_LIMIT);
  if (!far.length) return true;

  const maxDist = far.reduce((m, f) => Math.max(m, f.dist || 0), 0);
  console.warn('');
  console.warn('⚠️ ============================================================');
  if (pos) {
    console.warn(`⚠️ プレイヤー(${pos.x.toFixed(0)}, ${pos.y.toFixed(0)}, ${pos.z.toFixed(0)})から${FAR_LIMIT}マス以上離れた /fill が ${far.length} 件あります(最大 約${maxDist.toFixed(0)}マス)`);
  } else {
    console.warn(`⚠️ プレイヤーの位置を確認できませんでした(${posError})。安全のため /fill ${far.length} 件の確認が必要です`);
  }
  console.warn(`⚠️ 例: ${far[0].command}`);
  console.warn(`⚠️ 許可するならこの画面で yes と入力して Enter。それ以外(または${APPROVAL_TIMEOUT_MS / 1000}秒放置)で中止します。`);
  console.warn('⚠️ ※ チャットで「許可」と返しても無効です');
  console.warn('⚠️ ============================================================');
  await sayAI('注意:プレイヤーから離れた位置の建築を許可しますか？');
  await sayAI('bot.jsのコンソール画面で yes と入力してください(チャットの返事は無効です)');

  const answer = await askConsole(APPROVAL_TIMEOUT_MS);
  if (answer === 'yes' || answer === 'y') {
    console.log('✅ 許可されました。建築を続けます。');
    job.farApproved = true;
    return true;
  }
  console.log(`🚫 許可されなかったので中止しました(入力: ${answer || '空'})`);
  await sayAI('離れた位置の建築は許可されなかったので、中止したよ。');
  return false;
}

function startChatHttpServer() {
  const server = http.createServer((req, res) => {
    if (req.method !== 'POST' || req.url !== '/chat') {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'not found' }));
      return;
    }
    let body = '';
    req.setEncoding('utf8');
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const data = JSON.parse(body || '{}');
        const sender = cleanMessage(data.sender) || 'プレイヤー';
        const message = cleanMessage(data.message);
        if (message) void handleChat(sender, message);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: e.message || String(e) }));
      }
    });
  });
  server.listen(CHAT_HTTP_PORT, '127.0.0.1', () => {
    console.log(`💬 server.pyからのチャット受付: http://127.0.0.1:${CHAT_HTTP_PORT}/chat`);
  });
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connectBot(); }, 5000);
}
function connectBot() {
  if (connecting) return;
  connecting = true;
  console.log(`🔄 Minecraftに接続中: ${MC_HOST}:${MC_PORT}`);
  try {
    bot = bedrock.createClient({
      host: MC_HOST, port: MC_PORT, offline: false, authFlow: 'msa'
    });
  } catch (e) {
    connecting = false;
    console.error('接続開始エラー:', e.message || e);
    scheduleReconnect();
    return;
  }
  bot.on('join', () => console.log('🔐 ログイン処理中...'));
  bot.on('spawn', () => {
    connecting = false;
    console.log('🎉 AIボットがワールドに参加しました！');
    console.log('ゲーム内で「AI、やっほー」と話しかけてね。');
  });
  bot.on('text', handleTextPacket);
  bot.on('set_health', packet => {
    if (Number(packet.health) <= 0) requestRespawn('health=0');
  });
  bot.on('entity_event', packet => {
    const isMe = String(packet.runtime_entity_id) === String(bot?.entityId || bot?.startGameData?.runtime_entity_id);
    if (isMe && packet.event_id === 'death_animation') requestRespawn('death_animation');
  });
  bot.on('respawn', packet => {
    if (packet.state === 0 || packet.state === 'searching_for_spawn') requestRespawn('server_respawn');
  });
  bot.on('kick', reason => console.error('⚠️ キックされました:', reason));
  bot.on('error', err => console.error('⚠️ Minecraft接続エラー:', err.message || err));
  bot.on('close', () => {
    connecting = false; bot = null;
    console.log('🔌 接続切断。5秒後に再接続します。');
    scheduleReconnect();
  });
}
startChatHttpServer();
connectBot();
