# onyx-rc2-waveform

The E Ink waveform of the **ONYX BOOX Robinson Crusoe 2** (internal name `MC_Kepler_R2`, Rockchip RK3026, panel
ED060KD1C2 1448×1072, PMIC TPS65185): where to get it, its format, a small decoder, and how the stock RK3026 kernel uses it.

Related: [onyx-rc2-ebc](https://github.com/hsw/onyx-rc2-ebc) (the stock EBC driver, described) and
[onyx-rc2-hardware](https://github.com/hsw/onyx-rc2-hardware) (board, PMIC/VCOM, pins, power).

The waveform file itself is **not** in this repository. It belongs to the vendor; the script below takes it out of the
vendor's public update.

## Getting the file

ONYX publishes the RC2 firmware update on its support page
<https://onyx-boox.ru/support/boox_robinson-crusoe2> (the firmware update download: 1.9.1-simple 2019-11-05_18-20 db0cce3,
a 211 912 503-byte `update.zip`, MD5 `bce0d3914e8acabb494685159fc9a141` as listed on that page).

```
python3 extract_waveform.py update.zip ebc_waveform.bin
```

It reads `boot.img` from the zip, takes the gzip'd cpio ramdisk out of the Android boot image and writes
`/ebc_waveform.bin`:

| | |
|---|---|
| size | 256 003 bytes |
| sha256 | `fe72b4ba1721e85dacb35c052e02f82d26cdd95b6df7f856df3dcb5e2bbc8ca8` |
| panel string | `320_R110_AE4D21_ED060KD1C2_TC` |

The same file (byte-identical) is in the ramdisk of the older 1.8.2 firmware on a real device.

## Decoder

```
python3 wbf.py info   ebc_waveform.bin             # header, every checksum, mode/temperature tables
python3 wbf.py frames ebc_waveform.bin             # frames per mode x temperature range
python3 wbf.py net    ebc_waveform.bin 7 8         # DU4 at range 8: net drive per 16-level transition
python3 wbf.py seq    ebc_waveform.bin 7 8 15 5    # per-frame codes for 15 -> 5:  ....+---------------+++.
python3 wbf.py vc     ebc_waveform.bin             # voltage-control block per mode x range (VPOS/VNEG/VGH/VGL/VCOM offset)
python3 -m unittest discover -s tests              # synthetic tests; set WBF=ebc_waveform.bin to add the real file
```

Python 3, standard library only. `Wbf(data).decode(mode, range)` returns `table[frame][old][new]`, codes 0/1/2;
`Wbf(data).vc(mode, range)` returns the voltage-control block in volts.

**How it was checked.** For all 8 modes × 14 ranges the decoded tables are identical to the table the stock kernel's own
decoder (`decodewaveform_19`, run alone in a CPU emulator on this file) writes into memory. The frame counts at range 8 match
the stock kernel's log on the device. The method is in onyx-rc2-ebc.

Confidence marks used below: **[CONFIRMED]** checked by decoding, emulation or a device log; **[STRONG]** inferred from several
confirmed facts; **[VERIFY]** open.

## Format

A standard E Ink **WBF** file. Every checksum in it validates [CONFIRMED].

### Header

| offset | field | this file |
|---|---|---|
| 0x00 | checksum: CRC-32 (zlib) of the whole file with bytes 0–3 zeroed | 0xc633dbbd |
| 0x04 | file size | 256 003 |
| 0x08 | serial | 5487 |
| 0x0c | run_type / fpl_platform / fpl_lot (u16) | 0x11 / 0x00 / 110 ("R110") |
| 0x10 | **mode_version** / wf_version / wf_subversion / wf_type | **0x19** / 0x4d / 0x21 / 0x50 |
| 0x14 | panel_size / amepd_part_number / wfm_rev / frame_rate | 0x00 / 0x5b / 0x00 / 0x85 (BCD: **85 Hz**) |
| 0x18 | reserved / **vcom_shifted** / reserved (u16) | 0x55 / 0x00 / 0 |
| 0x1c | xwia: 24-bit offset of the panel string / cs1 = sum(**0x08**..0x1e) & 0xff | 0x40 / 0x4f |
| 0x20 | wmta: 24-bit offset of the mode table / fvsn | 0x5f / 0x01 |
| 0x24 | luts / mode count − 1 / temperature range count − 1 / **adv_flags** | 0x04 / 7 / 13 / 0x03 |
| 0x28 | eb (end byte) / sb (single-byte toggle) / reserved / cs2 = sum(0x20..0x2e) & 0xff | 0xff / 0xfc / … / 0x77 |
| 0x30 | temperature bounds (range count + 1 bytes) + checksum | 0,3,6,…,33,38,43,48 °C → 14 ranges |
| xwia | Pascal string (length byte, text) + checksum | `320_R110_AE4D21_ED060KD1C2_TC` |
| wmta | per mode: 24-bit pointer + checksum → per range: 24-bit pointer + checksum → stream | |

Each checksum byte is the low byte of the sum of the bytes it covers.

Notes on the header [CONFIRMED on this file and on the R177 file from the panel flash unless marked]:
- **cs1 covers 0x08–0x1e, not 0x00–0x1e.** Amazon's Kindle kernel source (below) describes cs1 as the sum of bytes
  0x00–0x1e; on both RC2 files only the sum from 0x08 matches. The Kindle kernel uses cs1/cs2 only when the file-length field
  is 0; otherwise it checks the CRC-32, as `wbf.py` does.
- **frame_rate is BCD**: 0x85 = 85 Hz [STRONG: it matches the 85 Hz panel frame rate]. The stock RC2 kernel drives the panel at
  83.64 Hz (442 clocks per line at 40 MHz), so each frame of this waveform is about 1.6 % longer than designed. Whether 85 Hz
  timing looks better is open [VERIFY] (timings in onyx-rc2-hardware `panel/`).
- **vcom_shifted** (0x19) is 0. In the Kindle kernel a non-zero value moves the panel VCOM by +0.25 V for some waveform
  types; here it does nothing. The byte at 0x18 (0x55) is reserved in the Kindle source too; its meaning is unknown.
- **adv_flags** (0x27) = 3. The Kindle kernel reads it as two bits: bit 0 = **voltage control** data present, bit 1 =
  **algorithm control** data present. Both are in this file; see below.

### Pixel depth

`luts & 0x0c == 0x04` means **5 bits per pixel**: a frame has 32 × 32 entries. With a 4-bit decode the frame counts come out
4× too high (152 for GC16 instead of 38); with the 5-bit decode they match the device [CONFIRMED].

### Stream

- Each byte holds four 2-bit codes, least significant first.
- Outside a single-byte section a byte is followed by a repeat count: the four codes are emitted count + 1 times.
- `sb` (0xfc) toggles single-byte sections, where every byte stands alone.
- `eb` (0xff) ends the stream. The byte after it is the **stream checksum**: the low byte of the sum of all stream bytes
  from the first one up to and including `eb` [CONFIRMED for all 79 distinct streams in both files; `wbf.py info` checks it].
- With `adv_flags` bit 0 set, every stream is **preceded** by a 16-byte voltage-control block (next section). So between two
  streams there are exactly 1 + 16 bytes: the checksum of the previous stream and the block of the next.
- Stream order: the **fast index is the old (source) level, the slow index is the new (target) level** [CONFIRMED against the
  stock kernel's table layout; source/target meaning STRONG from the data: DU and A2 only have non-zero targets 0 and 15, and
  every same-level transition is a no-op in DU/A2/DU4].
- Codes: 0 = no drive, **1 = toward black**, **2 = toward white**. Code 3 never occurs in this file.
- Levels: **0 = black, 15 = white**.

### Voltage-control blocks

A 16-byte block sits directly in front of each stream (at stream pointer − 16; the first one right after the last mode
table). Several modes and ranges share a stream (GL16, GLR16 and GLD16 in every range; A2 in ranges 8–13), so this file
has 79 blocks for 112 mode × range pairs.

The field layout is the "voltage control" data of the **Amazon Kindle Paperwhite 3** (7th generation) kernel, which Amazon
publishes under the GPL ("Kindle Source Code Notice", package `Kindle_src_5.16.2.1.1_4097470002.tar.gz`, `linux-3.0.35`:
`drivers/video/mxc/mxc_epdc_fb.c` and `drivers/video/mxc/mxc_epdc_fb_lab126.c`). That kernel reads the same data from its
own pre-converted waveform format, not from a WBF; that the blocks sit in front of each WBF stream was found on the RC2
files. Our own description, all fields little-endian:

| offset | size | field | unit | this file (every block) |
|---|---|---|---|---|
| 0 | 2 | version | | 1 |
| 2 | 2 | VPOS (source driver positive rail) | +12.5 mV | 1200 = **+15.000 V** |
| 4 | 2 | VNEG (source driver negative rail), stored as a magnitude | −12.5 mV | 1200 = **−15.000 V** |
| 6 | 2 | VGH (gate high, VDDH on the TPS65185) | +12.5 mV | 2000 = **+25.000 V** |
| 8 | 2 | VGL (gate low, VEE on the TPS65185), stored as a magnitude | −12.5 mV | 1600 = **−20.000 V** |
| 10 | 2 | VCOM offset: 0x0000–0x0fff positive, 0x8000–0x8fff negative (bit 15 = sign) | 3.125 mV | 0 = **0 V** |
| 12 | 3 | unused | | 0 |
| 15 | 1 | checksum: low byte of the sum of bytes 0–14 | | valid in all 79 blocks |

The Kindle kernel treats a rail value above 0x7fff, or a VCOM offset outside the two ranges, as 0 (not given); `wbf.py`
returns `None` there. It adds the VCOM offset of the current mode and temperature range to the panel VCOM before each update
(only when `vcom_shifted` is 0).

All 79 blocks in this file **and** all 79 in the R177 file from the panel flash carry the same values: VPOS +15 V, VNEG −15 V,
VGH +25 V, VGL −20 V, VCOM offset 0 [CONFIRMED]. For a driver this means: **the gate-high rail must be 25 V**, the VCOM is
the same in every mode, and a driver can compare these values with what its PMIC is set to before the first update.

### Algorithm control (`adv_flags` bit 1)

With bit 1 set, each per-mode table holds a second list of `range count` checksummed 24-bit pointers after the stream
pointers (so each mode table is 2 × 14 × 4 bytes here). In this file only **modes 4 (GLR16) and 5 (GLD16)** have non-zero
pointers; they point to small records after the last stream (8 bytes per range for mode 4, 12 for mode 5, one record content
per mode for all 14 ranges). All other modes have 0. The record format is unknown [VERIFY]; `wbf.py` only checks the
pointer checksums (`Wbf.algo_offset()`). It fits the Kindle model in which GLR16/GLD16 are "REAGL"/"REAGLD": GL16 drive
plus a processing step on the host (next section).

### 5-bit to 16 levels

The stock kernel builds its 16-level hardware table from the **even** 5-bit indices: 16-level `k` = 5-bit index `2k`
[CONFIRMED in the stock code]. `wbf.py` does the same in `table16()`. Odd indices are empty except 29 and 31 near white:
the GL16 family has entries at 29..31 × 29/31, and DU/A2/DU4 rows 29 and 31 are zero.

## Modes

The mode labels are [STRONG], each verdict [CONFIRMED] by decoding all 14 ranges.

| wbf mode | name | frames at range 8 (24–27 °C) | what it does |
|---|---|---|---|
| 0 | INIT | 113 | the same black–white flashing for every pair, ends white |
| 1 | DU | 22 | any level → 0 or 15; grey targets are no-ops |
| 2 | GC16 | 38 | all 16 targets; every pixel is driven, same-level pixels flash too |
| 3 | GL16 | 38 | GC16 except 15 → 15 is a no-op (white background does not flash) |
| 4 | GLR16 | 38 | **same pointers as GL16**: no REAGL-specific drive data, only algorithm-control records |
| 5 | GLD16 | 38 | same pointers as GL16, plus algorithm-control records |
| 6 | A2 | 10 | only 0 → 15 and 15 → 0 |
| 7 | DU4 | 24 | any level → 0, 5, 10, 15 |

Frames per temperature range (`python3 wbf.py frames`):

```
range          0    1    2    3    4    5    6    7    8    9   10   11   12   13
from degC      0    3    6    9   12   15   18   21   24   27   30   33   38   43
0 INIT       152  136  120  108   92  163  139  125  113   87   79   71   63   59
1 DU          73   65   57   51   43   37   31   26   22   19   17   15   13   12
2 GC16       131  116  102   90   77   66   55   46   38   38   38   38   38   38
3 GL16       131  116  102   90   77   66   55   46   38   38   38   38   38   38
4 GLR16      131  116  102   90   77   66   55   46   38   38   38   38   38   38
5 GLD16      131  116  102   90   77   66   55   46   38   38   38   38   38   38
6 A2          35   31   28   24   21   17   15   12   10   10   10   10   10   10
7 DU4         84   75   65   58   49   42   35   29   24   24   24   24   24   24
```

Subsets over all ranges: the non-zero transitions of A2 ⊂ DU ⊂ DU4.

The Kindle Paperwhite 3 kernel uses the same mode numbering for mode version 0x19 (init, du, gc16, gl16, reagl, reagld, a2,
du4). There, modes 4 and 5 are REAGL and REAGL-D: the drive is that of GL16, and a separate, unpublished REAGL module
processes the pixel buffer before the update. The identical GL16/GLR16/GLD16 streams in this file match that model
[STRONG]: REAGL is an algorithm on the image, not a separate waveform.

Observations:
- **GC16 is coarser than 16 levels.** Several source rows are identical (5 = 6; 7 = 8 = 9 = 10; 11 = 12 = 13), so the panel
  distinguishes about 9–10 grey steps.
- **DU4 at range 8**, net drive in frames (+ toward white, − toward black, `.` = no drive):
  ```
  from\to   0    5    10   15     (all other targets: no drive from any source)
     0      .   +10  +15  +21
     5    -10     .   +5  +11
    10    -15    -5    .   +6
    15    -21   -11   -6    .
  ```
  15 → 0 takes 21 drive frames out of 24 (`..---------------------.`); DU does it in 21 of 22 (`---------------------.`).

## How the stock RK3026 kernel uses this file

From the stock RC2 kernel (Linux 3.0.36+, EBC driver banner `V1.0.20 VERSION 20171023-23`); details in onyx-rc2-ebc.

- **Source.** The kernel reads `/ebc_waveform.bin` from the boot ramdisk into a 1 MB buffer at `waveform_addr=0x7ff00000`
  (passed by the loader on the kernel command line). It runs **no format or CRC check** on this path. Without the file it falls
  back to a built-in Rockchip-format table for a different panel [CONFIRMED in code and log].
- **The panel has its own SPI flash with a different waveform** (`320_R177_AE6A41_ED060KD1U7_TC`, part `ED060KD1U7`,
  VCOM −2.06 V). The driver reads it only for `/proc/panel_info`; the display always uses the ramdisk file [CONFIRMED by log].
  So the panel ID in the flash (KD1U7, lot R177) and the waveform in use (KD1C2, lot R110) disagree.
- **Two formats in one driver.** Besides WBF, the driver also has Rockchip's own `rkf` format (XOR-scrambled, compressed,
  checked with a CRC whose polynomial is 0x04C10DB7, one bit off the standard CRC-32). On the RC2 only the WBF path is live.
