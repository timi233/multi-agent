// 独立参考实现比对：对 contracts/test-vectors 下全部契约对象的正向量，
// 用 Node 参考实现重算 canonicalPayloadB64 / payloadDigest，与固化向量逐字节比对。
// 任一不一致即退出码 1（对应手册 CT-01：主实现与独立参考实现逐字节一致）。
// 覆盖对象：attempt_contract / task_spec / event_envelope（含 CT-03 canonical 排序）/
// budget_grant / execution_plan_snapshot。
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const { canonicalPayload, payloadDigest } = require('./ref_impl/jcs.js');

const OBJECTS = [
  ['attempt_contract', 'attempt_contract'],
  ['task_spec', 'task_spec'],
  ['event_envelope', 'event_envelope'],
  ['budget_grant', 'budget_grant'],
  ['execution_plan_snapshot', 'execution_plan_snapshot'],
];

let checked = 0;
let failed = 0;
for (const [name, prefix] of OBJECTS) {
  const vectorFile = path.join(ROOT, 'contracts', 'test-vectors', name, 'v2', 'vectors.json');
  const profileFile = path.join(ROOT, 'contracts', 'jsonschema', prefix + '.v2.digestprofile.json');
  const vectors = JSON.parse(fs.readFileSync(vectorFile, 'utf8'));
  const profile = JSON.parse(fs.readFileSync(profileFile, 'utf8'));

  for (const v of vectors.vectors) {
    if (v.kind !== 'positive') continue;
    const payload = canonicalPayload(v.object, profile);
    const digest = payloadDigest(v.object, profile);
    checked++;
    const payloadOk = Buffer.from(payload, 'utf8').toString('base64') === v.canonicalPayloadB64;
    const digestOk = digest === v.payloadDigest;
    if (!payloadOk || !digestOk) {
      failed++;
      console.error(`[MISMATCH] ${name}/${v.id}: payloadOk=${payloadOk} digestOk=${digestOk}`);
    } else {
      console.log(`[OK] ${name}/${v.id} payload=${payload.length}B digest=${digest.slice(0, 19)}...`);
    }
  }
}
console.log(`\nNode 参考实现比对：${checked} 个正向量，${failed} 个不一致`);
if (failed > 0) process.exit(1);
console.log('CT-01 逐字节一致（Python 主实现 vs Node 独立参考实现）: PASS');