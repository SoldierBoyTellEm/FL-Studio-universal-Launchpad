# custom_mode.py
# Parses Novation Components .syx custom mode files and exposes per-pad data.
#
# Only pad-type note/CC controls are loaded (faders, keyboards, metadata widgets ignored).
# Two SysEx formats are supported:
#
# FORMAT A – "old binary" (factory-custom-modes.syx, older Components exports):
#   F0 00 20 29 02 0E 05 <version> <slot> <name-bytes> 00
#       [if version>0: metadata-bytes 7F]
#       customPalette lightshowMode onColor
#       [8-byte pad chunks: padIndex midiChannel msgType controlValue
#                           behaviour onValue offValue offColor]
#   F7
#   padIndex uses LP coordinates: 10*(8-row)+(col+1), 11..88
#   msgType: 0=off, 1=note, 2=CC, 3=program-change
#
# FORMAT B – "container" (current Components download/snapshot format):
#   F0 00 20 29 <dev0> <dev1> 20 00 <palette> 40 <slot>
#       [containers...] F7
#   Container types:
#     0x20 len chars  – name
#     0x21 len bytes  – metadata (ignored)

#     0x00/01/02/04/05/06/07/08 val – single-byte properties
#     0x40|len ctrl_idx [8 bytes] – pad/fader control (len=0 means OFF)

#   ctrl_idx 0–63 = 8×8 grid pads (row 0 = top, LP 81–88).
#   ctrl_idx 64–71 = faders (skipped).
#   LP coordinate: row=ctrl_idx//8, col=ctrl_idx%8
#                  → padIndex = 10*(8-row)+(col+1)

#   Pad control 8 bytes: channelAndBehaviour offColor onColor(?) ?
#                        ? ? ? note
from __future__ import annotations
from pathlib import Path
from typing import Iterator
# Public types
MSG_OFF  = 0
MSG_NOTE = 1
MSG_CC   = 2
MSG_PC   = 3

class CustomFader:
    """A fader strip parsed from a Format-B .syx custom mode."""
    __slots__ = (
        "fader_index", "midi_channel", "cc_number",
        "min_value", "max_value", "bipolar", "horizontal",
        "off_color", "on_color", "use_device_channel",
    )
    def __init__(
        self,
        fader_index:       int,
        midi_channel:      int,
        cc_number:         int,
        min_value:         int,
        max_value:         int,
        bipolar:           bool,
        horizontal:        bool,
        off_color:         int,
        on_color:          int,
        use_device_channel: bool,
    ) -> None:
        self.fader_index        = fader_index
        self.midi_channel       = midi_channel
        self.cc_number          = cc_number
        self.min_value          = min_value
        self.max_value          = max_value
        self.bipolar            = bipolar
        self.horizontal         = horizontal
        self.off_color          = off_color
        self.on_color           = on_color
        self.use_device_channel = use_device_channel

    def resolved_channel(self, device_channel: int) -> int:
        return device_channel if self.use_device_channel else self.midi_channel

    def pads(self) -> tuple[int, ...]:
        """LP pad indices covered by this fader strip, low→high value order."""
        if self.horizontal:
            row = 8 - self.fader_index
            return tuple(row * 10 + col for col in range(1, 9))
        else:
            col = self.fader_index + 1
            return tuple(row * 10 + col for row in range(1, 9))

class CustomPad:
    """A single pad entry loaded from a .syx custom mode."""
    __slots__ = (
        "pad_index", "midi_channel", "msg_type",
        "control_value", "behaviour",
        "on_value", "off_value", "off_color",
    )
    def __init__(
        self,
        pad_index:     int,
        midi_channel:  int,
        msg_type:      int,
        control_value: int,
        behaviour:     int,
        on_value:      int,
        off_value:     int,
        off_color:     int,
    ) -> None:
        self.pad_index     = pad_index
        self.midi_channel  = midi_channel
        self.msg_type      = msg_type
        self.control_value = control_value
        self.behaviour     = behaviour
        self.on_value      = on_value
        self.off_value     = off_value
        self.off_color     = off_color

    @property
    def is_note(self) -> bool:
        return self.msg_type == MSG_NOTE
    @property
    def is_cc(self) -> bool:
        return self.msg_type == MSG_CC

    @property
    def is_off(self) -> bool:
        return self.msg_type == MSG_OFF

    @property
    def uses_device_channel(self) -> bool:
        return self.midi_channel == 127

    def resolved_channel(self, device_channel: int) -> int:
        return device_channel if self.uses_device_channel else self.midi_channel

