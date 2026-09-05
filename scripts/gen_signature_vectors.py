#!/usr/bin/env python3
"""生成 attempt_contract v2 签名向量（手册 §12.2 CT-04 / Key Registry 粒度）。

- 有效签名：用 deploy/keys/sk-attempt.pem(Ed25519) 对 canonicalPayload 字节签名
- 正例：签名信封塞入对象后 schema 仍合法、payloadDigest 不变（签名不进 canonicalPayload）
- 失败向量：篡改签名、错误 keyId(用 sk-node 验 sk-attempt 的签名) 验证失败
"""
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jsonschema import Draft202012Validator

from app.contracts.codec import load_digest_profile, load_schema, payload_digest, signature_input

ROOT = Path(__file__).resolve().parent.parent
KEY_DIR = ROOT / "deploy" / "keys"
OUT = ROOT / "contracts" / "test-vectors" / "attempt_contract" / "v2"
SCHEMA = load_schema("attempt_contract", "2")
PROFILE = load_digest_profile("attempt_contract", "2")
VALIDATOR = Draft202012Validator(SCHEMA)


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
    sig_input = signature_input(obj, PROFILE)
    digest = payload_digest(obj, PROFILE)

    sig_hex = sign(KEY_DIR / "sk-attempt.pem", sig_input)
    signed_obj = json.loads(json.dumps(obj))
    signed_obj["signature"] = {
        "algorithm": "Ed25519", "keyId": "sk-attempt", "issuer": "attempt-service",
        "signedAt": "2026-09-05T08:01:00Z", "value": sig_hex,
    }
    schema_errors = list(VALIDATOR.iter_errors(signed_obj))
    digest_after = payload_digest(signed_obj, PROFILE)

    # 失败向量：篡改签名 1 字节；错误 keyId（sk-node 公钥验证 sk-attempt 签名）
    tampered_hex = ("0" if sig_hex[0] == "1" else "1") + sig_hex[1:]
    bad_sig_hex = "f" * 128
    out = {
        "objectType": "attempt_contract", "schemaVersion": "2",
        "signatureInputB64": base64.b64encode(sig_input).decode(),
        "payloadDigest": digest,
        "vectors": [
            {
                "id": "sig-positive", "kind": "positive",
                "note": "有效 Ed25519(sk-attempt) 签名；签名信封不改变 payloadDigest",
                "object": signed_obj,
                "algorithm": "Ed25519", "keyId": "sk-attempt", "issuer": "attempt-service",
                "signatureHex": sig_hex,
                "schemaValid": len(schema_errors) == 0,
                "digestUnchanged": digest == digest_after,
                "verifyWithSelf": verify(KEY_DIR / "sk-attempt.pub.pem", sig_input, sig_hex),
            },
            {
                "id": "sig-tampered", "kind": "negative",
                "note": "篡改签名 1 字节后验证失败",
                "signatureHex": tampered_hex,
                "verifyWithSelf": verify(KEY_DIR / "sk-attempt.pub.pem", sig_input, tampered_hex),
            },
            {
                "id": "sig-wrong-key", "kind": "negative",
                "note": "用 sk-node 公钥验证 sk-attempt 的签名（keyId/issuer 不匹配）",
                "signatureHex": bad_sig_hex,
                "verifyWithWrongKey": verify(KEY_DIR / "sk-node.pub.pem", sig_input, bad_sig_hex),
            },
        ],
    }
    (OUT / "signature_vectors.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"写签名向量 -> {OUT / 'signature_vectors.json'}")
    print(f"  digest={digest}（签名前后不变: {digest == digest_after}）")
    print(f"  有效签名验证: {out['vectors'][0]['verifyWithSelf']}; "
          f"篡改验证应失败: {not out['vectors'][1]['verifyWithSelf']}; "
          f"错 key 验证应失败: {not out['vectors'][2]['verifyWithWrongKey']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())