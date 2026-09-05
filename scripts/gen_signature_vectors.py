#!/usr/bin/env python3
"""生成 attempt_contract v2 签名向量（手册 §12.2 CT-04；蓝图 §9.4 域分离签名输入）。

签名输入 = JCS({signatureContext, objectType, schemaVersion, keyId, issuer,
                workloadIdentity, audience, controlPlaneEpoch, signedAt, payloadDigest})
- 有效签名：deploy/keys/sk-attempt.pem 对签名输入签名（Ed25519）
- 正例：对象带签名信封后 schema 合法、payloadDigest 不变
- 失败向量：篡改 payload/algorithm/keyId/issuer/signedAt/audience/epoch
"""
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jsonschema import Draft202012Validator

from app.contracts.codec import (
    build_signature_envelope,
    load_digest_profile,
    load_schema,
    payload_digest,
    signature_input,
)

ROOT = Path(__file__).resolve().parent.parent
KEY_DIR = ROOT / "deploy" / "keys"
OUT = ROOT / "contracts" / "test-vectors" / "attempt_contract" / "v2"
SCHEMA = load_schema("attempt_contract", "2")
PROFILE = load_digest_profile("attempt_contract", "2")
VALIDATOR = Draft202012Validator(SCHEMA)

ENVELOPE_BASE = {
    "objectType": "attempt_contract",
    "schemaVersion": "2",
    "signatureAlgorithm": "Ed25519",
    "keyId": "sk-attempt",
    "issuer": "attempt-service",
    "issuerWorkloadIdentity": "pi.attempt",
    "audience": "pi.platform",
    "controlPlaneEpoch": 0,
    "signedAt": "2026-09-05T08:01:00Z",
}


def sign(privkey: Path, data: bytes) -> str:
    with tempfile.NamedTemporaryFile() as tf:
        tf.write(data)
        tf.flush()
        proc = subprocess.run(
            ["openssl", "pkeyutl", "-sign", "-inkey", str(privkey), "-rawin", "-in", tf.name],
            capture_output=True, check=True,
        )
    return proc.stdout.hex()


def verify(pubkey: Path, data: bytes, sig_hex: str) -> bool:
    with tempfile.NamedTemporaryFile() as sf:
        sf.write(bytes.fromhex(sig_hex))
        sf.flush()
        with tempfile.NamedTemporaryFile() as df:
            df.write(data)
            df.flush()
            proc = subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pubkey),
                 "-rawin", "-in", df.name, "-sigfile", sf.name],
                capture_output=True,
            )
    return proc.returncode == 0 and b"Verified" in proc.stdout


def main() -> int:
    vectors = json.loads((OUT / "vectors.json").read_text(encoding="utf-8"))
    obj = next(v["object"] for v in vectors["vectors"] if v["id"] == "pos-minimal")
    # 原子构造（评审 fix-1/2）：Schema 校验 → 重算 digest → §9.4 信封 → 签名输入
    env, sig_input, digest = build_signature_envelope(obj, SCHEMA, PROFILE, ENVELOPE_BASE)
    sig_hex = sign(KEY_DIR / "sk-attempt.pem", sig_input)

    signed_obj = json.loads(json.dumps(obj))
    signed_obj["signature"] = {
        "signatureAlgorithm": "Ed25519", "keyId": env["keyId"], "issuer": env["issuer"],
        "issuerWorkloadIdentity": env["issuerWorkloadIdentity"], "audience": env["audience"],
        "objectType": env["objectType"], "schemaVersion": env["schemaVersion"],
        "payloadDigest": env["payloadDigest"], "controlPlaneEpoch": env["controlPlaneEpoch"],
        "signedAt": env["signedAt"], "value": sig_hex,
    }
    schema_errors = list(VALIDATOR.iter_errors(signed_obj))

    # 失败向量：对同一签名输入的关键字段各自篡改后用原签名验证（均应失败）
    def mk_negative(name, note, changes: dict):
        e = dict(env)
        e.update(changes)
        return {
            "id": f"sig-{name}", "kind": "negative", "note": note,
            "envelopeMutated": {k: str(v)[:24] for k, v in changes.items()},
            "verifyWithSelf": verify(KEY_DIR / "sk-attempt.pub.pem", signature_input(b"", e), sig_hex),
        }

    negatives = [
        mk_negative("payload-altered", "篡改 payloadDigest（对应 payload 被改）",
                    {"payloadDigest": "sha256:" + "0" * 64}),
        mk_negative("keyid-altered", "篡改 keyId", {"keyId": "sk-other"}),
        mk_negative("issuer-altered", "篡改 issuer", {"issuer": "other-service"}),
        mk_negative("signedat-altered", "篡改 signedAt", {"signedAt": "2026-09-05T09:00:00Z"}),
        mk_negative("audience-altered", "篡改 audience", {"audience": "evil.platform"}),
        mk_negative("epoch-altered", "篡改 controlPlaneEpoch", {"controlPlaneEpoch": 1}),
        mk_negative("alg-altered", "篡改 signatureAlgorithm", {"signatureAlgorithm": "ECDSA"}),
        mk_negative("wi-altered", "篡改 issuerWorkloadIdentity", {"issuerWorkloadIdentity": "pi.evil"}),
        mk_negative("otype-altered", "篡改 objectType", {"objectType": "other_contract"}),
        mk_negative("sver-altered", "篡改 schemaVersion", {"schemaVersion": "1"}),
    ]
    # 错误 keyId：用 sk-node 公钥验证 sk-attempt 的同一有效签名
    wrong_key = verify(KEY_DIR / "sk-node.pub.pem", sig_input, sig_hex)

    out = {
        "objectType": "attempt_contract", "schemaVersion": "2",
        "signatureConstruction": "JCS({signatureContext, signatureAlgorithm, keyId, issuer, issuerWorkloadIdentity, audience, objectType, schemaVersion, payloadDigest, controlPlaneEpoch, signedAt})",
        "canonicalPayloadB64": next(
            v["canonicalPayloadB64"] for v in vectors["vectors"]
            if v["id"] == "pos-minimal"),
        "payloadDigest": digest,
        "signatureInputB64": base64.b64encode(sig_input).decode(),
        "envelope": env,
        "vectors": [
            {
                "id": "sig-positive", "kind": "positive",
                "note": "有效 Ed25519(sk-attempt) 对域分离签名输入的签名；签名信封不改变 payloadDigest",
                "object": signed_obj,
                "signatureHex": sig_hex,
                "schemaValid": len(schema_errors) == 0,
                "digestUnchanged": digest == payload_digest(signed_obj, PROFILE),
                "verifyWithSelf": verify(KEY_DIR / "sk-attempt.pub.pem", sig_input, sig_hex),
            },
            *negatives,
            {
                "id": "sig-wrong-key", "kind": "negative",
                "note": "用 sk-node 公钥验证 sk-attempt 的同一有效签名（keyId/issuer 不匹配）",
                "verifyWithWrongKey": wrong_key,
            },
        ],
    }
    (OUT / "signature_vectors.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    fails = [v["id"] for v in out["vectors"] if v["kind"] == "negative"
             and v.get("verifyWithSelf", v.get("verifyWithWrongKey")) is not False]
    pos = out["vectors"][0]
    print(f"写签名向量：digest={digest[:24]}...")
    print(f"  正例: schemaValid={pos['schemaValid']} digestUnchanged={pos['digestUnchanged']} "
          f"verify={pos['verifyWithSelf']}")
    print(f"  失败向量应全 False: {fails or 'OK（全部按预期失败）'}")
    print(f"  错 key（同一签名）: 验证应失败 -> {not wrong_key}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())