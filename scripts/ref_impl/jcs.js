// 独立参考实现：RFC 8785-lite 规范化 + canonicalPayload 投影 + sha256 digest
// 与 Python 主实现（app/contracts/codec.py）遵循同一规范，代码独立编写，逐字节比对。
// 规范：键码点序；字符串最小转义且非 ASCII 一律 \uXXXX（非 BMP 编 UTF-16 代理对）；
//       数字仅安全整数（±2^53-1）；无空白；数组不重排。
'use strict';
const crypto = require('crypto');

const MAX_SAFE = Number.MAX_SAFE_INTEGER; // 2^53-1
const MIN_SAFE = Number.MIN_SAFE_INTEGER;

function escapeStr(s) {
  const out = [];
  for (const ch of s) {
    const cp = ch.codePointAt(0);
    if (ch === '"') out.push('\\"');
    else if (ch === '\\') out.push('\\\\');
    else if (cp === 8) out.push('\\b');
    else if (cp === 9) out.push('\\t');
    else if (cp === 10) out.push('\\n');
    else if (cp === 12) out.push('\\f');
    else if (cp === 13) out.push('\\r');
    else if (cp < 0x20 || cp === 0x2028 || cp === 0x2029) {
      out.push('\\u' + cp.toString(16).padStart(4, '0'));
    } else if (cp >= 0x80) {
      if (cp <= 0xffff) {
        out.push('\\u' + cp.toString(16).padStart(4, '0'));
      } else {
        const v = cp - 0x10000;
        const hi = 0xd800 + (v >> 10);
        const lo = 0xdc00 + (v & 0x3ff);
        out.push('\\u' + hi.toString(16).padStart(4, '0') + '\\u' + lo.toString(16).padStart(4, '0'));
      }
    } else out.push(ch);
  }
  return out.join('');
}

// 码点序键排序（对 ASCII 键与 Python sort(str) 一致）
function sortedKeys(obj) {
  return Object.keys(obj).map(k => ({ k, cp: [...k].map(c => c.codePointAt(0)) }))
    .sort((a, b) => compareSeq(a.cp, b.cp)).map(x => x.k);
}
function compareSeq(a, b) {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
}

function jcs(obj) {
  if (obj === null) return 'null';
  if (obj === true) return 'true';
  if (obj === false) return 'false';
  if (Number.isInteger(obj)) {
    if (obj < MIN_SAFE || obj > MAX_SAFE) throw new Error('integer out of safe range');
    return String(obj);
  }
  if (typeof obj === 'number') throw new Error('float not allowed');
  if (typeof obj === 'string') return '"' + escapeStr(obj) + '"';
  if (Array.isArray(obj)) return '[' + obj.map(jcs).join(',') + ']';
  if (typeof obj === 'object') {
    // JSON.parse 的整数已经过 IEEE-754（仍无损）；此处按安全范围校验
    return '{' + sortedKeys(obj).map(k => '"' + escapeStr(k) + '":' + jcs(obj[k])).join(',') + '}';
  }
  throw new Error('unsupported type');
}

function pointerGet(obj, pointer) {
  if (!pointer.startsWith('/')) throw new Error('bad pointer');
  const parts = pointer.slice(1).split('/');
  let cur = obj;
  for (const p of parts) {
    if (Array.isArray(cur) && /^\d+$/.test(p)) cur = cur[Number(p)];
    else if (cur && typeof cur === 'object' && p in cur) cur = cur[p];
    else throw new Error('pointer not found: ' + pointer);
  }
  return cur;
}

function canonicalPayload(obj, profile) {
  const projected = {};
  for (const ptr of profile.immutablePayloadPointers) {
    projected[ptr.slice(1)] = pointerGet(obj, ptr);
  }
  return jcs(projected);
}

function payloadDigest(obj, profile) {
  return 'sha256:' + crypto.createHash('sha256').update(canonicalPayload(obj, profile), 'utf8').digest('hex');
}

module.exports = { jcs, canonicalPayload, payloadDigest, escapeStr };