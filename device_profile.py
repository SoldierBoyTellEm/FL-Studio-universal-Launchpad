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
DEVICE_FAMILY_LPP = "lpp"
DEVICE_FAMILY_LPP3 = "lpp3"

# Families that speak the MK3-generation protocol: 0Eh programmer/live toggle,
# 03h LED command with typed colourspecs, 0-127 RGB, the 128-entry LP3 palette,
# CC 91-98 top row, and a CC side column. They differ only in SysEx product ID.
MK3_PROTOCOL_FAMILIES = (DEVICE_FAMILY_LPX, DEVICE_FAMILY_LPM3, DEVICE_FAMILY_LPP3)
# Families needing an explicit programmer-mode handshake on init, and a matching
# restore-to-live on deinit. The original Pro joins the MK3 families here even
# though its handshake is a different (two-message) dialect.
PROGRAMMER_MODE_FAMILIES = MK3_PROTOCOL_FAMILIES + (DEVICE_FAMILY_LPP,)
# Families with no hardware per-pad pulse, so lock indicators must be animated
# frame-by-frame from on_idle instead.
SOFTWARE_PULSE_FAMILIES = (DEVICE_FAMILY_MK2, DEVICE_FAMILY_MK1, DEVICE_FAMILY_LPP)

LP3_PROGRAMMER_MODE = 0x01
LP3_LIVE_MODE = 0x00

MK1_DEVICE_ID_PREFIX: bytes | None = None
MINI_MK1_DEVICE_ID_PREFIX: bytes | None = None
MINI_MK2_DEVICE_ID_PREFIX: bytes | None = None
LP_S_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x20, 0x00))
MK2_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x69))
LPX_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x03, 0x01))
LPM3_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x13, 0x01))
# Original Launchpad Pro. Its Device Inquiry reply carries device code 51h
# (see the Programmer's Reference Guide, "Device Inquiry"), giving hardware
# ID 00 20 29 51 — distinct from the Pro Mk3's own hardware ID, which this
# script does not target.
LPP_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x51))
# SysEx prefix for the original Launchpad Pro (product ID 10h), vs MK2's 18h.
LPP_SYSEX_PREFIX = (0xF0, 0x00, 0x20, 0x29, 0x02, 0x10)
# Launchpad Pro MK3. UNVERIFIED — no hardware on hand to confirm.
#
# The LPP3 guide's "Device Inquiry message" section prints the reply as
# 00 20 29 13 01, but that is verbatim Mini MK3's ID (LPM3_DEVICE_ID_PREFIX
# above) — the section is a copy-paste leftover from the Mini MK3 manual, and
# trusting it would make every Pro MK3 detect as a Mini MK3. The value below
# instead follows the MK3 family's established pattern, where the model byte
# tracks the SysEx product ID: X = 03/0Ch, Mini = 13/0Dh, Pro = 23/0Eh.
#
# Name matching (_LPP3_NAME_MATCHES) is the reliable path here; this prefix is
# a best guess. If a Pro MK3 logs a different hardware id on init, correct this
# constant and the supportedHardwareIds line in the main script.
LPP3_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x23, 0x01))
# SysEx product ID for the Pro MK3, alongside LPX's 0Ch and Mini MK3's 0Dh.
LPP3_SYSEX_PRODUCT_ID = 0x0E

# Pro devices (original + MK3) also have a physical bottom row of round
# buttons at CC 1-8 (labelled Record Arm/Track Select/Mute/Solo/Volume/Pan/
# Sends/Stop Clip on both the original Pro's Programmer layout and the Pro
# MK3's split bottom sidebar's lower half). Repurposed here as two extra
# direct-entry mode buttons; the rest are left unmapped for now.
PRO_BOTTOM_STEP_SEQ_CC = 2
PRO_BOTTOM_MODULATORS_CC = 6
PRO_BOTTOM_ROUTING_CC = 7
PRO_BOTTOM_MIXER_CC = 5
PRO_BOTTOM_MIXER_ARM_CC = 1
PRO_MK3_VIEW_SHORTCUT_CCS = tuple(range(101, 109))
# The original Pro has no 101-108 row. CC 10 mirrors the first shortcut slot
# so the feature can be exercised on its left-side control column.
LPP_VIEW_SHORTCUT_TEST_CC = 10

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
# Exact match only — "launchpad pro mk3" must NOT match here; it is a separate
# device with its own profile (see _LPP3_NAME_MATCHES), and is tested first.
_LPP_EXACT_NAMES = (
    "launchpad pro",
    "novation launchpad pro",
)
# Launchpad Pro MK3. Windows names its three interfaces "LPProMK3 MIDI",
# "MIDIIN2 (LPProMK3 MIDI)" and "MIDIIN3 (LPProMK3 MIDI)"; all three are
# matched so a user on the wrong one still gets identified (and warned) rather
# than silently falling through to the MK2 profile.
_LPP3_NAME_MATCHES = (
    "lppromk3 midi",
    "lppromk3",
    "launchpad pro mk3",
    "novation launchpad pro mk3",
    "launchpad pro [mk3]",
)


