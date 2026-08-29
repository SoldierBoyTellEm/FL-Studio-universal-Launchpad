# Novation Launchpad Unofficial Universal

This folder is laid out to be symlinked directly into the FL Studio user hardware-script directory:

- Windows user hardware path:
  `C:\Users\<you>\Documents\Image-Line\FL Studio\Settings\Hardware\Launchpad R`

The main script file is:

- `device_Launchpad unofficial universal.py`

Shared support modules include:

- `led_display.py` for LED I/O and color conversion
- `palette_lookup.py` for exact MK2 and LP3 palette-index-to-RGB tables

## Suggested install

From an elevated or Developer Mode-enabled PowerShell:

```powershell
New-Item -ItemType SymbolicLink `
  -Path "$env:USERPROFILE\Documents\Image-Line\FL Studio\Settings\Hardware\Launchpad R" `
  -Target "O:\LPX\fl-studio\Launchpad R"
```

Then in FL Studio:

1. Open `Options > MIDI settings`.
2. Enable the Launchpad input and output ports.
3. Set the controller type to `Novation Launchpad unofficial universal`.
4. Reload scripts if FL was already open.

## Getting FL Studio's device identification

To capture what FL Studio thinks your controller is:

1. Connect the 3rd gen Launchpad and assign this script as the controller type.
2. Reload the script, or restart FL Studio.
3. Open the FL Studio `View > Debug log` tab, or the script's own output tab in `MIDI settings`.
4. Look for lines like:

```text
[NovLPd unofficial universal] FL device name: ...
[NovLPd unofficial universal] FL hardware id: ...
```

Send me both values exactly as printed. The hardware ID is the one we can use in
`# supportedHardwareIds=...` so FL auto-matches the newer Launchpad.

## Current behavior

The script now auto-detects:

- Launchpad MK2
- Launchpad X
- Launchpad Mini MK3
- Launchpad Pro (original)
- Launchpad Pro MK3

On init:

- `Launchpad MK2` is switched to `User 2`.
- `Launchpad X`, `Launchpad Mini MK3` and `Launchpad Pro MK3` are switched to
  `Programmer Mode`.
- `Launchpad Pro` is switched to Standalone mode, then its `Programmer` layout
  (two separate SysEx messages — see "Launchpad Pro (original)" below).
- The detected hardware profile is printed to the FL debug log.

For all five devices:

- The 8x8 grid becomes a note layout for the currently selected FL channel.
- LEDs are driven by SysEx or matching MIDI events through the detected device profile.
- Performance mode is exposed on the dedicated `Session` button for 3rd gen devices.

Main page controls:

- `Launchpad MK2`
- Top row `104`: octave down
- Top row `105`: octave up
- Top row `106`: pan left
- Top row `107`: pan right
- Top row `108`: performance mode
- Top row `109`: note mode / note settings
- Top row `110`: FPC pads mode
- Top row `111`: play/pause on release; hold for FL record arm

- `Launchpad X / Mini MK3`
- Top row `91`: octave up
- Top row `92`: octave down
- Top row `93`: pan left
- Top row `94`: pan right
- Top row `95`: Session / performance mode
- Top row `96`: note mode / note settings
- Top row `97`: custom / FPC pads mode
- Top row `98`: play/pause on release; hold for FL record arm

Settings overlay:

- Top grid row `81..85`: overlap choices (`Sequential`, `2`, `3`, `4`, `5`)
- `87`: axis flip
- `88`: chromatic vs scale
- Third and fourth rows: root selection in the split sharp/natural layout
- Fifth and sixth rows: the 16 Novation-style scale slots
- Bottom two rows: MIDI channel `1..16` for FL note color / channel output
- Ninth-column pad `69`: hold to show routing, volume, and pan for FL's selected channel

Surface modes:

- `Note mode`: 8x9 note surface including the right-side note column
- `FPC pads`: simple 4x4 drum-pad layout tiled across the 8x8 body
- `Gross Beat override`: when Gross Beat is the focused plugin in `FPC pads`, the grid swaps to 36 slot triggers, a volume-slot toggle on `17`, and a vertical `Volume Mix` fader on `18..78`

## XY pad (modulators)

The positional XY page's left/right arrows cycle among 4 parallel XY pads,
each its own CC pair and its own crosshair hue (all devices, not Pro-only):

