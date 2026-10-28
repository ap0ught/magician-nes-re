# 20 — IPS of the added modules on the retail cart

Parent is tag `release` (`703392e`). `make rom` there is `Magician (USA).nes`, full sha1 `7b0da149c3892b9aec3e97400b79e1089cb8cc47`, header byte 6 `$42`. This branch keeps that cart, that header, `DEFAULT_CART = release`, and no `TITLE.SRC` overlay.

The modules are the ones from master (`b8dc908`). Their Beta 1 origins are not free on this image. What was placed, and what was not:

Placed. Each range below was `$00` (or, for the title hook, the three-byte `jsr`) before the module was written. Bytes the module writes that already equalled the cart are not in the IPS.

| module | CPU | PRG | what the retail bytes were |
| --- | --- | --- | --- |
| `DRAW.PDS` | `$FBC1` | `$1FBC1` | first 11 of the 127 `$00` bytes at `$FBC1`. SAM.SAM starts at `$FC40` (`AA 2A 1F…`, the same first bytes as Beta 1's SAM.SAM at `$FB80`). |
| `AGGRO.PDS` | `$FBCC` | `$1FBCC` | next 27 of that run |
| `HEALTH.PDS` | `$FBE7` | `$1FBE7` | next 9 |
| `WEAK.PDS` | `$FBF0` | `$1FBF0` | next 25, ending at `$FC08` |
| `INSPECT.PDS` | `$FFA0` | `$1FFA0` | 56 bytes of the 80 `$00` bytes at `$FFA0`. `$FFF0` is the `MAGIC-*` signature and is not written. The `$FBC1` run has 55 bytes left after the four helpers; this module is 56, so it does not fit there. |
| `INSPBODY.PDS` | `$B9C8` | `$019C8` | slot 0. Last nonzero in that slot is file `$19C6`. `$19C8` through the end of the bank is `$00` (1592 bytes). Slot 1, where Beta 1 put this body, has no hole that long (longest `$00` run is 30 bytes at `$BFA7`). `inspdraw` passes bank 0 to farjsr. |
| `INSPTITL.PDS` | `$8056` | `$00056` | retail `jsr initdma`. The bytes were `20 3C F9` and the next instruction, not written, is `jsr initcols` at `$F04A` (`20 4A F0`). `$F93C` is `lda #$00 / sta $017F / lda #$7E / sta $29 / rts`. Beta 1's `$8040` on this cart is `jsr $F9A3` then `lda #$EF`, so it is not that site. |

Not placed.

- `INSPHOOK.PDS`. Beta 1's site is the operand of `jsr tickmana` at `$DFF0`. Retail `$DFF0` is `10 06 85 12 BC EC 05 BD`. The sprite wipe that ends in `cpx #$40` is at `$F07C` and ends in `rts` at `$F08D`, not a `jsr`. `20 C5 F2` occurs nowhere.
- `SPNAME.PDS`. `$CAC2` is `00 00 00 00 00 00 01 01`, not ` UNUSED`. The string `UNUSED` does not occur. The high-bit text that starts at `$C600` runs through `UNKNOWN666` at `$C68D` and then stops being text.
- `SPSLOT.PDS`. `$8F9C` is `62 20 F1 8E`, not the `$12` of Beta 1's slot-27 vector. `$8FC6` is `10 F7 20 CE`, not `$91`.

`INSPECT.PDS` calls farjsr at the retail entry `$F8F7` (`jmp ($0025)` is the indirect inside it) and jumps to initdma at `$F93C`. Its `jsr tickmana` is still the source symbol `$F356`. The bytes at `$F356` are `A8 A5 13 99 9E 03 E8 E0`, not Beta 1's tickmana prologue, and no `lda / and #$08 / ora` sequence exists in this PRG. Nothing in the retail image calls `inspdraw`, because the hook was not placed.

`ips/retail-modules.ips` is the full-file delta, header included. `python3 tools/retail_ips.py check` applies it to the retail file and requires the result to equal `make rom`. The retail file's sha1 stays `7b0da149c3892b9aec3e97400b79e1089cb8cc47`. The patched ROM's sha1 is `ef8bb81bd6da2af0ee4b82671a636ba3a506e8eb`.

Hunks, file offset then length. 23 records, 662 bytes. A record stops where the module's byte already equalled the cart, so one module is several records. `python3 tools/retail_ips.py check` prints this same list:

```
$000067     2  INSPTITL.PDS
$0019D8    11  INSPBODY.PDS
$0019E4    70  INSPBODY.PDS
$001A2B   166  INSPBODY.PDS
$001AD2   123  INSPBODY.PDS
$001B4E    35  INSPBODY.PDS
$001B72     3  INSPBODY.PDS
$001B76    52  INSPBODY.PDS
$001BAB     1  INSPBODY.PDS
$001BAD    12  INSPBODY.PDS
$001BBA    28  INSPBODY.PDS
$001BD7    21  INSPBODY.PDS
$001BED    15  INSPBODY.PDS
$01FBD1     1  DRAW.PDS
$01FBD3     9  DRAW.PDS
$01FBDC    25  AGGRO.PDS
$01FBF6     1  AGGRO.PDS
$01FBF7     9  HEALTH.PDS
$01FC00    25  WEAK.PDS
$01FFB0    28  INSPECT.PDS
$01FFCD     3  INSPECT.PDS
$01FFD1     7  INSPECT.PDS
$01FFD9    15  INSPECT.PDS
```