class CustomMode:
    """All pads and faders from one slot in a .syx file."""
    def __init__(
        self,
        name:    str,
        slot:    int,
        on_color: int,
        pads:    dict[int, CustomPad],
        faders:  list[CustomFader] | None = None,
    ) -> None:
        self.name     = name
        self.slot     = slot
        self.on_color = on_color
        self._pads: dict[int, CustomPad] = pads
        self._faders: list[CustomFader] = faders or []
        # Build a lookup: LP pad index → fader that owns it
        self._pad_to_fader: dict[int, CustomFader] = {
            lp: fader
            for fader in self._faders
            for lp in fader.pads()
        }

    def pad(self, pad_index: int) -> CustomPad | None:
        return self._pads.get(pad_index)

    def fader_for_pad(self, pad_index: int) -> CustomFader | None:
        return self._pad_to_fader.get(pad_index)

    @property
    def faders(self) -> list[CustomFader]:
        return self._faders

    def __iter__(self) -> Iterator[CustomPad]:
        return iter(self._pads.values())

    def __len__(self) -> int:
        return len(self._pads)

    def with_slot(self, slot: int) -> "CustomMode":
        """Return the same parsed controls assigned to a different script slot."""
        return CustomMode(self.name, slot, self.on_color, self._pads, self._faders)

# Shared helpers
def _is_valid_pad_index(pad_index: int) -> bool:
    """True for LP grid coordinates 11..88 (row 1-8, col 1-8)."""
    col = pad_index % 10
    row = pad_index // 10
    return 1 <= row <= 8 and 1 <= col <= 8

def _container_idx_to_lp(sysex_idx: int) -> int:
    """Convert format-B pad ctrl_idx (0..63) to LP coordinate.
    ctrl_idx 0–63 = 8×8 grid, row 0 = top (LP 81–88).
    ctrl_idx 64–71 = faders (caller must skip these).
    """
    row = sysex_idx // 8
    col = sysex_idx % 8
    return 10 * (8 - row) + (col + 1)

# Format A parser  (F0 00 20 29 02 0E 05 …)
def _parse_format_a(msg: bytes) -> CustomMode | None:
    if len(msg) < 12 or msg[-1] != 0xF7:
        return None
    if msg[:5] != bytes((0xF0, 0x00, 0x20, 0x29, 0x02)):
        return None
    if msg[5] not in (0x0C, 0x0D, 0x0E) or msg[6] != 0x05:
        return None
    version = msg[7] & 0x7F
    slot    = msg[8] & 0x7F
    try:
        name_end = msg.index(0x00, 9)
    except ValueError:
        return None
    name    = msg[9:name_end].decode("ascii", errors="replace")
    payload = msg[name_end + 1:-1]  # strip F7
    i = 0
    if version > 0:
        try:
            gargoyle = payload.index(0x7F)
        except ValueError:
            gargoyle = len(payload)
        i = gargoyle + 1
    if i + 3 > len(payload):
        return None
    on_color = payload[i + 2] & 0x7F
    i += 3
    pads: dict[int, CustomPad] = {}
    while i + 8 <= len(payload):
        chunk         = payload[i:i + 8]
        pad_index     = chunk[0] & 0x7F
        midi_channel  = chunk[1] & 0x7F
        msg_type      = chunk[2] & 0x7F
        control_value = chunk[3] & 0x7F
        behaviour     = chunk[4] & 0x7F
        on_value      = chunk[5] & 0x7F
        off_value     = chunk[6] & 0x7F
        off_color     = chunk[7] & 0x7F
        if msg_type != MSG_OFF and _is_valid_pad_index(pad_index):
            pads[pad_index] = CustomPad(
                pad_index, midi_channel, msg_type,
                control_value, behaviour, on_value, off_value, off_color,
            )
        i += 8
    return CustomMode(name=name, slot=slot, on_color=on_color, pads=pads)

# Format B parser  (F0 00 20 29 <dev> <dev> 20 00 …)

# Matches both LPX (02 0C) and Mini MK3 (02 0D) product IDs
def _is_format_b(msg: bytes) -> bool:
    if len(msg) < 12:
        return False
    if msg[0] != 0xF0 or msg[1] != 0x00 or msg[2] != 0x20 or msg[3] != 0x29:
        return False
    if msg[4] != 0x02:
        return False
    if msg[5] not in (0x0C, 0x0D, 0x0E):  # LPX, MiniMK3, ProMK3
        return False
    if msg[6] != 0x20 or msg[7] != 0x00:   # container-format command bytes
        return False
    return msg[-1] == 0xF7
