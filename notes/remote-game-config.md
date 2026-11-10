# Remote game configuration and serial-only feasibility

## Request and current conclusion

The user owns an EPROM programmer but prefers a serial route. In addition to both
forms of reel control in `reel-control.md`, they want remote adjustment of maximum
bet, number of paylines, payouts and probabilities. This investigation used the
local `S6M00002B5` main image and `E595211X-05` personality. No live commands, ROM-byte
changes, configuration changes or new CLI setters were made.

**No stock serial code loader or arbitrary RAM writer was established in the paths
checked.** The follow-up below covers reset, startup errors, task initialization,
serial diagnostics and the remaining dispatch mechanisms. It still is not a proof
about every instruction or peripheral leaf. The apparent numbered download (`CF2`)
does not install its payload; the real startup UART path only reports an error.
There is no established stock serial-only installation route to build against.

**Recommended implementation route:** a one-time main-ROM extension that accepts
complete game profiles over the existing J10 link. Subsequent supported parameter changes would
be serial data updates, not another personality-ROM burn. It has since been built as
the experimental firmware extension (`firmware/INTEGRATION.md`; limited live bring-up
in `firmware-bringup.md`); where a proposal in this note differs from the code, the
code governs. It is recommended because the existing RAM-backed game
configuration and table pointers let us retain the original motion and payout engine.
A generic executable-code uploader is not required merely to edit these parameters.

## Requested controls

| Control | Installed source / consumer | What a serial-profile implementation needs |
|---|---|---|
| Maximum bet | Personality `0x10d0f8` → SafeRAM byte `0x43ca1e`, currently 3 | Update the cap together with matching wager eligibility and awards; raising the cap alone does not define a payable game. |
| Number of paylines | `0x10d0f9` → `0x43ca1f`, currently 1; evaluators `FUN_00065128` and `FUN_0003fbbc` | Select valid line patterns, matching record masks and wager rules; account for what the cabinet can display. |
| Payouts | RAM pointer pair `0x43ca92/0x43ca96` points to personality count `0x10d716` and records `0x10d718` | Stage validated records in allocated RAM and redirect the table pointers coherently. Original records are ROM-backed, not copied into a writable payout array. |
| Probabilities | Personality weights `0x10d840` → SafeRAM `0x43c91a`; totals `0x43ccd2[]`; selector `FUN_00008a60` | Update stop weights, computed totals, checksum and selection state together. Win frequency and return are derived consequences, not independent settings. |

These are CPU addresses; subtract `0x100000` for personality-file offsets. Geometry,
symbols and weights are loaded by `FUN_0003756c`. SAS `1F` / `53` read the max-bet
field (`FUN_00028480`, `FUN_0002c95c`); neither provides its setter.

### Wager and line constraints

The installed game is a three-coin, single-center-line game, not a generic
credits-per-line game. Its payout records have inclusive wager limits at record
bytes `+10` (minimum) and `+9` (maximum). Changing the maximum bet from 3 to a larger
number without extending these rules can leave wagers with no eligible award.
A byte-sized cap is not evidence that all values up to 255 are supported.

`FUN_000363f8` constructs seven symbol rows using start/step pairs read directly
from personality addresses `0x10d16c` and `0x10d188`. For three reels the current
patterns are:

| Row | Stop offsets across left, middle, right |
|---|---|
| 0 | `0, 0, 0` |
| 1 | `-1, -1, -1` |
| 2 | `+1, +1, +1` |
| 3 | `-1, 0, +1` |
| 4 | `+1, 0, -1` |
| 5 | `-2, -2, -2` |
| 6 | `+2, +2, +2` |

The evaluators construct these seven rows even though this personality enables
only one line. They use ordering tables `0x6ac58/0x6ac60` and masks `0x6ae3e`.
All eleven installed payout records have line mask **`0x01`** at byte `+12`, so
increasing the line count alone does not make those records pay on other lines.
They also require three matching reels (`+13`). Matching flags and record order
matter; this is not merely a list of symbol-to-amount pairs.

The physical glass marks one center payline. Additional logical lines need a
clear presentation and physical alignment check. Seven internal rows do not
establish seven visible or correctly configured playable lines. Arbitrary new
patterns would also need changes to the mapper, which currently reads ROM directly.

### Probability constraints

