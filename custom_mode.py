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
import time
from pathlib import Path
from typing import Iterator
from fl_stubs import channels, device
import channel_lock as cl
import led_display
import modulators as ps
from modulators import PadFader
from constants import LP3_MENU_INACTIVE, PAD_DISABLED, LedColor
from device_profile import DEVICE_FAMILY_LPM3, DEVICE_FAMILY_LPX
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
            i += 2
            continue
        if ctype in (1, 2, 4, 5, 6, 7, 8):
            i += 2
            continue
        # Name container (type 32 = 0x20)
        if ctype == 32:
            length = containers[i + 1]
            name = containers[i + 2:i + 2 + length].decode("ascii", errors="replace")
            i += 2 + length
            continue
        # Metadata container (type 33 = 0x21) – ignored
        if ctype == 33:
            length = containers[i + 1]
            i += 2 + length
            continue
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

# Runtime helpers used by LaunchpadSurface.

LPX_CUSTOM_MODE_PRODUCT_ID = 0x0C
LPM3_CUSTOM_MODE_PRODUCT_ID = 0x0D
LPX_CUSTOM_MODE_SLOT_IDS = (4, 5, 6, 7, 8, 9, 10, 11)
LPM3_CUSTOM_MODE_LAYOUT_IDS = (4, 5, 6, 7, 8, 9, 10, 11)
CUSTOM_MODE_READ_TIMEOUT_SECONDS = 1.5
CUSTOM_FADER_MICRO_BRIGHTNESS = (0.25, 0.5, 0.75, 1.0)


def prepare_runtime(surface, log) -> None:
    if surface.device_family in (DEVICE_FAMILY_LPX, DEVICE_FAMILY_LPM3):
        live_ready = True
        try:
            reset_live_folder(surface.script_dir)
        except Exception as exc:
            live_ready = False
            log(f"custom modes live reset failed: {exc}")
        surface._live_custom_mode_slots.clear()
        if live_ready:
            start_live_read(surface, log)
        else:
            surface._live_custom_mode_reading = False
        reload_runtime(surface, live_first=True, log=log)
    else:
        surface._live_custom_mode_reading = False
        surface._live_custom_mode_slots.clear()
        reload_runtime(surface, live_first=False, log=log)


def reload_runtime(surface, *, live_first: bool, log) -> None:
    surface._custom_modes = (
        load_live_then_static(surface.script_dir, surface._live_custom_mode_slots)
        if live_first
        else load_from_script_dir(surface.script_dir)
    )
    surface._custom_fader_helpers = {
        (mode.slot, fader.fader_index): PadFader(
            fader.pads(),
            minimum=0.0,
            maximum=float(fader.max_value),
            bipolar=fader.bipolar,
            interpolate_seconds=0.0,
        )
        for mode in surface._custom_modes
        for fader in mode.faders
    }
    if surface._custom_modes:
        surface._custom_mode_index = max(0, min(surface._custom_mode_index, len(surface._custom_modes) - 1))
    else:
        surface._custom_mode_index = 0
    source = "live/static syx" if live_first else "static syx"
    log(f"loaded {len(surface._custom_modes)} custom mode(s) from {source}")


def product_id(device_family: str) -> int | None:
    if device_family == DEVICE_FAMILY_LPX:
        return LPX_CUSTOM_MODE_PRODUCT_ID
    if device_family == DEVICE_FAMILY_LPM3:
        return LPM3_CUSTOM_MODE_PRODUCT_ID
    return None


def slot_ids(device_family: str) -> tuple[int, ...]:
    if device_family == DEVICE_FAMILY_LPX:
        return LPX_CUSTOM_MODE_SLOT_IDS
    if device_family == DEVICE_FAMILY_LPM3:
        return LPM3_CUSTOM_MODE_LAYOUT_IDS
    return ()


def read_request(device_family: str, slot_id: int) -> bytes | None:
    pid = product_id(device_family)
    if pid is None:
        return None
    if device_family == DEVICE_FAMILY_LPM3:
        return bytes((0xF0, 0x00, 0x20, 0x29, 0x02, pid, 0x05, 0x01, slot_id, 0xF7))
    return bytes((
        0xF0, 0x00, 0x20, 0x29, 0x02, pid,
        0x20, 0x00, 0x40, 0x40, slot_id, 0xF7,
    ))


def start_live_read(surface, log) -> None:
    if product_id(surface.device_family) is None:
        return
    ids = slot_ids(surface.device_family)
    if not ids:
        return
    surface._live_custom_mode_reading = True
    surface._live_custom_mode_deadline = time.monotonic() + CUSTOM_MODE_READ_TIMEOUT_SECONDS
    for slot_id in ids:
        request = read_request(surface.device_family, slot_id)
        if request is None:
            continue
        try:
            device.midiOutSysex(request)
        except Exception as exc:
            log(f"custom mode slot id {slot_id} read request failed: {exc}")
    log(f"requested {len(ids)} on-device custom mode slot id(s) from {surface.device_label}")