def strip_port_wrapper(name: str) -> str:
    """Strip the OS's multi-port wrapper from a MIDI port name.

    The Launchpad Pro exposes three port pairs, and Windows names the 2nd and
    3rd like "MIDIIN2 (Launchpad Pro)" / "MIDIOUT2 (Launchpad Pro)" rather
    than repeating the bare device name. Programmer layout lives on the 2nd
    port pair (PRM, "Basic Communication"), so the port we actually want is
    the one whose name is wrapped — matching only the bare name would detect
    the device on the one port where Programmer layout does not work.
    """
    cleaned = name.strip()
    open_paren = cleaned.find("(")
    if open_paren > 0 and cleaned.endswith(")"):
        prefix = cleaned[:open_paren].strip().lower()
        if prefix.startswith("midiin") or prefix.startswith("midiout"):
            return cleaned[open_paren + 1 : -1].strip()
    return cleaned


def lpp_port_index(name: str) -> int | None:
    """Return the 1-based Launchpad Pro port-pair index encoded in *name*.

    "Launchpad Pro" -> 1, "MIDIIN2 (Launchpad Pro)" -> 2, and so on. Returns
    None when the name carries no usable index.
    """
    cleaned = name.strip()
    open_paren = cleaned.find("(")
    if open_paren <= 0 or not cleaned.endswith(")"):
        return 1 if cleaned else None
    prefix = cleaned[:open_paren].strip().lower()
    for tag in ("midiin", "midiout"):
        if prefix.startswith(tag):
            digits = prefix[len(tag):].strip()
            if digits.isdigit():
                return int(digits)
    return None


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
    if device_id.startswith(LPP3_DEVICE_ID_PREFIX):
        return DEVICE_FAMILY_LPP3
    if device_id.startswith(LPP_DEVICE_ID_PREFIX):
        return DEVICE_FAMILY_LPP
    if device_id.startswith(LP_S_DEVICE_ID_PREFIX):
        return DEVICE_FAMILY_MK1
    for prefix in (MK1_DEVICE_ID_PREFIX, MINI_MK1_DEVICE_ID_PREFIX, MINI_MK2_DEVICE_ID_PREFIX):
        if prefix is not None and device_id.startswith(prefix):
            return DEVICE_FAMILY_MK1
    # Pro MK3 is matched by name even when a hardware id IS present, because
    # LPP3_DEVICE_ID_PREFIX is an unverified guess (the vendor guide misprints
    # it as Mini MK3's id). If the guess is wrong, a non-empty id would
    # otherwise skip the name checks below and land on the MK2 fallback. The
    # "lppromk3" name is distinctive enough to carry no false-positive risk.
    if strip_port_wrapper(device_name).lower() in _LPP3_NAME_MATCHES:
        return DEVICE_FAMILY_LPP3
    if not device_id:
        name_lower = device_name.strip().lower()
        # Unwrap "MIDIIN2 (Launchpad Pro)" before matching — the Pro's
        # Programmer layout only talks on its 2nd port pair, which is exactly
        # the port whose name carries the wrapper.
        if strip_port_wrapper(device_name).lower() in _LPP_EXACT_NAMES:
            return DEVICE_FAMILY_LPP
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
    elif surface.device_family == DEVICE_FAMILY_LPP3:
        # Protocol-identical to LPX / Mini MK3 (same 0Eh programmer toggle, 03h
        # LED command, colourspec types, 0-127 RGB, palette, CC 91-98 top row
        # and CC side column) — only the product ID differs. Its extra controls
        # (left column CC 10-80, Shift CC 90, logo CC 99, and the split bottom
        # sidebar: CC 101-108 upper / CC 1-8 lower) are simply unused; none of
        # those numbers collide with the 11-88 grid or the 19-89 side column.
        surface.device_label = "Launchpad Pro MK3"
        _configure_lp3(surface, product_id=LPP3_SYSEX_PRODUCT_ID)
        _apply_pro_top_row(surface)
        _warn_if_wrong_lpp3_port(surface, log)
    elif surface.device_family == DEVICE_FAMILY_LPP:
        surface.device_label = "Launchpad Pro"
        _configure_lpp(surface)
        _apply_pro_top_row(surface)
        _warn_if_wrong_lpp_port(surface, log)
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


def _warn_if_wrong_lpp_port(surface, log) -> None:
    """Flag the classic Launchpad Pro mis-wiring: bound to port pair 1.

    Port pair 1 is the Ableton/Live port; the Standalone layouts (Note, Drum,
    Fader, Programmer) all live on port pair 2 (PRM, "Basic Communication").
    On port 1 the symptom is confusing rather than dead: LED SysEx is still
    honoured device-wide so pads light up, but the Programmer layout-select is
    ignored (so the stock Note-layout colours stay visible underneath) and pad
    presses are emitted on port 2, where this script never sees them.
    """
    port = lpp_port_index(surface.device_name)
    if port == 2:
        return
    if port is None:
        log("Launchpad Pro: could not tell which port pair this is; if pads don't respond, try the 2nd Launchpad Pro port.")
        return
    log(
        f"Launchpad Pro WARNING: bound to port pair {port} "
        f"(FL port name {surface.device_name!r})."
    )
    log(
        "Launchpad Pro WARNING: Programmer layout only works on port pair 2. "
        "In FL's MIDI settings enable and assign this script to the SECOND "
        "Launchpad Pro input/output (usually named 'MIDIIN2 (Launchpad Pro)' / "
        "'MIDIOUT2 (Launchpad Pro)'). On port 1 the pads will light but not respond."
    )


