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


def vc_block(version=1, raw=(1200, 1200, 2000, 1600, 0), cs_delta=0):
    b = struct.pack('<6H', version, *raw) + bytes(3)
    return b + bytes([(sum(b) + cs_delta) & 0xff])


def build(streams, temps=(0, 25, 50), luts=0x04, name=b'TEST', adv=0, vc=None):
    """Minimal WBF: streams[mode][range] = raw stream bytes (terminator included; the stream checksum byte is
    appended here). adv bit 0: a voltage-control block (vc or the default) in front of every stream; adv bit 1: a
    second, all-zero pointer list in every mode table."""
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
        body += bytes(4 * trc * (2 if adv & 2 else 1))
        blobs.append(tp)
    for m in range(mc):
        for t in range(trc):
            if adv & 1:
                body += vc or vc_block()
            sp = 0x30 + len(body)
            struct.pack_into('4s', body, blobs[m] - 0x30 + 4 * t, p24cs(sp))
            body += streams[m][t] + bytes([sum(streams[m][t]) & 0xff])
    d = hdr + body
    struct.pack_into('<I', d, 4, len(d))
    d[0x10] = 0x19
    d[0x1c:0x1f] = struct.pack('<I', xwia)[:3]
    d[0x1f] = sum(d[0x08:0x1f]) & 0xff
    d[0x20:0x23] = struct.pack('<I', wmta)[:3]
    d[0x24], d[0x25], d[0x26], d[0x27] = luts, mc - 1, trc - 1, adv
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

    def test_bad_stream_checksum_reported(self):
        d = bytearray(build([[bytes([0xaa, 0xff, 0xff])]], temps=(0, 50)))
        d[-1] ^= 1
        bad = [n for n, ok in wbf.Wbf(bytes(d)).checks() if not ok]
        self.assertTrue(any(n.startswith('stream ') and n.endswith(' cs') for n in bad), bad)

    def test_stream_end_matches_decode(self):
        s = bytes([0xaa, 0xff, 0xfc, 0xff - 0x02, 0xfc, 0xaa, 0xfe, 0xff])   # 0xff as a count, a single-byte section
        w = wbf.Wbf(build([[s]], temps=(0, 50)))
        self.assertEqual(w.stream_end(w.stream_offset(0, 0)), w.decode(0, 0)[1])

    def test_voltage_control_block(self):
        s = bytes([0xaa, 0xff, 0xff])
        w = wbf.Wbf(build([[s, s]], temps=(0, 25, 50), adv=3))
        self.assertTrue(w.has_vc and w.has_algo)
        self.assertEqual([n for n, ok in w.checks() if not ok], [])
        v = w.vc(0, 1)
        self.assertEqual((v['version'], v['vpos'], v['vneg'], v['vgh'], v['vgl'], v['vcom_offset']),
                         (1, 15.0, -15.0, 25.0, -20.0, 0.0))
        self.assertEqual(w.algo_offset(0, 1), 0)

    def test_voltage_control_vcom_offset_sign(self):
        s = bytes([0xaa, 0xff, 0xff])
        w = wbf.Wbf(build([[s]], temps=(0, 50), adv=1, vc=vc_block(raw=(1200, 1200, 2000, 1600, 0x8000 + 16))))
        self.assertAlmostEqual(w.vc(0, 0)['vcom_offset'], -0.05)
        w = wbf.Wbf(build([[s]], temps=(0, 50), adv=1, vc=vc_block(raw=(1200, 1200, 2000, 1600, 0x9000))))
        self.assertIsNone(w.vc(0, 0)['vcom_offset'])

    def test_voltage_control_bad_checksum_reported(self):
        s = bytes([0xaa, 0xff, 0xff])
        w = wbf.Wbf(build([[s]], temps=(0, 50), adv=1, vc=vc_block(cs_delta=1)))
        self.assertFalse(w.vc(0, 0)['cs_ok'])
        self.assertTrue(any('vc block' in n for n, ok in w.checks() if not ok))

    def test_no_voltage_control_without_flag(self):
        w = wbf.Wbf(build([[bytes([0xaa, 0xff, 0xff])]], temps=(0, 50)))
        with self.assertRaises(wbf.WbfError):
            w.vc(0, 0)

    def test_frame_rate_bcd(self):
        d = bytearray(build([[bytes([0xaa, 0xff, 0xff])]], temps=(0, 50)))
        d[0x17] = 0x85
        self.assertEqual(wbf.Wbf(bytes(d)).frame_rate_hz(), 85)


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

    def test_voltage_control_blocks(self):
        # 79 distinct streams, one block each; the same voltages everywhere
        offs = self.w.stream_offsets()
        self.assertEqual(len(offs), 79)
        got = {(v['version'], v['vpos'], v['vneg'], v['vgh'], v['vgl'], v['vcom_offset'], v['cs_ok'])
               for v in (self.w.vc_at(s - wbf.VC_SIZE) for s in offs)}
        self.assertEqual(got, {(1, 15.0, -15.0, 25.0, -20.0, 0.0, True)})

    def test_header_extras(self):
        self.assertEqual((self.w.frame_rate_hz(), self.w.vcom_shifted, self.w.adv_flags), (85, 0, 3))

    def test_algo_pointers_only_for_glr16_gld16(self):
        modes = {m for m in range(8) for t in range(14) if self.w.algo_offset(m, t)}
        self.assertEqual(modes, {4, 5})

    def test_du4_targets(self):
        t = self.w.table16(7, 8)
        reached = {n for o in range(16) for n in range(16) if any(fr[o][n] for fr in t)}
        self.assertEqual(reached, {0, 5, 10, 15})
        self.assertEqual(wbf.sequence(t, 15, 5), '....+---------------+++.')


if __name__ == '__main__':
    unittest.main()