| Pad | CC pair | Hue (palette index) |
|---|---|---|
| 1st | `96/97` | `45` |
| 2nd | `98/99` | `53` |
| 3rd | `100/101` | `5` |
| 4th | `102/103` | `28` (the pre-existing default) |

Which pad is active is RAM-only — it always starts at the 4th (`102/103`)
each session, matching this page's sole CC pair before this existed, so a
DAW mapping already pointed at `102/103` keeps working with no setup.
Cycling wraps rather than clamping. Left/right stay a no-op on the other
XY-mode sub-pages (Vert/Horiz/bipolar faders), same as before.

## Launchpad Pro (original)

The original Launchpad Pro is a basic-support target: it gets the full grid,
side column, and top row, but no left/bottom sidebar (this script has never
used the left/bottom sidebars on any device, so nothing is lost there
relative to the other supported hardware).

Its LED SysEx protocol turned out to be MK2's, byte-for-byte (same set-LED,
set-LED-RGB, light-all, and scroll opcodes; same 6-bit RGB range; same
row*10+col grid/side-column pad numbering). The only real differences from
MK2:

- SysEx product-ID byte `10h`, not MK2's `18h`.
- Top row is round buttons on CCs `91..98` (Up/Down/Left/Right/Session/Note/
  Device/User) instead of MK2's `104..111`. These happen to already match
  the CC numbers this script uses for LPX/Mini MK3's top row, so the same
  arrow/session/note/custom/record mapping carries over unchanged.
- Only the 64 square pads send notes. *Every* round button sends CC, and the
  right-hand column (`19/29/.../89`) is round on this hardware — unlike MK2,
  whose square scene-launch column sends Note On. So the side column is CC
  here, the same as LPX/Mini MK3.
- Entering the addressable "blank canvas" layout is two SysEx messages, not
  one: force Standalone mode (`21h`), then select the Programmer layout
  (`2Ch`). MK2 does this in a single `22h` message; LPX/Mini MK3 in a single
  `0Eh` toggle.

### Top-row remap (experimental)

Both Pro devices get a dedicated top row instead of the stock LP3-style
cycling buttons, plus two bottom-row launchers:

- `Up/Down/Left/Right` (91-94): unchanged (octave/pan).
- `Session` (95) → Performance mode.
- `Note` (96) → Note mode. Dedicated — no longer cycles to Custom. Long-press
  still opens Note settings, unchanged.
- `Device` (97) → FPC. Dedicated — no longer cycles to Step Sequencer or the
  Gross Beat overlay.
- `User` (98) → Custom Modes selector (replaces Record; Record is
  unreachable from the top row for now).
- Bottom-row round button 2 (CC `2`, "Track Select") → Step Sequencer.
- Bottom-row round button 6 (CC `6`, "Pan") → the modulator/XY-fader pages.

This CC `1-8` bottom row is physically the same position on both Pro
devices: the original Pro's only bottom row, and the Pro MK3's *lower*
half-row (its upper half, CC `101-108`, is unused here).

This is a work in progress — Record and the Gross Beat overlay toggle have
no button for now, pending a permanent home elsewhere. LPX, Mini MK3, and
MK2 are unaffected; they keep the original cycling behaviour.

### Use the second MIDI port

The Launchpad Pro exposes three port pairs. Port pair 1 is the Ableton/Live
port; the Standalone layouts (Note, Drum, Fader, Programmer) all live on
**port pair 2**. Assign this script to the second Launchpad Pro input *and*
output — usually named `MIDIIN2 (Launchpad Pro)` / `MIDIOUT2 (Launchpad Pro)`.

On port 1 the failure is confusing rather than obvious: LED SysEx is honoured
device-wide so pads light up, but the Programmer layout-select is ignored (the
stock Note-layout colours stay visible underneath) and pad presses are emitted
on port 2, where this script never sees them. The script logs a warning when
it detects it is bound to the wrong port pair.

Because the LED protocol is otherwise identical to MK2's, the Launchpad Pro
profile also inherits MK2's software-driven note-lock pulse (no hardware
CC-channel pulse like LPX/Mini MK3 have in Programmer layout).