- **Mode mapping** (mode_version 0x19): RESET → INIT, FULL/PART → GC16, BLACK_WHITE → DU, A2 → A2, REAGL → GLR16 (= GL16 here),
  OED_PART → DU4. The kernel **never builds a LUT for the DU4 type**: it decodes the stream, then leaves the hardware table all
  zero, so OED_PART updates draw nothing on the stock kernel [CONFIRMED in code, consistent with a device test].
- **Decoded on every update**, cached as a hardware LUT keyed by (EPD mode, temperature range).
- **Temperature quirks of the stock code**, worth not copying:
  - range = first `i` with `temp < bound[i+1]`; for `temp ≥ 43 °C` the lookup falls through and returns range 0
    (the coldest, 0–3 °C: GC16 131 frames instead of 38);
  - the PMIC temperature is a signed byte but is clamped as unsigned (`> 50 → 25`), so −1 °C becomes 25 °C;
  - the decoder checks the 512-frame limit only after a whole RLE run, so a corrupt stream can write past its table.
- **For comparison, the Kindle Paperwhite 3 kernel** keeps one lower bound per range and picks "the last `i` with
  `temp ≥ bound[i]`, stopping at the first `temp < bound[i+1]`", but its loop stops one index early: the hottest range is
  never used, and a temperature at or above its lower bound gets the second-to-last range (below `bound[0]` it uses range 0).
  With a WBF table (range count + 1 bounds plus a checksum, as here) the rule `wbf.py frames` prints is: range `i` covers
  `bound[i] ≤ temp < bound[i+1]`; below `bound[0]` use range 0, at or above the last bound use the last range [STRONG].

