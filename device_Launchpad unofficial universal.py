# name=Novation Launchpad unofficial universal
# supportedDevices=Launchpad MK2,Launchpad [MK2],Novation Launchpad MK2,Launchpad X,Novation Launchpad X,Launchpad Mini MK3,Novation Launchpad Mini MK3,Launchpad S,Novation Launchpad S,Launchpad,Novation Launchpad,Launchpad Mini,Novation Launchpad Mini,Launchpad Mini MK2,Novation Launchpad Mini MK2
# supportedHardwareIds=00 20 29 69,00 20 29 03 01,00 20 29 13 01,00 20 29 20 00
# NOTE: Hardware IDs for the original Launchpad, Mini MK1, and Mini MK2 are not
#       yet confirmed — those devices do not respond to MIDI Device Inquiry, so FL
#       may report an empty hardware ID.  To fill in a slot:
#         1. Check the log output for "FL hardware id: ..." on init.
#         2. If non-empty, add it to supportedHardwareIds and set the matching
#            *_DEVICE_ID_PREFIX constant below.
#         3. If empty, device-name matching (supportedDevices) handles routing
#            and the PREFIX constant can stay None.
#       Launchpad S does respond to Device Inquiry; its device code is 20h 00h,
#       giving hardware ID 00 20 29 20 00 (already in supportedHardwareIds above).

# [Reference BS for AI below]
# Main entry point.  This file owns:
#   - LaunchpadSurface  (surface co-ordinator)
#   - The FL Studio callback functions (OnInit, OnMidiMsg, …)
#
# Feature-specific logic lives in the companion modules:
#   constants.py        – every magic number and lookup table
#   fl_stubs.py         – FL Studio API shim for offline editing
#   state_io.py         – JSON persistence
#   led_display.py      – sysex LED output + colour utilities
#   note_mode.py        – note/scale layout, settings screen, pan/octave
#   fpc_mode.py         – FPC drum-pad mode
#   performance_mode.py – performance/live-clip mode, launch map, API probe
from __future__ import annotations
import re
import time
import traceback
from pathlib import Path
from fl_stubs import channels, transport, midi, plugins, ui, device, mixer
import state_io
import led_display
import note_mode as nm
import fpc_mode  as fm
import performance_mode as pm
import step_sequencer as ss
import custom_mode as cm
import modulators as xp
import modulators as ps
import channel_lock as cl
from modulators import PadFader
from constants import (
    # layout
    LAYOUT_SESSION,
    LAYOUT_USER_2,
    # top-row CCs
    TOP_CCS,
    TOP_OCTAVE_DOWN, TOP_OCTAVE_UP,
    TOP_PAN_LEFT, TOP_PAN_RIGHT,
    TOP_PERFORMANCE, TOP_NOTE_MODE,
    TOP_FPC_MODE, TOP_RECORD_ARM,
    # pad groups
    SETTINGS_GRID_PADS,
    PLAYABLE_PADS,
    PERFORMANCE_PAGE_HOTKEY_PADS,
    GROSS_BEAT_SLOT_PADS,
    GROSS_BEAT_TOGGLE_PAD,
    GROSS_BEAT_FADER_PADS,
    GROSS_BEAT_TIME_COLOR,
    GROSS_BEAT_VOLUME_COLOR,
    GROSS_BEAT_FADER_DIM_COLOR,
    GROSS_BEAT_FADER_MICRO_COLORS,
    # modes
    MODE_NOTE, MODE_FPC, MODE_PERFORMANCE, MODE_CUSTOM,
    MODE_XY_PAD, MODE_STEP_SEQ, MODE_BLANK,
    # MIDI routing
    SESSION_CHANNEL,
    # timing
    TAP_AND_HOLD_DURATION_SECONDS,
    NOTE_DOUBLE_TAP_SECONDS,
    PERFORMANCE_DOUBLE_TAP_SECONDS,
    # note range
    LOWEST_NOTE, HIGHEST_NOTE,
    # plugin overrides
    PLUGIN_PAD_OVERRIDE_IDS,
    PLUGIN_PAD_OVERRIDE_GROSS_BEAT,
    # color constants needed for plugin-override lighting
    PAD_ACTION, PAD_DISABLED,
    FPC_COLOR_SATURATION_MK1,
    FPC_COLOR_GAMMA_MK1,
    MK1_DUTY_CYCLE_NUMERATOR,
    MK1_DUTY_CYCLE_DENOMINATOR,
    FPC_COLOR_SATURATION_MK2,
    FPC_COLOR_GAMMA_MK2,
    FPC_COLOR_SATURATION_LP3,
    FPC_COLOR_GAMMA_LP3,
    LP3_BACKGROUND_OFF,
    LP3_MENU_ACTIVE,
    LP3_MENU_INACTIVE,
    LP3_MENU_LOCKED,
    LP3_PERFORMANCE_READY,
    LP3_PERFORMANCE_HYBRID,
    LP3_ARROW_OCTAVE_ACTIVE,
    LP3_ARROW_PAN_ACTIVE,
    LP3_ARROW_INACTIVE,
    SYSEX_PREFIX,
    STATE_FILE,
    DEFAULT_STATE,
    WID_PLUGIN, WID_PLUGIN_EFFECT, WID_PLUGIN_GENERATOR,
    SIDE_COLUMN_PADS,
    XY_PAD_X_CC, XY_PAD_Y_CC,
    performance_modwheel_CC,
    XY_VERT_FADER_CCS, XY_HORIZ_FADER_CCS,
    XY_FADER_ON_COLOR, XY_FADER_OFF_COLOR, performance_modwheel_COLOR,
    XY_PAGE_XY, XY_PAGE_VERT, XY_PAGE_HORIZ, XY_PAGE_COUNT,
    NOTE_LOCK_PULSE_RGB,
    mk1_note_to_pad,
    LedColor,
    LED_OFF,
)
DEVICE_FAMILY_MK1 = "mk1"
DEVICE_FAMILY_MK2 = "mk2"
DEVICE_FAMILY_LPX = "lpx"
DEVICE_FAMILY_LPM3 = "lpm3"
# First-generation devices share the MK1 LED protocol (bi-colour note-on only,
# no SysEx LED commands).  Hardware IDs are unknown for some devices; set each PREFIX once confirmed, or leave None for
# name-based detection.
MK1_DEVICE_ID_PREFIX:      bytes | None = None  # original Launchpad
MINI_MK1_DEVICE_ID_PREFIX: bytes | None = None  # Launchpad Mini MK1
MINI_MK2_DEVICE_ID_PREFIX: bytes | None = None  # Launchpad Mini MK2
LP_S_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x20, 0x00))
MK2_DEVICE_ID_PREFIX  = bytes((0x00, 0x20, 0x29, 0x69))
LPX_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x03, 0x01))
LPM3_DEVICE_ID_PREFIX = bytes((0x00, 0x20, 0x29, 0x13, 0x01))
LPX_CUSTOM_MODE_PRODUCT_ID = 0x0C
LPM3_CUSTOM_MODE_PRODUCT_ID = 0x0D
LPX_CUSTOM_MODE_SLOT_IDS = (4, 5, 6, 7, 8, 9, 10, 11)
LPM3_CUSTOM_MODE_LAYOUT_IDS = (4, 5, 6, 7, 8, 9, 10, 11)
CUSTOM_MODE_READ_TIMEOUT_SECONDS = 1.5
LP3_PROGRAMMER_MODE = 0x01
LP3_LIVE_MODE = 0x00
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

def _log(message: str) -> None:
    print(f"[NovLPd unofficial universal] {message}")

def _script_dir() -> Path:
    script_file = globals().get("__file__")
    if script_file:
        return Path(script_file).resolve().parent
    return Path.cwd()

def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))

def _format_device_id(device_id) -> str:
    if device_id is None:
        return "<none>"
    if isinstance(device_id, (bytes, bytearray)):
        if not device_id:
            return "<empty>"
        return " ".join(f"{byte:02X}" for byte in device_id)
    return str(device_id)

def _normalize_device_id(device_id) -> bytes:
    if isinstance(device_id, (bytes, bytearray)):
        return bytes(device_id)
    return b""

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

def _detect_device_family(device_id: bytes, device_name: str = "") -> str:
    if device_id.startswith(LPX_DEVICE_ID_PREFIX):
        return DEVICE_FAMILY_LPX
    if device_id.startswith(LPM3_DEVICE_ID_PREFIX):
        return DEVICE_FAMILY_LPM3
    # Launchpad S: has Device Inquiry, similar LED protocol as MK1 generation.
    if device_id.startswith(LP_S_DEVICE_ID_PREFIX):
        return DEVICE_FAMILY_MK1
    for prefix in (MK1_DEVICE_ID_PREFIX, MINI_MK1_DEVICE_ID_PREFIX, MINI_MK2_DEVICE_ID_PREFIX):
        if prefix is not None and device_id.startswith(prefix):
            return DEVICE_FAMILY_MK1
    # Name-based fallback for first-gen devices that don't respond to Device Inquiry.
    # Only applied when FL reports an empty hardware ID (avoids misidentifying an
    # unrecognised MK2-generation device that happens to share the name pattern).
    if not device_id:
        name_lower = device_name.strip().lower()
        if name_lower in _MK1_GEN_EXACT_NAMES:
            return DEVICE_FAMILY_MK1
        if any(name_lower.startswith(sub) for sub in _MK1_GEN_NAME_SUBSTRINGS):
            return DEVICE_FAMILY_MK1
    return DEVICE_FAMILY_MK2

def _mk1_label(device_id: bytes, device_name: str) -> str:
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

def _log_device_identification() -> None:
    name = "<unknown>"
    hardware_id = "<unknown>"
    try:
        name = device.getName()
    except Exception as exc:
        name = f"<error: {exc}>"
    try:
        hardware_id = _format_device_id(device.getDeviceID())
    except Exception as exc:
        hardware_id = f"<error: {exc}>"
    _log(f"FL device name: {name}")
    _log(f"FL hardware id: {hardware_id}")
    _log("If hardware id is non-empty and the device is unrecognised, copy it into supportedHardwareIds and the appropriate DEVICE_ID_PREFIX constant.")

