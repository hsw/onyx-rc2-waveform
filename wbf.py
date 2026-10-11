#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Decoder for E Ink WBF waveform files, written for the ONYX BOOX Robinson Crusoe 2 file
(320_R110_AE4D21_ED060KD1C2_TC, mode_version 0x19, 5 bits per pixel). Standard library only.

    python3 wbf.py info   FILE                 header, checksums, mode and temperature tables
    python3 wbf.py frames FILE                 frame count per mode x temperature range
    python3 wbf.py net    FILE MODE RANGE      16-level net-drive matrix (+ toward white, - toward black)
    python3 wbf.py seq    FILE MODE RANGE OLD NEW   per-frame drive sequence for one 16-level transition
    python3 wbf.py vc     FILE                 voltage-control blocks (VPOS/VNEG/VGH/VGL/VCOM offset) per mode x range

Conventions (see README.md):
  table[frame][old][new] = code, 0 = no drive, 1 = toward black, 2 = toward white (3 never occurs in this file)
  levels: 0 = black ... 15 = white; on a 5-bit file, 16-level k is 5-bit index 2k (what the stock kernel uses)
"""
import struct
import sys
import zlib

MODE_NAMES_0x19 = ['INIT', 'DU', 'GC16', 'GL16', 'GLR16', 'GLD16', 'A2', 'DU4']

# Voltage-control block (adv_flags bit 0): 16 bytes in front of every stream. Field layout as described for the
# "advanced waveform" voltage data in Amazon's Kindle Paperwhite 3 GPL kernel (see README.md).
VC_SIZE = 16
VC_STEP = 0.0125          # VPOS, VNEG, VGH, VGL: 12.5 mV per unit
VC_VCOM_STEP = 0.003125   # VCOM offset: 3.125 mV per unit, 0x8000 = sign


def a24(d, o):
    return d[o] | d[o + 1] << 8 | d[o + 2] << 16


class WbfError(Exception):
    pass


class Wbf:
    def __init__(self, data):
        if len(data) < 0x30:
            raise WbfError('file shorter than the 0x30-byte header')
        self.d = d = bytes(data)
        u32 = lambda o: struct.unpack_from('<I', d, o)[0]
        self.checksum = u32(0x00)
        self.filesize = u32(0x04)
        self.serial = u32(0x08)
        self.run_type, self.fpl_platform = d[0x0c], d[0x0d]
        self.fpl_lot = struct.unpack_from('<H', d, 0x0e)[0]
        self.mode_version, self.wf_version, self.wf_subversion, self.wf_type = d[0x10:0x14]
        self.panel_size, self.amepd_part_number, self.wfm_rev, self.frame_rate = d[0x14:0x18]
        self.vcom_shifted = d[0x19]
        self.xwia = a24(d, 0x1c)
        self.wmta = a24(d, 0x20)
        self.fvsn = d[0x23]
        self.luts, mc, trc, self.adv_flags = d[0x24:0x28]
        self.mode_count, self.range_count = mc + 1, trc + 1
        self.eb, self.sb = d[0x28], d[0x29]
        self.bits = 5 if self.luts & 0x0c == 0x04 else 4
        self.levels = 1 << self.bits
        self.temps = list(d[0x30:0x30 + self.range_count + 1])

    @property
    def has_vc(self):
        """adv_flags bit 0: a 16-byte voltage-control block sits in front of every stream."""
        return bool(self.adv_flags & 1)

    @property
    def has_algo(self):
        """adv_flags bit 1: each mode table has a second list of range_count pointers (algorithm control)."""
        return bool(self.adv_flags & 2)

    def frame_rate_hz(self):
        """frame_rate is BCD: 0x85 = 85 Hz."""
        hi, lo = self.frame_rate >> 4, self.frame_rate & 0xf
        return hi * 10 + lo if hi < 10 and lo < 10 else None

    # --- checksums -------------------------------------------------------------------------------------
    def checks(self):
        d = self.d
        out = [('file crc32 (bytes 0-3 zeroed)', zlib.crc32(b'\0' * 4 + d[4:]) == self.checksum),
               ('file size', self.filesize == len(d)),
               ('header cs1 = sum(0x08..0x1e)', sum(d[0x08:0x1f]) & 0xff == d[0x1f]),
               ('header cs2 = sum(0x20..0x2e)', sum(d[0x20:0x2f]) & 0xff == d[0x2f])]
        n = self.range_count + 1
        out.append(('temperature table cs', sum(d[0x30:0x30 + n]) & 0xff == d[0x30 + n]))
        if self.xwia:
            ln = d[self.xwia]
            out.append(('xwia string cs', sum(d[self.xwia:self.xwia + 1 + ln]) & 0xff == d[self.xwia + 1 + ln]))
        for m in range(self.mode_count):
            o = self.wmta + 4 * m
            out.append(('mode %d pointer cs' % m, sum(d[o:o + 3]) & 0xff == d[o + 3]))
            for t in range(self.range_count):
                p = a24(d, o) + 4 * t
                out.append(('mode %d range %d pointer cs' % (m, t), sum(d[p:p + 3]) & 0xff == d[p + 3]))
                if self.has_algo:
                    p += 4 * self.range_count
                    out.append(('mode %d range %d algo pointer cs' % (m, t), sum(d[p:p + 3]) & 0xff == d[p + 3]))
        for s in self.stream_offsets():
            e = self.stream_end(s)
            out.append(('stream %#x cs' % s, e + 1 < len(d) and sum(d[s:e + 1]) & 0xff == d[e + 1]))
            if self.has_vc:
                out.append(('stream %#x vc block cs' % s, self.vc_at(s - VC_SIZE)['cs_ok']))
        return out

    def xwia_string(self):
        if not self.xwia:
            return ''
        ln = self.d[self.xwia]
        return self.d[self.xwia + 1:self.xwia + 1 + ln].decode('latin1')

    def mode_name(self, m):
        if self.mode_version == 0x19 and m < len(MODE_NAMES_0x19):
            return MODE_NAMES_0x19[m]
        return 'mode%d' % m

    # --- stream ----------------------------------------------------------------------------------------
    def stream_offset(self, mode, rng):
        if not (0 <= mode < self.mode_count and 0 <= rng < self.range_count):
            raise WbfError('mode %d / range %d out of bounds' % (mode, rng))
        return a24(self.d, a24(self.d, self.wmta + 4 * mode) + 4 * rng)

    def stream_offsets(self):
        """Distinct stream offsets (modes and ranges may share a stream), in file order."""
        return sorted({self.stream_offset(m, t) for m in range(self.mode_count) for t in range(self.range_count)})

    def stream_end(self, i):
        """Offset of the eb terminator of the stream starting at i (walks the RLE without expanding it)."""
        d, single = self.d, False
        while True:
            if i >= len(d):
                raise WbfError('stream runs past end of file')
            if d[i] == self.eb:
                return i
            if d[i] == self.sb:
                single = not single
                i += 1
            else:
                i += 1 if single else 2

    # --- voltage control -------------------------------------------------------------------------------
    def vc_offset(self, mode, rng):
        if not self.has_vc:
            raise WbfError('adv_flags %#x: no voltage-control blocks' % self.adv_flags)
        return self.stream_offset(mode, rng) - VC_SIZE

    def vc(self, mode, rng):
        """Voltage-control block of (mode, range) as a dict; volts in V, None where the raw value is out of range."""
        return self.vc_at(self.vc_offset(mode, rng))

    def vc_at(self, o):
        if o < 0x30 or o + VC_SIZE > len(self.d):
            raise WbfError('voltage-control block at %#x out of bounds' % o)
        b = self.d[o:o + VC_SIZE]
        version, vpos, vneg, vgh, vgl, vcom = struct.unpack_from('<6H', b)

        def volts(raw, sign):
            return sign * raw * VC_STEP if raw <= 0x7fff else None
        if vcom <= 0x0fff:
            vcom_v = vcom * VC_VCOM_STEP
        elif 0x8000 <= vcom <= 0x8fff:
            vcom_v = -(vcom - 0x8000) * VC_VCOM_STEP
        else:
            vcom_v = None
        return {'offset': o, 'version': version, 'vpos': volts(vpos, +1), 'vneg': volts(vneg, -1),
                'vgh': volts(vgh, +1), 'vgl': volts(vgl, -1), 'vcom_offset': vcom_v,
                'raw': (vpos, vneg, vgh, vgl, vcom), 'cs_ok': sum(b[:15]) & 0xff == b[15]}

    # --- algorithm control -----------------------------------------------------------------------------
    def algo_offset(self, mode, rng):
        """Pointer from the second list of the mode table (0 = none); the record format is not known."""
        if not self.has_algo:
            raise WbfError('adv_flags %#x: no algorithm-control table' % self.adv_flags)
        if not (0 <= mode < self.mode_count and 0 <= rng < self.range_count):
            raise WbfError('mode %d / range %d out of bounds' % (mode, rng))
        return a24(self.d, a24(self.d, self.wmta + 4 * mode) + 4 * (self.range_count + rng))

    def decode(self, mode, rng):
        """Return (table, end): table[frame][old][new] -> 2-bit code; end = offset of the 0xFF terminator."""
        d, i, single, codes = self.d, self.stream_offset(mode, rng), False, []
        while True:
            if i >= len(d):
                raise WbfError('stream runs past end of file')
            b = d[i]
            if b == self.eb:
                break
            if b == self.sb:
                single = not single
                i += 1
                continue
            q = [(b >> s) & 3 for s in (0, 2, 4, 6)]
            if single:
                codes += q
                i += 1
            else:
                codes += q * (d[i + 1] + 1)
                i += 2
        lv = self.levels
        if len(codes) % (lv * lv):
            raise WbfError('%d codes is not a whole number of %dx%d frames' % (len(codes), lv, lv))
        # Stream order: the fast index is OLD (source level), the slow index is NEW (target level).
        nf = len(codes) // (lv * lv)
        table = [[[codes[f * lv * lv + new * lv + old] for new in range(lv)] for old in range(lv)] for f in range(nf)]
        return table, i

    def table16(self, mode, rng):
        """16-level view: on a 5-bit file, level k is 5-bit index 2k."""
        t, _ = self.decode(mode, rng)
        step = self.levels // 16
        return [[[fr[step * o][step * n] for n in range(16)] for o in range(16)] for fr in t]


def net_drive(t16, old, new):
    return sum(+1 if fr[old][new] == 2 else -1 if fr[old][new] == 1 else 0 for fr in t16)


def sequence(t16, old, new):
    return ''.join('.-+3'[fr[old][new]] for fr in t16)


def cmd_info(w):
    print('file size       %d (header says %d)' % (len(w.d), w.filesize))
    print('checksum        %#010x' % w.checksum)
    print('serial          %d' % w.serial)
    print('run_type %#x  fpl_platform %#x  fpl_lot %d' % (w.run_type, w.fpl_platform, w.fpl_lot))
    print('mode_version %#x  wf_version %#x  wf_subversion %#x  wf_type %#x'
          % (w.mode_version, w.wf_version, w.wf_subversion, w.wf_type))
    print('panel_size %#x  amepd_part_number %#x  wfm_rev %#x  frame_rate %#x (%s Hz)  vcom_shifted %#x'
          % (w.panel_size, w.amepd_part_number, w.wfm_rev, w.frame_rate, w.frame_rate_hz(), w.vcom_shifted))
    print('xwia %#x  wmta %#x  fvsn %#x' % (w.xwia, w.wmta, w.fvsn))
    print('luts %#x (%d bits/pixel)  modes %d  ranges %d  adv_flags %#x  eb %#x  sb %#x'
          % (w.luts, w.bits, w.mode_count, w.range_count, w.adv_flags, w.eb, w.sb))
    print('temperature bounds', w.temps)
    print('panel string    %r' % w.xwia_string())
    checks = w.checks()
    bad = [n for n, ok in checks if not ok]
    print('checksums       %d, %s' % (len(checks), 'all OK' if not bad else 'FAILED: ' + ', '.join(bad)))
    print('streams         %d distinct' % len(w.stream_offsets()))
    if w.has_vc:
        for line in vc_summary(w):
            print('voltage control ' + line)
    for m in range(w.mode_count):
        mp = a24(w.d, w.wmta + 4 * m)
        print('mode %d %-5s @%#07x ranges: %s' % (m, w.mode_name(m), mp,
              ' '.join('%#x' % w.stream_offset(m, t) for t in range(w.range_count))))


def fmt_vc(v):
    def f(x):
        return '?' if x is None else '%+.3f' % x
    return 'v%d VPOS %s VNEG %s VGH %s VGL %s VCOM offset %s V' % (
        v['version'], f(v['vpos']), f(v['vneg']), f(v['vgh']), f(v['vgl']), f(v['vcom_offset']))


def vc_summary(w):
    """One line per distinct set of voltages, with how many streams carry it."""
    seen = {}
    for s in w.stream_offsets():
        v = w.vc_at(s - VC_SIZE)
        key = fmt_vc(v) + ('' if v['cs_ok'] else '  BAD CHECKSUM')
        seen[key] = seen.get(key, 0) + 1
    return ['%3d x %s' % (n, k) for k, n in seen.items()]


def cmd_vc(w):
    if not w.has_vc:
        print('adv_flags %#x: no voltage-control blocks' % w.adv_flags)
        return
    for line in vc_summary(w):
        print(line)
    for m in range(w.mode_count):
        for t in range(w.range_count):
            v = w.vc(m, t)
            print('%d %-5s range %2d @%#07x %s%s' % (m, w.mode_name(m), t, v['offset'], fmt_vc(v),
                                                   '' if v['cs_ok'] else '  BAD CHECKSUM'))


def cmd_frames(w):
    print('range       ' + ' '.join('%4d' % t for t in range(w.range_count)))
    print('from degC   ' + ' '.join('%4d' % t for t in w.temps[:w.range_count]))
    for m in range(w.mode_count):
        print('%d %-8s  ' % (m, w.mode_name(m)) + ' '.join('%4d' % len(w.decode(m, t)[0]) for t in range(w.range_count)))


def cmd_net(w, mode, rng):
    t = w.table16(mode, rng)
    print('%s, range %d, %d frames; net drive in frames (+ white, - black, . = no drive at all)'
          % (w.mode_name(mode), rng, len(t)))
    print('old\\new ' + ''.join('%5d' % n for n in range(16)))
    for o in range(16):
        row = []
        for n in range(16):
            if all(fr[o][n] == 0 for fr in t):
                row.append('    .')
            else:
                row.append('%+5d' % net_drive(t, o, n))
        print('%7d ' % o + ''.join(row))


def main(argv):
    if len(argv) < 3 or argv[1] not in ('info', 'frames', 'net', 'seq', 'vc'):
        raise SystemExit(__doc__)
    w = Wbf(open(argv[2], 'rb').read())
    if argv[1] == 'info':
        cmd_info(w)
    elif argv[1] == 'frames':
        cmd_frames(w)
    elif argv[1] == 'vc':
        cmd_vc(w)
    elif argv[1] == 'net':
        cmd_net(w, int(argv[3]), int(argv[4]))
    else:
        t = w.table16(int(argv[3]), int(argv[4]))
        print(sequence(t, int(argv[5]), int(argv[6])))


if __name__ == '__main__':
    main(sys.argv)