def _warn_if_wrong_lpp3_port(surface, log) -> None:
    """Flag a Pro MK3 bound to its DIN or DAW interface instead of MIDI.

    Note this is the *opposite* of the original Pro, which wants port pair 2.
    The Pro MK3's three interfaces are MIDI (1), DIN (2) and DAW (3), and per
    the LPP3 guide it is interface 1 that "light[s] LEDs in Custom Modes and
    Programmer Mode". Interface 3 speaks the Session/DAW protocol, which this
    script does not implement.
    """
    port = lpp_port_index(surface.device_name)
    if port in (1, None):
        return
    role = {2: "DIN (MIDI jack passthrough)", 3: "DAW (Session mode)"}.get(port, f"index {port}")
    log(
        f"Launchpad Pro MK3 WARNING: bound to interface {port} - the {role} port "
        f"(FL port name {surface.device_name!r})."
    )
    log(
        "Launchpad Pro MK3 WARNING: Programmer mode runs on the FIRST interface, "
        "usually named 'LPProMK3 MIDI'. Reassign this script there."
    )


def _apply_pro_top_row(surface) -> None:
    """Pro-specific control remap, layered on top of the stock LP3-style
    top row set up by _configure_lp3 / _configure_lpp.

    Session/Note/Device already land on performance/note/FPC via the shared
    LP3_TOP_* assignment, so only three things change: User (98) becomes a
    dedicated Custom Modes button instead of Record, Record itself has no
    button for now, and two bottom-row round buttons (CC 1-8, present on
    both Pro devices — see PRO_BOTTOM_STEP_SEQ_CC/PRO_BOTTOM_MODULATORS_CC)
    become direct launchers for Step Sequencer and the modulator/XY pages.
    Experimental: several existing modes (Record, Gross Beat toggle) are
    left unreachable here until they're given a permanent home elsewhere.
    """
    surface._top_custom_mode = LP3_TOP_RECORD
    surface._top_record_arm = None
    surface._top_step_seq = PRO_BOTTOM_STEP_SEQ_CC
    surface._top_modulators = PRO_BOTTOM_MODULATORS_CC
    surface._top_routing = PRO_BOTTOM_ROUTING_CC
    surface._top_mixer = PRO_BOTTOM_MIXER_CC
    surface._top_mixer_arm = PRO_BOTTOM_MIXER_ARM_CC
    if surface.device_family == DEVICE_FAMILY_LPP3:
        surface._view_shortcut_ccs = {
            cc: slot for slot, cc in enumerate(PRO_MK3_VIEW_SHORTCUT_CCS)
        }
    else:
        surface._view_shortcut_ccs = {LPP_VIEW_SHORTCUT_TEST_CC: 0}
    surface._top_ccs = surface._top_ccs + (
        PRO_BOTTOM_STEP_SEQ_CC,
        PRO_BOTTOM_MODULATORS_CC,
        PRO_BOTTOM_ROUTING_CC,
        PRO_BOTTOM_MIXER_CC,
        PRO_BOTTOM_MIXER_ARM_CC,
        *surface._view_shortcut_ccs,
    )
    led_display.set_top_ccs(surface._top_ccs)


def _configure_lpp(surface) -> None:
    """Original Launchpad Pro: MK2-style grid/side-column pad IDs (row*10+col,
    side column at col 9) and MK2-style LED SysEx (opcodes 0Ah/0Bh/0Eh/14h,
    6-bit RGB) under a different product-ID prefix (10h vs MK2's 18h). Its
    top-row round buttons (Up/Down/Left/Right/Session/Note/Device/User) use
    CCs 91-98, which happen to already be LP3_TOP_CCS — so the same
    arrow/session/note/custom/record mapping used for LPX/Mini MK3 lines up
    here too. See led_display "lpp" mode, and the two-message
    Standalone+Programmer layout switch in led_display.set_layout.

    Only the 64 square pads send notes: per the PRM's Programmer Layout
    section, *every* round button sends CC, and the right-hand column
    (19/29/.../89) is round on this hardware — unlike MK2, whose square
    scene-launch column sends Note On. So the side column is CC here, same as
    LPX/Mini MK3. Getting this wrong is invisible in the LEDs (the LED SysEx
    addresses every button by index regardless of its type) and only shows up
    as a side column that lights but never responds.
    """
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
        sysex_prefix=LPP_SYSEX_PREFIX,
        top_ccs=surface._top_ccs,
        side_column_is_cc=True,
        mode="lpp",
        color_saturation=FPC_COLOR_SATURATION_MK2,
        color_gamma=FPC_COLOR_GAMMA_MK2,
    )
# ~gargoyles rule~