The selector uses a 32-entry circular stop-index space, but this game has only
22 physical stops. The RAM weight matrix uses five-byte rows. The installed weight
sum is 32 on each reel; **32 is not the required sum of every possible profile**.
`FUN_00008a60` calls `FUN_000380d6(0, total-1)` using the per-reel word totals at
`0x43ccd2`, then advances through weighted candidate positions.

A valid profile must keep unused stop slots zero, provide nonzero valid totals,
respect the firmware's byte/word arithmetic, and reset/reconcile the selector's
candidate/residual state (`0x4101da[]`, `0x4101c2[]`). An all-zero distribution or
weights that do not agree with their total is not a usable configuration.
Changing one symbol's frequency changes several winning combinations at once.

The symbols on the physical strips must still agree with the symbol table. Adjust
weights rather than relabelling physical symbols in software. A future host-side
profile preview should calculate nominal return and hit frequency before upload.

## Stock serial capabilities and checked dead ends

- **SAS payout multiplication exists:** LP `8B` → `FUN_0002b172` accepts multiplier
  1–10, min/max eligible win, wager restriction, tax-status encoding and enable/disable.
  It writes bounded fields `0x43d370–0x43d37e`; `FUN_0005ab18` checks eligible wins,
  and `FUN_00024d6a` participates in bonus payout. The CLI already encodes this as
  `multiplied-jackpot`. It is **not live-tested**, does not edit individual paytable
  records, and does not alter reel odds. The handler requires bonusing mode
  `0x43e8a2` equal to 1 or 3 and refuses particular setup/diagnostic/door states.
  SAS 6.02 **§13.4 Multiplied Jackpots** discourages this legacy feature because
  implementations differ; **§13.4.3** says eligible progressive wins can also be
  multiplied. It is not the foundation recommended for a general profile editor.
- **J3 `CF1` is limited option editing:** `FUN_00022132` uses option/width table
  `0x742de` and descriptors around `0x73bbc`. Enumerating its 23 table entries finds
  option destinations in `0x43e800–0x43e82e` plus a sentinel, not the game geometry,
  payout pointers or weight matrix above. The setter has additional state/range
  restrictions (`FUN_00057010`, `FUN_0005737e`). These are not arbitrary addresses.
- **J3 `CF2` is not a functioning loader:** `FUN_000223a2`, assembly
  `0x2246c–0x2249e`, reads successive payload bytes but writes every one to the same
  scratch/status byte `0x4124b8`, subsequently overwritten with ASCII success `1`.
  It tracks blocks in `0x43ce94/0x43e961` when `0x43e8cd == 2`; no advancing
  destination pointer or installation/execution call was found.
- **SED's full dispatch matrix was checked:** the 12×12 pointer table at `0x69902`
  contains only `0x26ce4`, `0x26d00`, `0x26d10`, `0x26d66`, `0x26d78` and no-op
  `0x6828e`. These handlers manipulate fixed secondary-device state and replies,
  not an identified code-loading interface.
- **Other apparent routes remain narrow:** default `C/P` dispatch handles fixed
  option, credit/award, host-control and progressive fields; it does not expose the
  requested general game profile. `CF8` is sound, not remote buttons. SAS `6E`
  (`FUN_0002f598` and helpers) resolves known components for authentication/CRC,
  not software installation. SAS `51`/`55` report one game and selected game 1.
  Serial tasks `0x160ec–0x16242` route to peripheral, SAS/SDS, BSG/SED or the known
  generic handler; no separate debug monitor was identified in that routing.

The physical main chips are EPROMs; stock serial access does not make them
in-circuit rewritable. A pure-stock serial route would need an actual existing
RAM-install/control path. Replacing a chip pair once to add such a service is a
different route and must not be described as stock serial support.

## Follow-up: startup and remaining serial paths

This was an offline firmware-interface audit, not a malformed-packet experiment or
an exhaustive security review. No UART traffic, resets, DIP changes or RAM clears
were performed. Analysis definitions in Ghidra were extended; the original image
bytes and CLI were unchanged.

### Reset, errors and diagnostics

