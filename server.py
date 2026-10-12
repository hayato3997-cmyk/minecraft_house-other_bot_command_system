// 家の設計パラメータ → Minecraft Bedrock用コマンド列(絶対座標)を生成する。
// 座標計算はAIではなくここで行うので、屋根が浮いたり形が崩れたりしない。
const WOODS = ['spruce', 'oak', 'birch', 'dark_oak', 'acacia', 'jungle', 'mangrove', 'cherry', 'warped', 'crimson'];
const NETHER = ['warped', 'crimson'];   // 原木ではなく幹(stem)という名前になる
const logName = w => NETHER.includes(w) ? `${w}_stem` : `${w}_log`;
const strippedName = w => NETHER.includes(w) ? `stripped_${w}_stem` : `stripped_${w}_log`;

// ---- 素材(木材以外のブロックも指定できる) ----
const COLORS = ['white', 'orange', 'magenta', 'light_blue', 'yellow', 'lime', 'pink', 'gray', 'light_gray',
  'cyan', 'purple', 'blue', 'brown', 'green', 'red', 'black'];
// 使ってよいブロックID(Bedrock)。ここに無い名前はコマンドに入れない
const STONES = new Set([
  'stone', 'cobblestone', 'mossy_cobblestone', 'stone_bricks', 'mossy_stone_bricks', 'cracked_stone_bricks',
  'chiseled_stone_bricks', 'smooth_stone', 'andesite', 'polished_andesite', 'diorite', 'polished_diorite',
  'granite', 'polished_granite', 'deepslate', 'cobbled_deepslate', 'polished_deepslate', 'deepslate_bricks',
  'deepslate_tiles', 'cracked_deepslate_bricks', 'tuff', 'polished_tuff', 'tuff_bricks', 'calcite',
  'brick_block', 'mud_bricks', 'packed_mud', 'sandstone', 'smooth_sandstone', 'cut_sandstone', 'chiseled_sandstone',
  'red_sandstone', 'smooth_red_sandstone', 'cut_red_sandstone', 'chiseled_red_sandstone',
  'quartz_block', 'smooth_quartz', 'quartz_bricks', 'quartz_pillar', 'chiseled_quartz_block',
  'purpur_block', 'purpur_pillar', 'prismarine', 'prismarine_bricks', 'dark_prismarine',
  'nether_brick', 'red_nether_brick', 'chiseled_nether_bricks', 'cracked_nether_bricks',
  'blackstone', 'polished_blackstone', 'polished_blackstone_bricks', 'chiseled_polished_blackstone',
  'cracked_polished_blackstone_bricks', 'end_stone', 'end_bricks', 'copper_block', 'cut_copper',
  'exposed_cut_copper', 'weathered_cut_copper', 'oxidized_cut_copper', 'bone_block', 'bamboo_block',
  'bamboo_planks', 'bamboo_mosaic', 'terracotta', 'clay', 'coal_block', 'gold_block', 'iron_block',
  'diamond_block', 'emerald_block', 'lapis_block', 'obsidian', 'glowstone',
]);
// 呼び方が違うもの → 正しいBedrockのID
const ALIASES = {
  brick: 'brick_block', bricks: 'brick_block', red_bricks: 'brick_block', stone_brick: 'stone_bricks',
  end_stone_bricks: 'end_bricks', nether_bricks: 'nether_brick', red_nether_bricks: 'red_nether_brick',
  quartz: 'quartz_block', copper: 'copper_block', concrete: 'white_concrete', wool: 'white_wool',
  mud_brick: 'mud_bricks', polished_blackstone_brick: 'polished_blackstone_bricks',
};
const colorBlock = s => {
  const m = s.match(/^(.*)_(concrete|terracotta|wool)$/);
  return !!(m && COLORS.includes(m[1]));
};
const woodMat = w => ({ plank: `${w}_planks`, log: logName(w), strip: strippedName(w), name: w });
const blockMat = id => ({ plank: id, log: id, strip: id, name: id });

