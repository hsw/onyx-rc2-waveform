# SPDX-License-Identifier: MIT
"""python3 -m unittest discover -s tests          (synthetic files only)
WBF=path/to/ebc_waveform.bin python3 -m unittest discover -s tests   (also checks the real RC2 file)
"""
import os
import struct
import sys
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import wbf  # noqa: E402

RC2_SHA256 = 'fe72b4ba1721e85dacb35c052e02f82d26cdd95b6df7f856df3dcb5e2bbc8ca8'


def p24cs(v):
    b = struct.pack('<I', v)[:3]
    return b + bytes([sum(b) & 0xff])


def build(streams, temps=(0, 25, 50), luts=0x04, name=b'TEST'):
    """Minimal WBF: streams[mode][range] = raw stream bytes (terminator included)."""
    mc, trc = len(streams), len(streams[0])
    hdr = bytearray(0x30)
    body = bytearray()
    tt = bytes(temps) + bytes([sum(temps) & 0xff])
    xwia = 0x30 + len(tt)
    body += tt + bytes([len(name)]) + name + bytes([(len(name) + sum(name)) & 0xff])
    wmta = 0x30 + len(body)
    body += bytes(4 * mc)
    blobs = []
    for m in range(mc):
        tp = 0x30 + len(body)
        struct.pack_into('4s', body, wmta - 0x30 + 4 * m, p24cs(tp))
        body += bytes(4 * trc)
        blobs.append(tp)
    for m in range(mc):
        for t in range(trc):
            sp = 0x30 + len(body)
            struct.pack_into('4s', body, blobs[m] - 0x30 + 4 * t, p24cs(sp))
            body += streams[m][t]
    d = hdr + body
    struct.pack_into('<I', d, 4, len(d))
    d[0x10] = 0x19
    d[0x1c:0x1f] = struct.pack('<I', xwia)[:3]
    d[0x1f] = sum(d[0x08:0x1f]) & 0xff
    d[0x20:0x23] = struct.pack('<I', wmta)[:3]
    d[0x24], d[0x25], d[0x26] = luts, mc - 1, trc - 1
    d[0x28], d[0x29] = 0xff, 0xfc
    d[0x2f] = sum(d[0x20:0x2f]) & 0xff
    struct.pack_into('<I', d, 0, zlib.crc32(b'\0' * 4 + bytes(d[4:])))
    return bytes(d)


class Synthetic(unittest.TestCase):
    def test_rle_and_single_byte_sections(self):
        # one 32x32 frame = 1024 codes = 256 bytes of 4 codes; all "toward white" (0b10 x4 = 0xaa)
        run = bytes([0xaa, 0xff, 0xaa, 0xff - 0x02])          # 256 + 254 bytes via RLE
        single = bytes([0xfc, 0xaa, 0xaa, 0xfc])               # +2 bytes in single-byte mode
        s = run + single + bytes([0xff])
        w = wbf.Wbf(build([[s]], temps=(0, 50)))
        self.assertTrue(all(ok for _, ok in w.checks()), [n for n, ok in w.checks() if not ok])
        t, end = w.decode(0, 0)
        self.assertEqual(len(t), 2)
        self.assertTrue(all(c == 2 for fr in t for row in fr for c in row))

    def test_axes_fast_index_is_old(self):
        # first byte codes = 1,0,0,0: only stream index 0 -> (old 0, new 0); second byte: index 4 -> old 4, new 0
        frame = bytearray(256)
        frame[0] = 0x01
        frame[1] = 0x02
        s = bytes([0xfc]) + bytes(frame) + bytes([0xfc, 0xff])
        t, _ = wbf.Wbf(build([[s]], temps=(0, 50))).decode(0, 0)
        self.assertEqual(t[0][0][0], 1)
        self.assertEqual(t[0][4][0], 2)
        self.assertEqual(t[0][0][4], 0)

    def test_partial_frame_rejected(self):
        s = bytes([0xaa, 0x00, 0xff])
        with self.assertRaises(wbf.WbfError):
            wbf.Wbf(build([[s]], temps=(0, 50))).decode(0, 0)

    def test_bad_checksum_reported(self):
        d = bytearray(build([[bytes([0xaa, 0xff, 0xff])]], temps=(0, 50)))
        d[0x1f] ^= 1
        bad = [n for n, ok in wbf.Wbf(bytes(d)).checks() if not ok]
        self.assertIn('header cs1 = sum(0x08..0x1e)', bad)


@unittest.skipUnless(os.environ.get('WBF'), 'set WBF=path to the RC2 ebc_waveform.bin')
class RealRC2File(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hashlib
        data = open(os.environ['WBF'], 'rb').read()
        if hashlib.sha256(data).hexdigest() != RC2_SHA256:
            raise unittest.SkipTest('not the RC2 1.9.1 file')
        cls.w = wbf.Wbf(data)

    def test_checksums(self):
        self.assertEqual([n for n, ok in self.w.checks() if not ok], [])

    def test_frame_counts_match_device_at_range_8(self):
        # stock kernel dmesg at 24-27 degC: INIT 113, DU 22, GC16 38, DU4 24
        got = {m: len(self.w.decode(m, 8)[0]) for m in range(8)}
        self.assertEqual(got, {0: 113, 1: 22, 2: 38, 3: 38, 4: 38, 5: 38, 6: 10, 7: 24})

    def test_gl16_family_shares_streams(self):
        self.assertEqual({self.w.stream_offset(m, 8) for m in (3, 4, 5)}, {self.w.stream_offset(3, 8)})

    def test_du4_targets(self):
        t = self.w.table16(7, 8)
        reached = {n for o in range(16) for n in range(16) if any(fr[o][n] for fr in t)}
        self.assertEqual(reached, {0, 5, 10, 15})
        self.assertEqual(wbf.sequence(t, 15, 5), '....+---------------+++.')


if __name__ == '__main__':
    unittest.main()