| Path | Checked behavior | Why it is not an identified loader |
|---|---|---|
| Reset `0x800` | Vectors SP=`0x437ffe`, PC=`0x800`; RAM tests/clear at `0x978–0x9d6`, ROM checks `0x9ec–0xa54`, kernel init `0xb88`, startup checks `0xb9e`, task creation via `0xbca→FUN_0000154e`, scheduling `0xbec` | Initialization and entry points come from ROM, not incoming bytes. |
| Display helper `FUN_00000c24` | `JMP(A2)` at `0xc92` returns to ROM `0x93c` or `0xb02`; `JMP(A0)` at `0xdec` returns from display bit-banging at `FE10C0` | These apparent indirect transfers use PC-relative continuations established in ROM. |
| Startup error `FUN_00038a06→FUN_00038698` | Configured-host branch initializes DUART2 B through `FUN_00016834`, then calls `FUN_000168d2` with fixed response `0x36` | This is an error-reporting loop, detailed below, not an upload service. |
| Kernel `FUN_00002814` | `0x36fe–0x370a` copies hooks from ROM `0x27c0–0x27cc`, all zero; normal task entry addresses are supplied by ROM startup | Writable scheduler state alone is not a host-settable code hook. |
| Diagnostic Test 8 `FUN_000132ee` | Emits port index + ASCII `A` at `0x134ce/0x135b4`; reads at `0x13520/0x135f0` and records character-comparison results | Loopback/character testing, not a stream-to-RAM installation protocol. |

The startup UART branch requires `FUN_00035b48(0x34)==1` and nonzero host address
`0x43e8dc`. `FUN_000168d2` validates a marked address at `0x168fa–0x1691a`, transmits
the fixed response at `0x1692c`, then repeatedly **reads and discards** received
bytes at `0x16932–0x16948`. Its sole non-stack RAM write is status `0x412508=1`
at `0x1694a`.
The decompiler omits those discard reads; the assembly is the evidence. There is
no payload storage, table activation or jump into received bytes in this routine.

### Runtime receive and dispatch boundaries

- **SAS initialization:** `FUN_00016366`, loop `0x163d6–0x16406`, copies four
  constants from ROM `0x74aca` to destinations listed at `0x74ada`:

  | Destination | Installed value | Meaning |
  |---|---|---|
  | `0x43e8f0` | `0x19732` | `sas_rx_byte` |
  | `0x43e8f4` | `0x19b16` | SAS worker |
  | `0x43e8e8` | `0x100` | Command-table count |
  | `0x43e8ec` | `0x744ca` | Command-table address |

  DUART receive calls at `0x13d04/0x1411e` use the initialized callback. Crucially,
  the worker's calls at `0x1a0ce/0x1a13e` fetch handler pointers from the **literal
  ROM table `0x744cc + commandIndex*6`**, not an incoming execution address. The
  presence of a RAM copy of a table address does not make SAS an uploader.
- **SDS/default protocol:** host option value 2 routes through `FUN_000162a2`,
  then `FUN_00021af0`. Its ordinary receive path is `FUN_00021f4c→FUN_000174e0`,
  then `C→FUN_00022d02` or `P→FUN_00022df8`. In addition to the previously checked
  `CF` handlers, `C1/C2`, `C4/C9`, `C5/C6`, `CA` and `CB` were inspected. They
  manipulate fixed credit/award/host-control fields and queues; none installs
  a game-table payload or a host-chosen code entry point. `CC/CD/CE` return without
  an operation in this dispatcher.
- **Alternate bonusing dispatch:** when `0x43e8d2 & 0x04` is set, `FUN_00021af0`
  uses `FUN_0001a892` and ROM callback table `0x74af2`. `FUN_0001acd4` maps 19
  recognized wire command bytes to indices 1–14, or 0 for unrecognized input.
  All 15 table entries point into main ROM. The leaves cover handshake/sequence,
  information, bonus amounts, multiplier parameters, a fixed wager/event action,
  enable state and a bounded reel/stop trigger list—not arbitrary code loading.
  Activation and wire-protocol identity of this branch were not established.
- **Peripheral routing:** `FUN_00016134` selects `FUN_0001163c` / `FUN_0000fa1c`,
  `FUN_00034fc2`, or the generic handler. `FUN_000161f2` selects `FUN_0001e696` or
  the generic handler. The examined tasks use fixed buffers and protocol states.
  The apparent transfer in `FUN_00034fc2` uses fixed 32- and 26-byte blocks through
  `FUN_000350a8/FUN_0003513a`, with outbound peripheral commands `FUN_00035386` /
  `FUN_00035632`; this is not an established host-to-MPU code loader. Every
  bill-acceptor/printer leaf has not been exhaustively audited.

One potentially misleading capability deserves a separate boundary: commands
`0x76–0x7a` in the alternate bonusing branch reach `FUN_0001b5a2`, which stores up
to 50 reel/one-based-stop pairs at `0x43e8fa`. `FUN_0006616a` compares those pairs
against **already committed** stops at `0x43e000`; its caller `FUN_00065b4a` performs
bonus waiting/award handling. This is a **bonus-trigger list**, not a reel-position
setter, probability-weight editor or code loader. It was not activated or tested.