def complete_live_read_if_due(surface, log) -> None:
    if not surface._live_custom_mode_reading or time.monotonic() < surface._live_custom_mode_deadline:
        return
    surface._live_custom_mode_reading = False
    log(
        "custom modes live read complete: "
        f"{len(surface._live_custom_mode_slots)} device slot(s), {len(surface._custom_modes)} total loaded"
    )
    if surface._pending_custom_mode_index is not None and surface._custom_modes:
        surface._custom_mode_index = max(0, min(len(surface._custom_modes) - 1, surface._pending_custom_mode_index))
        surface._pending_custom_mode_index = None
        surface._refresh_needed = True


def handle_sysex(surface, event, log) -> None:
    sysex = event_sysex_bytes(event)
    reply = reply_slot(surface.device_family, sysex)
    if reply is None:
        return
    slot, slot_id = reply
    parsed_modes = parse_syx_bytes(sysex)
    mode = parsed_modes[0] if parsed_modes else None
    if mode is None or (len(mode) == 0 and not mode.faders):
        log(f"custom mode slot id {slot_id} reply was empty; keeping fallback slot")
        return
    try:
        write_live_slot(surface.script_dir, slot, sysex)
    except Exception as exc:
        log(f"custom mode slot id {slot_id} live write failed: {exc}")
        return
    if slot not in surface._live_custom_mode_slots:
        surface._live_custom_mode_slots.add(slot)
        log(
            f"loaded live custom mode slot {slot + 1} "
            f"(id {slot_id}): {mode.name or '(unnamed)'}"
        )
    reload_runtime(surface, live_first=True, log=log)
    surface._refresh_needed = True


def event_sysex_bytes(event) -> bytes:
    try:
        return bytes(int(value) & 0xFF for value in event.sysex)
    except Exception:
        return b""


def reply_slot(device_family: str, sysex: bytes) -> tuple[int, int] | None:
    pid = product_id(device_family)
    ids = slot_ids(device_family)
    if pid is None or not ids or len(sysex) < 12:
        return None
    if sysex[0] != 0xF0 or sysex[-1] != 0xF7:
        return None
    if device_family == DEVICE_FAMILY_LPM3:
        if sysex[:8] != bytes((0xF0, 0x00, 0x20, 0x29, 0x02, pid, 0x05, 0x01)):
            return None
        slot_id = sysex[8] & 0x7F
    else:
        if sysex[1:8] != bytes((0x00, 0x20, 0x29, 0x02, pid, 0x20, 0x00)):
            return None
        if sysex[9] != 0x40:
            return None
        slot_id = sysex[10] & 0x7F
    try:
        slot = ids.index(slot_id)
    except ValueError:
        return None
    return slot, slot_id


def index_for_selector_slot(surface, slot: int) -> int | None:
    n_slots = len(ps.SELECTOR_PADS)
    if not 0 <= slot < n_slots:
        return None
    upper_slot = slot + n_slots
    if (
        surface._custom_mode_index % n_slots == slot
        and upper_slot < len(surface._custom_modes)
    ):
        if surface._custom_mode_index == slot:
            return upper_slot
        return slot
    if slot < len(surface._custom_modes):
        return slot
    return None


def handle_pad(surface, event, pad: int, velocity: int, pressed: bool) -> bool:
    slot = ps.pad_to_slot(pad)
    if slot is not None:
        if pressed:
            mode_index = index_for_selector_slot(surface, slot)
            if mode_index is not None:
                surface._custom_mode_index = mode_index
                surface._custom_mode_selecting = True
                surface._refresh_surface()
                surface._save_state()
        return True
    mode = surface._active_custom_mode()
    if mode is None:
        return True
    fader = mode.fader_for_pad(pad)
    if fader is not None and pressed:
        return handle_fader_pad(surface, event, pad, fader, mode.slot)
    cp = mode.pad(pad)
    if cp is None or cp.is_off:
        return True
    # Explicit routes take precedence over the normal custom-mode MIDI event
    # path even when the page has not been channel-locked.  A routing set is
    # deliberately independent of a lock: it is how one custom page fans out
    # to several channels.
    if cp.is_note and (
        cl.is_locked(surface.state, surface._lock_context())
        or cl.has_routes(surface.state, surface._lock_context())
    ):
        return handle_routed_note(surface, pad, cp, velocity, pressed)
    channel = cp.resolved_channel(int(surface.state.get("midi_channel", 0))) & 0x0F
    if cp.is_note:
        if pressed:
            vel = max(1, velocity or cp.on_value or 100)
            event.status = 0x90 | channel
            event.data1 = cp.control_value
            event.data2 = vel
            surface.active_pads[pad] = (channel, cp.control_value)
        else:
            info = surface.active_pads.pop(pad, None)
            ch, note = info if info is not None else (channel, cp.control_value)
            event.status = 0x80 | ch
            event.data1 = note
            event.data2 = 0
    elif cp.is_cc:
        val = cp.on_value if pressed else cp.off_value
        event.status = 0xB0 | channel
        event.data1 = cp.control_value
        event.data2 = val
    else:
        led_display.refresh_grid_pad(pad, surface._grid_lighting, surface._grid_led_cache)
        return True
    led_display.refresh_grid_pad(pad, surface._grid_lighting, surface._grid_led_cache)
    return False