def _parse_format_b(msg: bytes) -> CustomMode | None:
    if not _is_format_b(msg):
        return None
    # byte[8]=palette/modeType, byte[9]=0x40=readCommand, byte[10]=slot
    slot = msg[10] & 0x7F
    containers = msg[11:-1]
    i = 0
    name     = ""
    on_color = 0
    pads: dict[int, CustomPad]   = {}
    faders: list[CustomFader]    = []
    while i < len(containers):
        ctype = containers[i]
        # Single-byte value containers
        if ctype == 0:   # onColor
            on_color = containers[i + 1] & 0x7F
            i += 2; continue
        if ctype in (1, 2, 4, 5, 6, 7, 8):
            i += 2; continue
        # Name container (type 32 = 0x20)
        if ctype == 32:
            length = containers[i + 1]
            name = containers[i + 2:i + 2 + length].decode("ascii", errors="replace")
            i += 2 + length; continue
        # Metadata container (type 33 = 0x21) – ignored
        if ctype == 33:
            length = containers[i + 1]
            i += 2 + length; continue
        # Control containers: top 2 bits = 01 (i.e. 64 == 96 & ctype)
        if 64 == (96 & ctype):
            length    = ctype & 0x1F
            ctrl_idx  = containers[i + 1]
            data      = containers[i + 2:i + 2 + length]
            i += 2 + length
            if length == 0:
                continue
            if ctrl_idx >= 64:
                # Fader strip: ctrl_idx 64–71, fader_index = ctrl_idx & 7
                # Byte layout as stored by Components:
                #              [sysexControlType, stripColor, idleColor,
                #               channelAndBehaviour, flags, sensitivity,
                #               controlValue(CC), onValue(max)]
                if length < 8:
                    continue
                # sysexControlType encodes orientation+polarity:
                #   0=V-unipolar, 1=V-bipolar, 2=H-unipolar, 3=H-bipolar
                ctrl_type   = data[0] & 0x7F
                fader_index = ctrl_idx & 0x07
                on_color_f  = data[1] & 0x7F
                off_color   = data[2] & 0x7F
                ch_beh      = data[3] & 0x7F
                flags       = data[4] & 0x7F
                cc_number   = data[6] & 0x7F
                on_value    = data[7] & 0x7F
                use_dev_ch  = bool(flags & 0x40)
                bipolar     = bool(ctrl_type & 0x01)
                horizontal  = bool(ctrl_type & 0x02)
                midi_ch     = 0 if use_dev_ch else (ch_beh & 0x0F)
                faders.append(CustomFader(
                    fader_index, midi_ch, cc_number,
                    min_value=0, max_value=on_value,
                    bipolar=bipolar, horizontal=horizontal,
                    off_color=off_color, on_color=on_color_f,
                    use_device_channel=use_dev_ch,
                ))
                continue
            # Grid pad: ctrl_idx 0–63
            # Byte layout: [channelAndBehaviour, offColor, onColor, ?,
            #               ?, ?, ?, note/controlValue]
            if length < 8:
                continue
            channel_and_beh = data[0] & 0x7F
            off_color       = data[1] & 0x7F
            midi_channel    = channel_and_beh & 0x0F
            behaviour       = (channel_and_beh >> 4) & 0x0F
            note            = data[7] & 0x7F
            pad_index = _container_idx_to_lp(ctrl_idx)
            if not _is_valid_pad_index(pad_index):
                continue
            pads[pad_index] = CustomPad(
                pad_index, midi_channel, MSG_NOTE,
                note, behaviour,
                on_value=0x7F, off_value=0, off_color=off_color,
            )
            continue
        # Unknown container — stop parsing to avoid misalignment
        break
    if not name and not pads and not faders:
        return None
    return CustomMode(name=name, slot=slot, on_color=on_color, pads=pads, faders=faders)

# Top-level loader
def parse_syx_bytes(data: bytes) -> list[CustomMode]:
    """
    Load all custom modes from a SysEx byte payload (format A or B).
    Returns a list ordered by appearance in the file.
    Silently skips malformed or unrecognised messages.
    """
    modes: list[CustomMode] = []
    start = 0
    while start < len(data):
        f0 = data.find(0xF0, start)
        if f0 < 0:
            break
        f7 = data.find(0xF7, f0)
        if f7 < 0:
            break
        msg = data[f0:f7 + 1]
        mode = _parse_format_a(msg)
        if mode is None:
            mode = _parse_format_b(msg)
        if mode is not None:
            modes.append(mode)
        start = f7 + 1
    return modes
