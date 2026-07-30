#!/usr/bin/env python3
"""TriXX 11.2.0 RCDATA blob decryptor.

Recovered scheme (byte stream, no block cipher):
    p[i] = c[i] ^ c[i-1] ^ key[i % len(key)]      (c[-1] = 0)
i.e. encryption is  c[i] = p[i] ^ key[i % len(key)] ^ c[i-1]  -- a running
XOR chain over a repeating ASCII key.
Keys were recovered as known-plaintext against the standard 0x3C-byte MZ/DOS
header, then confirmed by the resulting plaintext being a valid PE with a
BSJB CLI metadata header.
"""
import sys, os

MZ = bytes.fromhex(
 "4d5a90000300000004000000ffff0000"
 "b8000000000000004000000000000000"
 "00000000000000000000000000000000"
 "000000000000000000000000")  # standard PE DOS header 0x00..0x3B

def unchain(c):
    out = bytearray(len(c)); prev = 0
    for i, b in enumerate(c):
        out[i] = b ^ prev; prev = b
    return bytes(out)

def recover_key(c, maxlen=64):
    """Known-plaintext recover of the repeating key from the MZ header."""
    u = unchain(c[:len(MZ)])
    raw = bytes(u[i] ^ MZ[i] for i in range(len(MZ)))
    for L in range(1, maxlen + 1):
        if all(raw[i] == raw[i % L] for i in range(len(raw))):
            return raw[:L]
    return None

def decrypt(c, key):
    u = unchain(c)
    return bytes(u[i] ^ key[i % len(key)] for i in range(len(u)))

# Real identity of each blob (from the PE headers / export names of the plaintext).
EXT = {"98": ".exe", "99": ".exe", "132": ".exe", "133": ".sys", "134": ".sys"}

if __name__ == "__main__":
    src, dst = "resources", "resources/decrypted"
    os.makedirs(dst, exist_ok=True)
    for name in sys.argv[1:] or ["98", "99", "132", "133", "134"]:
        c = open(os.path.join(src, name), "rb").read()
        key = recover_key(c)
        p = decrypt(c, key) if key else None
        ok_mz = p[:2] == b"MZ" if p else False
        bsjb = p.find(b"BSJB") if p else -1
        print("%-5s len=%-9d key=%-16r MZ=%-5s BSJB@%s" % (
            name, len(c), key.decode("latin1") if key else None, ok_mz,
            hex(bsjb) if bsjb >= 0 else "NONE"))
        if p:
            open(os.path.join(dst, name + EXT.get(name, ".dll")), "wb").write(p)