### Additional cross-checks and limits

A search of Ghidra's currently disassembled `JMP` and `JSR` instructions was used
to find overlooked indirect dispatch sites, not to claim full binary coverage.
The late `JMP(A0/A1)` sites at `0x6896a`, `0x68e66`, `0x68f18`, `0x68f26`, and
`0x68f8a` are compiler helpers returning via saved stack addresses. The game-state
call at `0xa660` indexes ROM `0x78f86`; state-machine calls at `0x48992/0x48ba8`
select callbacks supplied by their caller, with examined callers `FUN_0005391c`
and `FUN_00054c70` supplying ROM tables. None is evidence of a serial loader.
Unparsed instructions, computed writes and every possible failure state are not
covered by that search.

The manual's explicit boot/download passages examined refer to peripheral devices:
**Module 8, ZT1200 Modes** describes the bill acceptor's boot mode, and **Ardac WACS
Features** mentions EPROM replacement or download. They do not establish an MPU
firmware-update interface.

Useful original-byte reproduction points in `roms/s6000_main_S6M00002B5.bin`:
reset vectors at `0`, kernel hooks `0x27c0–0x27cc`, SAS source/destination tables
`0x74aca–0x74ae9`, alternate command bytes `0x1ad2e–0x1ad40` and callbacks
`0x74af2–0x74b2d`. Addresses in the main image equal file offsets. SHA-256:

- Main: `49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a`
- Personality: `7befde1d6ae296122ef2024f9d21e5e3d1c714a4c4d085aabfc6405a4176316f`

**Decision:** the examined native interfaces do not provide a credible initial
serial-only installation route. Recommend the one-time replacement main-ROM pair,
then serial profiles, rather than treating an unidentified boot mode as an
implementation dependency. This is an engineering recommendation, not proof that
an undiscovered route is mathematically impossible.

## Coherent profiles, persistence and reporting

Configuration is protected, not a collection of independent writable bytes.
`FUN_0003756c` fills `0x43c87a–0x43ccdb` and writes a negative word sum at
`0x43ccdc`. `FUN_0000f2da` checks the complete block through `0x43ccdd` for a
zero 16-bit additive sum. Editing a cap, weights or table pointers without updating
that sum can trigger the configuration fault flag `0x43c31c`.

The tables are in battery-backed SafeRAM, but that does not prove arbitrary edits
persist through all transitions. Startup's load is conditional on `0x43c332 != 55aa`
(`FUN_0000154e`); other calls into the loader occur at `0x63db8`, `0x64120`, and
`0x6430a`. Do not claim that every warm reset reloads everything, or that no reset
or mode change can overwrite a profile. Any payout-table RAM allocation must also
have an explicit restoration/validation lifecycle.

The proposed extension should stage a complete versioned profile, validate it,
then activate it atomically while the game and selector cannot observe half an
update. It must preserve sensor checks, accounting and existing credit state,
rebuild dependent caches, and distinguish accepted, active and failed requests.
Read-back should identify the active profile and its checksum, with an explicit
return-to-original operation. Persistence and recovery behavior still need design.

SAS `1F`/`53` currently obtain paytable identity and percentage text from ROM even
though the wager cap comes from RAM. Their original `90.85%` must not be presented
as the active profile's calculated return after probability/award changes.
Code-ROM signatures likewise do not attest a mutable game profile. Reporting and
wire-format limits must be handled explicitly, not silently mislabelled.

## Offline math cross-check

A host-only enumeration of all 22³ stop triples, weighted by the three ROM weight
columns and evaluated against the installed records in order, reproduced:

| Wager | Weighted nominal award / outcome mass | Nominal return / wager | Hit frequency |
|---|---|---|---|
| 1 | `29312 / 32768` | 89.453125000% | 13.409423828% |
| 2 | `58912 / 32768` | 89.892578125% | 14.068603516% |
| 3 | `89312 / 32768` | 90.852864583% | 14.068603516% |

The rounded one-/three-coin returns match the personality's `89.45% / 90.85%`
strings; max-bet hit frequency matches the local game list's `14.07`
(`roms/README.md`, **Which game is installed**). This supports the extracted
installed-game model, not a general multiline implementation. It uses nominal ROM
awards, excludes external bonuses and tournament state, and does not substitute
for the actual progressive amount or physical-machine testing.