# Surface coordinator
class LaunchpadSurface:
    def __init__(self) -> None:
        self.script_dir  = _script_dir()
        self.state_path  = self.script_dir / STATE_FILE
        self.midi_port   = 0
        self.state       = dict(DEFAULT_STATE)
        self.settings_visible = False
        self.surface_mode     = MODE_NOTE
        self._restoring_mode  = False
        self._pending_custom_mode_index: int | None = None
        # Active note tracking
        self.active_pads:  dict[int, tuple[int, int]] = {}
        self.active_notes: dict[tuple[int, int], int] = {}
        self._xy_last_x_val: int | None = 0
        self._xy_last_y_val: int | None = 127
        # XY fader pages: one PadFader per grid fader (8 vertical + 8 horizontal)
        # plus the side-column modwheel.  Values are 0.0–127.0 floats keyed by CC.
        self._xy_faders: dict[int, PadFader] = {}
        self._xy_pad_to_fader: dict[int, dict[int, int]] = {  # page → {pad: cc}
            XY_PAGE_VERT: {},
            XY_PAGE_HORIZ: {},
        }
        for cc, pads in xp.vert_fader_defs():
            self._xy_faders[cc] = PadFader(pads, minimum=0.0, maximum=127.0)
            for p in pads:
                self._xy_pad_to_fader[XY_PAGE_VERT][p] = cc
        for cc, pads in xp.horiz_fader_defs():
            self._xy_faders[cc] = PadFader(pads, minimum=0.0, maximum=127.0)
            for p in pads:
                self._xy_pad_to_fader[XY_PAGE_HORIZ][p] = cc
        self._performance_modwheel_fader = PadFader(xp.modwheel_pads(), minimum=0.0, maximum=127.0)
        self._xy_faders[performance_modwheel_CC] = self._performance_modwheel_fader
        self._xy_fader_values: dict[int, float] = {performance_modwheel_CC: 63.5}   # cc → 0.0-127.0
        # Up+down simultaneous detection for XY mode modwheel toggle
        self._xy_up_held: bool = False
        self._xy_down_held: bool = False
        self._xy_combo_fired: bool = False
        self.performance_direct_pads: dict[int, tuple[int, int]] = {}
        self._fpc_active_pad_order: list[int] = []
        # FPC slot recovery (RAM-only): last-known global channel name per slot,
        # plus the last rack signature so recovery runs only when the channel
        # rack actually changed (e.g. after "make unique from the sample"
        # inserts a channel and shifts global indices below it).
        self._fpc_slot_last_known_names: dict[int, str] = {}
        self._last_rack_signature: tuple = ()
        # Launch map
        self._launch_map_ready = False
        # Idle-poll caches
        self._last_selected_channel  = -2
        self._last_playhead_step     = -1
        self._last_unsafe_midi_in_notice = 0.0
        self._last_plugin_override_id: str | None = None
        self._plugin_override_held_pads: set[int] = set()
        self._suppressed_plugin_override_id: str | None = None
        self._plugin_param_specs: dict[tuple[str, int, int, int], dict] = {}
        self._gross_beat_fader = PadFader(GROSS_BEAT_FADER_PADS, tension=-3.0)
        self.device_name = "Unknown Launchpad"
        self.device_id = b""
        self.device_family = DEVICE_FAMILY_MK2
        self.device_label = "Launchpad MK2"
        self._side_column_is_cc = False
        self._top_ccs = TOP_CCS
        self._top_octave_down = TOP_OCTAVE_DOWN
        self._top_octave_up = TOP_OCTAVE_UP
        self._top_pan_left = TOP_PAN_LEFT
        self._top_pan_right = TOP_PAN_RIGHT
        self._top_performance = TOP_PERFORMANCE
        self._top_note_mode = TOP_NOTE_MODE
        self._top_fpc_mode = TOP_FPC_MODE
        self._top_record_arm = TOP_RECORD_ARM
        # Note-mode button gesture state
        self._note_button_pressed              = False
        self._note_button_hold_started         = 0.0
        self._note_button_hold_fired           = False
        self._note_button_last_tap             = 0.0
        self._note_button_entered_from_outside = False
        # Record button gesture state: double-tap = channel lock, long-hold = record
        self._record_button_pressed      = False
        self._record_button_hold_started = 0.0
        self._record_button_hold_fired   = False
        self._record_button_last_tap     = 0.0
        # FPC selector gesture state
        self._fpc_selector_pressed: int | None = None
        self._fpc_selector_hold_started = 0.0
        self._fpc_selector_hold_fired   = False
        # Performance double-tap
        self._performance_button_pressed = False
        self._performance_button_hold_started = 0.0
        self._performance_button_hold_fired = False
        self._performance_button_last_tap = 0.0
        self._performance_button_armed_from_performance = False
        # Step sequencer channel toggle hold detection
        self._step_toggle_pad_pressed: int | None = None
        self._step_toggle_hold_started = 0.0
        self._step_toggle_hold_fired = False
        # Lock routing page: set to the channel index while the page is open
        self._step_lock_page_channel: int | None = None
        self._step_lock_page_test_note_sent: bool = False
        # Custom mode (syx file)
        self._custom_modes: list[cm.CustomMode] = []
        self._custom_mode_index: int = 0          # which slot is active
        self._custom_mode_selecting: bool = False  # selector overlay visible
        self._custom_fader_helpers: dict[tuple[int, int], PadFader] = {}
        self._live_custom_mode_reading: bool = False
        self._live_custom_mode_deadline: float = 0.0
        self._live_custom_mode_slots: set[int] = set()
        # Fader CC values: (mode_slot, fader_index) → float 0.0–1.0
        self._custom_fader_values: dict[tuple[int, int], float] = {}
        self._fpc_button_pressed: bool = False
        self._fpc_button_hold_started: float = 0.0
        self._fpc_button_hold_fired: bool = False
        # LED caches
        self._refresh_needed   = True
        self._grid_led_cache:  dict[int, LedColor] = {}
        self._top_led_cache:   dict[int, LedColor] = {}
        # MK2 software pulse: timestamp when lock was engaged (phase origin)
        self._pulse_start      = 0.0
        self._pulse_last_frame = 0.0
    _CUSTOM_FADER_MICRO_BRIGHTNESS = (0.25, 0.5, 0.75, 1.0)
    # FL Studio lifecycle callbacks
    def on_init(self) -> None:
        _log(f"init script_dir={self.script_dir}")
        _log_device_identification()
        self._configure_device_profile()
        self.midi_port = device.getPortNumber()
        self._load_state()
        self._prepare_custom_modes()
        state_io.load_flp_state(self.midi_port, self.state)
        # Note: self.surface_mode stays at its __init__ default (MODE_NOTE) here —
        # _restore_surface_mode() below transitions to the saved mode via the
        # normal _enter_*_mode() setters so the programmer-mode layout SysEx is
        # always sent. (_enter_performance_mode() short-circuits and skips the
        # SysEx when self.surface_mode already equals MODE_PERFORMANCE, which
        # would happen here if we pre-assigned it from saved state — leaving
        # the hardware stuck in Live mode after a restart with that mode saved.)
        self._xy_last_x_val = int(self.state.get("xy_cursor_x", 0))
        self._xy_last_y_val = int(self.state.get("xy_cursor_y", 127))
        saved_faders = self.state.get("xy_fader_values", {})
        if isinstance(saved_faders, dict):
            for k, v in saved_faders.items():
                try:
                    self._xy_fader_values[int(k)] = float(v)
                except (ValueError, TypeError):
                    pass
        if self._repair_invalid_note_window():
            self._save_state()
        if bool(self.state.get("lights_out", False)):
            self.state["lights_out"] = False
            self._save_state()
            _log("startup reset lights_out=false")
        self._init_launch_map()
        # Seed FPC slot-recovery baseline from the saved (trusted) indices so
        # later rack edits can be detected and repaired by name.
        self._last_rack_signature = fm.rack_signature()
        fm.remember_slot_assignment_names(self.state, self._fpc_slot_last_known_names)
        led_display.clear_surface(self._grid_led_cache, self._top_led_cache)
        self._restore_surface_mode()
        _log(f"startup state mode={self.surface_mode} settings={self.settings_visible}")
        self._refresh_needed = False
    def on_deinit(self) -> None:
        self._release_all_notes()
        led_display.clear_surface(self._grid_led_cache, self._top_led_cache)
        if self.device_family in (DEVICE_FAMILY_LPX, DEVICE_FAMILY_LPM3):
            led_display.set_layout(LP3_LIVE_MODE)
        elif self.device_family != DEVICE_FAMILY_MK1:
            led_display.set_layout(LAYOUT_SESSION)
    def on_idle(self) -> None:
        selected = self._selected_channel()
        if selected != self._last_selected_channel:
            self._last_selected_channel = selected
            if (
                self.surface_mode == MODE_FPC
                and not fm.has_any_fpc_slot_assignment(self.state)
                and fm.selected_channel_is_fpc(selected)
            ):
                fm.auto_assign_new_fpc(self.state, selected)
                fm.remember_slot_assignment_names(self.state, self._fpc_slot_last_known_names)
                self._save_state()
            self._refresh_needed = True
        # Repaint the step-sequencer grid as the playhead advances so the column
        # under it stays highlighted during playback.
        playhead_step = ss.playhead_step() if self._step_sequencer_grid_visible() else -1
        if playhead_step != self._last_playhead_step:
            self._last_playhead_step = playhead_step
            self._refresh_needed = True
        plugin_override_id = self._active_plugin_pad_override()
        if plugin_override_id != self._last_plugin_override_id:
            self._last_plugin_override_id = plugin_override_id
            self._plugin_override_held_pads.clear()
            self._refresh_needed = True
        if (
            self._note_button_pressed
            and not self._note_button_hold_fired
            and time.monotonic() - self._note_button_hold_started >= TAP_AND_HOLD_DURATION_SECONDS
        ):
            self._note_button_hold_fired = True
            if self.surface_mode != MODE_NOTE:
                self._enter_note_mode()
            self.settings_visible = not self.settings_visible
            self._refresh_needed  = True
        if (
            self._fpc_selector_pressed is not None
            and not self._fpc_selector_hold_fired
            and self.surface_mode == MODE_FPC
            and time.monotonic() - self._fpc_selector_hold_started >= TAP_AND_HOLD_DURATION_SECONDS
        ):
            self._fpc_selector_hold_fired = True
            fm.clear_fpc_selector(
                self._fpc_selector_pressed,
                self.state,
                page_index=fm.current_fpc_page(self.state),
            )
            self._refresh_needed = True
        if (
            self._performance_button_pressed
            and not self._performance_button_hold_fired
            and time.monotonic() - self._performance_button_hold_started >= TAP_AND_HOLD_DURATION_SECONDS
        ):
            self._performance_button_hold_fired = True
            self.state["lights_out"] = not bool(self.state.get("lights_out", False))
            self._performance_button_last_tap = 0.0
            self._save_state()
            self._refresh_needed = True
        if (
            self._record_button_pressed
            and not self._record_button_hold_fired
            and time.monotonic() - self._record_button_hold_started >= TAP_AND_HOLD_DURATION_SECONDS
        ):
            self._record_button_hold_fired = True
            self._record_button_last_tap = 0.0
            transport.globalTransport(midi.FPT_Record, 1, getattr(midi, "PME_System", 0))
            self._refresh_needed = True
        if (
            self._step_toggle_pad_pressed is not None
            and not self._step_toggle_hold_fired
            and self.surface_mode == MODE_PERFORMANCE
            and not pm.performance_available()
            and time.monotonic() - self._step_toggle_hold_started >= TAP_AND_HOLD_DURATION_SECONDS
        ):
            self._step_toggle_hold_fired = True
            channel_index = ss.channel_for_pad(self._step_toggle_pad_pressed, self.state)
            if channel_index >= 0 and channel_index < ss.channel_count():
                if cl.is_locked(self.state, cl.STEP_CONTEXT) and cl.get(self.state, cl.STEP_CONTEXT) == channel_index:
                    cl.clear(self.state, cl.STEP_CONTEXT)
                else:
                    cl.set_lock(self.state, cl.STEP_CONTEXT, channel_index)
                    self._pulse_start = time.monotonic()
                self._save_state()
                self._refresh_needed = True
        if (
            self._step_toggle_pad_pressed is not None
            and not self._step_toggle_hold_fired
            and self.surface_mode == MODE_STEP_SEQ
            and time.monotonic() - self._step_toggle_hold_started >= TAP_AND_HOLD_DURATION_SECONDS
        ):
            self._step_toggle_hold_fired = True
            channel_index = ss.channel_for_pad(self._step_toggle_pad_pressed, self.state)
            if 0 <= channel_index < ss.channel_count():
                self._step_lock_page_channel = channel_index
                self._step_lock_page_test_note_sent = False
                if not transport.isPlaying():
                    midi_channel = int(self.state["midi_channel"]) & 0x0F
                    channels.midiNoteOn(channel_index, self._LOCK_PAGE_TEST_NOTE, 100, midi_channel)
                    self._step_lock_page_test_note_sent = True
                self._refresh_needed = True
        if (
            self.device_family == DEVICE_FAMILY_MK2
            and self._channel_lock_enabled()
            and not bool(self.state.get("lights_out", False))
        ):
            now = time.monotonic()
            if now - self._pulse_last_frame >= 1.0 / 30.0:
                self._pulse_last_frame = now
                self._send_software_pulse_frame()
                self._refresh_needed = True
        if self._live_custom_mode_reading and time.monotonic() >= self._live_custom_mode_deadline:
            self._live_custom_mode_reading = False
            _log(
                "custom modes live read complete: "
                f"{len(self._live_custom_mode_slots)} device slot(s), {len(self._custom_modes)} total loaded"
            )
            if self._pending_custom_mode_index is not None and self._custom_modes:
                self._custom_mode_index = max(0, min(len(self._custom_modes) - 1, self._pending_custom_mode_index))
                self._pending_custom_mode_index = None
                self._refresh_needed = True
        if self._refresh_needed:
            self._refresh_surface()
            self._refresh_needed = False
    def on_refresh(self, _flags: int) -> None:
        self._recover_fpc_if_rack_changed()
        self._refresh_surface()
        self._refresh_needed = False
    def on_project_load(self, _status: int) -> None:
        # A freshly loaded project's saved indices are trusted as-is; just seed
        # the recovery baseline so later rack edits can be detected/repaired.
        self._last_rack_signature = fm.rack_signature()
        fm.remember_slot_assignment_names(self.state, self._fpc_slot_last_known_names)
        self._refresh_surface()
        self._refresh_needed = False
    def _recover_fpc_if_rack_changed(self) -> None:
        """When the channel rack changes (insert/delete/reorder), re-locate any
        FPC slot whose global index drifted, matching by last-known name."""
        signature = fm.rack_signature()
        if signature == self._last_rack_signature:
            return
        self._last_rack_signature = signature
        if fm.recover_shifted_slot_assignments(self.state, self._fpc_slot_last_known_names):
            self._save_state()
    def _prepare_custom_modes(self) -> None:
        if self.device_family in (DEVICE_FAMILY_LPX, DEVICE_FAMILY_LPM3):
            live_ready = True
            try:
                cm.reset_live_folder(self.script_dir)
            except Exception as exc:
                live_ready = False
                _log(f"custom modes live reset failed: {exc}")
            self._live_custom_mode_slots.clear()
            if live_ready:
                self._start_live_custom_mode_read()
            else:
                self._live_custom_mode_reading = False
            self._reload_custom_modes(live_first=True)
        else:
            self._live_custom_mode_reading = False
            self._live_custom_mode_slots.clear()
            self._reload_custom_modes(live_first=False)
    def _reload_custom_modes(self, *, live_first: bool) -> None:
        self._custom_modes = (
            cm.load_live_then_static(self.script_dir, self._live_custom_mode_slots)
            if live_first
            else cm.load_from_script_dir(self.script_dir)
        )
        self._custom_fader_helpers = {
            (mode.slot, fader.fader_index): PadFader(
                fader.pads(),
                minimum=0.0,
                maximum=float(fader.max_value),
                bipolar=fader.bipolar,
            )
            for mode in self._custom_modes
            for fader in mode.faders
        }
        if self._custom_modes:
            self._custom_mode_index = max(0, min(self._custom_mode_index, len(self._custom_modes) - 1))
        else:
            self._custom_mode_index = 0
        source = "live/static syx" if live_first else "static syx"
        _log(f"loaded {len(self._custom_modes)} custom mode(s) from {source}")
    def _custom_mode_product_id(self) -> int | None:
        if self.device_family == DEVICE_FAMILY_LPX:
            return LPX_CUSTOM_MODE_PRODUCT_ID
        if self.device_family == DEVICE_FAMILY_LPM3:
            return LPM3_CUSTOM_MODE_PRODUCT_ID
        return None
    def _custom_mode_slot_ids(self) -> tuple[int, ...]:
        if self.device_family == DEVICE_FAMILY_LPX:
            return LPX_CUSTOM_MODE_SLOT_IDS
        if self.device_family == DEVICE_FAMILY_LPM3:
            return LPM3_CUSTOM_MODE_LAYOUT_IDS
        return ()
    def _custom_mode_read_request(self, slot_id: int) -> bytes | None:
        product_id = self._custom_mode_product_id()
        if product_id is None:
            return None
        if self.device_family == DEVICE_FAMILY_LPM3:
            return bytes((0xF0, 0x00, 0x20, 0x29, 0x02, product_id, 0x05, 0x01, slot_id, 0xF7))
        return bytes((
            0xF0, 0x00, 0x20, 0x29, 0x02, product_id,
            0x20, 0x00, 0x40, 0x40, slot_id, 0xF7,
        ))
    def _start_live_custom_mode_read(self) -> None:
        if self._custom_mode_product_id() is None:
            return
        slot_ids = self._custom_mode_slot_ids()
        if not slot_ids:
            return
        self._live_custom_mode_reading = True
        self._live_custom_mode_deadline = time.monotonic() + CUSTOM_MODE_READ_TIMEOUT_SECONDS
        for slot_id in slot_ids:
            request = self._custom_mode_read_request(slot_id)
            if request is None:
                continue
            try:
                device.midiOutSysex(request)
            except Exception as exc:
                _log(f"custom mode slot id {slot_id} read request failed: {exc}")
        _log(f"requested {len(slot_ids)} on-device custom mode slot id(s) from {self.device_label}")
    def on_sysex(self, event) -> None:
        sysex = self._event_sysex_bytes(event)
        reply = self._custom_mode_reply_slot(sysex)
        if reply is None:
            return
        slot, slot_id = reply
        parsed_modes = cm.parse_syx_bytes(sysex)
        mode = parsed_modes[0] if parsed_modes else None
        if mode is None or (len(mode) == 0 and not mode.faders):
            _log(f"custom mode slot id {slot_id} reply was empty; keeping fallback slot")
            return
        try:
            cm.write_live_slot(self.script_dir, slot, sysex)
        except Exception as exc:
            _log(f"custom mode slot id {slot_id} live write failed: {exc}")
            return
        if slot not in self._live_custom_mode_slots:
            self._live_custom_mode_slots.add(slot)
            _log(
                f"loaded live custom mode slot {slot + 1} "
                f"(id {slot_id}): {mode.name or '(unnamed)'}"
            )
        self._reload_custom_modes(live_first=True)
        self._refresh_needed = True
    def _event_sysex_bytes(self, event) -> bytes:
        try:
            return bytes(int(value) & 0xFF for value in event.sysex)
        except Exception:
            return b""
    def _custom_mode_reply_slot(self, sysex: bytes) -> tuple[int, int] | None:
        product_id = self._custom_mode_product_id()
        slot_ids = self._custom_mode_slot_ids()
        if product_id is None or not slot_ids or len(sysex) < 12:
            return None
        if sysex[0] != 0xF0 or sysex[-1] != 0xF7:
            return None
        if self.device_family == DEVICE_FAMILY_LPM3:
            if sysex[:8] != bytes((0xF0, 0x00, 0x20, 0x29, 0x02, product_id, 0x05, 0x01)):
                return None
            slot_id = sysex[8] & 0x7F
            try:
                slot = slot_ids.index(slot_id)
            except ValueError:
                return None
            return slot, slot_id
        if sysex[1:8] != bytes((0x00, 0x20, 0x29, 0x02, product_id, 0x20, 0x00)):
            return None
        if sysex[9] != 0x40:
            return None
        slot_id = sysex[10] & 0x7F
        try:
            slot = slot_ids.index(slot_id)
        except ValueError:
            return None
        return slot, slot_id
    def on_update_live_mode(self, _last_track: int) -> None:
        if pm.performance_available():
            if self.surface_mode == MODE_PERFORMANCE:
                self._sync_performance_view()
            else:
                self._launch_map_ready = pm.update_launch_map(self._launch_map_ready)
        else:
            pm.clear_performance_view()
            self._launch_map_ready = pm.update_launch_map(self._launch_map_ready)
        self._refresh_needed = True
    def on_midi_in(self, event) -> None:
        if self.device_family == DEVICE_FAMILY_MK1 and (event.status & 0xF0) in (
            midi.MIDI_NOTEON, midi.MIDI_NOTEOFF
        ):
            event.data1 = mk1_note_to_pad(event.data1)
        if not self._should_filter_midi_in(event):
            return
        event.handled = True
        try:
            self.on_midi_msg(event)
        except RuntimeError as exc:
            if "unsafe at current time" not in str(exc).lower():
                raise
            self._rollback_unsafe_midi_in_event(event)
            now = time.monotonic()
            if now - self._last_unsafe_midi_in_notice >= 2.0:
                self._last_unsafe_midi_in_notice = now
                _log("input mapping is waiting; Note/FPC pads are filtered until the mapping window closes")
        event.handled = True

    def _should_filter_midi_in(self, event) -> bool:
        status = event.status & 0xF0
        if status in (midi.MIDI_NOTEON, midi.MIDI_NOTEOFF):
            if event.data1 not in self._note_input_pads() and not fm.is_fpc_selector(event.data1):
                return False
        elif status == midi.MIDI_CONTROLCHANGE:
            if event.data1 not in self._cc_input_pads():
                return False
        else:
            return False
        return self.settings_visible or self.surface_mode in (MODE_NOTE, MODE_FPC)

    def _rollback_unsafe_midi_in_event(self, event) -> None:
        status = event.status & 0xF0
        if status != midi.MIDI_NOTEON or event.data2 <= 0:
            return
        note_info = self.active_pads.pop(event.data1, None)
        self._drop_recent_active_pad(event.data1)
        if note_info is None:
            return
        channel_index, note = note_info
        self._drop_active_note(channel_index, note)
        self._refresh_needed = True

    def on_midi_msg(self, event) -> None:
        status = event.status & 0xF0
        if status == midi.MIDI_NOTEON:
            pressed = event.data2 > 0
            if event.data1 in self._note_input_pads() or fm.is_fpc_selector(event.data1):
                event.handled = self._handle_grid_pad(event, event.data1, event.data2, pressed)
                return
        elif status == midi.MIDI_NOTEOFF:
            if event.data1 in self._note_input_pads() or fm.is_fpc_selector(event.data1):
                event.handled = self._handle_grid_pad(event, event.data1, 0, False)
                return
        elif status == midi.MIDI_CONTROLCHANGE and event.data1 in self._top_ccs:
            pressed = event.data2 > 0
            self._handle_top_button(event.data1, pressed, event)
            event.handled = True
            return
        elif status == midi.MIDI_CONTROLCHANGE and event.data1 in self._cc_input_pads():
            pressed = event.data2 > 0
            event.handled = self._handle_grid_pad(event, event.data1, event.data2, pressed)
            return
        event.handled = True
    # Grid pad routing
    def _handle_grid_pad(self, event, pad: int, velocity: int, pressed: bool) -> bool:
        if self.settings_visible:
            if pressed and pad in SETTINGS_GRID_PADS + tuple(nm.SCALE_SETTING_PADS):
                if nm.handle_settings_pad(pad, self.state):
                    self._save_state()
            self._refresh_needed = True
            return True
        if self.surface_mode == MODE_CUSTOM:
            return self._handle_custom_mode_pad(event, pad, velocity, pressed)
        if self.surface_mode == MODE_XY_PAD:
            return self._handle_xy_pad(event, pad, velocity, pressed)
        if self.surface_mode == MODE_STEP_SEQ:
            return self._handle_step_sequencer_pad(event, pad, velocity, pressed)
        if self.surface_mode == MODE_PERFORMANCE:
            if not pm.performance_available():
                if ss.is_channel_toggle_pad(pad):
                    if pressed:
                        self._step_toggle_pad_pressed = pad
                        self._step_toggle_hold_started = time.monotonic()
                        self._step_toggle_hold_fired = False
                    else:
                        if self._step_toggle_pad_pressed == pad and not self._step_toggle_hold_fired:
                            if ss.toggle_pad(pad, self.state):
                                self._save_state()
                        if self._step_toggle_pad_pressed == pad:
                            self._step_toggle_pad_pressed = None
                        self._step_toggle_hold_started = 0.0
                        self._step_toggle_hold_fired = False
                else:
                    if pressed and ss.toggle_pad(pad, self.state):
                        self._save_state()
                self._refresh_surface()
                self._refresh_needed = False
                return True
            # Side column intercept: modwheel fader when toggled on.
            if self._performance_modwheel_on() and ps.pad_to_slot(pad) is not None:
                if pressed:
                    self._xy_apply_fader(event, performance_modwheel_CC, pad)
                    self.active_pads[pad] = ("xy_pad", pad)
                else:
                    self.active_pads.pop(pad, None)
                led_display.refresh_grid_pad(pad, self._grid_lighting, self._grid_led_cache)
                self._refresh_needed = True
                return True
            self._handle_performance_pad(event, pad, velocity, pressed)
            if pad in PERFORMANCE_PAGE_HOTKEY_PADS and pressed:
                self._refresh_surface()
                self._refresh_needed = False
            else:
                led_display.refresh_grid_pad(pad, self._grid_lighting, self._grid_led_cache)
            return True
        plugin_override_id = self._active_plugin_pad_override()
        if plugin_override_id is not None:
            handled = self._handle_plugin_pad_override(plugin_override_id, event, pad, velocity, pressed)
            if handled:
                led_display.refresh_grid_pad(pad, self._grid_lighting, self._grid_led_cache)
                return True
        if self.surface_mode == MODE_FPC and fm.is_fpc_selector(pad):
            if pressed:
                self._fpc_selector_pressed      = pad
                self._fpc_selector_hold_started = time.monotonic()
                self._fpc_selector_hold_fired   = False
            else:
                if self._fpc_selector_pressed == pad and not self._fpc_selector_hold_fired:
                    selected_channel = self._host_selected_channel()
                    if fm.selected_channel_is_fpc(selected_channel):
                        fm.handle_fpc_selector(
                            pad,
                            self.state,
                            selected_channel,
                            page_index=fm.current_fpc_page(self.state),
                        )
                        fm.remember_slot_assignment_names(self.state, self._fpc_slot_last_known_names)
                        self._save_state()
                if self._fpc_selector_pressed == pad:
                    self._fpc_selector_pressed = None
                self._fpc_selector_hold_started = 0.0
                self._fpc_selector_hold_fired   = False
            self._refresh_needed = True
            return True
        # Note-on / note-off — only active in note or FPC mode
        if self.surface_mode not in (MODE_NOTE, MODE_FPC):
            return True
        midi_channel = int(self.state["midi_channel"]) & 0x0F
        if pressed:
            note          = self._note_for_pad(pad)
            channel_index = self._channel_for_pad(pad)
            if channel_index >= 0 and LOWEST_NOTE <= note <= HIGHEST_NOTE:
                self.active_pads[pad] = (channel_index, note)
                self._mark_pad_recently_active(pad)
                key = (channel_index, note)
                self.active_notes[key] = self.active_notes.get(key, 0) + 1
                # Consume the Launchpad event so FL MIDI learn/auto-detect does not
                # see pad notes, then play the intended channel explicitly.
                channels.midiNoteOn(
                    channel_index, note, max(1, velocity or 127), midi_channel
                )
                led_display.refresh_grid_pad(pad, self._grid_lighting, self._grid_led_cache)
                self._refresh_needed = True
                return True
        else:
            note_info = self.active_pads.pop(pad, None)
            self._drop_recent_active_pad(pad)
            if note_info is not None:
                channel_index, note = note_info
                self._drop_active_note(channel_index, note)
                channels.midiNoteOn(channel_index, note, 0, midi_channel)
                led_display.refresh_grid_pad(pad, self._grid_lighting, self._grid_led_cache)
                self._refresh_needed = True
                return True
        led_display.refresh_grid_pad(pad, self._grid_lighting, self._grid_led_cache)
        self._refresh_needed = True
        return True
    # Top-row button routing
    def _handle_top_button(self, cc: int, pressed: bool, event) -> None:
        if pressed:
            _log(f"top button cc={cc} action={self._top_button_action_name(cc)}")
        if cc == self._top_performance:
            if self._handle_performance_button(pressed):
                self._refresh_needed = True
            return
        if cc == self._top_note_mode:
            self._handle_note_mode_button(pressed)
            self._refresh_needed = True
            return
        if cc == self._top_fpc_mode:
            self._handle_fpc_mode_button(pressed, event)
            self._refresh_needed = True
            return
        if cc == self._top_record_arm:
            self._handle_record_button(pressed, event)
            self._refresh_needed = True
            return
        # Track up/down held state for simultaneous-press combo detection (used in
        # both XY mode for page stepping and performance mode for modwheel toggle).
        if cc == self._top_octave_up:
            self._xy_up_held = pressed
        if cc == self._top_octave_down:
            self._xy_down_held = pressed
        if self._handle_xy_octave(cc, pressed):
            self._refresh_needed = True
            return
        if self._handle_performance_modwheel_combo(cc, pressed):
            self._refresh_needed = True
            return
        if not pressed:
            return
        if cc == self._top_octave_down:
            if self.surface_mode == MODE_PERFORMANCE:
                if pm.performance_available():
                    pm.step_tracks(-1, self.state)  # down arrow = scroll down
                    self._sync_performance_view()
                else:
                    ss.step_channels(-1, self.state)
            elif self.surface_mode == MODE_STEP_SEQ:
                ss.step_channels(-1, self.state)
            elif self.surface_mode == MODE_NOTE:
                nm.step_octave(self.state, -1)
        elif cc == self._top_octave_up:
            if self.surface_mode == MODE_PERFORMANCE:
                if pm.performance_available():
                    pm.step_tracks(1, self.state)   # up arrow = scroll up
                    self._sync_performance_view()
                else:
                    ss.step_channels(1, self.state)
            elif self.surface_mode == MODE_STEP_SEQ:
                ss.step_channels(1, self.state)
            elif self.surface_mode == MODE_NOTE:
                nm.step_octave(self.state, 1)
        elif cc == self._top_pan_left:
            if self.surface_mode == MODE_PERFORMANCE:
                if pm.performance_available():
                    pm.step_blocks(-1, self.state)
                    self._sync_performance_view()
                else:
                    ss.step_steps(-1, self.state)
            elif self.surface_mode == MODE_STEP_SEQ:
                ss.step_steps(-1, self.state)
            elif self.surface_mode == MODE_FPC:
                fm.step_fpc_page(-1, self.state)
                self._refresh_surface()
            elif self.surface_mode == MODE_NOTE:
                nm.step_pan(self.state, -1)
        elif cc == self._top_pan_right:
            if self.surface_mode == MODE_PERFORMANCE:
                if pm.performance_available():
                    pm.step_blocks(1, self.state)
                    self._sync_performance_view()
                else:
                    ss.step_steps(1, self.state)
            elif self.surface_mode == MODE_STEP_SEQ:
                ss.step_steps(1, self.state)
            elif self.surface_mode == MODE_FPC:
                fm.step_fpc_page(1, self.state)
                self._refresh_surface()
            elif self.surface_mode == MODE_NOTE:
                nm.step_pan(self.state, 1)
        elif cc == self._top_performance:
            self._enter_performance_mode()
        self._save_state()
        self._refresh_needed = True
    def _top_button_action_name(self, cc: int) -> str:
        if cc == self._top_performance:
            return "performance/session"
        if cc == self._top_note_mode:
            return "note"
        if cc == self._top_fpc_mode:
            return "fpc/custom"
        if cc == self._top_octave_down:
            return "octave_down"
        if cc == self._top_octave_up:
            return "octave_up"
        if cc == self._top_pan_left:
            return "pan_left"
        if cc == self._top_pan_right:
            return "pan_right"
        if cc == self._top_record_arm:
            return "record"
        return "unmapped"
    def _session_key_is_inert(self) -> bool:
        """Pressing the session key does nothing when already in the modulator
        (XY) pages and performance mode can't be cycled to — there's no other
        session surface to switch to, so the input is ignored without an LED
        refresh."""
        return self.surface_mode == MODE_XY_PAD and not pm.performance_available()

    def _handle_performance_button(self, pressed: bool) -> bool:
        """Returns True if the surface changed and the LEDs need a refresh."""
        if pressed:
            now = time.monotonic()
            if now - self._performance_button_last_tap <= PERFORMANCE_DOUBLE_TAP_SECONDS:
                # Double-tap while performance mode enabled: toggle hybrid FPC.
                # Use the surface mode captured at the first press — the first
                # tap's release already cycled the surface (e.g. Performance →
                # XY/faders), so re-checking the live surface_mode here would
                # never see MODE_PERFORMANCE and the gesture would be swallowed
                # by the plain mode-cycle branch below.
                if self._performance_button_armed_from_performance:
                    if self.surface_mode != MODE_PERFORMANCE:
                        # The first tap's release already cycled us away from
                        # Performance mode (e.g. to XY/faders); bring it back
                        # so the hybrid-FPC toggle lands where the user expects.
                        self._enter_performance_mode()
                    self.state["performance_direct_audio"] = not bool(
                        self.state.get("performance_direct_audio", False)
                    )
                    if not self.state["performance_direct_audio"]:
                        pm.release_performance_direct_notes(
                            self.performance_direct_pads,
                            int(self.state["midi_channel"]),
                        )
                    self._performance_button_last_tap = 0.0
                    self._performance_button_pressed = False
                    self._performance_button_hold_started = 0.0
                    self._performance_button_hold_fired = True
                    self._save_state()
                    _log(
                        "performance hybrid empty-pad FPC "
                        f"{'on' if self.state['performance_direct_audio'] else 'off'}"
                    )
                    return True
                # Second single tap while not in performance mode: cycle modes
                self._cycle_session_modes()
                self._performance_button_last_tap = 0.0
                self._performance_button_pressed = False
                self._performance_button_hold_started = 0.0
                self._performance_button_hold_fired = True
                self._save_state()
                return True
            self._performance_button_last_tap = now
            self._performance_button_pressed = True
            self._performance_button_hold_started = now
            self._performance_button_hold_fired = False
            self._performance_button_armed_from_performance = (
                self.surface_mode == MODE_PERFORMANCE
            )
            return False
        was_pressed = self._performance_button_pressed
        hold_fired = self._performance_button_hold_fired
        self._performance_button_pressed = False
        self._performance_button_hold_started = 0.0
        self._performance_button_hold_fired = False
        if not was_pressed or hold_fired or self._session_key_is_inert():
            return False
        # If we're already in a session-style surface, cycle; otherwise enter the
        # session default (Performance when available, else XY/faders).
        if self.surface_mode in (MODE_PERFORMANCE, MODE_XY_PAD):
            self._cycle_session_modes()
        else:
            self._enter_session_default()
        return True
    def _enter_note_mode(self) -> None:
        self.surface_mode     = MODE_NOTE
        self.settings_visible = False
        self._release_all_notes()
        ss.clear_channel_rack_view()
        self._apply_surface_layout()
        self._save_state()

    def _cycle_note_modes(self) -> None:
        """Note key cycle: Note → Custom → Note (skips Custom if none loaded)."""
        if self.surface_mode == MODE_CUSTOM:
            self._enter_note_mode()
        elif self._custom_modes:
            self._enter_custom_mode_selector()
        # else: no custom modes loaded — stay in Note

    def _handle_note_mode_button(self, pressed: bool) -> None:
        # Long-hold (handled in on_idle) opens settings while in Note mode.
        # A quick tap cycles Note ↔ Custom; arriving from another surface enters
        # Note immediately.
        if pressed:
            entered_from_outside = self.surface_mode not in (MODE_NOTE, MODE_CUSTOM)
            if entered_from_outside:
                self._enter_note_mode()
            self._note_button_pressed        = True
            self._note_button_hold_started   = time.monotonic()
            self._note_button_hold_fired     = False
            self._note_button_entered_from_outside = entered_from_outside
            return
        was_pressed    = self._note_button_pressed
        hold_fired     = self._note_button_hold_fired
        entered_from_outside = getattr(self, "_note_button_entered_from_outside", False)
        self._note_button_pressed      = False
        self._note_button_hold_started = 0.0
        self._note_button_hold_fired   = False
        self._note_button_entered_from_outside = False
        if not was_pressed or hold_fired or entered_from_outside:
            return
        if self.settings_visible:
            self.settings_visible = False
            return
        self._cycle_note_modes()
    def _enter_fpc_mode(self, *, suppress_gross_beat: bool) -> None:
        self.surface_mode     = MODE_FPC
        self.settings_visible = False
        self._suppressed_plugin_override_id = (
            self._focused_plugin_pad_override_id() if suppress_gross_beat else None
        )
        self._plugin_override_held_pads.clear()
        self._release_all_notes()
        ss.clear_channel_rack_view()
        if not fm.has_any_fpc_slot_assignment(self.state):
            selected = self._host_selected_channel()
            if fm.selected_channel_is_fpc(selected):
                fm.auto_assign_new_fpc(self.state, selected)
        self._apply_surface_layout()
        self._refresh_surface()
        self._save_state()

    def _handle_fpc_mode_button(self, pressed: bool, _event) -> None:
        # FPC key cycle: FPC → Step Sequencer → Gross Beat → FPC.
        # Gross Beat is skipped when no Gross Beat plugin is focused.
        if pressed:
            self._fpc_button_pressed = True
            return
        if not self._fpc_button_pressed:
            return
        self._fpc_button_pressed = False
        self._fpc_cycle_step()

    def _fpc_cycle_step(self) -> None:
        gb_id = self._focused_plugin_pad_override_id()
        if self.surface_mode == MODE_FPC:
            gb_overlay_active = gb_id is not None and self._suppressed_plugin_override_id != gb_id
            if gb_overlay_active:
                # Gross Beat → back to plain FPC (suppress the overlay).
                self._suppressed_plugin_override_id = gb_id
                self._plugin_override_held_pads.clear()
                self._refresh_surface()
                self._save_state()
            else:
                # Plain FPC → Step Sequencer.
                self._enter_step_sequencer_mode()
        elif self.surface_mode == MODE_STEP_SEQ:
            # Step Sequencer → Gross Beat if available, else back to plain FPC.
            self._enter_fpc_mode(suppress_gross_beat=gb_id is None)
        else:
            # Arriving from another surface → plain FPC.
            self._enter_fpc_mode(suppress_gross_beat=True)

    def _handle_xy_octave(self, cc: int, pressed: bool) -> bool:
        """XY mode up/down arrows: cycle pages on release.  Returns True if
        the event was consumed."""
        if self.surface_mode != MODE_XY_PAD:
            return False
        if cc not in (self._top_octave_up, self._top_octave_down):
            return False
        if pressed:
            return True
        direction = -1 if cc == self._top_octave_up else 1
        self.state["xy_page"] = ps.step_page(int(self.state.get("xy_page", 0)), direction, XY_PAGE_COUNT)
        self._save_state()
        return True

    def _handle_performance_modwheel_combo(self, cc: int, pressed: bool) -> bool:
        """Performance mode up+down simultaneous press: toggle modwheel side column.
        Suppresses the normal track-scroll action when both buttons are pressed together.
        Returns True if the event was consumed (combo fired or combo-release absorbed)."""
        if self.surface_mode != MODE_PERFORMANCE or not pm.performance_available():
            return False
        if cc not in (self._top_octave_up, self._top_octave_down):
            return False
        if pressed:
            other_held = (
                self._xy_down_held if cc == self._top_octave_up else self._xy_up_held
            )
            if other_held:
                self.state["performance_modwheel"] = not bool(self.state.get("performance_modwheel", False))
                self._xy_combo_fired = True
                self._save_state()
                return True
            return False  # single press falls through to normal scroll
        # Release: absorb only if this was part of a combo.
        if self._xy_combo_fired:
            if not self._xy_up_held and not self._xy_down_held:
                self._xy_combo_fired = False
            return True
        return False

    def _handle_record_button(self, pressed: bool, _event) -> None:
        # Double-tap toggles the channel lock for the active context; a long
        # hold toggles FL transport recording (fired from on_idle).
        if pressed:
            now = time.monotonic()
            if now - self._record_button_last_tap <= NOTE_DOUBLE_TAP_SECONDS:
                self._toggle_channel_lock()
                self._record_button_last_tap     = 0.0
                self._record_button_pressed      = False
                self._record_button_hold_started = 0.0
                self._record_button_hold_fired   = True
                self._refresh_needed              = True
                return
            self._record_button_last_tap     = now
            self._record_button_pressed      = True
            self._record_button_hold_started = now
            self._record_button_hold_fired   = False
            return
        self._record_button_pressed      = False
        self._record_button_hold_started = 0.0
        self._record_button_hold_fired   = False
    def _enter_custom_mode_selector(self) -> None:
        """Switch to custom mode with the persistent selector sidebar active."""
        self.surface_mode         = MODE_CUSTOM
        self._custom_mode_selecting = True
        self.settings_visible     = False
        self._release_all_notes()
        ss.clear_channel_rack_view()
        self._apply_surface_layout()
        self._refresh_surface()
        self._save_state()
    def _active_custom_mode(self) -> cm.CustomMode | None:
        if not self._custom_modes:
            return None
        idx = max(0, min(len(self._custom_modes) - 1, self._custom_mode_index))
        return self._custom_modes[idx]
    def _custom_mode_is_blackout(self, mode: cm.CustomMode) -> bool:
        """True when a custom mode defines no visible color anywhere: its
        on_color is black and every pad is either unassigned or has a black
        off_color. Some .syx exports omit the onColor container entirely
        (parsed as 0/black), which otherwise leaves the persistent selector —
        and the whole grid — looking dead while the mode is active."""
        if mode.on_color != 0 or mode.faders:
            return False
        return all(pad.is_off or pad.off_color == 0 for pad in mode)
    def _lights_effectively_out(self) -> bool:
        if bool(self.state.get("lights_out", False)):
            return True
        if self.surface_mode == MODE_CUSTOM:
            mode = self._active_custom_mode()
            if mode is not None and self._custom_mode_is_blackout(mode):
                return True
        return False
    def _custom_mode_index_for_selector_slot(self, slot: int) -> int | None:
        n_slots = len(ps.SELECTOR_PADS)
        if not 0 <= slot < n_slots:
            return None
        upper_slot = slot + n_slots
        if (
            self._custom_mode_index % n_slots == slot
            and upper_slot < len(self._custom_modes)
        ):
            if self._custom_mode_index == slot:
                return upper_slot
            return slot
        if slot < len(self._custom_modes):
            return slot
        return None
    # Custom-mode pad handling
    def _handle_custom_mode_pad(self, event, pad: int, velocity: int, pressed: bool) -> bool:
        # Persistent selector sidebar: right-column pads choose the active mode slot.
        slot = ps.pad_to_slot(pad)
        if slot is not None:
            if pressed:
                mode_index = self._custom_mode_index_for_selector_slot(slot)
                if mode_index is not None:
                    self._custom_mode_index = mode_index
                    self._custom_mode_selecting = True
                    self._refresh_surface()
                    self._save_state()
            return True
        mode = self._active_custom_mode()
        if mode is None:
            return True
        # Fader pad?
        fader = mode.fader_for_pad(pad)
        if fader is not None and pressed:
            return self._handle_custom_fader_pad(event, pad, fader, mode.slot)
        cp = mode.pad(pad)
        if cp is None or cp.is_off:
            return True
        # When this custom index is channel-locked, route its NOTE pads to the
        # plugin selected when the lock was set (faders/CCs stay as-mapped CCs).
        if cp.is_note and cl.is_locked(self.state, self._lock_context()):
            return self._handle_custom_locked_note(pad, cp, velocity, pressed)
        channel = cp.resolved_channel(int(self.state.get("midi_channel", 0))) & 0x0F
        if cp.is_note:
            if pressed:
                vel = max(1, velocity or cp.on_value or 100)
                event.status = 0x90 | channel
                event.data1  = cp.control_value
                event.data2  = vel
                self.active_pads[pad] = (channel, cp.control_value)
            else:
                info = self.active_pads.pop(pad, None)
                ch, note = info if info is not None else (channel, cp.control_value)
                event.status = 0x80 | ch
                event.data1  = note
                event.data2  = 0
        elif cp.is_cc:
            val = cp.on_value if pressed else cp.off_value
            event.status = 0xB0 | channel
            event.data1  = cp.control_value
            event.data2  = val
        else:
            led_display.refresh_grid_pad(pad, self._grid_lighting, self._grid_led_cache)
            return True
        led_display.refresh_grid_pad(pad, self._grid_lighting, self._grid_led_cache)
        return False
    def _handle_custom_locked_note(self, pad: int, cp, velocity: int, pressed: bool) -> bool:
        """Play a custom-mode note pad on the locked channel via the FL channel
        API (consumes the event), mirroring note-mode routing."""
        midi_channel = int(self.state["midi_channel"]) & 0x0F
        note = cp.control_value
        if pressed:
            target = cl.get(self.state, self._lock_context())
            vel = max(1, velocity or cp.on_value or 100)
            self.active_pads[pad] = (target, note)
            key = (target, note)
            self.active_notes[key] = self.active_notes.get(key, 0) + 1
            channels.midiNoteOn(target, note, vel, midi_channel)
        else:
            info = self.active_pads.pop(pad, None)
            if info is not None:
                target, note = info
                self._drop_active_note(target, note)
                channels.midiNoteOn(target, note, 0, midi_channel)
        led_display.refresh_grid_pad(pad, self._grid_lighting, self._grid_led_cache)
        self._refresh_needed = True
        return True
    def _handle_custom_fader_pad(self, event, pad: int, fader: cm.CustomFader, slot: int) -> bool:
        key = (slot, fader.fader_index)
        pf = self._custom_fader_helpers.get(key)
        if pf is None:
            return True
        current = self._custom_fader_values.get(key, 0.0)
        new_value = pf.next_value_for_pad(pad, current)
        self._custom_fader_values[key] = new_value
        cc_val = max(0, min(127, int(round(new_value))))
        channel = fader.resolved_channel(int(self.state.get("midi_channel", 0))) & 0x0F
        event.status = 0xB0 | channel
        event.data1  = fader.cc_number
        event.data2  = cc_val
        for fader_pad in fader.pads():
            led_display.refresh_grid_pad(fader_pad, self._grid_lighting, self._grid_led_cache)
        return False

    def _process_xy_cc(self, event, cc: int, value: int) -> None:
        """Emit a generated XY CC for FL to process or learn (used by faders)."""
        channel = int(self.state["midi_channel"]) & 0x0F
        cc = int(cc) & 0x7F
        value = max(0, min(127, int(value)))
        event.midiId = midi.MIDI_CONTROLCHANGE
        event.status = midi.MIDI_CONTROLCHANGE | channel
        event.data1  = cc
        event.data2  = value
        self._sync_xy_cc_event_aliases(event, channel, cc, value)
        device.processMIDICC(event)

    def _xy_axis_event_id(self, cc: int) -> int | None:
        """Resolve the linked control event ID for an XY CC, or None if the CC
        isn't mapped to anything yet."""
        channel = int(self.state["midi_channel"]) & 0x0F
        try:
            base_id = midi.EncodeRemoteControlID(device.getPortNumber(), channel, int(cc) & 0x7F)
            event_id = int(device.findEventID(base_id, 0))
        except Exception:
            return None
        if event_id < 0 or event_id >= int(getattr(midi, "MaxInt", 2147483647)):
            return None
        return event_id

    def _emit_xy_axis(self, event, cc: int, value: int, *, allow_learn: bool) -> bool:
        """Send one XY axis. If the CC is already mapped, drive its control
        directly by event ID via automateEvent() — this has no per-callback
        contention, so both axes can move on a diagonal. If it's unmapped and
        learning is allowed this callback, fall back to processMIDICC() so FL's
        learn window can latch it.

        Returns True if it consumed the single per-callback processMIDICC()
        learn slot."""
        value = max(0, min(127, int(value)))
        event_id = self._xy_axis_event_id(cc)
        if event_id is not None:
            from_midi_max = int(getattr(midi, "FromMIDI_Max", 1073741824))
            out_value = round(value * (from_midi_max / 127))
            try:
                mixer.automateEvent(event_id, out_value, midi.REC_MIDIController, 0)
            except Exception:
                pass
            return False
        if allow_learn:
            self._process_xy_cc(event, cc, value)
            return True
        return False

    def _sync_xy_cc_event_aliases(self, event, channel: int, cc: int, value: int) -> None:
        try:
            port_part = (device.getPortNumber() + 1) << 6
        except Exception:
            port_part = 0
        from_midi_max = int(getattr(midi, "FromMIDI_Max", 1073741824))
        out_value = round(value * (from_midi_max / 127))
        for attr, attr_value in (
            ("midiId", midi.MIDI_CONTROLCHANGE),
            ("midiChan", channel),
            ("midiChanEx", channel + port_part),
            ("controlNum", cc),
            ("controlVal", value),
            ("inEv", value),
            ("outEv", out_value),
            ("isIncrement", 0),
            ("res", 1.0),
        ):
            try:
                setattr(event, attr, attr_value)
            except Exception:
                pass

    def _xy_page(self) -> int:
        return int(self.state.get("xy_page", XY_PAGE_XY)) % XY_PAGE_COUNT

    def _performance_modwheel_on(self) -> bool:
        return bool(self.state.get("performance_modwheel", False))

    def _handle_xy_pad(self, event, pad: int, velocity: int, pressed: bool) -> bool:
        """Handle XY pad mode — dispatches to page / selector pads."""
        page = self._xy_page()

        # Side column: always the page selector in XY mode.
        slot = ps.pad_to_slot(pad)
        if slot is not None:
            if pressed and slot < XY_PAGE_COUNT:
                self.state["xy_page"] = slot
                self._save_state()
                self._refresh_needed = True
            return True

        if xp.pad_to_xy(pad) is None:
            return True

        # Grid pads — dispatch by page
        if page == XY_PAGE_XY:
            if pressed:
                xy = xp.pad_to_xy(pad)
                x_val, y_val = xp.xy_values(*xy)
                # Send only the axes whose value actually changed, so a same-row
                # press emits X alone, a same-column press emits Y alone, and
                # re-pressing the current pad sends nothing.
                #
                # Each axis is emitted independently: a mapped axis is driven
                # directly by event ID via mixer.automateEvent() (no callback
                # contention — both axes move on a diagonal), while an unmapped
                # axis rides processMIDICC() so FL's learn window can catch it.
                # FL honours only one processMIDICC() per callback, so if BOTH
                # axes are unmapped on a diagonal, X learns now and Y is left for
                # the next single-axis move to learn.
                x_changed = x_val != self._xy_last_x_val
                y_changed = y_val != self._xy_last_y_val
                if x_changed:
                    self._xy_last_x_val = x_val
                if y_changed:
                    self._xy_last_y_val = y_val
                learn_slot_used = False
                if x_changed:
                    learn_slot_used = self._emit_xy_axis(
                        event, XY_PAD_X_CC, x_val, allow_learn=True
                    )
                if y_changed:
                    self._emit_xy_axis(
                        event, XY_PAD_Y_CC, 127 - y_val,
                        allow_learn=not learn_slot_used,
                    )
                self.active_pads[pad] = ("xy_pad", pad)
            else:
                self.active_pads.pop(pad, None)
        else:
            cc = xp.grid_fader_cc(pad, page)
            if cc is None:
                return True
            if pressed:
                self._xy_apply_fader(event, cc, pad)
                self.active_pads[pad] = ("xy_pad", pad)
            else:
                self.active_pads.pop(pad, None)

        self._refresh_needed = True
        return True

    def _xy_apply_fader(self, event, cc: int, pad: int) -> None:
        """Advance an XY/modwheel fader by one micro-step on the pressed pad and
        emit the resulting CC value."""
        pf = self._xy_faders.get(cc)
        if pf is None:
            return
        current = self._xy_fader_values.get(cc, 0.0)
        new_value = pf.next_value_for_pad(pad, current)
        self._xy_fader_values[cc] = new_value
        self._process_xy_cc(event, cc, int(round(new_value)))

    def _xy_lighting(self, pad: int) -> LedColor:
        page = self._xy_page()
        # Side column: always the page selector in XY mode.
        slot = ps.pad_to_slot(pad)
        if slot is not None:
            return ps.selector_lighting(pad, page, XY_PAGE_COUNT)
        # Grid pads
        if page == XY_PAGE_XY:
            return xp.xy_grid_lighting(
                pad,
                self._xy_last_x_val,
                self._xy_last_y_val,
            )
        cc = xp.grid_fader_cc(pad, page)
        if cc is None:
            return LedColor(PAD_DISABLED)
        return self._fader_pad_lighting(
            self._xy_faders[cc],
            XY_FADER_ON_COLOR,
            XY_FADER_OFF_COLOR,
            pad,
            self._xy_fader_values.get(cc, 0.0),
        )

    def _fader_pad_lighting(
        self,
        pad_fader: PadFader,
        on_color: int,
        off_color: int,
        pad: int,
        current_value: float,
    ) -> LedColor:
        """Generic firmware-style fader pad lighting shared by XY and custom faders.

        Stays MK1-agnostic (no explicit mk1) because custom-mode faders use
        user-loaded palette indices we can't predict; first-gen reverse-maps the
        index. If a specific caller (e.g. XY) wants a deliberate MK1 value, set it
        at that call site rather than here, so custom faders keep the old method.
        """
        state, micro = pad_fader.progress_for_pad(pad, current_value)
        if state == "off":
            return LedColor(PAD_DISABLED)
        if state == "dim":
            return LedColor(off_color)
        if state == "full":
            return LedColor(on_color)
        if micro is None:
            return LedColor(on_color)
        brightness = self._CUSTOM_FADER_MICRO_BRIGHTNESS[micro]
        return LedColor(on_color, led_display.dim_palette_rgb(on_color, brightness))

    def _handle_step_sequencer_pad(self, event, pad: int, velocity: int, pressed: bool) -> bool:
        """Handle step sequencer mode - delegates to step_sequencer module."""
        if ss.is_channel_toggle_pad(pad):
            if pressed:
                self._step_toggle_pad_pressed = pad
                self._step_toggle_hold_started = time.monotonic()
                self._step_toggle_hold_fired = False
            else:
                if self._step_toggle_pad_pressed == pad and not self._step_toggle_hold_fired:
                    if ss.toggle_pad(pad, self.state):
                        self._save_state()
                if self._step_toggle_pad_pressed == pad:
                    self._step_toggle_pad_pressed = None
                self._step_toggle_hold_started = 0.0
                self._step_toggle_hold_fired = False
                if self._step_lock_page_test_note_sent:
                    midi_channel = int(self.state["midi_channel"]) & 0x0F
                    channels.midiNoteOn(self._step_lock_page_channel, self._LOCK_PAGE_TEST_NOTE, 0, midi_channel)
                    self._step_lock_page_test_note_sent = False
                self._step_lock_page_channel = None
        elif self._step_lock_page_channel is not None:
            if pressed:
                self._handle_step_lock_page_press(pad)
        else:
            if pressed and ss.toggle_pad(pad, self.state):
                self._save_state()
        self._refresh_surface()
        self._refresh_needed = False
        return True

    # Lock routing page (step sequencer channel hold)
    # Layout (8-wide grid, top→bottom):
    #   Row 8 (pad 81): Note mode context
    #   Row 7 (pad 71): empty
    #   Row 6 (pads 61–68): custom contexts 0–7
    #   Row 5 (pads 51–58): custom contexts 8–15
    _LOCK_PAGE_NOTE_PAD = 81
    _LOCK_PAGE_TEST_NOTE = 72  # C5
    _LOCK_PAGE_CUSTOM_ROW0 = tuple(range(61, 69))   # custom 0–7
    _LOCK_PAGE_CUSTOM_ROW1 = tuple(range(51, 59))   # custom 8–15

    def _step_lock_page_context_for_pad(self, pad: int) -> str | None:
        if pad == self._LOCK_PAGE_NOTE_PAD:
            return cl.NOTE_CONTEXT
        if pad in self._LOCK_PAGE_CUSTOM_ROW0:
            return cl.custom_context(pad - 61)
        if pad in self._LOCK_PAGE_CUSTOM_ROW1:
            return cl.custom_context(pad - 51 + 8)
        return None

    def _handle_step_lock_page_press(self, pad: int) -> None:
        channel = self._step_lock_page_channel
        ctx = self._step_lock_page_context_for_pad(pad)
        if ctx is None or channel is None:
            return
        if cl.is_locked(self.state, ctx) and cl.get(self.state, ctx) == channel:
            cl.clear(self.state, ctx)
        else:
            cl.set_lock(self.state, ctx, channel)
        self._save_state()

    def _step_lock_page_lighting(self, pad: int) -> LedColor:
        channel = self._step_lock_page_channel
        ctx = self._step_lock_page_context_for_pad(pad)
        if ctx is None:
            return LedColor(PAD_DISABLED)
        if channel is not None and cl.is_locked(self.state, ctx) and cl.get(self.state, ctx) == channel:
            return LedColor(LP3_MENU_LOCKED)
        return LedColor(LP3_MENU_INACTIVE)

    # Performance-mode pad handling
    def _handle_performance_pad(self, event, pad: int, velocity: int, pressed: bool) -> None:
        consumed = pm.try_empty_pad_fallback(
            pad, velocity, pressed,
            self.state,
            self.performance_direct_pads,
            self.active_notes,
            lambda p: fm.fpc_assignment_for_performance_pad(
                p,
                self.state,
                self._selected_channel,
            ),
            lambda channel_index, pad_index: fm.fpc_pad_note(channel_index, pad_index),
            int(self.state["midi_channel"]),
        )
        if consumed:
            return
        if pressed:
            pm.trigger_pad(pad, velocity, self.state)
            self._sync_performance_view()
            self._save_state()
            return
        # release side of non-direct pads — nothing to do for clip pads
        self.performance_direct_pads.pop(pad, None)
    # Plugin pad override (Gross Beat, etc.)
    def _active_plugin_pad_override(self) -> str | None:
        if self.surface_mode != MODE_FPC or self.settings_visible:
            return None
        override_id = self._focused_plugin_pad_override_id()
        if override_id == self._suppressed_plugin_override_id:
            return None
        return override_id
    def _focused_plugin_pad_override_id(self) -> str | None:
        return PLUGIN_PAD_OVERRIDE_IDS.get(self._focused_plugin_name())
    def _focused_plugin_name(self) -> str:
        if not self._plugin_window_focused():
            return ""
        try:
            return str(ui.getFocusedPluginName() or "").strip().lower()
        except Exception:
            return ""
    def _plugin_window_focused(self) -> bool:
        for wid in (WID_PLUGIN_EFFECT, WID_PLUGIN_GENERATOR, WID_PLUGIN):
            try:
                if int(ui.getFocused(wid)) == 1:
                    return True
            except Exception:
                continue
        return False
    def _handle_plugin_pad_override(
        self, override_id: str, event, pad: int, velocity: int, pressed: bool
    ) -> bool:
        if override_id == PLUGIN_PAD_OVERRIDE_GROSS_BEAT:
            return self._handle_gross_beat_pad(event, pad, pressed)
        return False
    def _plugin_pad_override_lighting(
        self, override_id: str, pad: int
    ) -> LedColor:
        if override_id != PLUGIN_PAD_OVERRIDE_GROSS_BEAT:
            return LedColor(PAD_DISABLED)
        return self._gross_beat_lighting(pad)
    def _handle_gross_beat_pad(self, _event, pad: int, pressed: bool) -> bool:
        if fm.is_fpc_selector(pad):
            return False
        if pressed:
            self._plugin_override_held_pads.add(pad)
        else:
            self._plugin_override_held_pads.discard(pad)
            return True
        if pad == GROSS_BEAT_TOGGLE_PAD:
            self.state["gross_beat_slot_mode"] = (
                "volume"
                if self._gross_beat_slot_mode() == "time"
                else "time"
            )
            self._save_state()
            self._refresh_surface()
            self._refresh_needed = False
            return True
        target = self._focused_plugin_target()
        if target is None:
            return True
        spec = self._gross_beat_spec(target)
        if spec is None:
            return True
        if pad in GROSS_BEAT_SLOT_PADS:
            slot_index = GROSS_BEAT_SLOT_PADS.index(pad)
            self._gross_beat_trigger_slot(target, spec, slot_index)
            self._refresh_surface()
            self._refresh_needed = False
            return True
        if self._gross_beat_fader.contains(pad):
            current_value = self._plugin_param_value(target, spec["mix_param"])
            new_value = self._gross_beat_fader.next_value_for_pad(pad, current_value)
            self._plugin_set_param_value(target, spec["mix_param"], new_value)
            self._refresh_surface()
            self._refresh_needed = False
            return True
        return True
    def _gross_beat_lighting(self, pad: int) -> LedColor:
        slot_mode = self._gross_beat_slot_mode()
        slot_color = (
            GROSS_BEAT_TIME_COLOR
            if slot_mode == "time"
            else GROSS_BEAT_VOLUME_COLOR
        )
        selected_slot_color = (
            GROSS_BEAT_VOLUME_COLOR
            if slot_mode == "time"
            else GROSS_BEAT_TIME_COLOR
        )
        if pad == GROSS_BEAT_TOGGLE_PAD:
            if pad in self._plugin_override_held_pads:
                return LedColor(PAD_ACTION)
            return LedColor(GROSS_BEAT_VOLUME_COLOR)
        if self._gross_beat_fader.contains(pad):
            target = self._focused_plugin_target()
            spec = self._gross_beat_spec(target) if target is not None else None
            current_value = 0.0
            if spec is not None:
                current_value = self._plugin_param_value(target, spec["mix_param"])
            return LedColor(
                self._gross_beat_fader.palette_for_pad(
                    pad,
                    current_value,
                    dim_palette=GROSS_BEAT_FADER_DIM_COLOR,
                    bright_palettes=GROSS_BEAT_FADER_MICRO_COLORS,
                    off_palette=LP3_BACKGROUND_OFF,
                )
            )
        if pad in GROSS_BEAT_SLOT_PADS:
            target = self._focused_plugin_target()
            spec = self._gross_beat_spec(target) if target is not None else None
            slot_index = GROSS_BEAT_SLOT_PADS.index(pad)
            active_slot = self._gross_beat_active_slot(target, spec)
            if active_slot == slot_index:
                return LedColor(selected_slot_color)
            if pad in self._plugin_override_held_pads:
                return LedColor(PAD_ACTION)
            return LedColor(slot_color)
        if fm.is_fpc_selector(pad):
            return LedColor(fm.fpc_selector_color(
                pad,
                self.state,
                lambda: fm.selected_channel_is_fpc(self._selected_channel()),
                self._selected_channel,
                hide_if_not_fpc=True,
            ))
        if pad in SETTINGS_GRID_PADS:
            return LedColor(LP3_BACKGROUND_OFF)
        return LedColor(PAD_DISABLED)
    def _gross_beat_slot_mode(self) -> str:
        mode = str(self.state.get("gross_beat_slot_mode", "time")).lower()
        return "volume" if mode == "volume" else "time"
    def _focused_plugin_target(self) -> tuple[int, int, int] | None:
        try:
            focused_id = int(ui.getFocusedFormID())
        except Exception:
            return None
        try:
            if int(ui.getFocused(WID_PLUGIN_EFFECT)) == 1:
                encoded = (focused_id >> 16) & 0xFFFFFFFF
                return encoded >> 6, encoded & 0x3F, focused_id
        except Exception:
            pass
        try:
            if int(ui.getFocused(WID_PLUGIN_GENERATOR)) == 1 and focused_id >= 0:
                return focused_id, -1, focused_id
        except Exception:
            pass
        try:
            if int(ui.getFocused(WID_PLUGIN)) == 1 and focused_id >= 0:
                return focused_id, -1, focused_id
        except Exception:
            pass
        return None
    def _gross_beat_spec(self, target: tuple[int, int, int] | None) -> dict | None:
        if target is None:
            return None
        index, slot_index, target_id = target
        key = (PLUGIN_PAD_OVERRIDE_GROSS_BEAT, index, slot_index, target_id)
        cached = self._plugin_param_specs.get(key)
        if cached is not None:
            return cached
        param_names: list[str] = []
        try:
            param_count = int(plugins.getParamCount(index, slot_index))
        except Exception:
            return None
        for param_index in range(max(0, param_count)):
            try:
                param_names.append(str(plugins.getParamName(param_index, index, slot_index) or ""))
            except Exception:
                param_names.append("")
        spec = self._discover_gross_beat_spec(param_names)
        if spec is None:
            return None
        self._plugin_param_specs[key] = spec
        return spec
    def _discover_gross_beat_spec(self, param_names: list[str]) -> dict | None:
        time_slots: dict[int, int] = {}
        volume_slots: dict[int, int] = {}
        time_selector: int | None = None
        volume_selector: int | None = None
        mix_param: int | None = None
        for param_index, param_name in enumerate(param_names):
            normalized = self._normalize_param_name(param_name)
            slot_number = self._extract_slot_number(normalized)
            if mix_param is None and "volume mix" in normalized:
                mix_param = param_index
            if "time" in normalized and "slot" in normalized:
                if slot_number is None and time_selector is None:
                    time_selector = param_index
                elif slot_number is not None and 1 <= slot_number <= 36:
                    time_slots[slot_number - 1] = param_index
                continue
            if "volume" in normalized and "slot" in normalized:
                if slot_number is None and volume_selector is None:
                    volume_selector = param_index
                elif slot_number is not None and 1 <= slot_number <= 36:
                    volume_slots[slot_number - 1] = param_index
        if mix_param is None:
            for param_index, param_name in enumerate(param_names):
                normalized = self._normalize_param_name(param_name)
                if normalized in ("mix", "mix level", "vol mix"):
                    mix_param = param_index
                    break
        if mix_param is None:
            return None
        if len(time_slots) == 36 and len(volume_slots) == 36:
            return {
                "mode": "button_matrix",
                "time_slots": [time_slots[index] for index in range(36)],
                "volume_slots": [volume_slots[index] for index in range(36)],
                "mix_param": mix_param,
            }
        if time_selector is not None and volume_selector is not None:
            return {
                "mode": "selector_pair",
                "time_selector": time_selector,
                "volume_selector": volume_selector,
                "mix_param": mix_param,
            }
        return None
    def _normalize_param_name(self, name: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(name or "").strip().lower()).strip()
    def _extract_slot_number(self, normalized_name: str) -> int | None:
        match = re.search(r"\bslot\s+(\d{1,2})\b", normalized_name)
        if match is not None:
            return int(match.group(1))
        match = re.search(r"\b(\d{1,2})\b", normalized_name)
        if match is not None:
            return int(match.group(1))
        return None
    def _gross_beat_trigger_slot(
        self,
        target: tuple[int, int, int],
        spec: dict,
        slot_index: int,
    ) -> None:
        slot_mode = self._gross_beat_slot_mode()
        if spec["mode"] == "button_matrix":
            if slot_mode == "time":
                param_index = spec["time_slots"][slot_index]
            else:
                param_index = spec["volume_slots"][slot_index]
            self._plugin_set_param_value(target, param_index, 1.0)
            return
        selector_param = (
            spec["time_selector"]
            if slot_mode == "time"
            else spec["volume_selector"]
        )
        self._plugin_set_param_value(target, selector_param, slot_index / 35.0)
    def _gross_beat_active_slot(
        self,
        target: tuple[int, int, int] | None,
        spec: dict | None,
    ) -> int | None:
        if target is None or spec is None:
            return None
        slot_mode = self._gross_beat_slot_mode()
        if spec["mode"] == "button_matrix":
            slot_params = spec["time_slots"] if slot_mode == "time" else spec["volume_slots"]
            strongest_index = 0
            strongest_value = -1.0
            for slot_index, param_index in enumerate(slot_params):
                value = self._plugin_param_value(target, param_index)
                if value > strongest_value:
                    strongest_index = slot_index
                    strongest_value = value
            return strongest_index
        selector_param = (
            spec["time_selector"]
            if slot_mode == "time"
            else spec["volume_selector"]
        )
        value = self._plugin_param_value(target, selector_param)
        return _clamp(int(round(value * 35.0)), 0, 35)
    def _plugin_param_value(self, target: tuple[int, int, int], param_index: int) -> float:
        index, slot_index, _target_id = target
        try:
            return float(plugins.getParamValue(param_index, index, slot_index))
        except Exception:
            return 0.0
    def _plugin_set_param_value(
        self,
        target: tuple[int, int, int],
        param_index: int,
        value: float,
    ) -> None:
        index, slot_index, _target_id = target
        try:
            plugins.setParamValue(float(value), param_index, index, slot_index, midi.PIM_None)
        except Exception:
            return
    # LED rendering
    def _refresh_surface(self) -> None:
        # Note-key pulse when Note mode is channel-locked.
        lights_out = self._lights_effectively_out()
        note_lock_pulse = (
            cl.is_locked(self.state, cl.NOTE_CONTEXT)
            and not lights_out
        )
        # Custom sidebar entries pulse when their custom index is locked (LP3 hw pulse).
        custom_pulse_pads = (
            self._locked_custom_selector_pads()
            if self.surface_mode == MODE_CUSTOM
            and self.device_family != DEVICE_FAMILY_MK2
            and not lights_out
            else set()
        )
        led_display.refresh_surface(
            self._grid_lighting,
            self._top_color,
            self._grid_led_cache,
            self._top_led_cache,
            pulse_top_ccs={self._top_note_mode} if note_lock_pulse else None,
            pulse_grid_pads=custom_pulse_pads or None,
        )
        if note_lock_pulse and self.device_family != DEVICE_FAMILY_MK2:
            led_display.send_top_led_pulse(self._top_note_mode, LP3_MENU_LOCKED)
        for pad in custom_pulse_pads:
            # Side column is CC on LP3, so the CC-channel-3 pulse path applies.
            led_display.send_top_led_pulse(pad, LP3_MENU_LOCKED)
    def _locked_custom_selector_pads(self) -> set[int]:
        pads: set[int] = set()
        for slot, pad in enumerate(ps.SELECTOR_PADS):
            display_index = self._custom_slot_display_index(slot)
            if display_index is None:
                continue
            idx = min(display_index, len(self._custom_modes) - 1)
            if cl.is_locked(self.state, cl.custom_context(idx)):
                pads.add(pad)
        return pads
    def _send_software_pulse_frame(self) -> None:
        # 120 BPM → 2-second cycle (one period = two beats); skewed triangle: 25% rise, 75% fall
        phase = (time.monotonic() - self._pulse_start) % 2.0 / 2.0
        brightness = led_display.software_pulse_brightness(phase)
        fr, fg, fb = NOTE_LOCK_PULSE_RGB
        rgb = (
            int(round(fr * brightness)),
            int(round(fg * brightness)),
            int(round(fb * brightness)),
        )
        led_display.send_top_led_rgb(self._top_note_mode, rgb)
    def _custom_slot_display_index(self, slot: int) -> int | None:
        """Which custom-mode index a sidebar slot currently displays, or None."""
        n_slots = len(ps.SELECTOR_PADS)
        if not (0 <= slot < len(self._custom_modes)):
            return None
        if self._custom_mode_index % n_slots == slot:
            return self._custom_mode_index
        return slot
    def _custom_mode_lighting(self, pad: int) -> LedColor:
        # Custom modes are user-loaded .syx files, so their palette indices are
        # arbitrary and unpredictable — we can't assign meaningful page-level MK1
        # values here. Every LedColor below is left with mk1=None on purpose, so
        # first-gen hardware derives red/green from the index's MK3-palette RGB
        # (the shared RGB pipeline). Do not add explicit mk1= values here.
        # Persistent selector sidebar: right column shows one LED per available mode slot.
        slot = ps.pad_to_slot(pad)
        if slot is not None:
            display_index = self._custom_slot_display_index(slot)
            if display_index is None:
                return LedColor(PAD_DISABLED)
            n_slots = len(ps.SELECTOR_PADS)
            mode = self._custom_modes[min(display_index, len(self._custom_modes) - 1)]
            active = self._custom_mode_index % n_slots == slot
            color = mode.on_color if active else LP3_MENU_INACTIVE
            return LedColor(color)
        # Grid pads: show the pad's off_color from the active custom mode
        mode = self._active_custom_mode()
        if mode is None:
            return LedColor(PAD_DISABLED)
        fader = mode.fader_for_pad(pad)
        if fader is not None:
            key = (mode.slot, fader.fader_index)
            pf = self._custom_fader_helpers.get(key)
            if pf is None:
                return LedColor(PAD_DISABLED)
            current = self._custom_fader_values.get(key, 0.0)
            return self._custom_fader_lighting(pf, fader, pad, current)
        cp = mode.pad(pad)
        if cp is None or cp.is_off:
            return LedColor(PAD_DISABLED)
        if pad in self.active_pads:
            return LedColor(mode.on_color)
        return LedColor(cp.off_color)
    def _custom_fader_lighting(
        self,
        pad_fader: PadFader,
        fader: cm.CustomFader,
        pad: int,
        current_value: float,
    ) -> LedColor:
        return self._fader_pad_lighting(
            pad_fader, fader.on_color, fader.off_color, pad, current_value
        )
    def _step_sequencer_grid_visible(self) -> bool:
        """True when the step-sequencer grid (ss.lighting) is the active grid:
        Step Seq mode (outside the lock page), or the Session fallback when FL
        performance mode is unavailable."""
        if self.settings_visible:
            return False
        if self.surface_mode == MODE_STEP_SEQ:
            return self._step_lock_page_channel is None
        if self.surface_mode == MODE_PERFORMANCE:
            return not pm.performance_available()
        return False

    def _grid_lighting(self, pad: int) -> LedColor:
        if self._lights_effectively_out():
            return LedColor(PAD_DISABLED)
        if self.settings_visible:
            return nm.settings_color(pad, self.state)
        if self.surface_mode == MODE_XY_PAD:
            return self._xy_lighting(pad)
        if self.surface_mode == MODE_STEP_SEQ:
            if self._step_lock_page_channel is not None:
                return self._step_lock_page_lighting(pad)
            return ss.lighting(pad, self.state)
        if self.surface_mode == MODE_PERFORMANCE:
            if not pm.performance_available():
                return ss.lighting(pad, self.state)
            if self._performance_modwheel_on() and ps.pad_to_slot(pad) is not None:
                return self._fader_pad_lighting(
                    self._performance_modwheel_fader,
                    performance_modwheel_COLOR,
                    XY_FADER_OFF_COLOR,
                    pad,
                    self._xy_fader_values.get(performance_modwheel_CC, 0.0),
                )
            return pm.performance_lighting(
                pad, self.state,
                lambda p: fm.fpc_performance_lighting(
                    p, self.state,
                    self._selected_channel,
                    self._is_note_active,
                    self._is_fpc_pad_recently_active,
                ),
            )
        override_id = self._active_plugin_pad_override()
        if override_id is not None:
            return self._plugin_pad_override_lighting(override_id, pad)
        if self.surface_mode == MODE_CUSTOM:
            return self._custom_mode_lighting(pad)
        if self.surface_mode == MODE_FPC:
            return fm.fpc_lighting(
                pad, self.state,
                self._selected_channel,
                lambda: fm.selected_channel_is_fpc(self._selected_channel()),
                self._is_note_active,
                self._is_fpc_pad_recently_active,
            )
        if self.surface_mode == MODE_NOTE:
            return nm.note_mode_lighting(
                pad, self.state,
                self._is_note_active,
                self._channel_for_pad,
                self._playable_pads(),
            )
        return LedColor(PAD_DISABLED)
    def _top_color(self, cc: int) -> int:
        # Returns a bare palette index; refresh_surface normalises it to a
        # LedColor (top CCs don't yet set explicit MK1 values).
        if self._lights_effectively_out():
            return PAD_DISABLED
        if cc == self._top_octave_down:
            if self.surface_mode == MODE_XY_PAD:
                page = self._xy_page()
                return LP3_ARROW_OCTAVE_ACTIVE if page < XY_PAGE_COUNT - 1 else LP3_ARROW_INACTIVE
            if self.surface_mode == MODE_STEP_SEQ:
                return ss.arrow_color(
                    ss.remaining_channel_steps(-1, self.state),
                    LP3_ARROW_OCTAVE_ACTIVE,
                )
            if self.surface_mode == MODE_PERFORMANCE:
                if not pm.performance_available():
                    return ss.arrow_color(
                        ss.remaining_channel_steps(-1, self.state),
                        LP3_ARROW_OCTAVE_ACTIVE,
                    )
                return pm.performance_arrow_color(
                    pm.remaining_track_steps(-1, self.state, self.surface_mode),
                    self.surface_mode,
                )
            if self.surface_mode == MODE_NOTE:
                return nm.arrow_color(
                    self.surface_mode,
                    nm.remaining_octave_steps(self.state, -1, self.surface_mode),
                    LP3_ARROW_OCTAVE_ACTIVE,
                    out_of_range=not nm.is_window_valid(self.state),
                )
            return LP3_ARROW_INACTIVE
        if cc == self._top_octave_up:
            if self.surface_mode == MODE_XY_PAD:
                page = self._xy_page()
                return LP3_ARROW_OCTAVE_ACTIVE if page > 0 else LP3_ARROW_INACTIVE
            if self.surface_mode == MODE_STEP_SEQ:
                return ss.arrow_color(
                    ss.remaining_channel_steps(1, self.state),
                    LP3_ARROW_OCTAVE_ACTIVE,
                )
            if self.surface_mode == MODE_PERFORMANCE:
                if not pm.performance_available():
                    return ss.arrow_color(
                        ss.remaining_channel_steps(1, self.state),
                        LP3_ARROW_OCTAVE_ACTIVE,
                    )
                return pm.performance_arrow_color(
                    pm.remaining_track_steps(1, self.state, self.surface_mode),
                    self.surface_mode,
                )
            if self.surface_mode == MODE_NOTE:
                return nm.arrow_color(
                    self.surface_mode,
                    nm.remaining_octave_steps(self.state, 1, self.surface_mode),
                    LP3_ARROW_OCTAVE_ACTIVE,
                    out_of_range=not nm.is_window_valid(self.state),
                )
            return LP3_ARROW_INACTIVE
        if cc == self._top_pan_left:
            if self.surface_mode == MODE_PERFORMANCE:
                if not pm.performance_available():
                    return ss.arrow_color(
                        ss.remaining_step_pages(-1, self.state),
                        LP3_ARROW_PAN_ACTIVE,
                    )
                return pm.performance_arrow_color(
                    pm.remaining_block_steps(-1, self.state, self.surface_mode),
                    self.surface_mode,
                )
            if self.surface_mode == MODE_STEP_SEQ:
                return ss.arrow_color(
                    ss.remaining_step_pages(-1, self.state),
                    LP3_ARROW_PAN_ACTIVE,
                )
            if self.surface_mode == MODE_FPC:
                if fm.remaining_fpc_page_steps(-1, self.state) > 0:
                    return LP3_ARROW_PAN_ACTIVE
                return LP3_ARROW_INACTIVE
            if self.surface_mode == MODE_NOTE:
                return nm.arrow_color(
                    self.surface_mode,
                    nm.remaining_pan_steps(self.state, -1, self.surface_mode),
                    LP3_ARROW_PAN_ACTIVE,
                    out_of_range=not nm.is_window_valid(self.state),
                )
            return LP3_ARROW_INACTIVE
        if cc == self._top_pan_right:
            if self.surface_mode == MODE_PERFORMANCE:
                if not pm.performance_available():
                    return ss.arrow_color(
                        ss.remaining_step_pages(1, self.state),
                        LP3_ARROW_PAN_ACTIVE,
                    )
                return pm.performance_arrow_color(
                    pm.remaining_block_steps(1, self.state, self.surface_mode),
                    self.surface_mode,
                )
            if self.surface_mode == MODE_STEP_SEQ:
                return ss.arrow_color(
                    ss.remaining_step_pages(1, self.state),
                    LP3_ARROW_PAN_ACTIVE,
                )
            if self.surface_mode == MODE_FPC:
                if fm.remaining_fpc_page_steps(1, self.state) > 0:
                    return LP3_ARROW_PAN_ACTIVE
                return LP3_ARROW_INACTIVE
            if self.surface_mode == MODE_NOTE:
                return nm.arrow_color(
                    self.surface_mode,
                    nm.remaining_pan_steps(self.state, 1, self.surface_mode),
                    LP3_ARROW_PAN_ACTIVE,
                    out_of_range=not nm.is_window_valid(self.state),
                )
            return LP3_ARROW_INACTIVE
        if cc == self._top_performance:
            if self.surface_mode == MODE_PERFORMANCE:
                if not pm.performance_available():
                    return LP3_MENU_ACTIVE
                if bool(self.state.get("performance_direct_audio", False)):
                    return LP3_PERFORMANCE_HYBRID
                return LP3_MENU_ACTIVE
            if self.surface_mode == MODE_XY_PAD:
                return LP3_MENU_ACTIVE
            if pm.performance_available():
                return LP3_PERFORMANCE_READY
            return LP3_MENU_INACTIVE
        if cc == self._top_note_mode:
            if cl.is_locked(self.state, cl.NOTE_CONTEXT):
                return LP3_MENU_LOCKED
            return LP3_MENU_ACTIVE if self.surface_mode in (MODE_NOTE, MODE_CUSTOM) else LP3_MENU_INACTIVE
        if cc == self._top_fpc_mode:
            if self.surface_mode in (MODE_FPC, MODE_STEP_SEQ):
                return LP3_MENU_ACTIVE
            return LP3_MENU_INACTIVE
        if cc == self._top_record_arm:
            return LP3_MENU_ACTIVE if transport.isRecording() else LP3_MENU_INACTIVE
        return LP3_MENU_INACTIVE
    # Note mapping
    def _note_for_pad(self, pad: int) -> int:
        if self.surface_mode == MODE_FPC:
            return fm.fpc_note_for_pad(pad, self.state, self._selected_channel)
        return nm.note_for_pad(pad, self.state)
    def _channel_for_pad(self, pad: int) -> int:
        if self.surface_mode == MODE_FPC:
            assignment = fm.fpc_assignment_for_pad(pad, self.state, self._selected_channel)
            if assignment is not None:
                return assignment[0]
        return self._selected_channel()
    # Channel locking
    def _host_selected_channel(self) -> int:
        try:
            selected = channels.selectedChannel(0, 0, 1)
            if selected >= 0:
                return selected
            return channels.channelNumber(0, 0)
        except Exception:
            return -1
    def _lock_context(self) -> str:
        """The channel-lock context for the active surface."""
        if self.surface_mode == MODE_CUSTOM:
            return cl.custom_context(self._custom_mode_index)
        if self.surface_mode == MODE_STEP_SEQ:
            return cl.STEP_CONTEXT
        if self.surface_mode == MODE_PERFORMANCE and not pm.performance_available():
            return cl.STEP_CONTEXT
        return cl.NOTE_CONTEXT
    def _selected_channel(self) -> int:
        return cl.resolve(self.state, self._lock_context(), self._host_selected_channel())
    def _channel_lock_enabled(self) -> bool:
        return cl.is_locked(self.state, self._lock_context())
    def _toggle_channel_lock(self) -> None:
        ctx = self._lock_context()
        now_locked = cl.toggle(self.state, ctx, self._host_selected_channel())
        if now_locked:
            self._pulse_start = time.monotonic()
        self._save_state()
    # Active-note tracking
    def _is_note_active(self, channel_index: int, note: int) -> bool:
        return self.active_notes.get((channel_index, note), 0) > 0
    def _mark_pad_recently_active(self, pad: int) -> None:
        try:
            self._fpc_active_pad_order.remove(pad)
        except ValueError:
            pass
        self._fpc_active_pad_order.append(pad)
    def _drop_recent_active_pad(self, pad: int) -> None:
        try:
            self._fpc_active_pad_order.remove(pad)
        except ValueError:
            pass
    def _is_fpc_pad_recently_active(self, pad: int, channel_index: int, note: int) -> bool:
        if not self._fpc_active_pad_order:
            return False
        recent_pad = self._fpc_active_pad_order[-1]
        if recent_pad != pad:
            return False
        return self.active_pads.get(recent_pad) == (channel_index, note)
    def _drop_active_note(self, channel_index: int, note: int) -> None:
        key   = (channel_index, note)
        count = self.active_notes.get(key, 0)
        if count <= 1:
            self.active_notes.pop(key, None)
        else:
            self.active_notes[key] = count - 1
    def _release_all_notes(self) -> None:
        for pad_info in self.active_pads.values():
            if isinstance(pad_info, tuple) and len(pad_info) == 2:
                first, second = pad_info
                if first != "xy_pad":
                    channel_index, note = first, second
                    channels.midiNoteOn(
                        channel_index, note, 0,
                        int(self.state["midi_channel"]),
                    )
        self.active_pads.clear()
        self.active_notes.clear()
        self._fpc_active_pad_order.clear()
        pm.release_performance_direct_notes(
            self.performance_direct_pads,
            int(self.state["midi_channel"]),
        )
        self._plugin_override_held_pads.clear()
    # Surface layout / helpers
    def _playable_pads(self) -> tuple[int, ...]:
        if self.surface_mode in (MODE_NOTE, MODE_PERFORMANCE, MODE_XY_PAD, MODE_STEP_SEQ):
            return PLAYABLE_PADS
        if self.surface_mode == MODE_CUSTOM:
            return PLAYABLE_PADS  # include side column for mode selector
        return SETTINGS_GRID_PADS  # FPC
    def _note_input_pads(self) -> tuple[int, ...]:
        if self._side_column_is_cc:
            return SETTINGS_GRID_PADS
        return self._playable_pads()
    def _cc_input_pads(self) -> tuple[int, ...]:
        if self._side_column_is_cc:
            return SIDE_COLUMN_PADS
        return ()
    def _apply_surface_layout(self) -> None:
        if self.device_family in (DEVICE_FAMILY_LPX, DEVICE_FAMILY_LPM3):
            layout = LP3_PROGRAMMER_MODE
            led_display.set_layout(layout)
        elif self.device_family == DEVICE_FAMILY_MK1:
            # MK1 has no SysEx layout command; XY mode (CC0=1) is the default
            # and is already active after reset.  No set_layout needed.
            pass
        else:
            layout = LAYOUT_SESSION if self.surface_mode == MODE_PERFORMANCE else LAYOUT_USER_2
            led_display.set_layout(layout)
        self._grid_led_cache.clear()
        self._top_led_cache.clear()
    def _enter_performance_mode(self) -> None:
        if self.surface_mode == MODE_PERFORMANCE:
            if pm.performance_available():
                self._sync_performance_view()
            else:
                ss.sync_channel_rack_view(self.state)
            return
        self.surface_mode     = MODE_PERFORMANCE
        self.settings_visible = False
        self._release_all_notes()
        self._apply_surface_layout()
        if pm.performance_available():
            self._launch_map_ready = pm.update_launch_map(self._launch_map_ready)
            self._sync_performance_view()
        else:
            ss.sync_channel_rack_view(self.state)
        self._save_state()

    def _enter_xy_pad_mode(self) -> None:
        self.surface_mode     = MODE_XY_PAD
        self.settings_visible = False
        self._release_all_notes()
        self._apply_surface_layout()
        self._save_state()
        _log("XY pad mode")

    def _enter_step_sequencer_mode(self) -> None:
        self.surface_mode     = MODE_STEP_SEQ
        self.settings_visible = False
        self._release_all_notes()
        self._apply_surface_layout()
        ss.sync_channel_rack_view(self.state)
        self._save_state()
        _log("step sequencer mode")

    def _restore_surface_mode(self) -> None:
        """Restore the surface mode saved in state, falling back to Note mode."""
        self._restoring_mode = True
        try:
            mode = self.state.get("surface_mode", MODE_NOTE)
            if mode == MODE_FPC:
                self._enter_fpc_mode(suppress_gross_beat=False)
            elif mode == MODE_PERFORMANCE:
                self._enter_performance_mode()
            elif mode == MODE_XY_PAD:
                self._enter_xy_pad_mode()
            elif mode == MODE_STEP_SEQ:
                self._enter_step_sequencer_mode()
            elif mode == MODE_CUSTOM:
                saved_index = int(self.state.get("custom_mode_index", 0))
                if self._custom_modes:
                    self._custom_mode_index = max(0, min(len(self._custom_modes) - 1, saved_index))
                if self._live_custom_mode_reading and saved_index >= len(self._custom_modes):
                    self._pending_custom_mode_index = saved_index
                self._enter_custom_mode_selector()
            else:
                self._enter_note_mode()
        finally:
            self._restoring_mode = False

    def _send_mk1_duty_cycle(self) -> None:
        """Send the MK1 'set duty cycle' (brightness) SysEx-free CC, per the
        Launchpad S PRM. Brightness = numerator/denominator, configured via
        MK1_DUTY_CYCLE_NUMERATOR / MK1_DUTY_CYCLE_DENOMINATOR in constants.py."""
        numerator   = MK1_DUTY_CYCLE_NUMERATOR
        denominator = MK1_DUTY_CYCLE_DENOMINATOR
        if numerator < 9:
            cc, value = 0x1E, 16 * (numerator - 1) + (denominator - 3)
        else:
            cc, value = 0x1F, 16 * (numerator - 9) + (denominator - 3)
        device.midiOutMsg(midi.MIDI_CONTROLCHANGE, SESSION_CHANNEL, cc, value)

    def _enter_session_default(self) -> None:
        """Session key default surface: Performance when FL performance mode is
        on, otherwise the XY/faders page."""
        if pm.performance_available():
            self._enter_performance_mode()
        else:
            self._enter_xy_pad_mode()

    def _cycle_session_modes(self) -> None:
        """Session key cycle.
        Performance available: Performance ↔ XY/faders.
        Performance unavailable: XY/faders only.
        (Step sequencer now lives on the FPC key.)
        """
        if pm.performance_available() and self.surface_mode == MODE_PERFORMANCE:
            self._enter_xy_pad_mode()
        else:
            self._enter_session_default()

    def _configure_device_profile(self) -> None:
        try:
            self.device_name = str(device.getName())
        except Exception:
            self.device_name = "Unknown Launchpad"
        try:
            self.device_id = _normalize_device_id(device.getDeviceID())
        except Exception:
            self.device_id = b""
        self.device_family = _detect_device_family(self.device_id, self.device_name)
        if self.device_family == DEVICE_FAMILY_LPX:
            self.device_label = "Launchpad X"
            self._side_column_is_cc = True
            self._top_ccs = LP3_TOP_CCS
            self._top_performance = LP3_TOP_SESSION
            self._top_note_mode = LP3_TOP_NOTE
            self._top_fpc_mode = LP3_TOP_CUSTOM
            self._top_octave_down = LP3_TOP_DOWN
            self._top_octave_up = LP3_TOP_UP
            self._top_pan_left = LP3_TOP_LEFT
            self._top_pan_right = LP3_TOP_RIGHT
            self._top_record_arm = LP3_TOP_RECORD
            led_display.configure_surface(
                sysex_prefix=(0xF0, 0x00, 0x20, 0x29, 0x02, 0x0C),
                top_ccs=self._top_ccs,
                side_column_is_cc=True,
                mode="lp3",
                color_saturation=FPC_COLOR_SATURATION_LP3,
                color_gamma=FPC_COLOR_GAMMA_LP3,
            )
        elif self.device_family == DEVICE_FAMILY_LPM3:
            self.device_label = "Launchpad Mini MK3"
            self._side_column_is_cc = True
            self._top_ccs = LP3_TOP_CCS
            self._top_performance = LP3_TOP_SESSION
            self._top_note_mode = LP3_TOP_NOTE
            self._top_fpc_mode = LP3_TOP_CUSTOM
            self._top_octave_down = LP3_TOP_DOWN
            self._top_octave_up = LP3_TOP_UP
            self._top_pan_left = LP3_TOP_LEFT
            self._top_pan_right = LP3_TOP_RIGHT
            self._top_record_arm = LP3_TOP_RECORD
            led_display.configure_surface(
                sysex_prefix=(0xF0, 0x00, 0x20, 0x29, 0x02, 0x0D),
                top_ccs=self._top_ccs,
                side_column_is_cc=True,
                mode="lp3",
                color_saturation=FPC_COLOR_SATURATION_LP3,
                color_gamma=FPC_COLOR_GAMMA_LP3,
            )
        elif self.device_family == DEVICE_FAMILY_MK1:
            self.device_label = _mk1_label(self.device_id, self.device_name)
            self._side_column_is_cc = False
            self._top_ccs = TOP_CCS
            self._top_octave_down = TOP_OCTAVE_DOWN
            self._top_octave_up = TOP_OCTAVE_UP
            self._top_pan_left = TOP_PAN_LEFT
            self._top_pan_right = TOP_PAN_RIGHT
            self._top_performance = TOP_PERFORMANCE
            self._top_note_mode = TOP_NOTE_MODE
            self._top_fpc_mode = TOP_FPC_MODE
            self._top_record_arm = TOP_RECORD_ARM
            # MK1 has no SysEx LED batch commands; the led_display "mk1" mode
            # falls back entirely to per-pad note-on messages.  Palette is the
            # 16-colour red/green velocity scheme from the original PRM.
            led_display.configure_surface(
                sysex_prefix=SYSEX_PREFIX,
                top_ccs=self._top_ccs,
                side_column_is_cc=False,
                mode="mk1",
                color_saturation=FPC_COLOR_SATURATION_MK1,
                color_gamma=FPC_COLOR_GAMMA_MK1,
            )
            self._send_mk1_duty_cycle()
        else:
            self.device_label = "Launchpad MK2"
            self._side_column_is_cc = False
            self._top_ccs = TOP_CCS
            self._top_octave_down = TOP_OCTAVE_DOWN
            self._top_octave_up = TOP_OCTAVE_UP
            self._top_pan_left = TOP_PAN_LEFT
            self._top_pan_right = TOP_PAN_RIGHT
            self._top_performance = TOP_PERFORMANCE
            self._top_note_mode = TOP_NOTE_MODE
            self._top_fpc_mode = TOP_FPC_MODE
            self._top_record_arm = TOP_RECORD_ARM
            led_display.configure_surface(
                sysex_prefix=SYSEX_PREFIX,
                top_ccs=self._top_ccs,
                side_column_is_cc=False,
                mode="mk2",
                color_saturation=FPC_COLOR_SATURATION_MK2,
                color_gamma=FPC_COLOR_GAMMA_MK2,
            )
        _log(f"device profile: {self.device_label} top_ccs={self._top_ccs} side_column_is_cc={self._side_column_is_cc}")
    def _repair_invalid_note_window(self) -> bool:
        changed = False
        if int(self.state.get("scale_index", 0)) >= len(nm.SCALES):
            self.state["scale_index"] = int(DEFAULT_STATE["scale_index"])
            changed = True
        if int(self.state.get("row_stride", 0)) <= 0:
            self.state["row_stride"] = int(DEFAULT_STATE["row_stride"])
            changed = True
        try:
            valid_window = nm.can_display_window(
                self.state,
                int(self.state["base_octave"]),
                int(self.state["pan_offset"]),
            )
        except Exception:
            valid_window = False
        if not valid_window:
            self.state["base_octave"] = int(DEFAULT_STATE["base_octave"])
            self.state["pan_offset"] = int(DEFAULT_STATE["pan_offset"])
            changed = True
            _log("startup repaired invalid note window")
        return changed
    def _sync_performance_view(self) -> None:
        self._launch_map_ready = pm.sync_performance_view(
            self.state, self._launch_map_ready
        )
    def _init_launch_map(self) -> None:
        self._launch_map_ready = pm.init_launch_map(self.script_dir)
        if self._launch_map_ready:
            self._launch_map_ready = pm.update_launch_map(self._launch_map_ready)
    # State persistence
    def _load_state(self) -> None:
        self.state, file_missing = state_io.load_state(self.state_path, self.midi_port)
        for warning in state_io.pop_warnings():
            _log(f"state warning: {warning}")
        cl.migrate_legacy(self.state)
        if file_missing:
            _log("state file not found, creating with defaults")
            self._save_state()
    def _save_state(self) -> None:
        if self._restoring_mode:
            return
        self.state["surface_mode"] = self.surface_mode
        self.state["custom_mode_index"] = self._custom_mode_index
        if self._xy_last_x_val is not None:
            self.state["xy_cursor_x"] = self._xy_last_x_val
        if self._xy_last_y_val is not None:
            self.state["xy_cursor_y"] = self._xy_last_y_val
        self.state["xy_fader_values"] = {str(k): v for k, v in self._xy_fader_values.items()}
        state_io.save_state(self.state_path, self.midi_port, self.state)
        state_io.save_flp_state(self.midi_port, self.state)

