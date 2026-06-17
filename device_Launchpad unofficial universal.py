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
#   step_sequencer.py   - channel-rack step grid + lock-routing page
#   custom_mode.py      - custom-mode parsing, live reads, input, lighting
#   device_profile.py   - hardware detection + LED/profile configuration
#   plugin_overrides.py - focused-plugin pad overlays such as Gross Beat
from __future__ import annotations
import time
import traceback
from pathlib import Path
from fl_stubs import channels, transport, midi, device, mixer
import state_io
import led_display
import note_mode as nm
import fpc_mode  as fm
import performance_mode as pm
import step_sequencer as ss
import custom_mode as cm
import device_profile as dp
import plugin_overrides as po
import modulators as xp
import modulators as ps
import channel_lock as cl
from modulators import PadFader
from gestures import ButtonGesture
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
    # modes
    MODE_NOTE, MODE_FPC, MODE_PERFORMANCE, MODE_CUSTOM,
    MODE_XY_PAD, MODE_STEP_SEQ, MODE_BLANK,
    # MIDI routing
    SESSION_CHANNEL,
    # timing
    TAP_AND_HOLD_DURATION_SECONDS,
    PERFORMANCE_DOUBLE_TAP_SECONDS,
    # note range
    LOWEST_NOTE, HIGHEST_NOTE,
    PAD_DISABLED,
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
    FPC_SCROLL_COLOR,
    STATE_FILE,
    DEFAULT_STATE,
    SIDE_COLUMN_PADS,
    NOTE_TOP_ROW_MODWHEEL_PADS,
    NOTE_ROUTING_SETTING_PAD,
    XY_PAD_X_CC, XY_PAD_Y_CC,
    performance_modwheel_CC,
    XY_VERT_FADER_CCS, XY_HORIZ_FADER_CCS,
    XY_FADER_ON_COLOR, XY_FADER_OFF_COLOR, performance_modwheel_COLOR,
    XY_PAGE_XY, XY_PAGE_VERT, XY_PAGE_HORIZ,
    XY_PAGE_VERT_BIPOLAR, XY_PAGE_HORIZ_BIPOLAR, XY_PAGE_COUNT,
    NOTE_LOCK_PULSE_RGB,
    NOTE_LOCK_PULSE_RGB_8BIT,
    mk1_note_to_pad,
    LedColor,
    LED_OFF,
)
DEVICE_FAMILY_MK1 = dp.DEVICE_FAMILY_MK1
DEVICE_FAMILY_MK2 = dp.DEVICE_FAMILY_MK2
DEVICE_FAMILY_LPX = dp.DEVICE_FAMILY_LPX
DEVICE_FAMILY_LPM3 = dp.DEVICE_FAMILY_LPM3
LP3_PROGRAMMER_MODE = dp.LP3_PROGRAMMER_MODE
LP3_LIVE_MODE = dp.LP3_LIVE_MODE

def _log(message: str) -> None:
    print(f"[NovLPd unofficial universal] {message}")

def _script_dir() -> Path:
    script_file = globals().get("__file__")
    if script_file:
        return Path(script_file).resolve().parent
    return Path.cwd()

def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))