Custom modes (`.syx` slot loading) are LPX/Mini MK3-only for now — Launchpad
Pro's custom-mode SysEx format hasn't been reverse-engineered here, so the
Custom/Device key just has nothing to cycle to on this device.

## Launchpad Pro MK3

Untested — implemented from the LPP3 programmer's reference without hardware.

The Pro MK3 is protocol-identical to the Launchpad X / Mini MK3 path, so it
reuses that profile wholesale: same `0Eh` Programmer/Live toggle, same `03h`
LED command with the same typed colourspecs (static / flashing / pulsing /
RGB), same 0–127 RGB range, same 128-entry palette, same `row*10+col` grid,
same CC `91..98` top row, and the same CC side column. The only difference
that matters is the SysEx product ID: `0Eh`, alongside LPX's `0Ch` and Mini
MK3's `0Dh`.

Its extra controls are recognised as *not ours* and left alone: the left
column (CC `10..80`), Shift (CC `90`), the logo (CC `99`), and the split
bottom sidebar — upper half-row CC `101..108`, lower half-row CC `1..8`.
None of those numbers collide with the `11..88` grid or the `19..89` side
column, so the split bottom rows have no effect on pad-ID interpretation.
They currently pass through to FL as unmapped MIDI.

### Use the first MIDI port

Note this is the **opposite** of the original Pro. The Pro MK3's three
interfaces are MIDI (1), DIN (2) and DAW (3), and Programmer mode runs on
interface **1**, usually named `LPProMK3 MIDI`. Interface 3 speaks the
Session/DAW protocol, which this script does not implement. The script warns
if it detects it is bound to interface 2 or 3.

### Unverified hardware ID

The LPP3 guide's *Device Inquiry* section prints the Pro MK3's reply as
`00 20 29 13 01` — but that is verbatim *Mini MK3's* ID, i.e. a copy-paste
leftover from the Mini MK3 manual. Trusting it would make every Pro MK3
detect as a Mini MK3, so the code instead uses `00 20 29 23 01`, following the
family pattern where the model byte tracks the product ID (X = 03/`0Ch`,
Mini = 13/`0Dh`, Pro = 23/`0Eh`).

This is a guess. Detection therefore also matches the distinctive
`LPProMK3 MIDI` port name *regardless* of what hardware ID is reported, so the
device is still recognised if the guess is wrong. If real hardware logs a
different ID, correct `LPP3_DEVICE_ID_PREFIX` in `device_profile.py` and the
`supportedHardwareIds` line in the main script.

Custom modes (`.syx` slot loading) are LPX / Mini MK3-only; the Pro MK3's
custom-mode SysEx format is not documented in its guide, so the Custom/Device
key has nothing to cycle to there.

## 3rd Gen Assumption

The current Launchpad X / Mini MK3 path assumes Programmer mode uses:

- Top-row CCs `91..98`, physically ordered as arrows, Session, Note, Custom, Record
- Right-column CCs `19, 29, 39, 49, 59, 69, 79, 89`

That matches the common Novation 3rd gen programmer layout. If any right-column control behaves strangely on hardware, that is the first place to verify and adjust.

Current scale presets:

- Minor
- Major
- Dorian
- Phrygian
- Mixolydian
- Melodic Minor
- Harmonic Minor
- Bebop Dorian
- Blues
- Minor Pentatonic
- Hungarian Minor
- Ukrainian Dorian
- Marva
- Todi
- Whole Tone
- Hirajoshi

## State

The script writes its persistent state beside itself:

- `launchpad_unofficial_universal_state.json`

## FPC Color Tuning

The RGB tuning is now selected per device profile:

- `Launchpad MK2` / `Launchpad Pro`: saturation `1.25`, gamma `0.5`
- `Launchpad X / Mini MK3`: saturation `1.35`, gamma `1.2`

Those values are applied from the device profile setup in
`device_Launchpad unofficial universal.py`.

That file stores:

- root
- scale preset
- chromatic/in-key mode
- overlap / row stride
- base octave
- axis flip
- MIDI channel

## Extension ideas

The next useful parity steps would be:

1. Add a true pan offset that shifts the rendered note base without changing tonic.
2. Add an axis/rotation mode that feels like the firmware patch family.
3. Add optional FL playback feedback so pads relight from incoming note data, not only local presses.
4. Match the Mini 467 preset set and settings-page semantics more closely.