# Module-level singleton and error guard
SURFACE = LaunchpadSurface()
_LAST_ERROR_SIGNATURE = None
def _guard(callback_name: str, fn, *args):
    global _LAST_ERROR_SIGNATURE
    try:
        return fn(*args)
    except Exception:
        trace     = traceback.format_exc()
        signature = f"{callback_name}\n{trace}"
        if signature != _LAST_ERROR_SIGNATURE:
            _LAST_ERROR_SIGNATURE = signature
            _log(f"{callback_name} failed")
            _log(trace)
    return None

# FL Studio entry points
def OnInit():
    _guard("OnInit", SURFACE.on_init)

def OnDeInit():
    _guard("OnDeInit", SURFACE.on_deinit)

def OnMidiIn(event):
    _guard("OnMidiIn", SURFACE.on_midi_in, event)

def OnIdle():
    _guard("OnIdle", SURFACE.on_idle)

def OnRefresh(flags):
    _guard("OnRefresh", SURFACE.on_refresh, flags)

def OnProjectLoad(status):
    _guard("OnProjectLoad", SURFACE.on_project_load, status)

def OnUpdateLiveMode(lastTrack):
    _guard("OnUpdateLiveMode", SURFACE.on_update_live_mode, lastTrack)

def OnMidiMsg(event):
    _guard("OnMidiMsg", SURFACE.on_midi_msg, event)

def OnSysEx(event):
    _guard("OnSysEx", SURFACE.on_sysex, event)