Reproduce from the repository root; this reads the original personality only:

```python
from fractions import Fraction
from itertools import product
from math import prod
from pathlib import Path

p = Path("roms/personality_E595211X-05.bin").read_bytes()
max_bet, lines, stops, reels = p[0xd0f8:0xd0fc]
symbols = [p[0xd7d2+s*5:0xd7d2+s*5+reels] for s in range(stops)]
weights = [p[0xd840+s*5:0xd840+s*5+reels] for s in range(stops)]
records = [p[0xd718+i*16:0xd728+i*16] for i in range(p[0xd716])]
totals = [int.from_bytes(p[0xd7c8+r*2:0xd7ca+r*2], "big") for r in range(reels)]
assert lines == 1 and reels == 3
assert totals == [sum(w[r] for w in weights) for r in range(reels)]
assert all(r[12] == 1 and r[13] == reels and r[11] & 0x0b == 0 for r in records)

def matches(want, got):
    if want == 0x24:
        return True
    if want & 0x80:
        a = 0xd000 + (want & 0x7f)*10
        return got in p[a+1:a+1+p[a]]
    return want == got

denominator = prod(totals)
for bet in range(1, max_bet+1):
    wins = awards = 0
    for ss in product(range(stops), repeat=reels):
        mass = prod(weights[s][r] for r, s in enumerate(ss))
        got = [symbols[s][r] for r, s in enumerate(ss)]
        for rec in records:
            if (rec[12] & 1 and rec[10] <= bet <= rec[9]
                    and all(matches(w, g) for w, g in zip(rec[4:4+reels], got))):
                award = int.from_bytes(rec[:4], "big")
                awards += mass*award
                wins += mass*(award > 0)
                break
    print(bet, awards, denominator,
          float(Fraction(awards, denominator*bet)*100),
          float(Fraction(wins, denominator)*100))
```

## Next implementation decisions

If a one-time main-ROM change is acceptable, specify supported wager ranges and
line/wager semantics, preserve native progressive handling unless deliberately
changed, decide profile persistence, and implement upload/read-back/activation
before any live reel or award experiment. The stock-serial-only alternative remains
an unproven research path, not a promised prerequisite-free implementation.

## Native implementation handoff — restricted first ABI

This section records approved extension contracts and read-only static evidence;
it is not a completed patch or deployment approval. Main image and personality
hashes are in **Additional cross-checks and limits**. Ghidra identifies the main
image as raw `68000:BE:32:default`; main addresses equal file offsets. No machine,
ROM or intentional Ghidra documentation edits accompanied this handoff.

### Supported profile contract

- CHARGE PER LINE: enabled line count L=1..5; max coins/line=1..3; aggregate
  wager T=b*L and aggregate cap `L*max_per_line`, at most15. Keep native accounting
  in T; use b only inside evaluation. Neither255 nor255/L is a supported limit.
- Fixed installed geometry: three reels,22 physical stops; selected outcome
  bytes are zero-based0..21. Fixed active row order is `00,01,02,03,04`, using
  the first five patterns in **Wager and line constraints**, independent of L.
  Physical alignment/presentation of added logical lines still needs acceptance.
- Exactly11 ordered16-byte records. Used symbol columns permit only
  `0A,0B,0C,0D,0E,18,80,81`; wildcard24 is forbidden there. Preserve unused
  symbol columns+7/+8 as24, matching count+13 as3, ordinal+14 as exact record
  index0..10, and+15 as0. Inclusive per-line wager bounds are+10 minimum/+9
  maximum, within1..3 with minimum<=maximum. Normal line masks are nonzero subsets
  of `(1<<L)-1`. Flags+11 permit only00/10/20/40; nonzero flags require mask01.
- Nominal award per record:0..1500 credits. At most one normal matching record
  pays per line under these flags, so maximum nominal spin award is7500. This
  approved first-ABI ceiling is based on the stock largest nominal1500; it is
  not a discovered universal native ceiling. INFO must report these limits.
- 66 unsigned byte weights,22*3, mapped into native five-byte rows. Clear unused
  reel columns and stop slots22..31. Each reel total must be nonzero; the largest
  possible22-byte sum is5610, fitting the native word total. Update `43CCD2[]`
  and reconcile selector residuals `4101C2[]`, candidates `4101DA[]` and current
  reel selector `4101D6` while selection cannot run.