// 素材名 → {plank(壁・床・屋根用), log(柱用), strip(帯用)}。使えない名前はfallbackにして警告を出す
function material(v, fallback, warnings, label) {
  let s = String(v == null ? '' : v).toLowerCase().replace(/^minecraft:/, '').replace(/[^a-z_]/g, '');
  if (!s) return fallback;
  if (ALIASES[s]) s = ALIASES[s];
  if (WOODS.includes(s)) return woodMat(s);
  const m = s.match(/^(.*)_(planks|log|stem|wood)$/);
  if (m && WOODS.includes(m[1])) return woodMat(m[1]);
  if (STONES.has(s) || colorBlock(s)) return blockMat(s);
  warnings.push(`${label}の「${v}」は使えないので ${fallback.name} にしたよ`);
  return fallback;
}
function windowBlock(v, warnings) {
  const s = String(v == null ? '' : v).toLowerCase().replace(/^minecraft:/, '').replace(/[^a-z_]/g, '');
  if (!s) return 'glass_pane';
  if (s === 'glass_pane' || s === 'glass' || s === 'iron_bars') return s;
  const m = s.match(/^(.*)_stained_glass(_pane)?$/);
  if (m && COLORS.includes(m[1])) return s;
  warnings.push(`窓の「${v}」は使えないので glass_pane にしたよ`);
  return 'glass_pane';
}

const FACINGS = ['west', 'east', 'north', 'south'];

function wood(v, def) {
  const s = String(v || '').toLowerCase().replace(/[^a-z_]/g, '');
  return WOODS.includes(s) ? s : def;
}
function toInt(v, def) {
  const n = Math.round(Number(v));
  return Number.isFinite(n) ? n : def;
}
function oddClamp(v, def, lo, hi) {
  let n = Math.min(hi, Math.max(lo, toInt(v, def)));
  if (n % 2 === 0) n = n + 1 > hi ? n - 1 : n + 1;
  return n;
}

const MAX_FLOORS = 40;     // 階数の上限
const BUILD_TOP = 319;     // 建築できる一番上の高さ(オーバーワールド)
const ROOF_ROOM = 11;      // 屋根に必要な高さの余裕

// くり抜きの壁を、/fill の上限(5000ブロック)に収まる高さごとに分けて作る。
// 分け目は各階の床の高さにそろえる(あとで床板が張られる層なので、見た目に影響しない)
function hollowChunks(fill, x1, z1, x2, z2, Y, floors, block) {
  const area = (x2 - x1 + 1) * (z2 - z1 + 1);
  const per = Math.max(1, Math.floor((4800 / area - 1) / 4));   // 1回で何階分くり抜けるか
  for (let f0 = 0; f0 < floors; f0 += per) {
    const f1 = Math.min(floors, f0 + per);
    const bottom = f0 === 0 ? Y - 1 : Y + 4 * f0 - 1;
    fill(x1, bottom, z1, x2, Y + 4 * f1 - 1, z2, block, 'hollow');
  }
}