def handle_routed_note(surface, pad: int, cp, velocity: int, pressed: bool) -> bool:
    midi_channel = int(surface.state["midi_channel"]) & 0x0F
    note = cp.control_value
    if pressed:
        vel = max(1, velocity or cp.on_value or 100)
        # Fan out to the slot's routing set when one is configured; otherwise
        # this collapses to the single locked channel it always used.
        targets = surface._route_targets(cl.get(surface.state, surface._lock_context()))
        if not targets:
            targets = [cl.get(surface.state, surface._lock_context())]
        surface.active_pads[pad] = (targets[0], note)
        surface._pad_routed_notes[pad] = [(target, note) for target in targets]
        for target in targets:
            key = (target, note)
            surface.active_notes[key] = surface.active_notes.get(key, 0) + 1
            channels.midiNoteOn(target, note, vel, midi_channel)
    else:
        info = surface.active_pads.pop(pad, None)
        routed = surface._pad_routed_notes.pop(pad, None)
        if routed:
            for target, routed_note in routed:
                surface._drop_active_note(target, routed_note)
                channels.midiNoteOn(target, routed_note, 0, midi_channel)
        elif info is not None:
            target, note = info
            surface._drop_active_note(target, note)
            channels.midiNoteOn(target, note, 0, midi_channel)
    led_display.refresh_grid_pad(pad, surface._grid_lighting, surface._grid_led_cache)
    surface._refresh_needed = True
    return True


def handle_fader_pad(surface, event, pad: int, fader: CustomFader, slot: int) -> bool:
    key = (slot, fader.fader_index)
    pf = surface._custom_fader_helpers.get(key)
    if pf is None:
        return True
    current = surface._custom_fader_values.get(key, 0.0)
    new_value = pf.next_value_for_pad(pad, current)
    surface._custom_fader_values[key] = new_value
    cc_val = max(0, min(127, int(round(new_value))))
    channel = fader.resolved_channel(int(surface.state.get("midi_channel", 0))) & 0x0F
    event.status = 0xB0 | channel
    event.data1 = fader.cc_number
    event.data2 = cc_val
    surface._refresh_grid_pads(fader.pads())
    return False


def slot_display_index(surface, slot: int) -> int | None:
    n_slots = len(ps.SELECTOR_PADS)
    if not (0 <= slot < len(surface._custom_modes)):
        return None
    if surface._custom_mode_index % n_slots == slot:
        return surface._custom_mode_index
    return slot


def locked_selector_pads(surface) -> set[int]:
    pads: set[int] = set()
    for slot, pad in enumerate(ps.SELECTOR_PADS):
        display_index = slot_display_index(surface, slot)
        if display_index is None:
            continue
        idx = min(display_index, len(surface._custom_modes) - 1)
        if cl.is_locked(surface.state, cl.custom_context(idx)):
            pads.add(pad)
    return pads


def lighting(surface, pad: int) -> LedColor:
    slot = ps.pad_to_slot(pad)
    if slot is not None:
        display_index = slot_display_index(surface, slot)
        if display_index is None:
            return LedColor(PAD_DISABLED)
        n_slots = len(ps.SELECTOR_PADS)
        mode = surface._custom_modes[min(display_index, len(surface._custom_modes) - 1)]
        active = surface._custom_mode_index % n_slots == slot
        color = mode.on_color if active else LP3_MENU_INACTIVE
        return LedColor(color)
    mode = surface._active_custom_mode()
    if mode is None:
        return LedColor(PAD_DISABLED)
    fader = mode.fader_for_pad(pad)
    if fader is not None:
        key = (mode.slot, fader.fader_index)
        pf = surface._custom_fader_helpers.get(key)
        if pf is None:
            return LedColor(PAD_DISABLED)
        current = surface._custom_fader_values.get(key, 0.0)
        return surface._fader_pad_lighting(pf, fader.on_color, fader.off_color, pad, current)
    cp = mode.pad(pad)
    if cp is None or cp.is_off:
        return LedColor(PAD_DISABLED)
    if pad in surface.active_pads:
        return LedColor(mode.on_color)
    return LedColor(cp.off_color)
# ~gargoyles rule~
