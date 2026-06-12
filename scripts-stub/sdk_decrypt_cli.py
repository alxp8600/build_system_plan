#!/usr/bin/env python3
"""
sdk-build-plan/scripts-stub/sdk_decrypt_cli.py

紧急通道：当 decrypt-proxy 不可用时，管理员在本地解密 dump/log。
与 decrypt_proxy 共享同样的密文格式：
    magic(4) ver(1) key_id(12, ascii, \0 padded) nonce(12)  + ciphertext + tag(16)
    magic = b"SDKL"  -> 解密后再 zstd 解压（日志）
    magic = b"SDKD"  -> 直接输出（dump）

用法:
    python3 sdk_decrypt_cli.py \
        --keyring /etc/sdk/keyring.json \
        --in     ab12.dmp.enc \
        --out    ab12.dmp
"""
import argparse, base64, json, struct, sys
import zstandard
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

HEADER_FMT = "<4sB12s12s"
HEADER_SZ  = struct.calcsize(HEADER_FMT)
TAG_SZ     = 16
CHUNK      = 1024 * 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyring", required=True, help="path to keyring.json (base64 32B keys)")
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", dest="out_path", required=True)
    args = ap.parse_args()

    with open(args.keyring) as f:
        keyring = {k: base64.b64decode(v) for k, v in json.load(f).items()}

    with open(args.in_path, "rb") as fi, open(args.out_path, "wb") as fo:
        header = fi.read(HEADER_SZ)
        if len(header) < HEADER_SZ:
            sys.exit("input too short")
        magic, ver, key_id, nonce = struct.unpack(HEADER_FMT, header)
        if magic not in (b"SDKL", b"SDKD") or ver != 1:
            sys.exit(f"bad magic/version: {magic!r}/{ver}")
        kid = key_id.rstrip(b"\x00").decode()
        if kid not in keyring:
            sys.exit(f"unknown key id: {kid}")

        cipher = Cipher(algorithms.AES(keyring[kid]),
                        modes.GCM(nonce, tag=None, min_tag_length=TAG_SZ),
                        backend=default_backend())
        dec  = cipher.decryptor()
        zdec = zstandard.ZstdDecompressor().decompressobj() if magic == b"SDKL" else None

        tail = b""
        while True:
            buf = fi.read(CHUNK)
            if not buf:
                break
            data = tail + buf
            if len(data) <= TAG_SZ:
                tail = data
                continue
            body, tail = data[:-TAG_SZ], data[-TAG_SZ:]
            plain = dec.update(body)
            if plain:
                fo.write(zdec.decompress(plain) if zdec else plain)

        if len(tail) != TAG_SZ:
            sys.exit("ciphertext truncated")
        dec.finalize_with_tag(tail)
        if zdec:
            rest = zdec.flush()
            if rest:
                fo.write(rest)

    print(f"OK  {args.in_path} -> {args.out_path}  (kid={kid}, magic={magic.decode()})")


if __name__ == "__main__":
    main()