## Licensing and linux-firmware

- **The decoder is open** (MIT), and so is this description. **The waveform file is not**: it belongs to E Ink and the panel
  vendor, and no public licence for redistributing it was found. That ONYX's update can be downloaded does not grant the right to
  redistribute single files from it. So the file is not in this repository, and it should not go into other public repositories
  or into images others download.
- **linux-firmware** accepts only files under a licence that lets anyone redistribute them without registration or payment, for
  use with the hardware, perpetual and irrevocable, with a patent grant, and needs a `Signed-off-by` from someone entitled to
  license the file (normally the vendor) (`LICENSE-CRITERIA.md` in the linux-firmware repository).
  Neither this file nor the R177 file in the panel flash can go there without a written grant from the rights holders.
- **What works today:** each user extracts the file locally, either from the official update with
  [`extract_waveform.py`](extract_waveform.py) or from the panel's own SPI flash, which holds the panel's factory waveform
  `320_R177_AE6A41_ED060KD1U7_TC` (see [onyx-rc2-hardware](https://github.com/hsw/onyx-rc2-hardware) `panel-flash/`). A driver
  should load it with `request_firmware()` under a name derived from the panel compatible and check the panel ID and the CRC in
  the WBF header before raising any panel voltage.

## References

- [fread-ink/inkwave](https://github.com/fread-ink/inkwave): WBF parser (header, RLE with the 0xFC toggle, bits per pixel).
- [Ralim/ebc-dev-reverse-engineering](https://github.com/Ralim/ebc-dev-reverse-engineering): a reversed Rockchip EBC LUT
  decoder (`pvi_waveform.c`).
- Amazon, Kindle Paperwhite 3 (7th generation) GPL source, release 5.16.2.1.1 (`Kindle_src_5.16.2.1.1_4097470002.tar.gz` from
  the "Kindle Source Code Notice" page, kernel `linux-3.0.35`, GPL-2.0): WBF header field names, `adv_flags`, the
  voltage-control field layout and units, mode names for mode version 0x19. Only facts are taken from it; no code is copied.

## About this work

Not affiliated with ONYX or Rockchip. This came out of porting Android 4.4 to the RC2 on its stock kernel. The analysis was
done with an AI coding assistant (Claude); results were checked on the hardware where marked [CONFIRMED].

Code: MIT (`LICENSE`). Text: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

The EBC driver that reads this file is part of the vendor's GPLv2 kernel, which ONYX distributes without source; see
[SOURCE-AVAILABILITY.md](https://github.com/hsw/onyx-rc2-ebc/blob/main/SOURCE-AVAILABILITY.md) in onyx-rc2-ebc.
