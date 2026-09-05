// 独立参考实现：RFC 8785-lite 规范化 + canonicalPayload 投影 + sha256 digest
// 与 Python 主实现（app/contracts/codec.py）遵循同一规范，代码独立编写，逐字节比对。
// 规范：键按 Unicode 码点升序；字符串最小转义 + 非 ASCII 一律 \uXXXX；
//       数字仅整数；无空白分隔；数组不重排。
'use strict';
const crypto = require('crypto');

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
    else if (cp < 0x20 || cp === 0x2028 || cp === 0x2029) out.push('\\u' + cp.toString(16).padStart(4, '0'));
    else if (cp >= 0x80) out.push('\\u' + cp.toString(16).padStart(4, '0')); // ensure_ascii 约定
    else out.push(ch);
  }
  return out.join('');
}

// 码点序键排序（与 Python sort(key=str) 对 ASCII 键一致；对非 BMP 用完整 code point）
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
  if (Number.isInteger(obj)) return String(obj);
  if (typeof obj === 'number') throw new Error('float not allowed');
  if (typeof obj === 'string') return '"' + escapeStr(obj) + '"';
  if (Array.isArray(obj)) return '[' + obj.map(jcs).join(',') + ']';
  if (typeof obj === 'object') {
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