- The agreed wire profile is252 bytes, with count at+7 and records at+76. These
  are NEW ABI choices, not stock protocol fields. The active persistent slot must
  contain TWO extra zero guard records at+252..+283, outside the wire payload;
  allocate and validate them after restoration as well as upload. Slot allocation,
  metadata and power-fail commit protocol remain implementation responsibilities.

### Native inputs, counters and normal start

`0xAE6A` has no arguments. It examines queues1,2,3 in that priority order and
consumes one event into longword `0x4105DC`. D0.w returns the selected queue's
PRE-DEQUEUE COUNT, not the event enum; zero means no event. Main `0xA53E` copies
D0.w into D6.w and tests zero/nonzero. A private-event adapter may return1 after
setting4105DC. Preserve native queue priority and events.

Dispatcher `0xA5B4..0xA5E0` chooses
`77CC6 + ((event-1)&255)*120 + mode*4`:

| Event | Native meaning / routing |
|---|---|
|1 | SPIN/repeat wager; modes2/4 -> `FUN_00061172`; mode0 -> `FUN_0004320a`, which may debit a repeat wager first |
|9 | Credit BET ONE; modes0/2 -> unnamed routine42F08; mode4 ->430A8 |
|4 | Physical coin processing; NEVER synthesize this for CLI credit wagering |

`FUN_0001b2a2`, `1B302..1B336`, already posts queue1/event9 after state, cap and
counter69 checks: evidence for an internal remote queued wager equivalent, not a
proven J10 wire API. Physical SPIN's debounced `FF1014 & 1000` reaches
`72EE -> FUN_00008c1e -> queue3/event1`.

Define CLI `placebet` as an idempotently tracked target paid wager for one game;
`startnormalspin` requires that matching paid wager in mode2/4. Do not silently
post event1 in idle mode0 and trigger an unintended repeat debit. Before starting,
require `T%L==0`, `1<=T/L<=max_per_line`, matching active profile/game identity,
normal eligibility, and no diagnostic/motion/request conflict.

`FUN_0004341c` is a ONE-CREDIT primitive: actions56 then55, then event73.
Action56 (`FUN_000468de`) debits credits and relevant sub-balances and increments
wager-related counter91. Action55 (`FUN_0004687a`) increments wager and counters98
and0, retaining native meter handling. BET ONE must preflight credits>=L and
T+L<=cap, then perform L native units. Do not globally multiply4341C:
`FUN_0004320a`, `432F8..43362`, already loops in aggregate credits over the repeat
wager/cap. Validate repeat wagers across profile changes. Physical coin routines
also increment wagers in single actual-coin units; do not multiply deposits.
Unsupported coin/alternate wager modes must be rejected or separately implemented.

ROM descriptor6A14C identifies wager counter43 as three-byte packed BCD at438034.
Descriptor6A16C identifies credits47 as protected four-byte BCD at43D546.
`FUN_0003bc5c`, `FUN_0003cf86`, `FUN_0003bdca`, `FUN_0003bea6` implement counter
validation/arithmetic/mirrors/checksums. Never overwrite these as binary integers.

### Original-byte hook manifest and register contracts

These bytes were checked against the original main file. They are expected-input
assertions, NOT replacement machine code. Preserve stock behavior when inactive.

| Site | Expected original bytes | Active contract |
|---|---|---|
|0000A53E | `4eb90000ae6a` | Request/native reader adapter, no args;4105DC event and D0.w availability as above |
|00042FAC | `4eb90004341c` | L-credit BET ONE wrapper with aggregate preflight; keep4341C as one-credit primitive elsewhere |
|0004324E | `4eb9000549c8` | Repeat-wager read adapter if needed; existing stack argument counterC9, D0 returns a valid aggregate wager |
|00065168 | `4eb900035fdc` | Call original35FDC, then active-only normalize already-capped global4103B0 from T to T/L |
|0003FC34 | `23c0004103b0` | Diagnostic/pre-evaluation assembly shim: incoming D0 is capped T; store active-only T/L at4103B0 |
|00065184 | `0c3900020043ca1f` | Line-order entry trampoline, set A2 and resume651A6 |
|0003FBCC | `0c3900020043ca1f` | Diagnostic line-order entry trampoline, set A2 and resume3FBEE |
|000615CE | `207c004101dd12300000` | Candidate-source trampoline; D2=reel index, supply D1.b=validated stop, resume615D8 |