def _log_device_identification() -> None:
    dp.log_device_identification(_log)

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
        # RAM-only memory of the last member used within each button "family",
        # so re-entering the family from another surface returns to where you
        # left off instead of the family default. Not persisted to json/flp.
        #   Note family  (note/custom button): MODE_NOTE | MODE_CUSTOM
        #   FPC family   (fpc/step button):    "fpc" | "fpc_gb" | "step"
        self._note_family_last = MODE_NOTE
        self._fpc_family_last  = "fpc"
        # Active note tracking
        self.active_pads:  dict[int, tuple[int, int]] = {}
        self.active_notes: dict[tuple[int, int], int] = {}
        self._xy_last_x_val: int | None = 0
        self._xy_last_y_val: int | None = 127
        # XY fader pages: one PadFader per grid fader (8 vertical + 8 horizontal,
        # unipolar and bipolar) plus the side-column modwheel.  Values are
        # 0.0–127.0 floats keyed by CC.
        self._xy_faders: dict[int, PadFader] = {}
        self._xy_fader_values: dict[int, float] = {}   # cc → 0.0-127.0
        # Active fader ramps: key → (PadFader, emit_callback(value), refresh_callback()).
        # Polled from on_idle to glide faders to their newly pressed value
        # instead of jumping (PadFader.interpolate_seconds, default 0.1s).
        self._fader_ramps: dict[object, tuple] = {}
        self._xy_pad_to_fader: dict[int, dict[int, int]] = {  # page → {pad: cc}
            XY_PAGE_VERT: {},
            XY_PAGE_HORIZ: {},
            XY_PAGE_VERT_BIPOLAR: {},
            XY_PAGE_HORIZ_BIPOLAR: {},
        }
        for cc, pads in xp.vert_fader_defs():
            self._xy_faders[cc] = PadFader(pads, minimum=0.0, maximum=127.0)
            for p in pads:
                self._xy_pad_to_fader[XY_PAGE_VERT][p] = cc
        for cc, pads in xp.horiz_fader_defs():
            self._xy_faders[cc] = PadFader(pads, minimum=0.0, maximum=127.0)
            for p in pads:
                self._xy_pad_to_fader[XY_PAGE_HORIZ][p] = cc
        for cc, pads in xp.vert_bipolar_fader_defs():
            self._xy_faders[cc] = PadFader(pads, minimum=0.0, maximum=127.0, bipolar=True)
            self._xy_fader_values[cc] = 63.5
            for p in pads:
                self._xy_pad_to_fader[XY_PAGE_VERT_BIPOLAR][p] = cc
        for cc, pads in xp.horiz_bipolar_fader_defs():
            self._xy_faders[cc] = PadFader(pads, minimum=0.0, maximum=127.0, bipolar=True)
            self._xy_fader_values[cc] = 63.5
            for p in pads:
                self._xy_pad_to_fader[XY_PAGE_HORIZ_BIPOLAR][p] = cc
        self._performance_modwheel_fader = PadFader(xp.modwheel_pads(), minimum=0.0, maximum=127.0)
        self._xy_faders[performance_modwheel_CC] = self._performance_modwheel_fader
        self._xy_fader_values[performance_modwheel_CC] = 63.5
        # Note mode: top row (81-88) + its side-column pad (89) become a 9-wide
        # modwheel fader when toggled on (settings pad 85). Drives the same CC
        # and shared value as the performance modwheel fader above.
        self._note_top_row_modwheel_fader = PadFader(NOTE_TOP_ROW_MODWHEEL_PADS, minimum=0.0, maximum=127.0)
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
        self._gross_beat_fader = PadFader(po.GROSS_BEAT_FADER_PADS, tension=-3.0)
        self.device_name = "Unknown Launchpad"
        self.device_id = b""
        self.device_family = DEVICE_FAMILY_MK2
        self.device_label = "Launchpad MK2"
        self._active_layout: int | None = None
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
        # Top mode-button gestures (tap / double-tap / long-hold). Long-holds
        # are polled uniformly from _poll_button_holds in on_idle.
        self._note_gesture = ButtonGesture()
        self._fpc_gesture = ButtonGesture()
        self._performance_gesture = ButtonGesture(double_tap_seconds=PERFORMANCE_DOUBLE_TAP_SECONDS)
        self._record_gesture = ButtonGesture()
        self._note_routing_gesture = ButtonGesture()
        # Captured at press time: was the surface in Performance mode when the
        # double-tap window opened? (The first tap's release may already have
        # cycled the surface before the second tap lands.)
        self._performance_button_armed_from_performance = False
        # FPC selector gesture state (pad hold, cleared via fm)
        self._fpc_selector_pressed: int | None = None
        self._fpc_selector_hold_started = 0.0
        self._fpc_selector_hold_fired   = False
        # Step sequencer channel toggle hold detection
        self._step_toggle_pad_pressed: int | None = None
        self._step_toggle_hold_started = 0.0
        self._step_toggle_hold_fired = False
        # Lock routing page: set to the channel index while the page is open
        self._step_lock_page_channel: int | None = None
        self._step_lock_page_test_note_sent: bool = False
        # Step sequencer settings pane (long-hold the step-seq mode key)
        self._step_seq_settings_visible: bool = False
        # Lock routing page faders: bottom two rows control the held channel's
        # volume (unipolar) and pan (bipolar).
        self._lock_page_volume_fader = PadFader(ss.LOCK_PAGE_VOLUME_ROW, minimum=0.0, maximum=1.0)
        self._lock_page_pan_fader = PadFader(ss.LOCK_PAGE_PAN_ROW, minimum=-1.0, maximum=1.0, bipolar=True)
        # Step sequencer: pads currently held in press order, each
        # (pad, (channel_index, step)) — the last entry is shown on the
        # velocity fader (right side column).
        self._step_held_pads: list[tuple[int, tuple[int, int]]] = []
        self._step_velocity_fader = PadFader(ss.VELOCITY_FADER_PADS, minimum=0.0, maximum=1.0)
        # Authoritative fader velocity per held (channel, step), seeded from FL
        # and owned locally while held so the fader holds its position.
        self._step_velocity_cache: dict[tuple[int, int], float] = {}
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
        # LED caches
        self._refresh_needed   = True
        self._grid_led_cache:  dict[int, LedColor] = {}
        self._top_led_cache:   dict[int, LedColor] = {}
        # True while the "FPC" placeholder text scroll is on the hardware
        # (shown when FPC mode has no mapped banks). Tracked so mode switches
        # can tear it down and force a clean repaint exactly once.
        self._fpc_scroll_active = False
        # MK2 software pulse: timestamp when lock was engaged (phase origin)
        self._pulse_start      = 0.0
        self._pulse_last_frame = 0.0
        # Per-mode grid lighting dispatch (after the global lights-out and
        # settings-pane overlays in _grid_lighting). Unknown modes paint black.
        self._grid_lighting_for_mode = {
            MODE_XY_PAD: self._xy_lighting,
            MODE_STEP_SEQ: self._step_seq_grid_lighting,
            MODE_PERFORMANCE: self._performance_grid_lighting,
            MODE_CUSTOM: self._custom_grid_lighting,
            MODE_FPC: self._fpc_grid_lighting,
            MODE_NOTE: self._note_grid_lighting,
        }
    _CUSTOM_FADER_MICRO_BRIGHTNESS = (0.25, 0.5, 0.75, 1.0)
    def _now(self) -> float:
        return time.monotonic()

    def _refresh_grid_pad(self, pad: int) -> None:
        led_display.refresh_grid_pad(pad, self._grid_lighting, self._grid_led_cache)

    def _refresh_grid_pads(self, pads) -> None:
        led_display.refresh_grid_pads(pads, self._grid_lighting, self._grid_led_cache)

    # Top-button long-holds (polled from on_idle)
    def _poll_button_holds(self, now: float) -> None:
        """Uniform long-hold dispatch for the top mode buttons. Each gesture
        fires at most once per press; conditional holds stay armed until their
        condition holds or the button is released."""
        if self._note_gesture.poll_hold(now):
            # Note settings pane; from another surface, enter Note first.
            if self.surface_mode != MODE_NOTE:
                self._enter_note_mode()
            self.settings_visible = not self.settings_visible
            self._refresh_needed = True
        if self.surface_mode == MODE_STEP_SEQ and self._fpc_gesture.poll_hold(now):
            self._step_seq_settings_visible = not self._step_seq_settings_visible
            self._refresh_needed = True
        if self._performance_gesture.poll_hold(now):
            self.state["lights_out"] = not bool(self.state.get("lights_out", False))
            self._save_state()
            self._refresh_needed = True
        if self._record_gesture.poll_hold(now):
            transport.globalTransport(midi.FPT_Record, 1, getattr(midi, "PME_System", 0))
            self._refresh_needed = True
        if (
            self.settings_visible
            and self.surface_mode == MODE_NOTE
            and self._note_routing_gesture.poll_hold(now)
        ):
            channel_index = self._host_selected_channel()
            if channel_index >= 0:
                self._step_lock_page_channel = channel_index
                self._step_lock_page_test_note_sent = False
                if not transport.isPlaying():
                    midi_channel = int(self.state["midi_channel"]) & 0x0F
                    channels.midiNoteOn(channel_index, ss.LOCK_PAGE_TEST_NOTE, 100, midi_channel)
                    self._step_lock_page_test_note_sent = True
            self._refresh_needed = True

    # Surface-mode switching
    #
    # Every mode change goes through _begin_mode_switch (mode flag, settings
    # panes, held notes, family memory) and usually _finish_mode_switch
    # (hardware layout, optional immediate repaint, persistence). Modes that
    # need a specific ordering between the layout SysEx and their view sync
    # (performance, step sequencer) sequence the epilogue themselves.
    #
    # Mode families: the Note key owns {Note, Custom}, the FPC key owns
    # {FPC, FPC+GrossBeat, Step Sequencer}. Each key remembers (RAM only, not
    # saved to json/flp) the member last used, and pressing the key from a
    # surface outside its family returns to that member.
    def _stop_fpc_scroll(self) -> None:
        """Tear down the "FPC" placeholder scroll if it's showing.

        The hardware doesn't repaint after a scroll stops, and the LED caches
        still reflect pre-scroll state, so we clear them to force the next
        refresh into a full repaint.
        """
        if not self._fpc_scroll_active:
            return
        self._fpc_scroll_active = False
        led_display.stop_scroll()
        led_display.clear_surface(self._grid_led_cache, self._top_led_cache)

    def _maybe_scroll_fpc_placeholder(self) -> None:
        """Scroll "FPC" once when FPC mode has no mapped banks, so the surface
        isn't left entirely blank. Native-scroll surfaces only (MK2/LPX/LPM3)."""
        if (
            led_display.supports_text_scroll()
            and not fm.has_any_fpc_slot_assignment(self.state)
        ):
            led_display.scroll_text("FPC", FPC_SCROLL_COLOR)
            self._fpc_scroll_active = True

    def _begin_mode_switch(self, mode) -> None:
        # Any pending placeholder scroll belongs to the mode we're leaving.
        self._stop_fpc_scroll()
        if self._note_routing_gesture.pressed:
            self._close_note_routing_page()
        self.surface_mode = mode
        self.settings_visible = False
        self._step_seq_settings_visible = False
        self._release_all_notes()
        self._record_mode_family(mode)

    def _finish_mode_switch(self, *, refresh: bool = False) -> None:
        self._apply_surface_layout()
        if refresh:
            self._refresh_surface()
        self._save_state()

    def _record_mode_family(self, mode) -> None:
        if mode in (MODE_NOTE, MODE_CUSTOM):
            self._note_family_last = mode
        elif mode == MODE_STEP_SEQ:
            self._fpc_family_last = "step"
        elif mode == MODE_FPC:
            gb_id = self._focused_plugin_pad_override_id()
            gb_active = gb_id is not None and self._suppressed_plugin_override_id != gb_id
            self._fpc_family_last = "fpc_gb" if gb_active else "fpc"

    def _enter_mode_family_last(self, family: str) -> None:
        """Enter the remembered last member of a mode-button family, with
        sensible fallbacks when that member is currently unavailable."""
        if family == "note":
            if self._note_family_last == MODE_CUSTOM and self._custom_modes:
                self._enter_custom_mode_selector()
            else:
                self._enter_note_mode()
            return
        if family == "fpc":
            gb_id = self._focused_plugin_pad_override_id()
            if self._fpc_family_last == "step":
                self._enter_step_sequencer_mode()
            elif self._fpc_family_last == "fpc_gb":
                # Re-show Gross Beat only if one is still focused; otherwise
                # this degrades to plain FPC.
                self._enter_fpc_mode(suppress_gross_beat=gb_id is None)
            else:
                self._enter_fpc_mode(suppress_gross_beat=True)

    # FL Studio lifecycle callbacks
    def on_init(self) -> None:
        _log(f"init script_dir={self.script_dir}")
        _log_device_identification()
        self._configure_device_profile()
        self.midi_port = device.getPortNumber()
        self._load_state()
        self._prepare_custom_modes()
        state_io.load_flp_state(self.midi_port, self.state)
        # Restore the active custom slot for the session regardless of which
        # surface mode the session starts in. Previously this only happened
        # inside _restore_surface_mode's MODE_CUSTOM branch, so a session that
        # started in any other mode kept slot 0 in RAM — and the first
        # _save_state() then overwrote the project's saved slot with 0. Any
        # later visit to Custom (cycle or restored-from-outside) displayed the
        # wrong layout until a selector pad was pressed.
        saved_custom_index = int(self.state.get("custom_mode_index", 0))
        if self._custom_modes:
            self._custom_mode_index = max(0, min(len(self._custom_modes) - 1, saved_custom_index))
        if self._live_custom_mode_reading and saved_custom_index >= len(self._custom_modes):
            self._pending_custom_mode_index = saved_custom_index
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
        self._active_layout = None
        led_display.clear_surface(self._grid_led_cache, self._top_led_cache)
        self._restore_surface_mode()
        _log(f"startup state mode={self.surface_mode} settings={self.settings_visible}")
        self._refresh_needed = False
    def on_deinit(self) -> None:
        self._release_all_notes()
        if self._fpc_scroll_active:
            self._fpc_scroll_active = False
            led_display.stop_scroll()
        led_display.clear_surface(self._grid_led_cache, self._top_led_cache)
        if self.device_family in (DEVICE_FAMILY_LPX, DEVICE_FAMILY_LPM3):
            led_display.set_layout(LP3_LIVE_MODE)
            self._active_layout = LP3_LIVE_MODE
        elif self.device_family != DEVICE_FAMILY_MK1:
            led_display.set_layout(LAYOUT_SESSION)
            self._active_layout = LAYOUT_SESSION
        else:
            self._active_layout = None
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
                # Banks now exist — drop the "FPC" placeholder scroll so the
                # repaint below shows the pads immediately instead of waiting
                # for the scroll to finish. (clear_surface inside resets the
                # caches, so the forced refresh paints the full grid.)
                self._stop_fpc_scroll()
            self._refresh_needed = True
        # Repaint the step-sequencer grid as the playhead advances so the column
        # under it stays highlighted during playback. Only the old and new
        # playhead columns actually change, so refresh just those pads instead
        # of the whole surface — a full refresh every step is noticeably slow
        # on MK1-protocol hardware.
        playhead_step = ss.playhead_step() if self._step_sequencer_grid_visible() else -1
        if playhead_step != self._last_playhead_step:
            changed_pads: list[int] = []
            for changed_step in (self._last_playhead_step, playhead_step):
                changed_pads.extend(ss.pads_for_step(changed_step, self.state))
            if changed_pads:
                self._refresh_grid_pads(changed_pads)
            self._last_playhead_step = playhead_step
        plugin_override_id = self._active_plugin_pad_override()
        if plugin_override_id != self._last_plugin_override_id:
            self._last_plugin_override_id = plugin_override_id
            self._plugin_override_held_pads.clear()
            self._refresh_needed = True
        self._poll_button_holds(time.monotonic())
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
                    channels.midiNoteOn(channel_index, ss.LOCK_PAGE_TEST_NOTE, 100, midi_channel)
                    self._step_lock_page_test_note_sent = True
                self._refresh_needed = True
        if (
            self.device_family in (DEVICE_FAMILY_MK2, DEVICE_FAMILY_MK1)
            and cl.is_locked(self.state, cl.NOTE_CONTEXT)
            and not bool(self.state.get("lights_out", False))
        ):
            now = time.monotonic()
            if now - self._pulse_last_frame >= 1.0 / 30.0:
                self._pulse_last_frame = now
                self._send_software_pulse_frame()
                self._refresh_needed = True
        cm.complete_live_read_if_due(self, _log)
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
        self._tick_fader_ramps()
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
        cm.prepare_runtime(self, _log)
    def _reload_custom_modes(self, *, live_first: bool) -> None:
        cm.reload_runtime(self, live_first=live_first, log=_log)
    def _custom_mode_product_id(self) -> int | None:
        return cm.product_id(self.device_family)
    def _custom_mode_slot_ids(self) -> tuple[int, ...]:
        return cm.slot_ids(self.device_family)
    def _custom_mode_read_request(self, slot_id: int) -> bytes | None:
        return cm.read_request(self.device_family, slot_id)
    def _start_live_custom_mode_read(self) -> None:
        cm.start_live_read(self, _log)
    def on_sysex(self, event) -> None:
        cm.handle_sysex(self, event, _log)
    def _event_sysex_bytes(self, event) -> bytes:
        return cm.event_sysex_bytes(event)
    def _custom_mode_reply_slot(self, sysex: bytes) -> tuple[int, int] | None:
        return cm.reply_slot(self.device_family, sysex)
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
        if (
            pad == NOTE_ROUTING_SETTING_PAD
            and not pressed
            and self._note_routing_gesture.pressed
        ):
            self._close_note_routing_page()
            self._refresh_needed = True
            return True
        if self.settings_visible:
            if pad == NOTE_ROUTING_SETTING_PAD:
                if pressed:
                    self._note_routing_gesture.press(self._now())
                self._refresh_needed = True
                return True
            if self._note_routing_gesture.hold_fired and self._step_lock_page_channel is not None:
                if pressed:
                    self._refresh_grid_pads(self._handle_step_lock_page_press(pad))
                self._refresh_needed = False
                return True
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
                        # A bank now exists — drop the "FPC" placeholder scroll
                        # so the grid repaints immediately.
                        self._stop_fpc_scroll()
                if self._fpc_selector_pressed == pad:
                    self._fpc_selector_pressed = None
                self._fpc_selector_hold_started = 0.0
                self._fpc_selector_hold_fired   = False
            self._refresh_needed = True
            return True
        # Note mode: top row + side-column arrow intercepted as a modwheel
        # fader when toggled on (settings pad 85).
        if (
            self.surface_mode == MODE_NOTE
            and self._note_top_row_modwheel_on()
            and self._note_top_row_modwheel_fader.contains(pad)
        ):
            if pressed:
                self._xy_apply_fader(event, performance_modwheel_CC, pad, self._note_top_row_modwheel_fader)
                self.active_pads[pad] = ("note_modwheel", pad)
            else:
                self.active_pads.pop(pad, None)
            self._refresh_grid_pads(NOTE_TOP_ROW_MODWHEEL_PADS)
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
        self._handle_arrow_press(cc)
        self._save_state()
        self._refresh_needed = True

    # Octave/pan arrows: one vertical and one horizontal action per mode.
    def _handle_arrow_press(self, cc: int) -> None:
        if cc in (self._top_octave_down, self._top_octave_up):
            if self.surface_mode == MODE_STEP_SEQ:
                self._step_arrow_vertical(1 if cc == self._top_octave_down else -1)
            else:
                self._step_arrow_vertical(1 if cc == self._top_octave_up else -1)
        elif cc in (self._top_pan_left, self._top_pan_right):
            self._step_arrow_horizontal(1 if cc == self._top_pan_right else -1)

    def _step_arrow_vertical(self, direction: int) -> None:
        if self.surface_mode == MODE_PERFORMANCE:
            if pm.performance_available():
                pm.step_tracks(direction, self.state)  # up = scroll up
                self._sync_performance_view()
            else:
                ss.step_channels(direction, self.state)
        elif self.surface_mode == MODE_STEP_SEQ:
            ss.step_channels(direction, self.state)
        elif self.surface_mode == MODE_NOTE:
            nm.step_octave(self.state, direction)

    def _step_arrow_horizontal(self, direction: int) -> None:
        if self.surface_mode == MODE_PERFORMANCE:
            if pm.performance_available():
                pm.step_blocks(direction, self.state)
                self._sync_performance_view()
            else:
                ss.step_steps(direction, self.state)
        elif self.surface_mode == MODE_STEP_SEQ:
            ss.step_steps(direction, self.state)
        elif self.surface_mode == MODE_FPC:
            fm.step_fpc_page(direction, self.state)
            self._refresh_surface()
        elif self.surface_mode == MODE_NOTE:
            nm.step_pan(self.state, direction)
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
            return "play/record"
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
            now = self._now()
            if self._performance_gesture.tap_tap(now):
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
                    self._save_state()
                    _log(
                        "performance hybrid empty-pad FPC "
                        f"{'on' if self.state['performance_direct_audio'] else 'off'}"
                    )
                    return True
                # Second single tap while not in performance mode: cycle modes
                self._cycle_session_modes()
                self._save_state()
                return True
            self._performance_gesture.press(now)
            self._performance_button_armed_from_performance = (
                self.surface_mode == MODE_PERFORMANCE
            )
            return False
        released = self._performance_gesture.release()
        if not released.was_pressed or released.hold_fired or self._session_key_is_inert():
            return False
        # If we're already in a session-style surface, cycle; otherwise enter the
        # session default (Performance when available, else XY/faders).
        if self.surface_mode in (MODE_PERFORMANCE, MODE_XY_PAD):
            self._cycle_session_modes()
        else:
            self._enter_session_default()
        return True
    def _enter_note_mode(self) -> None:
        self._begin_mode_switch(MODE_NOTE)
        ss.clear_channel_rack_view()
        self._finish_mode_switch()

    def _cycle_note_modes(self) -> None:
        """Note key cycle: Note → Custom → Note (skips Custom if none loaded)."""
        if self.surface_mode == MODE_CUSTOM:
            self._enter_note_mode()
        elif self._custom_modes:
            self._enter_custom_mode_selector()
        # else: no custom modes loaded — stay in Note

    def _handle_note_mode_button(self, pressed: bool) -> None:
        # Tap: cycle Note ↔ Custom; from another surface, return to the
        # family's last member immediately on press (so a long-hold lands on a
        # live note-family surface). Long-hold (on_idle): Note settings pane.
        if pressed:
            entered_from_outside = self.surface_mode not in (MODE_NOTE, MODE_CUSTOM)
            if entered_from_outside:
                self._enter_mode_family_last("note")
            self._note_gesture.press(self._now(), context=entered_from_outside)
            return
        released = self._note_gesture.release()
        entered_from_outside = bool(released.context)
        if not released.was_pressed or released.hold_fired or entered_from_outside:
            return
        if self.settings_visible:
            self.settings_visible = False
            return
        self._cycle_note_modes()
    def _enter_fpc_mode(self, *, suppress_gross_beat: bool) -> None:
        # Suppression must be settled before _begin_mode_switch so the family
        # memory records plain-FPC vs FPC+GrossBeat correctly.
        self._suppressed_plugin_override_id = (
            self._focused_plugin_pad_override_id() if suppress_gross_beat else None
        )
        self._plugin_override_held_pads.clear()
        self._begin_mode_switch(MODE_FPC)
        ss.clear_channel_rack_view()
        if not fm.has_any_fpc_slot_assignment(self.state):
            selected = self._host_selected_channel()
            if fm.selected_channel_is_fpc(selected):
                fm.auto_assign_new_fpc(self.state, selected)
        self._finish_mode_switch(refresh=True)
        # If FPC still has nothing mapped, scroll "FPC" once so the grid isn't
        # left blank. Sent after the refresh so it overlays the painted grid.
        self._maybe_scroll_fpc_placeholder()

    def _handle_fpc_mode_button(self, pressed: bool, _event) -> None:
        # Tap: cycle FPC → Step Sequencer → Gross Beat → FPC (Gross Beat
        # skipped when no Gross Beat plugin is focused); from another surface,
        # return to the family's last member. Long-hold (on_idle) while in
        # Step Sequencer: its settings pane.
        if pressed:
            self._fpc_gesture.press(self._now())
            return
        released = self._fpc_gesture.release()
        if not released.was_pressed or released.hold_fired:
            return
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
                self._record_mode_family(MODE_FPC)
            else:
                # Plain FPC → Step Sequencer.
                self._enter_step_sequencer_mode()
        elif self.surface_mode == MODE_STEP_SEQ:
            # Step Sequencer → Gross Beat if available, else back to plain FPC.
            self._enter_fpc_mode(suppress_gross_beat=gb_id is None)
        else:
            self._enter_mode_family_last("fpc")

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
        # Short release toggles playback; a long hold claims the gesture for
        # FL transport recording from on_idle.
        if pressed:
            self._record_gesture.press(self._now())
            return
        released = self._record_gesture.release()
        if released.was_pressed and not released.hold_fired:
            transport.globalTransport(midi.FPT_Play, 1, getattr(midi, "PME_System", 0))
    def _enter_custom_mode_selector(self) -> None:
        """Switch to custom mode with the persistent selector sidebar active."""
        self._begin_mode_switch(MODE_CUSTOM)
        self._custom_mode_selecting = True
        ss.clear_channel_rack_view()
        self._finish_mode_switch(refresh=True)
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
        return cm.index_for_selector_slot(self, slot)
    # Custom-mode pad handling
    def _handle_custom_mode_pad(self, event, pad: int, velocity: int, pressed: bool) -> bool:
        return cm.handle_pad(self, event, pad, velocity, pressed)
    def _handle_custom_locked_note(self, pad: int, cp, velocity: int, pressed: bool) -> bool:
        return cm.handle_locked_note(self, pad, cp, velocity, pressed)
    def _handle_custom_fader_pad(self, event, pad: int, fader: cm.CustomFader, slot: int) -> bool:
        return cm.handle_fader_pad(self, event, pad, fader, slot)

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

    def _note_top_row_modwheel_on(self) -> bool:
        return bool(self.state.get("note_top_row_modwheel", False))

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

    def _xy_apply_fader(self, event, cc: int, pad: int, pad_fader: PadFader | None = None) -> None:
        """Advance an XY/modwheel fader by one micro-step on the pressed pad,
        gliding to the new value over PadFader.interpolate_seconds. Bipolar
        centre-chord reset is handled by PadFader.apply_press().

        `pad_fader` defaults to the PadFader registered for `cc`; pass it
        explicitly when a different pad layout drives the same CC (e.g. the
        note-mode top-row modwheel fader vs. the performance-mode one).

        Gliding only applies once the CC is mapped to an FL parameter (driven
        via automateEvent, like _emit_xy_axis's mapped path). An unmapped CC
        is sent immediately at its target value so FL's MIDI-learn window —
        which only fires during this callback — can still catch it."""
        pf = pad_fader if pad_fader is not None else self._xy_faders.get(cc)
        if pf is None:
            return
        now = time.monotonic()
        current_target = self._xy_fader_values.get(cc, 0.0)
        new_value = pf.apply_press(pad, current_target, now)
        self._xy_fader_values[cc] = new_value
        key = ("xy", cc)
        if pf.interpolate_seconds > 0.0 and self._xy_axis_event_id(cc) is not None:
            start_value = pf.current_ramped_value(now)
            if start_value is None:
                start_value = current_target
            pf.start_ramp(start_value, new_value, now)
            self._fader_ramps[key] = (pf, lambda value, cc=cc: self._automate_xy_cc(cc, value))
            self._refresh_needed = True
        else:
            self._fader_ramps.pop(key, None)
            self._emit_xy_axis(event, cc, int(round(new_value)), allow_learn=True)

    def _begin_channel_param_ramp(self, pf: PadFader, key, now: float, current_value: float, new_value: float, *, emit) -> None:
        """Start (or restart) a glide on `pf` toward `new_value`, registering
        `emit` for on_idle polling. Used for FL channel-API parameters (volume,
        pan) that need no MIDI event. If `pf.interpolate_seconds` is 0, applies
        `new_value` immediately."""
        if pf.interpolate_seconds <= 0.0:
            self._fader_ramps.pop(key, None)
            emit(new_value)
            return
        start_value = pf.current_ramped_value(now)
        if start_value is None:
            start_value = current_value
        pf.start_ramp(start_value, new_value, now)
        self._fader_ramps[key] = (pf, emit)
        self._refresh_needed = True

    def _automate_xy_cc(self, cc: int, value: float) -> None:
        """Drive an already-mapped XY CC directly by event ID (no FL MIDI
        event required) — used while a fader ramp is in progress."""
        event_id = self._xy_axis_event_id(cc)
        if event_id is None:
            return
        value = max(0, min(127, int(round(value))))
        from_midi_max = int(getattr(midi, "FromMIDI_Max", 1073741824))
        out_value = round(value * (from_midi_max / 127))
        try:
            mixer.automateEvent(event_id, out_value, midi.REC_MIDIController, 0)
        except Exception:
            pass

    def _tick_fader_ramps(self) -> None:
        if not self._fader_ramps:
            return
        now = time.monotonic()
        finished_keys = []
        for key, (pf, emit) in self._fader_ramps.items():
            value = pf.advance_ramp(now)
            if value is None:
                finished_keys.append(key)
                continue
            emit(value)
            if not pf.is_ramping():
                finished_keys.append(key)
        for key in finished_keys:
            self._fader_ramps.pop(key, None)
        self._refresh_needed = True

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

        While `pad_fader` is mid-glide (see PadFader.start_ramp), the in-progress
        interpolated value is shown instead of `current_value` so the lit pad
        tracks the glide rather than jumping straight to the target.
        """
        ramped = pad_fader.current_ramped_value(time.monotonic())
        if ramped is not None:
            current_value = ramped
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
        return ss.handle_surface_pad(self, event, pad, velocity, pressed)

    # Lock routing page (step sequencer channel hold)
    # Layout (8-wide grid, top→bottom):
    #   Row 8 (pad 81): Note mode context
    #   Row 7 (pad 71): empty
    #   Row 6 (pads 61–68): custom contexts 0–7
    #   Row 5 (pads 51–58): custom contexts 8–15
    #   Row 2 (pads 21–28): held channel volume (unipolar)
    #   Row 1 (pads 11–18): held channel pan (bipolar)
    _LOCK_PAGE_NOTE_PAD = 81
    _LOCK_PAGE_TEST_NOTE = 72  # C5
    _LOCK_PAGE_CUSTOM_ROW0 = tuple(range(61, 69))   # custom 0–7
    _LOCK_PAGE_CUSTOM_ROW1 = tuple(range(51, 59))   # custom 8–15
    _LOCK_PAGE_VOLUME_ROW = tuple(range(21, 29))    # channel volume
    _LOCK_PAGE_PAN_ROW = tuple(range(11, 19))       # channel pan

    def _step_lock_page_context_for_pad(self, pad: int) -> str | None:
        return ss.lock_page_context_for_pad(pad)

    def _handle_step_lock_page_press(self, pad: int) -> list[int]:
        return ss.handle_lock_page_press(self, pad)

    def _step_lock_page_lighting(self, pad: int) -> LedColor:
        return ss.lock_page_lighting(self, pad)

    def _close_note_routing_page(self) -> None:
        if self._step_lock_page_test_note_sent and self._step_lock_page_channel is not None:
            midi_channel = int(self.state["midi_channel"]) & 0x0F
            channels.midiNoteOn(
                self._step_lock_page_channel,
                ss.LOCK_PAGE_TEST_NOTE,
                0,
                midi_channel,
            )
        self._step_lock_page_test_note_sent = False
        self._step_lock_page_channel = None
        self._note_routing_gesture.reset()

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
        return po.active_override(self)
    def _focused_plugin_pad_override_id(self) -> str | None:
        return po.focused_override_id()
    def _focused_plugin_name(self) -> str:
        return po.focused_plugin_name()
    def _plugin_window_focused(self) -> bool:
        return po.plugin_window_focused()
    def _handle_plugin_pad_override(
        self, override_id: str, event, pad: int, velocity: int, pressed: bool
    ) -> bool:
        return po.handle_pad(self, override_id, event, pad, velocity, pressed)
    def _plugin_pad_override_lighting(
        self, override_id: str, pad: int
    ) -> LedColor:
        return po.lighting(self, override_id, pad)
    def _handle_gross_beat_pad(self, _event, pad: int, pressed: bool) -> bool:
        return po.handle_gross_beat_pad(self, _event, pad, pressed)
    def _gross_beat_lighting(self, pad: int) -> LedColor:
        return po.gross_beat_lighting(self, pad)
    def _gross_beat_slot_mode(self) -> str:
        return po.gross_beat_slot_mode(self)
    def _focused_plugin_target(self) -> tuple[int, int, int] | None:
        return po.focused_plugin_target()
    def _gross_beat_spec(self, target: tuple[int, int, int] | None) -> dict | None:
        return po.gross_beat_spec(self, target)
    def _discover_gross_beat_spec(self, param_names: list[str]) -> dict | None:
        return po.discover_gross_beat_spec(param_names)
    def _normalize_param_name(self, name: str) -> str:
        return po.normalize_param_name(name)
    def _extract_slot_number(self, normalized_name: str) -> int | None:
        return po.extract_slot_number(normalized_name)
    def _gross_beat_trigger_slot(
        self,
        target: tuple[int, int, int],
        spec: dict,
        slot_index: int,
    ) -> None:
        po.gross_beat_trigger_slot(target, spec, self._gross_beat_slot_mode(), slot_index)
    def _gross_beat_active_slot(
        self,
        target: tuple[int, int, int] | None,
        spec: dict | None,
    ) -> int | None:
        return po.gross_beat_active_slot(target, spec, self._gross_beat_slot_mode())
    def _plugin_param_value(self, target: tuple[int, int, int], param_index: int) -> float:
        return po.plugin_param_value(target, param_index)
    def _plugin_set_param_value(
        self,
        target: tuple[int, int, int],
        param_index: int,
        value: float,
    ) -> None:
        po.plugin_set_param_value(target, param_index, value)
    # LED rendering
    def _refresh_surface(self) -> None:
        # Note-key pulse when Note mode is channel-locked.
        lights_out = self._lights_effectively_out()
        note_lock_pulse = (
            cl.is_locked(self.state, cl.NOTE_CONTEXT)
            and not lights_out
        )
        # Custom sidebar entries pulse when their custom index is locked (LP3 hw pulse;
        # MK1 has no per-pad pulse, so it keeps the static selector lighting instead).
        custom_pulse_pads = (
            self._locked_custom_selector_pads()
            if self.surface_mode == MODE_CUSTOM
            and self.device_family not in (DEVICE_FAMILY_MK2, DEVICE_FAMILY_MK1)
            and not lights_out
            else set()
        )
        led_display.refresh_surface(
            self._grid_lighting,
            self._top_color,
            self._grid_led_cache,
            self._top_led_cache,
            # MK1's note-key pulse is driven by the periodic _send_software_pulse_frame
            # (velocity CC), not the static batch, so it's excluded here too.
            pulse_top_ccs={self._top_note_mode} if note_lock_pulse else None,
            pulse_grid_pads=custom_pulse_pads or None,
        )
        if note_lock_pulse and self.device_family not in (DEVICE_FAMILY_MK2, DEVICE_FAMILY_MK1):
            led_display.send_top_led_pulse(self._top_note_mode, LP3_MENU_LOCKED)
        for pad in custom_pulse_pads:
            # Side column is CC on LP3, so the CC-channel-3 pulse path applies.
            led_display.send_top_led_pulse(pad, LP3_MENU_LOCKED)
    def _locked_custom_selector_pads(self) -> set[int]:
        return cm.locked_selector_pads(self)
    def _send_software_pulse_frame(self) -> None:
        # 120 BPM → 2-second cycle (one period = two beats); skewed triangle: 25% rise, 75% fall
        phase = (time.monotonic() - self._pulse_start) % 2.0 / 2.0
        if self.device_family == DEVICE_FAMILY_MK1:
            # MK1's velocity steps are too coarse for a smooth fade to read as
            # anything but flicker, so flash fully on/off on the same cycle
            # instead of following the LP3/MK2 brightness curve.
            rgb = NOTE_LOCK_PULSE_RGB_8BIT if phase < 0.50 else (0, 0, 0)
            led_display.send_top_led_mk1_pulse(self._top_note_mode, rgb)
            return
        brightness = led_display.software_pulse_brightness(phase)
        fr, fg, fb = NOTE_LOCK_PULSE_RGB
        rgb = (
            int(round(fr * brightness)),
            int(round(fg * brightness)),
            int(round(fb * brightness)),
        )
        led_display.send_top_led_rgb(self._top_note_mode, rgb)
    def _custom_slot_display_index(self, slot: int) -> int | None:
        return cm.slot_display_index(self, slot)
    def _custom_mode_lighting(self, pad: int) -> LedColor:
        return cm.lighting(self, pad)
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
            return self._step_lock_page_channel is None and not self._step_seq_settings_visible
        if self.surface_mode == MODE_PERFORMANCE:
            return not pm.performance_available()
        return False

    def _grid_lighting(self, pad: int) -> LedColor:
        """Grid pad color: global overlays (lights-out, settings pane) first,
        then the active mode's lighting via _grid_lighting_for_mode."""
        if self._lights_effectively_out():
            return LedColor(PAD_DISABLED)
        if self.settings_visible:
            if self._note_routing_gesture.hold_fired and self._step_lock_page_channel is not None:
                return self._step_lock_page_lighting(pad)
            return nm.settings_color(pad, self.state)
        handler = self._grid_lighting_for_mode.get(self.surface_mode)
        if handler is None:
            return LedColor(PAD_DISABLED)
        return handler(pad)

    def _step_seq_grid_lighting(self, pad: int) -> LedColor:
        if self._step_seq_settings_visible:
            return ss.settings_lighting(pad, self.state)
        if self._step_lock_page_channel is not None:
            return self._step_lock_page_lighting(pad)
        if pad in ss.VELOCITY_FADER_PADS and ss.velocity_fader_active(self):
            return ss.velocity_fader_lighting(self, pad)
        return ss.lighting(pad, self.state)

    def _performance_grid_lighting(self, pad: int) -> LedColor:
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

    def _custom_grid_lighting(self, pad: int) -> LedColor:
        override_id = self._active_plugin_pad_override()
        if override_id is not None:
            return self._plugin_pad_override_lighting(override_id, pad)
        return self._custom_mode_lighting(pad)

    def _fpc_grid_lighting(self, pad: int) -> LedColor:
        override_id = self._active_plugin_pad_override()
        if override_id is not None:
            return self._plugin_pad_override_lighting(override_id, pad)
        return fm.fpc_lighting(
            pad, self.state,
            self._selected_channel,
            lambda: fm.selected_channel_is_fpc(self._selected_channel()),
            self._is_note_active,
            self._is_fpc_pad_recently_active,
        )

    def _note_grid_lighting(self, pad: int) -> LedColor:
        override_id = self._active_plugin_pad_override()
        if override_id is not None:
            return self._plugin_pad_override_lighting(override_id, pad)
        if self._note_top_row_modwheel_on() and pad in NOTE_TOP_ROW_MODWHEEL_PADS:
            return self._fader_pad_lighting(
                self._note_top_row_modwheel_fader,
                performance_modwheel_COLOR,
                XY_FADER_OFF_COLOR,
                pad,
                self._xy_fader_values.get(performance_modwheel_CC, 0.0),
            )
        return nm.note_mode_lighting(
            pad, self.state,
            self._is_note_active,
            self._channel_for_pad,
            self._playable_pads(),
        )
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
            if self._active_layout != layout:
                led_display.set_layout(layout)
                self._grid_led_cache.clear()
                self._top_led_cache.clear()
                self._active_layout = layout
        elif self.device_family == DEVICE_FAMILY_MK1:
            # MK1 has no layout command, so keep the LED caches hot across page
            # switches and let the next refresh diff old page vs new page
            # instead of forcing a blank-first full repaint.
            pass
        else:
            layout = LAYOUT_SESSION if self.surface_mode == MODE_PERFORMANCE else LAYOUT_USER_2
            if self._active_layout != layout:
                led_display.set_layout(layout)
                self._grid_led_cache.clear()
                self._top_led_cache.clear()
                self._active_layout = layout
    def _enter_performance_mode(self) -> None:
        if self.surface_mode == MODE_PERFORMANCE:
            if pm.performance_available():
                self._sync_performance_view()
            else:
                ss.sync_channel_rack_view(self.state)
            return
        self._begin_mode_switch(MODE_PERFORMANCE)
        # Layout SysEx must precede the launch-map / rack-view sync.
        self._apply_surface_layout()
        if pm.performance_available():
            self._launch_map_ready = pm.update_launch_map(self._launch_map_ready)
            self._sync_performance_view()
        else:
            ss.sync_channel_rack_view(self.state)
        self._save_state()

    def _enter_xy_pad_mode(self) -> None:
        self._begin_mode_switch(MODE_XY_PAD)
        self._finish_mode_switch()
        _log("XY pad mode")

    def _enter_step_sequencer_mode(self) -> None:
        self._begin_mode_switch(MODE_STEP_SEQ)
        # Layout SysEx must precede the channel-rack view sync.
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
        dp.send_mk1_duty_cycle()

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
        dp.configure_surface_profile(self, _log)
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
# ~gargoyles rule~