function houseCommands(b) {
  const shape = String(b.shape || 'rect').toLowerCase() === 'l' ? 'L' : 'rect';
  const cx = Number(b.cx), cy = Number(b.cy), cz = Number(b.cz);
  if (![cx, cy, cz].every(Number.isFinite)) throw new Error('建築の中心座標が不正です');
  const X = Math.round(cx), Y = Math.round(cy), Z = Math.round(cz);
  const w = oddClamp(b.width, 11, shape === 'L' ? 9 : 7, 17);   // x方向
  const d = oddClamp(b.depth, 9, shape === 'L' ? 9 : 7, 17);    // z方向
  const facing = FACINGS.includes(String(b.facing).toLowerCase()) ? String(b.facing).toLowerCase() : 'west';
  const warnings = [];
  const wallM = material(b.wall, woodMat('spruce'), warnings, '壁');
  const frameM = b.frame ? material(b.frame, wallM, warnings, '柱') : wallM;
  // 屋根なし(豆腐建築)の指定: hasRoof=false / roofStyle="none" / roof="none"
  const noRoof = b.hasRoof === false || ['false', 'no', '0'].includes(String(b.hasRoof).toLowerCase()) ||
    ['none', 'flat'].includes(String(b.roofStyle || '').toLowerCase()) || String(b.roof || '').toLowerCase() === 'none';
  // 階数: 上限(40階)と高さ制限(Y=319)に収める
  const floorsReq = Math.max(1, toInt(b.floors, 1));
  const floorsCap = Math.min(MAX_FLOORS, Math.max(1, Math.floor((BUILD_TOP - (noRoof ? 0 : ROOF_ROOM) - Y + 1) / 4)));
  const floors = Math.min(floorsCap, floorsReq);
  if (floorsReq > floors) warnings.push(`${floorsReq}階建ては高さ制限などで作れないので、${floors}階建てにしたよ`);
  const roofM = noRoof ? woodMat('dark_oak') : material(b.roof, woodMat('dark_oak'), warnings, '屋根');
  const floorM = b.floor ? material(b.floor, wallM, warnings, '床') : wallM;
  const baseM = b.base ? material(b.base, blockMat('cobblestone'), warnings, '土台') : blockMat('cobblestone');
  const wallB = wallM.plank;
  const frameB = frameM.log;
  const stripB = frameM.strip;
  const floorB = floorM.plank;
  const roofB = roofM.plank;
  const baseB = baseM.plank;
  const windowB = windowBlock(b.window, warnings);

  if (shape === 'L') {
    const out = lShape({ b, X, Y, Z, w, d, floors, facing, wallB, frameB, stripB, floorB, roofB, baseB, windowB, noRoof });
    out.warnings = warnings;
    return out;
  }
  const hx = (w - 1) / 2, hz = (d - 1) / 2;
  const x1 = X - hx, x2 = X + hx, z1 = Z - hz, z2 = Z + hz;
  const yT = Y + 4 * floors - 1;      // 壁の上端
  const C = [];
  const fill = (ax, ay, az, bx, by, bz, block, mode) =>
    C.push(`/fill ${ax} ${ay} ${az} ${bx} ${by} ${bz} ${block}${mode ? ' ' + mode : ''}`);
  const set = (x, y, z, block) => C.push(`/setblock ${x} ${y} ${z} ${block}`);
  // 1段だけの輪っか(外周)。1段のfillにoutlineを使うと全面が塗られてしまうので4本のfillで作る
  const ring = (y, block) => {
    fill(x1, y, z1, x2, y, z1, block);
    fill(x1, y, z2, x2, y, z2, block);
    fill(x1, y, z1 + 1, x1, y, z2 - 1, block);
    fill(x2, y, z1 + 1, x2, y, z2 - 1, block);
  };

  // 1. 基礎(傾斜地でも浮かないよう下も埋める)
  fill(x1, Y - 3, z1, x2, Y - 1, z2, baseB);
  // 2. 壁(中は空洞。hollowは上下の面も殻として残るので、Y-1が床、yTが天井になる)
  hollowChunks(fill, x1, z1, x2, z2, Y, floors, wallB);
  // 床
  fill(x1, Y - 1, z1, x2, Y - 1, z2, floorB);
  // 3. 土台の石の帯
  ring(Y, baseB);
  // 4. 階の境目の帯と軒下の帯(皮むき原木)
  for (let i = 1; i <= floors; i++) ring(Y + 4 * i - 1, stripB);
  // 5. 床板(各階の天井兼床)
  for (let i = 1; i <= floors; i++) fill(x1 + 1, Y + 4 * i - 1, z1 + 1, x2 - 1, Y + 4 * i - 1, z2 - 1, floorB);
  // 6. 四隅の柱
  for (const [px, pz] of [[x1, z1], [x2, z1], [x1, z2], [x2, z2]]) fill(px, Y, pz, px, yT, pz, frameB);
  // 7. 上の階への階段(普通のブロックを1段ずつ高くする。ジャンプで登れる)
  //    各階ごとに、奥の壁沿いの角から3段 → 4マス目は上の階の床に着く。段の真上の床は穴にする
  //    階ごとに角を交互(手前側/奥側)にして、階段同士がぶつからないようにする
  for (let i = 1; i < floors; i++) {
    const base = Y + 4 * (i - 1);
    const far = (i % 2 === 0);   // 偶数番目の階段は反対側の角
    let steps; // [x, z] を低い段から順に
    if (facing === 'west') {
      const sx = x2 - 1;
      steps = far ? [[sx, z2 - 1], [sx, z2 - 2], [sx, z2 - 3]] : [[sx, z1 + 1], [sx, z1 + 2], [sx, z1 + 3]];
    } else if (facing === 'east') {
      const sx = x1 + 1;
      steps = far ? [[sx, z2 - 1], [sx, z2 - 2], [sx, z2 - 3]] : [[sx, z1 + 1], [sx, z1 + 2], [sx, z1 + 3]];
    } else if (facing === 'north') {
      const sz = z2 - 1;
      steps = far ? [[x2 - 1, sz], [x2 - 2, sz], [x2 - 3, sz]] : [[x1 + 1, sz], [x1 + 2, sz], [x1 + 3, sz]];
    } else {
      const sz = z1 + 1;
      steps = far ? [[x2 - 1, sz], [x2 - 2, sz], [x2 - 3, sz]] : [[x1 + 1, sz], [x1 + 2, sz], [x1 + 3, sz]];
    }
    const a = steps[0], c = steps[2];
    fill(Math.min(a[0], c[0]), base + 3, Math.min(a[1], c[1]), Math.max(a[0], c[0]), base + 3, Math.max(a[1], c[1]), 'air'); // 床の穴
    steps.forEach(([sx, sz], n) => fill(sx, base, sz, sx, base + n, sz, floorB));
  }
  // 8. 出入口(正面の中央、高さ2)
  let doorX = null, doorZ = null;
  if (facing === 'west')       { doorX = x1; doorZ = Z; }
  else if (facing === 'east')  { doorX = x2; doorZ = Z; }
  else if (facing === 'north') { doorX = X;  doorZ = z1; }
  else                         { doorX = X;  doorZ = z2; }
  fill(doorX, Y, doorZ, doorX, Y + 1, doorZ, 'air');
  // 9. 窓(縦2マスのガラス板)
  for (let f = 0; f < floors; f++) {
    const wy = Y + 4 * f + 1;
    const xs = [x1 + 2, x2 - 2]; if (w >= 9) xs.push(X);
    const zs = [z1 + 2, z2 - 2]; if (d >= 9) zs.push(Z);
    for (const wz of [z1, z2]) for (const wx of xs) {
      if (f === 0 && wx === doorX && wz === doorZ) continue;
      fill(wx, wy, wz, wx, wy + 1, wz, windowB);
    }
    for (const wx of [x1, x2]) for (const wz of zs) {
      if (f === 0 && wx === doorX && wz === doorZ) continue;
      fill(wx, wy, wz, wx, wy + 1, wz, windowB);
    }
  }
  if (!noRoof) {
  // 10. 段々の屋根(階段ブロックは使わず、普通のブロックだけ)
  //     各段を2マス幅にして、斜めに隙間ができないようにしている
  const ridgeAlongZ = (facing === 'west' || facing === 'east'); // 正面が軒側、棟は奥行方向
  if (ridgeAlongZ) {
    const h = hx;
    for (let k = 0; k <= h; k++) {
      const y = yT + 1 + k;
      fill(x1 - 1 + k, y, z1 - 1, x1 + k, y, z2 + 1, roofB);
      fill(x2 - k, y, z1 - 1, x2 + 1 - k, y, z2 + 1, roofB);
      if (x1 + k + 1 <= x2 - k - 1) {            // 前後の三角の壁
        fill(x1 + k + 1, y, z1, x2 - k - 1, y, z1, wallB);
        fill(x1 + k + 1, y, z2, x2 - k - 1, y, z2, wallB);
      }
    }
    fill(X, yT + h + 2, z1 - 1, X, yT + h + 2, z2 + 1, roofB);
  } else {
    const h = hz;
    for (let k = 0; k <= h; k++) {
      const y = yT + 1 + k;
      fill(x1 - 1, y, z1 - 1 + k, x2 + 1, y, z1 + k, roofB);
      fill(x1 - 1, y, z2 - k, x2 + 1, y, z2 + 1 - k, roofB);
      if (z1 + k + 1 <= z2 - k - 1) {
        fill(x1, y, z1 + k + 1, x1, y, z2 - k - 1, wallB);
        fill(x2, y, z1 + k + 1, x2, y, z2 - k - 1, wallB);
      }
    }
    fill(x1 - 1, yT + h + 2, Z, x2 + 1, yT + h + 2, Z, roofB);
  }
  }
  // 11. 明かり(各階の中央)
  for (let f = 0; f < floors; f++) set(X, Y + 4 * f, Z, 'lantern');
  C.warnings = warnings;
  return C;
}