C-call adapters must preserve D2..D7/A2..A6 and SP balance. Scalar returns are D0;
word/byte native returns must be explicitly narrowed. Small scalar stack arguments
occupy32-bit slots; byte/word fields are their low-order portions on this big-endian
ABI. Do not adopt Ghidra's truncated post-yield function signatures.

For each line-order site, replace the eight-byte compare with a six-byte JMP plus
NOP to an assembly shim. Save D0/D1/A0/A1; call a shared no-argument C helper
returning the row-table pointer in D0; move D0 to A2; restore saved registers;
JMP the stated continuation. C preserves D3 (diagnostic wager) and the outer A6
frame. A2 is intentionally replaced after C returns. The helper returns active
order00,01,02,03,04; inactive behavior returns6AC58 for count2/4, otherwise6AC60.
This avoids count4 selecting row6 (+2 offsets).

**Implemented** differently (`LINE_ORDER` in `firmware/src/game_hooks.S`): no C helper
is called. The assembly macro tests `game_state.identity_order` and loads A2 directly:
a custom profile uses native identity table `6AC60`; otherwise it reproduces the
native choice (`6AC58` for counts 2/4, else `6AC60`). Only A2 and CCR change, so no
registers are saved.

The3FC34 shim must explicitly pass incoming D0 as a stack argument to the common
normalization helper, store its result at4103B0, restore incoming registers, and
RTS to3FC3A. A normal C argument is not implicitly in D0. Diagnostic callers must
use valid aggregate units; invalid requests must not display stale scratch awards.
Normal65168 runs after the native total cap, and leaves438034 and all native
accounting snapshots in aggregate units. Set active43CA27=1 to enable all L lines.

The candidate shim retains protected stop writes through00400 at615E4
(`4eb900000400`), and everything downstream. Preserve other live registers and
only replace D1.b. The original copy-loop guards at615AE..615C4 must still govern
entry: do not consume a request when recovery/preserved/special outcomes skip the
copy. Bind all three validated stops to the eligible game, not independent reel
requests. `616E2 -> FUN_00036028` maps committed43E000; `617E4 -> FUN_0005d368`
starts normal motion; `FUN_00065128` evaluates that same committed outcome.

Useful native function interfaces for assembly/typed C wrappers (addresses are
fixed entry points, not new Ghidra signatures):

```c
uint32_t counter_read(uint32_t id);                 /* 000549C8 */
int32_t counter_compare(uint32_t id, uint32_t n);   /* 000548BC: -1/0/+1 */
void accounting_action(uint32_t id, uint32_t n);    /* 00053DD0; low word id */
void queue_post(uint32_t q, uint32_t event, uint16_t *status); /* 00003B84 */
void saferam_u8(uint8_t *p, uint32_t n);            /* 00000400 */
void saferam_u16(uint16_t *p, uint32_t n);          /* 00000426 */
void saferam_u32(uint32_t *p, uint32_t n);          /* 0000044C */
void clear_current_awards(void);                    /* 00035FDC */
void rebuild_progressive_stop_cache(void);          /* 00036A7A */
void position_absolute(const uint8_t *stops, uint32_t force); /* 0005FB78 */
void normal_motion(uint32_t reels, const uint8_t *stops);     /* 0005D368 */
```

Queue posting uses trap0:03B84 loads D1.w=queue, D2=event, D0=26 before the trap;
its optional status pointer receives the returned word. Motion APIs yield or
handoff pointers, so their buffers must outlive consumption; never call them from
receive interrupt context. Direct diagnostic positioning uses force0 and exclusive
idle diagnostic ownership, not a payout test or normal-game commit.

### Confirmed native cache reuse under the restricted schema

Reuse `FUN_00036a7a`; NO entry hook or replacement builder is needed under the
contract above. It clears150 bytes at41C74A and enable41C7E0, selects the first
record with flags&10, then builds per-reel symbol-stop lists. It does not test
wager eligibility when choosing that record. First flag10 mask01 exits its
transformation section at36DFC..36E06: no multiline or FF expansion runs.

The native bound quirk remains real:36B30 reads flags before36B38..36B50 tests
index<=count. For count11 with no flag10 it reaches records11 AND12. With records
at profile+76, its furthest flag read is profile+279, inside the two guarded
records ending at+284. Never point this consumer at an unguarded wire buffer.

The verified groups80/81 at personality10D000/10D00A contain unique members
0D,0E and0A,0B,0C respectively. Thus each reel produces at most22 entries plus
FF sentinel, fitting each native30-entry column. Arbitrary high-bit group IDs
are NOT permitted. If any constraint is relaxed, reassess cache safety first.

