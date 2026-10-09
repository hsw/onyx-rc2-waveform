#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Extract ebc_waveform.bin from the public ONYX BOOX Robinson Crusoe 2 update.

    python3 extract_waveform.py update.zip ebc_waveform.bin

update.zip -> boot.img (Android "ANDROID!" boot image) -> gzip'd newc cpio ramdisk -> /ebc_waveform.bin.
Standard library only.
"""
import gzip
import hashlib
import struct
import sys
import zipfile

EXPECTED_SHA256 = 'fe72b4ba1721e85dacb35c052e02f82d26cdd95b6df7f856df3dcb5e2bbc8ca8'


def ramdisk_of(boot):
    if boot[:8] != b'ANDROID!':
        raise SystemExit('boot.img: no ANDROID! magic')
    ksize, _, rsize, _, _, _, _, page = struct.unpack_from('<8I', boot, 8)
    roff = page * (1 + (ksize + page - 1) // page)
    return boot[roff:roff + rsize]


def cpio_files(data):
    off = 0
    while off + 110 <= len(data):
        if data[off:off + 6] != b'070701':
            raise SystemExit('ramdisk: not a newc cpio at %#x' % off)
        f = [int(data[off + 6 + 8 * i:off + 14 + 8 * i], 16) for i in range(13)]
        fsize, nsize = f[6], f[11]
        name = data[off + 110:off + 110 + nsize - 1].decode()
        doff = (off + 110 + nsize + 3) & ~3
        if name == 'TRAILER!!!':
            return
        yield name, data[doff:doff + fsize]
        off = (doff + fsize + 3) & ~3


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    with zipfile.ZipFile(sys.argv[1]) as z:
        boot = z.read('boot.img')
    for name, body in cpio_files(gzip.decompress(ramdisk_of(boot))):
        if name.lstrip('./') == 'ebc_waveform.bin':
            open(sys.argv[2], 'wb').write(body)
            h = hashlib.sha256(body).hexdigest()
            print('%s: %d bytes, sha256 %s%s' % (sys.argv[2], len(body), h,
                  '' if h == EXPECTED_SHA256 else '  (differs from the 1.9.1 file this repo describes)'))
            return
    raise SystemExit('ebc_waveform.bin not found in the boot ramdisk')


if __name__ == '__main__':
    main()