// ---------------------------------------------------------------
// L字の家。外形は「長方形から角を四角く切り欠いた形」。
// 内部では切り欠きを北東(x大・z小)に固定して作り、実際の角に合わせて座標を反転して出力する。
// ---------------------------------------------------------------
function evenClamp(v, def, lo, hi) {
  let n = Math.min(hi, Math.max(lo, toInt(v, def)));
  if (n % 2 !== 0) n -= 1;
  return Math.max(lo, n);
}

function lShape(P) {
  const { b, X, Y, Z, w: W, d: D, floors, wallB, frameB, stripB, floorB, roofB, baseB, windowB, noRoof } = P;
  const nw = evenClamp(b.notchW, Math.round(W * 0.4 / 2) * 2, 2, W - 5);   // 切り欠きの幅(x方向)
  const nd = evenClamp(b.notchD, Math.round(D * 0.4 / 2) * 2, 2, D - 5);   // 切り欠きの奥行(z方向)

  // 切り欠きの角(既定は正面の反対側=裏の角)
  let corner = String(b.notch || '').toLowerCase();
  if (!['ne', 'nw', 'se', 'sw'].includes(corner)) {
    corner = { west: 'ne', east: 'nw', north: 'se', south: 'ne' }[P.facing];
  }
  const mirX = corner.includes('w');
  const mirZ = corner.includes('s');
  const swap = (f) => {
    if (mirX && (f === 'west' || f === 'east')) return f === 'west' ? 'east' : 'west';
    if (mirZ && (f === 'north' || f === 'south')) return f === 'north' ? 'south' : 'north';
    return f;
  };
  const fc = swap(P.facing);    // 内部座標での正面の向き

  const hx = (W - 1) / 2, hz = (D - 1) / 2;
  const x1 = X - hx, x2 = X + hx, z1 = Z - hz, z2 = Z + hz;
  const xe = x2 - nw;           // 奥行方向に長い棟(B)の東端
  const zn = z1 + nd;           // 幅方向に長い棟(A)の北端
  const yT = Y + 4 * floors - 1;
  const mx = v => (mirX ? 2 * X - v : v);
  const mz = v => (mirZ ? 2 * Z - v : v);

  const C = [];
  const fill = (ax, ay, az, bx, by, bz, block, mode) =>
    C.push(`/fill ${mx(ax)} ${ay} ${mz(az)} ${mx(bx)} ${by} ${mz(bz)} ${block}${mode ? ' ' + mode : ''}`);
  const set = (x, y, z, block) => C.push(`/setblock ${mx(x)} ${y} ${mz(z)} ${block}`);
  // 外周(L字の6辺)を1段分
  const perim = (y, block) => {
    fill(x1, y, z1, xe, y, z1, block);
    fill(xe, y, z1, xe, y, zn, block);
    fill(xe, y, zn, x2, y, zn, block);
    fill(x2, y, zn, x2, y, z2, block);
    fill(x1, y, z2, x2, y, z2, block);
    fill(x1, y, z1, x1, y, z2, block);
  };

  // 1. 基礎
  fill(x1, Y - 3, z1, xe, Y - 1, z2, baseB);
  fill(x1, Y - 3, zn, x2, Y - 1, z2, baseB);
  // 2. 壁(2つの棟を空洞の箱で作る。hollowは上下の面も残るのでY-1が床、yTが天井)
  hollowChunks(fill, x1, z1, xe, z2, Y, floors, wallB);
  hollowChunks(fill, x1, zn, x2, z2, Y, floors, wallB);
  // 3. 2つの棟のつなぎ目の壁を抜く(角の1本だけ残す)
  fill(xe, Y, zn + 1, xe, yT - 1, z2 - 1, 'air');
  fill(x1 + 1, Y, zn, xe - 1, yT - 1, zn, 'air');
  // 4. 床
  fill(x1, Y - 1, z1, xe, Y - 1, z2, floorB);
  fill(x1, Y - 1, zn, x2, Y - 1, z2, floorB);
  // 5. 土台の石の帯と、階の境目・軒下の帯
  perim(Y, baseB);
  for (let i = 1; i <= floors; i++) perim(Y + 4 * i - 1, stripB);
  // 6. 各階の床板
  for (let i = 1; i <= floors; i++) {
    const y = Y + 4 * i - 1;
    fill(x1 + 1, y, z1 + 1, xe - 1, y, z2 - 1, floorB);
    fill(x1 + 1, y, zn + 1, x2 - 1, y, z2 - 1, floorB);
  }
  // 7. 角の柱(6か所)
  for (const [px, pz] of [[x1, z1], [xe, z1], [xe, zn], [x2, zn], [x2, z2], [x1, z2]]) fill(px, Y, pz, px, yT, pz, frameB);
  // 8. 上の階への階段(西側の壁沿い。階ごとに北寄り/南寄りを交互に)
  for (let i = 1; i < floors; i++) {
    const base = Y + 4 * (i - 1);
    const far = (i % 2 === 0);
    const zs = far ? [z2 - 1, z2 - 2, z2 - 3] : [z1 + 1, z1 + 2, z1 + 3];
    fill(x1 + 1, base + 3, Math.min(zs[0], zs[2]), x1 + 1, base + 3, Math.max(zs[0], zs[2]), 'air');
    zs.forEach((sz, n) => fill(x1 + 1, base, sz, x1 + 1, base + n, sz, floorB));
  }
  // 9. 出入口と窓
  const segs = [
    { o: 'z', c: z1, lo: x1, hi: xe, n: [0, -1] },   // 0 北(B)
    { o: 'x', c: xe, lo: z1, hi: zn, n: [1, 0] },    // 1 切り欠きの西側面
    { o: 'z', c: zn, lo: xe, hi: x2, n: [0, -1] },   // 2 切り欠きの南側面
    { o: 'x', c: x2, lo: zn, hi: z2, n: [1, 0] },    // 3 東(A)
    { o: 'z', c: z2, lo: x1, hi: x2, n: [0, 1] },    // 4 南
    { o: 'x', c: x1, lo: z1, hi: z2, n: [-1, 0] },   // 5 西
  ];
  const frontIdx = { north: 0, east: 3, south: 4, west: 5 }[fc];
  const fs = segs[frontIdx];
  const doorPos = (fs.lo + fs.hi) / 2;
  if (fs.o === 'z') fill(doorPos, Y, fs.c, doorPos, Y + 1, fs.c, 'air');
  else fill(fs.c, Y, doorPos, fs.c, Y + 1, doorPos, 'air');

  for (let f = 0; f < floors; f++) {
    const wy = Y + 4 * f + 1;
    segs.forEach((sg, idx) => {
      const len = sg.hi - sg.lo;
      if (len < 4) return;
      const half = (f === 0 && len >= 8) ? 1 : 0;   // 1階は幅3の大きな窓
      let pos = [sg.lo + 2 + half, sg.hi - 2 - half];
      if (len >= 8) pos.push((sg.lo + sg.hi) / 2);
      pos = [...new Set(pos)].sort((p, q) => p - q)
        .filter(p => p - half >= sg.lo + 2 && p + half <= sg.hi - 2);
      const kept = [];
      for (const p of pos) if (!kept.length || p - kept[kept.length - 1] >= 2 * half + 2) kept.push(p);
      pos = kept;
      for (const p of pos) {
        if (idx === frontIdx && f === 0 && Math.abs(p - doorPos) <= half + 1) continue;
        if (sg.o === 'z') fill(p - half, wy, sg.c, p + half, wy + 1, sg.c, windowB);
        else fill(sg.c, wy, p - half, sg.c, wy + 1, p + half, windowB);
        // 2階の正面の窓に花箱
        if (idx === frontIdx && f === 1) {
          if (sg.o === 'z') {
            fill(p - 1, wy, sg.c + sg.n[1], p + 1, wy, sg.c + sg.n[1], 'dark_oak_planks');
            fill(p - 1, wy + 1, sg.c + sg.n[1], p + 1, wy + 1, sg.c + sg.n[1], 'flowering_azalea_leaves');
          } else {
            fill(sg.c + sg.n[0], wy, p - 1, sg.c + sg.n[0], wy, p + 1, 'dark_oak_planks');
            fill(sg.c + sg.n[0], wy + 1, p - 1, sg.c + sg.n[0], wy + 1, p + 1, 'flowering_azalea_leaves');
          }
        }
      }
    });
  }
  if (!noRoof) {
  // 10. 屋根: 外形から1マス張り出した範囲を、縁からの距離で1段ずつ高くする(寄せ棟の段々屋根)
  const inFoot = (x, z) =>
    (x >= x1 - 1 && x <= x2 + 1 && z >= zn - 1 && z <= z2 + 1) ||
    (x >= x1 - 1 && x <= xe + 1 && z >= z1 - 1 && z <= z2 + 1);
  let remaining = new Set();
  for (let x = x1 - 1; x <= x2 + 1; x++) for (let z = z1 - 1; z <= z2 + 1; z++) if (inFoot(x, z)) remaining.add(x + ',' + z);
  for (let level = 1; remaining.size; level++) {
    const layer = new Set();
    for (const key of remaining) {
      const [x, z] = key.split(',').map(Number);
      let edge = false;
      for (let dx = -1; dx <= 1 && !edge; dx++) for (let dz = -1; dz <= 1; dz++) {
        if ((dx || dz) && !remaining.has((x + dx) + ',' + (z + dz))) { edge = true; break; }
      }
      if (edge) layer.add(key);
    }
    const used = new Set();
    for (let z = z1 - 1; z <= z2 + 1; z++) for (let x = x1 - 1; x <= x2 + 1; x++) {
      const k0 = x + ',' + z;
      if (!layer.has(k0) || used.has(k0)) continue;
      let xe2 = x;
      while (layer.has((xe2 + 1) + ',' + z) && !used.has((xe2 + 1) + ',' + z)) xe2++;
      let ze2 = z;
      for (;;) {
        let ok = true;
        for (let xx = x; xx <= xe2; xx++) { const k = xx + ',' + (ze2 + 1); if (!layer.has(k) || used.has(k)) { ok = false; break; } }
        if (!ok) break;
        ze2++;
      }
      for (let xx = x; xx <= xe2; xx++) for (let zz = z; zz <= ze2; zz++) used.add(xx + ',' + zz);
      fill(x, yT + level, z, xe2, yT + level, ze2, roofB);
    }
    for (const k of layer) remaining.delete(k);
  }
  }
  // 11. 明かり(各階、長い棟の中央)
  for (let f = 0; f < floors; f++) set((x1 + xe) / 2, Y + 4 * f, Z, 'lantern');
  return C;
}


// ---- 安全機能: プレイヤーから離れた位置への /fill を見つける ----
// pos が null(プレイヤー位置が不明)のときは、安全側に倒してすべての /fill を対象にする
function findFarFills(commands, pos, limit = 50) {
  const found = [];
  for (const command of commands) {
    const parts = String(command).trim().split(/\s+/);
    if (parts[0].toLowerCase() !== '/fill') continue;
    const n = parts.slice(1, 7).map(Number);
    if (n.length < 6 || n.some(v => !Number.isFinite(v))) continue;
    if (!pos || ![pos.x, pos.y, pos.z].every(Number.isFinite)) { found.push({ command, dist: null }); continue; }
    let max = 0;
    for (const x of [n[0], n[3]]) for (const y of [n[1], n[4]]) for (const z of [n[2], n[5]]) {
      max = Math.max(max, Math.hypot(x - pos.x, y - pos.y, z - pos.z));
    }
    if (max >= limit) found.push({ command, dist: max });
  }
  return found;
}

module.exports = { houseCommands, findFarFills };