Loader3756C invokes36A7A at37B94 when !(43E8D2&80); profile activation must rebuild
under that condition after the active pointer/configuration/symbol state is
coherent. Award-only changes do not alter cache membership, but complete-profile
activation may consistently rebuild while quiescent.35FDC only clears current
line awards/flags; five slots are proved by35FFE's compare5, with43CCF4..43CD04
followed immediately by total43CD08. Line index5 aliases that total.

### Native CRC, configuration sum and main-ROM sum are different

`FUN_000185ac` is the native reflected CRC-16/KERMIT computation:

```c
/* Stack: pointer, 32-bit length, 32-bit seed slot (low word used).
   Return D0.w; upper D0 bits are not a defined zero extension. */
uint16_t native_crc(const uint8_t *p, uint32_t length, uint32_t seed);
/* Entry000185AC, nibble factor0x1081, message seed0, no final xor.
   Check: native_crc("123456789",9,0)==0x2189. */
```

It processes low then high nibble using
`crc=(crc>>4)^(((crc^nibble)&15)*0x1081)`. Existing SAS message CRC is appended
LOW BYTE FIRST (`tools/sas_protocol.py:with_crc`). Profile CRC coverage must be
specified by the shared new ABI; it is not inferred from stock SAS message length.
Validate the extra guard bytes independently if they are outside that coverage.

Configuration integrity: sum big-endian16-bit words43C87A..43CCDB; write the
negative low16 sum at43CCDC via00426. `FUN_0000f2da` checks through43CCDD for zero.
Change active cap43CA1E=L*max_per_line, line count43CA1F=L, all-lines43CA27=1,
weights/totals and the selected payout pointer pair coherently before rechecks.
`43CA92 + 8*43862D` points to count; `43CA96 + 8*43862D` points to records. For
this ABI point them to active-profile+7/+76, with count11 and validated guards.
Do not assume the selected payout index can change independently afterward.

Main-ROM boot integrity is NOT CRC16/KERMIT:0A32..0A54 use00C14 to add big-endian
words over000000..0007EF and000800..07FFFF, excluding0007F0..0007FF. Combined
low16 sum is zero in the original image.00C14 is register-based: A0=word pointer,
D1=nonzero even byte count, D0.w=running sum; it feeds watchdogFF101E. Retain the
checks and compensate a deliberately reserved build checksum word, rather than
disabling checks or blindly summing the entire file. Host reproduction of those
exact ranges returned0000; seed0 CRC check above returned2189.

### Nominal award ceiling and release blockers

The codec's former `max_award*lines<=UINT32_MAX` rule alone is not native-safe.
`FUN_000663c2`,66452..66490, adds full32-bit awards without overflow checks.
`FUN_000589ba`,5911A..59126, sends pending award to action10; `FUN_000445b0`,
4463E..44646, stores it in counter44 (four BCD bytes, maximum99,999,999).
`FUN_0003bd24`,3BD54..3BD64, converts only the descriptor's width, with no excess
value rejection.100,000,000 is an admitted-u32 counterexample that cannot be
stored faithfully. Counter-add3BDCA calls69BF6 at3BE24 and ignores its carry.

Enforce record<=1500 and nominal aggregate<=7500 in BOTH host and firmware; INFO
reports those limits. Preserve native credit-limit/handpay routing. Actual credit
updates must check current credits plus final post-bonus award against99,999,999
and configured credit limit. Ordinary nominal caps do not prove external award
safety:589BA has multiplier/bonus paths at58EBA,58EDA,58F5A and later; multiply
helper68C8A returns only the low32-bit product. Do not silently clamp/drop awards.

Persistent profiles, payout pointers and guards require validation/restoration
before any consumer after reset or personality reload. A transport ACK or separate
consumed flag does not prove exactly-once accounting: debit, wager and meter writes
are multiple operations, and0044C itself performs two word writes. Bind persistent
request identity, profile generation and complete selected-stop vector to native
game/recovery state; test failures between accounting/commit phases. Do not replay
an uncertain debit or start merely because a host retried. Crash-safe coupling,
physical coin/alternate modes, diagnostic entry/exit, external progressive/bonus
bounds, native reporting and on-machine alignment remain release blockers.

Next: implement the restricted ABI against this manifest, assert original bytes
before patching, preserve inactive stock behavior, and verify accounting/recovery
boundaries before any machine deployment.
