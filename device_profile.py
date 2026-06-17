from __future__ import annotations

from fl_stubs import device, midi
import led_display
from constants import (
    FPC_COLOR_GAMMA_LP3,
    FPC_COLOR_GAMMA_MK1,
    FPC_COLOR_GAMMA_MK2,
    FPC_COLOR_SATURATION_LP3,
    FPC_COLOR_SATURATION_MK1,
    FPC_COLOR_SATURATION_MK2,
    LAYOUT_SESSION,
    MK1_DUTY_CYCLE_DENOMINATOR,
    MK1_DUTY_CYCLE_NUMERATOR,
    SESSION_CHANNEL,
    SYSEX_PREFIX,
    TOP_CCS,
    TOP_FPC_MODE,
    TOP_NOTE_MODE,
    TOP_OCTAVE_DOWN,
    TOP_OCTAVE_UP,
    TOP_PAN_LEFT,
    TOP_PAN_RIGHT,
    TOP_PERFORMANCE,
    TOP_RECORD_ARM,
)

DEVICE_FAMILY_MK1 = "mk1"
DEVICE_FAMILY_MK2 = "mk2"
DEVICE_FAMILY_LPX = "lpx"
DEVICE_FAMILY_LPM3 = "lpm3"

LP3_PROGRAMMER_MODE = 0x01
LP3_LIVE_MODE = 0x00

MK1_DEVICE_ID_PREFIX: bytes | None = None
MINI_MK1_DEVICE_ID_PREFIX: bytes | None = None
MINI_MK2_DEVICE_ID_PREFIX: bytes | None = None
LP_S_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x20, 0x00))
MK2_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x69))
LPX_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x03, 0x01))
LPM3_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x13, 0x01))

LP3_TOP_UP = 91
LP3_TOP_DOWN = 92
LP3_TOP_LEFT = 93
LP3_TOP_RIGHT = 94
LP3_TOP_SESSION = 95
LP3_TOP_NOTE = 96
LP3_TOP_CUSTOM = 97
LP3_TOP_RECORD = 98
LP3_TOP_CCS = (
    LP3_TOP_UP,
    LP3_TOP_DOWN,
    LP3_TOP_LEFT,
    LP3_TOP_RIGHT,
    LP3_TOP_SESSION,
    LP3_TOP_NOTE,
    LP3_TOP_CUSTOM,
    LP3_TOP_RECORD,
)

_MK1_GEN_NAME_SUBSTRINGS = (
    "launchpad mini mk2",
    "launchpad mini",
    "novation launchpad mini",
)
_MK1_GEN_EXACT_NAMES = (
    "launchpad",
    "novation launchpad",
    "launchpad s",
)


def format_device_id(device_id) -> str:
    if device_id is None:
        return "<none>"
    if isinstance(device_id, (bytes, bytearray)):
        if not device_id:
            return "<empty>"
        return " ".join(f"{byte:02X}" for byte in device_id)
    return str(device_id)


def normalize_device_id(device_id) -> bytes:
    if isinstance(device_id, (bytes, bytearray)):
        return bytes(device_id)
    return b""


def detect_device_family(device_id: bytes, device_name: str = "") -> str:
    if device_id.startswith(LPX_DEVICE_ID_PREFIX):
        return DEVICE_FAMILY_LPX
    if device_id.startswith(LPM3_DEVICE_ID_PREFIX):
        return DEVICE_FAMILY_LPM3
    if device_id.startswith(LP_S_DEVICE_ID_PREFIX):
        return DEVICE_FAMILY_MK1
    for prefix in (MK1_DEVICE_ID_PREFIX, MINI_MK1_DEVICE_ID_PREFIX, MINI_MK2_DEVICE_ID_PREFIX):
        if prefix is not None and device_id.startswith(prefix):
            return DEVICE_FAMILY_MK1
    if not device_id:
        name_lower = device_name.strip().lower()
        if name_lower in _MK1_GEN_EXACT_NAMES:
            return DEVICE_FAMILY_MK1
        if any(name_lower.startswith(sub) for sub in _MK1_GEN_NAME_SUBSTRINGS):
            return DEVICE_FAMILY_MK1
    return DEVICE_FAMILY_MK2


def mk1_label(device_id: bytes, device_name: str) -> str:
    if device_id.startswith(LP_S_DEVICE_ID_PREFIX):
        return "Launchpad S"
    if MINI_MK2_DEVICE_ID_PREFIX is not None and device_id.startswith(MINI_MK2_DEVICE_ID_PREFIX):
        return "Launchpad Mini MK2"
    if MINI_MK1_DEVICE_ID_PREFIX is not None and device_id.startswith(MINI_MK1_DEVICE_ID_PREFIX):
        return "Launchpad Mini MK1"
    name_lower = device_name.strip().lower()
    if "mini" in name_lower:
        return "Launchpad Mini MK1/MK2"
    if name_lower in ("launchpad s", "novation launchpad s"):
        return "Launchpad S"
    return "Launchpad MK1"


