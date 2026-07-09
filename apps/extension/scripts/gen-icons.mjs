// 拡張アイコン生成(M0-36)。ラスタライブラリ(sharp/canvas 等)が無い環境向けに、
// Node 標準の zlib だけで角丸スレート地 + 白マークの PNG を生成する。
// slate = --pr-a(#3E5C76)。`node scripts/gen-icons.mjs` で
// public/icon/{16,32,48,128}.png を出力。
import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(HERE, "..", "public", "icon");

// slate #3E5C76(--pr-a)、白、淡い補助線。
const BG = [0x3e, 0x5c, 0x76];
const FG = [0xff, 0xff, 0xff];
const SUB = [0xdd, 0xe8, 0xe1];
const SIZES = [16, 32, 48, 128];

function crc32(buf) {
  let c = ~0;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return (~c) >>> 0;
}

function chunk(type, data) {
  const typeBuf = Buffer.from(type, "ascii");
  const body = Buffer.concat([typeBuf, data]);
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([len, body, crc]);
}

// 角丸判定(コーナー半径 = size * 0.22)。
function insideRounded(x, y, size, r) {
  const cx = Math.min(Math.max(x, r), size - r);
  const cy = Math.min(Math.max(y, r), size - r);
  const dx = x - cx;
  const dy = y - cy;
  return dx * dx + dy * dy <= r * r;
}

function distanceToSegment(px, py, ax, ay, bx, by) {
  const vx = bx - ax;
  const vy = by - ay;
  const wx = px - ax;
  const wy = py - ay;
  const len2 = vx * vx + vy * vy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, (wx * vx + wy * vy) / len2));
  const cx = ax + t * vx;
  const cy = ay + t * vy;
  const dx = px - cx;
  const dy = py - cy;
  return Math.sqrt(dx * dx + dy * dy);
}

function markColor(x, y, size) {
  const px = x / size;
  const py = y / size;
  const mainStroke = Math.max(1.35 / size, 0.055);
  const crossStroke = Math.max(1.2 / size, 0.047);
  const subStroke = Math.max(0.9 / size, 0.032);
  const inLeft = distanceToSegment(px, py, 0.3, 0.74, 0.5, 0.27) <= mainStroke;
  const inRight = distanceToSegment(px, py, 0.5, 0.27, 0.7, 0.74) <= mainStroke;
  const inCross = distanceToSegment(px, py, 0.4, 0.59, 0.6, 0.59) <= crossStroke;
  const inSub = distanceToSegment(px, py, 0.34, 0.82, 0.66, 0.82) <= subStroke;
  if (inSub) return SUB;
  if (inLeft || inRight || inCross) return FG;
  return null;
}

function buildPng(size) {
  const r = Math.round(size * 0.22);
  const raw = Buffer.alloc(size * (size * 4 + 1));
  let p = 0;
  for (let y = 0; y < size; y++) {
    raw[p++] = 0; // filter: none
    for (let x = 0; x < size; x++) {
      const inShape = insideRounded(x + 0.5, y + 0.5, size, r);
      const mark = markColor(x + 0.5, y + 0.5, size);
      if (!inShape) {
        raw[p++] = 0;
        raw[p++] = 0;
        raw[p++] = 0;
        raw[p++] = 0; // 透明
      } else {
        const [rr, gg, bb] = mark ?? BG;
        raw[p++] = rr;
        raw[p++] = gg;
        raw[p++] = bb;
        raw[p++] = 255;
      }
    }
  }

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // color type RGBA
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;

  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

mkdirSync(OUT_DIR, { recursive: true });
for (const size of SIZES) {
  const file = resolve(OUT_DIR, `${size}.png`);
  writeFileSync(file, buildPng(size));
  console.log(`wrote ${file}`);
}