def load_syx(path: Path | str) -> list[CustomMode]:
    """
    Load all custom modes from a .syx file (format A or B).
    Returns a list ordered by appearance in the file.
    Silently skips malformed or unrecognised messages.
    """
    return parse_syx_bytes(Path(path).read_bytes())

# Convenience: load from a script folder
_CUSTOM_MODES_SUBDIR = "custom modes"
_CUSTOM_MODES_LIVE_SUBDIR = "custom modes live"
_MAX_LIVE_SLOTS = 8
_MAX_TOTAL_SLOTS = 16
def _load_first_modes_from_folder(folder: Path, limit: int | None = None) -> list[CustomMode]:
    """
    Load first parsed mode from each .syx in a folder, alphabetically.
    """
    if not folder.is_dir():
        return []
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".syx")
    modes: list[CustomMode] = []
    for path in files:
        if limit is not None and len(modes) >= limit:
            break
        try:
            file_modes = load_syx(path)
            if file_modes:
                modes.append(file_modes[0])
        except Exception:
            pass
    return modes
def load_from_script_dir(script_dir: Path) -> list[CustomMode]:
    """
    Load up to 16 custom modes from ``<script_dir>/custom modes/``.
    Files are sorted alphabetically; the first mode parsed from each file
    is taken (one mode per file). Returns an empty list if the folder
    does not exist or no valid .syx files are found.
    """
    return _load_first_modes_from_folder(script_dir / _CUSTOM_MODES_SUBDIR, _MAX_TOTAL_SLOTS)

def live_folder(script_dir: Path) -> Path:
    return script_dir / _CUSTOM_MODES_LIVE_SUBDIR
def reset_live_folder(script_dir: Path) -> Path:
    """
    Begin a fresh live custom-mode session.
    FL Studio's embedded Python can fail on audited directory operations, so the
    runtime reset is logical: callers ignore old live files until a slot replies
    during this script init. The folder should already ship with the script.
    """
    return live_folder(script_dir)

def write_live_slot(script_dir: Path, slot: int, sysex: bytes) -> Path:
    """Persist one on-device custom-mode reply into the live folder."""
    if not 0 <= int(slot) < _MAX_LIVE_SLOTS:
        raise ValueError(f"custom mode slot out of range: {slot}")
    folder = live_folder(script_dir)
    path = folder / f"slot_{int(slot) + 1:02d}.syx"
    with open(str(path), "wb") as handle:
        handle.write(bytes(sysex))
    return path
def load_live_then_static(script_dir: Path, live_slots: set[int] | None = None) -> list[CustomMode]:
    """
    Load live custom modes first, then fill missing slots from static exports.
    Device modes are compacted to the top of the selector in live-slot order.
    If ``live_slots`` is supplied, only those slot files are considered live
    for this session. Static modes are read alphabetically from ``custom modes``
    and assigned after the live modes, up to the 16-slot script limit.
    """
    live_modes: list[CustomMode] = []
    live = live_folder(script_dir)
    if live_slots is None:
        for mode in _load_first_modes_from_folder(live):
            if len(live_modes) >= _MAX_LIVE_SLOTS:
                break
            live_modes.append(mode)
    else:
        for slot in sorted(live_slots):
            if not 0 <= int(slot) < _MAX_LIVE_SLOTS:
                continue
            try:
                file_modes = load_syx(live / f"slot_{int(slot) + 1:02d}.syx")
            except Exception:
                continue
            if file_modes:
                live_modes.append(file_modes[0])
            if len(live_modes) >= _MAX_LIVE_SLOTS:
                break
    modes_by_slot: dict[int, CustomMode] = {}
    for slot, mode in enumerate(live_modes):
        modes_by_slot[slot] = mode.with_slot(slot)
    next_slot = len(modes_by_slot)
    static_modes = _load_first_modes_from_folder(script_dir / _CUSTOM_MODES_SUBDIR, _MAX_TOTAL_SLOTS - next_slot)
    for offset, mode in enumerate(static_modes):
        slot = next_slot + offset
        modes_by_slot[slot] = mode.with_slot(slot)
    return [modes_by_slot[slot] for slot in range(_MAX_TOTAL_SLOTS) if slot in modes_by_slot]
# gargoyles rule