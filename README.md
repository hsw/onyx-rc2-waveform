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
python3 -m unittest discover -s tests              # synthetic tests; set WBF=ebc_waveform.bin to add the real file
```

Python 3, standard library only. `Wbf(data).decode(mode, range)` returns `table[frame][old][new]`, codes 0/1/2.

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
| 0x14 | panel_size / amepd_part_number / wfm_rev / frame_rate | 0x00 / 0x5b / 0x00 / 0x85 (85 Hz) |
| 0x18 | reserved | `55 00 00 00` |
| 0x1c | xwia: 24-bit offset of the panel string / cs1 = sum(0x08..0x1e) & 0xff | 0x40 / 0x4f |
| 0x20 | wmta: 24-bit offset of the mode table / fvsn | 0x5f / 0x01 |
| 0x24 | luts / mode count − 1 / temperature range count − 1 / adv_flags | 0x04 / 7 / 13 / 0x03 |
| 0x28 | eb (end byte) / sb (single-byte toggle) / reserved / cs2 = sum(0x20..0x2e) & 0xff | 0xff / 0xfc / … / 0x77 |
| 0x30 | temperature bounds (range count + 1 bytes) + checksum | 0,3,6,…,33,38,43,48 °C → 14 ranges |
| xwia | Pascal string (length byte, text) + checksum | `320_R110_AE4D21_ED060KD1C2_TC` |
| wmta | per mode: 24-bit pointer + checksum → per range: 24-bit pointer + checksum → stream | |

Each checksum byte is the low byte of the sum of the bytes it covers.

### Pixel depth

`luts & 0x0c == 0x04` means **5 bits per pixel**: a frame has 32 × 32 entries. With a 4-bit decode the frame counts come out
4× too high (152 for GC16 instead of 38); with the 5-bit decode they match the device [CONFIRMED].

### Stream

- Each byte holds four 2-bit codes, least significant first.
- Outside a single-byte section a byte is followed by a repeat count: the four codes are emitted count + 1 times.
- `sb` (0xfc) toggles single-byte sections, where every byte stands alone.
- `eb` (0xff) ends the stream. After it come one byte (probably a checksum) and 16 bytes of advanced data
  (`adv_flags` = 3), not used here.
- Stream order: the **fast index is the old (source) level, the slow index is the new (target) level** [CONFIRMED against the
  stock kernel's table layout; source/target meaning STRONG from the data: DU and A2 only have non-zero targets 0 and 15, and
  every same-level transition is a no-op in DU/A2/DU4].
- Codes: 0 = no drive, **1 = toward black**, **2 = toward white**. Code 3 never occurs in this file.
- Levels: **0 = black, 15 = white**.

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
| 4 | GLR16 | 38 | **same pointers as GL16**: this file has no REAGL-specific data |
| 5 | GLD16 | 38 | same pointers as GL16 |
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

## References

- [fread-ink/inkwave](https://github.com/fread-ink/inkwave): WBF parser (header, RLE with the 0xFC toggle, bits per pixel).
- [Ralim/ebc-dev-reverse-engineering](https://github.com/Ralim/ebc-dev-reverse-engineering): a reversed Rockchip EBC LUT
  decoder (`pvi_waveform.c`).

## About this work

Not affiliated with ONYX or Rockchip. This came out of porting Android 4.4 to the RC2 on its stock kernel. The analysis was
done with an AI coding assistant (Claude); results were checked on the hardware where marked [CONFIRMED].

Code: MIT (`LICENSE`). Text: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

The EBC driver that reads this file is part of the vendor's GPLv2 kernel, which ONYX distributes without source; see
[SOURCE-AVAILABILITY.md](https://github.com/hsw/onyx-rc2-ebc/blob/main/SOURCE-AVAILABILITY.md) in onyx-rc2-ebc.