def log_device_identification(log) -> None:
    name = "<unknown>"
    hardware_id = "<unknown>"
    try:
        name = device.getName()
    except Exception as exc:
        name = f"<error: {exc}>"
    try:
        hardware_id = format_device_id(device.getDeviceID())
    except Exception as exc:
        hardware_id = f"<error: {exc}>"
    log(f"FL device name: {name}")
    log(f"FL hardware id: {hardware_id}")
    log("If hardware id is non-empty and the device is unrecognised, copy it into supportedHardwareIds and the appropriate DEVICE_ID_PREFIX constant.")


def send_mk1_duty_cycle() -> None:
    numerator = MK1_DUTY_CYCLE_NUMERATOR
    denominator = MK1_DUTY_CYCLE_DENOMINATOR
    if numerator < 9:
        cc, value = 0x1E, 16 * (numerator - 1) + (denominator - 3)
    else:
        cc, value = 0x1F, 16 * (numerator - 9) + (denominator - 3)
    device.midiOutMsg(midi.MIDI_CONTROLCHANGE, SESSION_CHANNEL, cc, value)


def configure_surface_profile(surface, log) -> None:
    try:
        surface.device_name = str(device.getName())
    except Exception:
        surface.device_name = "Unknown Launchpad"
    try:
        surface.device_id = normalize_device_id(device.getDeviceID())
    except Exception:
        surface.device_id = b""
    surface.device_family = detect_device_family(surface.device_id, surface.device_name)

    surface._side_column_is_cc = False
    surface._top_ccs = TOP_CCS
    surface._top_octave_down = TOP_OCTAVE_DOWN
    surface._top_octave_up = TOP_OCTAVE_UP
    surface._top_pan_left = TOP_PAN_LEFT
    surface._top_pan_right = TOP_PAN_RIGHT
    surface._top_performance = TOP_PERFORMANCE
    surface._top_note_mode = TOP_NOTE_MODE
    surface._top_fpc_mode = TOP_FPC_MODE
    surface._top_record_arm = TOP_RECORD_ARM

    if surface.device_family == DEVICE_FAMILY_LPX:
        surface.device_label = "Launchpad X"
        _configure_lp3(surface, product_id=0x0C)
    elif surface.device_family == DEVICE_FAMILY_LPM3:
        surface.device_label = "Launchpad Mini MK3"
        _configure_lp3(surface, product_id=0x0D)
    elif surface.device_family == DEVICE_FAMILY_MK1:
        surface.device_label = mk1_label(surface.device_id, surface.device_name)
        led_display.configure_surface(
            sysex_prefix=SYSEX_PREFIX,
            top_ccs=surface._top_ccs,
            side_column_is_cc=False,
            mode="mk1",
            mk1_double_buffer=surface.device_label == "Launchpad S",
            color_saturation=FPC_COLOR_SATURATION_MK1,
            color_gamma=FPC_COLOR_GAMMA_MK1,
        )
        send_mk1_duty_cycle()
    else:
        surface.device_label = "Launchpad MK2"
        led_display.configure_surface(
            sysex_prefix=SYSEX_PREFIX,
            top_ccs=surface._top_ccs,
            side_column_is_cc=False,
            mode="mk2",
            color_saturation=FPC_COLOR_SATURATION_MK2,
            color_gamma=FPC_COLOR_GAMMA_MK2,
        )
    log(f"device profile: {surface.device_label} top_ccs={surface._top_ccs} side_column_is_cc={surface._side_column_is_cc}")


def _configure_lp3(surface, *, product_id: int) -> None:
    surface._side_column_is_cc = True
    surface._top_ccs = LP3_TOP_CCS
    surface._top_performance = LP3_TOP_SESSION
    surface._top_note_mode = LP3_TOP_NOTE
    surface._top_fpc_mode = LP3_TOP_CUSTOM
    surface._top_octave_down = LP3_TOP_DOWN
    surface._top_octave_up = LP3_TOP_UP
    surface._top_pan_left = LP3_TOP_LEFT
    surface._top_pan_right = LP3_TOP_RIGHT
    surface._top_record_arm = LP3_TOP_RECORD
    led_display.configure_surface(
        sysex_prefix=(0xF0, 0x00, 0x20, 0x29, 0x02, product_id),
        top_ccs=surface._top_ccs,
        side_column_is_cc=True,
        mode="lp3",
        color_saturation=FPC_COLOR_SATURATION_LP3,
        color_gamma=FPC_COLOR_GAMMA_LP3,
    )
# ~gargoyles rule~