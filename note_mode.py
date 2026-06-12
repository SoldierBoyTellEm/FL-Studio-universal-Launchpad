# note_mode.py
# Note-mode pad logic: scale/chromatic note mapping, settings-screen
# rendering, pan/octave navigation, and channel locking.
#
# All methods expect a *state* dict (passed by reference from the surface)
# and return values rather than touching hardware directly — LED output is
# handled by led_display.py.
from constants import (
    SCALES,
    PLAYABLE_PADS,
    SIDE_COLUMN_PADS,
    INACTIVE_SETTINGS_PADS,
    OVERLAP_SETTING_PADS,
    AXIS_SETTING_PAD,
    CHROMATIC_SETTING_PAD,
    ROOT_SETTING_PADS,
    SCALE_SETTING_PADS,
    MIDI_CHANNEL_SETTING_PADS,
    LOWEST_NOTE,
    HIGHEST_NOTE,
    MODE_NOTE,
    LedColor,
    LP3_NOTE_ROOT,
    LP3_NOTE_IN_SCALE,
    LP3_NOTE_OFF,
    LP3_BACKGROUND_OFF,
    LP3_UNUSED_GREY,
    LP3_SETTING_DIM,
    LP3_SETTING_ON,
    LP3_CHROMATIC_DIM,
    LP3_CHROMATIC_ON,
    LP3_ROOT_DIM,
    LP3_ROOT_ON,
    LP3_SCALE_DIM,
    LP3_SCALE_ON,
    LP3_CHANNEL_DIM,
    LP3_CHANNEL_ON,
    LP3_ARROW_ACTIVE,
    LP3_ARROW_INACTIVE,
    PAD_HELD,
)

# Note-mode page palette.  The palette index drives MK2/LP3; first-gen MK1
# derives its red/green from that index's MK3-palette RGB (blue-fold + the
# configured MK1 saturation/gamma), the same path RGB pads use, so indexed and
# RGB pads stay consistent.  LedColor still supports an explicit mk1=RG override
# (tens=red, ones=green, 0-3) for a deliberate per-page first-gen colour, but
# none is set here — these all resolve through the RGB lookup.
NOTE_OFF      = LedColor(LP3_NOTE_OFF)        # off / out of range
NOTE_IN_SCALE = LedColor(LP3_NOTE_IN_SCALE, mk1=23)   # in-scale degree
NOTE_ROOT     = LedColor(LP3_NOTE_ROOT, mk1=21)       # scale root
NOTE_HELD     = LedColor(PAD_HELD, mk1=3)            # sounding pad

# Settings-overlay page palette.
BACKGROUND_OFF = LedColor(LP3_BACKGROUND_OFF)   # blank pad
UNUSED_GREY    = LedColor(LP3_UNUSED_GREY, mk1=20)      # off-selectable
SETTING_DIM    = LedColor(LP3_SETTING_DIM, mk1=21)      # toggle off
SETTING_ON     = LedColor(LP3_SETTING_ON, mk1=33)       # toggle on
CHROMATIC_DIM  = LedColor(LP3_CHROMATIC_DIM, mk1=20)    # chromatic off
CHROMATIC_ON   = LedColor(LP3_CHROMATIC_ON, mk1=33)     # chromatic on
ROOT_DIM       = LedColor(LP3_ROOT_DIM, mk1=3)         # root in scale
ROOT_ON        = LedColor(LP3_ROOT_ON, mk1=33)          # current root
SCALE_DIM      = LedColor(LP3_SCALE_DIM, mk1=1)     # scale off            R0 G2
SCALE_ON       = LedColor(LP3_SCALE_ON, mk1=33)      # scale selected       R0 G3
CHANNEL_DIM    = LedColor(LP3_CHANNEL_DIM, mk1=10)  # channel not current  R1 G1
CHANNEL_ON     = LedColor(LP3_CHANNEL_ON, mk1=33)    # current channel      R0 G3

# Scale helpers
def scale_mask(state: dict) -> set[int]:
    """Return the set of pitch classes present in the current scale."""
    _name, degrees = SCALES[state["scale_index"]]
    return {((state["root"] + degree) % 12) for degree in degrees}

def note_for_pad(pad: int, state: dict) -> int:
    """Map a grid pad to a MIDI note number under the current note-mode state."""
    row = (pad // 10) - 1
    col = (pad % 10) - 1
    if state["axis_flip"]:
        degree_offset = row + (col + int(state["pan_offset"])) * state["row_stride"]
    else:
        degree_offset = col + int(state["pan_offset"]) + row * state["row_stride"]
    base_note = 12 * (state["base_octave"] + 2) + state["root"]
    if state["chromatic"]:
        return base_note + degree_offset
    _scale_name, degrees = SCALES[state["scale_index"]]
    octave_offset = degree_offset // len(degrees)
    degree_index  = degree_offset % len(degrees)
    return base_note + octave_offset * 12 + degrees[degree_index]

def settings_color(pad: int, state: dict) -> LedColor:
    """Return the LedColor for *pad* while the settings overlay is shown."""
    if pad in SIDE_COLUMN_PADS and pad not in SCALE_SETTING_PADS:
        return BACKGROUND_OFF
    if pad in INACTIVE_SETTINGS_PADS:
        return BACKGROUND_OFF
    if pad in OVERLAP_SETTING_PADS:
        return SETTING_ON if state["row_stride"] == OVERLAP_SETTING_PADS[pad] else UNUSED_GREY
    if pad == AXIS_SETTING_PAD:
        return SETTING_ON if state["axis_flip"] else SETTING_DIM
    if pad == CHROMATIC_SETTING_PAD:
        return CHROMATIC_ON if state["chromatic"] else CHROMATIC_DIM
    if pad in ROOT_SETTING_PADS:
        note_class = ROOT_SETTING_PADS[pad]
        if state["root"] == note_class:
            return ROOT_ON
        if note_class in scale_mask(state):
            return ROOT_DIM
        return UNUSED_GREY
    if pad in SCALE_SETTING_PADS:
        return SCALE_ON if state["scale_index"] == SCALE_SETTING_PADS[pad] else SCALE_DIM
    if pad in MIDI_CHANNEL_SETTING_PADS:
        channel = MIDI_CHANNEL_SETTING_PADS[pad]
        if state["midi_channel"] == channel:
            return CHANNEL_ON
        return CHANNEL_DIM
    return BACKGROUND_OFF

def handle_settings_pad(pad: int, state: dict) -> bool:
    """Mutate *state* in-place for a settings-screen pad press.
    Returns True if the state was changed (caller should save + refresh).
    """
    if pad in OVERLAP_SETTING_PADS:
        state["row_stride"] = OVERLAP_SETTING_PADS[pad]
    elif pad == AXIS_SETTING_PAD:
        state["axis_flip"] = not state["axis_flip"]
    elif pad == CHROMATIC_SETTING_PAD:
        state["chromatic"] = not state["chromatic"]
    elif pad in ROOT_SETTING_PADS:
        state["root"] = ROOT_SETTING_PADS[pad]
    elif pad in SCALE_SETTING_PADS:
        state["scale_index"] = SCALE_SETTING_PADS[pad]
    elif pad in MIDI_CHANNEL_SETTING_PADS:
        state["midi_channel"] = MIDI_CHANNEL_SETTING_PADS[pad]
    else:
        return False
    return True

def note_mode_lighting(
    pad: int,
    state: dict,
    is_note_active_fn,
    channel_for_pad_fn,
    playable_pads: tuple,
) -> LedColor:
    """Return the LedColor for *pad* in note mode."""
    if pad not in playable_pads:
        return NOTE_OFF
    note = note_for_pad(pad, state)
    if not LOWEST_NOTE <= note <= HIGHEST_NOTE:
        return NOTE_OFF
    if is_note_active_fn(channel_for_pad_fn(pad), note):
        return NOTE_HELD
    pitch_class = note % 12
    if pitch_class == state["root"]:
        return NOTE_ROOT
    if state["chromatic"]:
        mask = scale_mask(state)
        return NOTE_IN_SCALE if pitch_class in mask else NOTE_OFF
    return NOTE_IN_SCALE

# Pan / octave navigation
def note_window_bounds(state: dict, base_octave=None, pan_offset=None) -> tuple[int, int]:
    """Return (min_note, max_note) visible across all playable pads."""
    orig_octave = state["base_octave"]
    orig_pan    = state["pan_offset"]
    if base_octave is not None:
        state["base_octave"] = base_octave
    if pan_offset is not None:
        state["pan_offset"] = pan_offset
    try:
        notes = [note_for_pad(pad, state) for pad in PLAYABLE_PADS]
    finally:
        state["base_octave"] = orig_octave
        state["pan_offset"]  = orig_pan
    return min(notes), max(notes)

def overscroll_margin(state: dict, base_octave=None, pan_offset=None) -> int:
    minimum, maximum = note_window_bounds(state, base_octave, pan_offset)
    visible_span = max(1, maximum - minimum)
    return max(4, visible_span // 2)

def can_display_window(state: dict, base_octave: int, pan_offset: int) -> bool:
    minimum, maximum = note_window_bounds(state, base_octave, pan_offset)
    margin = overscroll_margin(state, base_octave, pan_offset)
    return minimum >= LOWEST_NOTE - margin and maximum <= HIGHEST_NOTE + margin

def remaining_pan_steps(state: dict, direction: int, surface_mode: str = MODE_NOTE, limit: int = 32) -> int:
    if surface_mode != MODE_NOTE:
        return 0
    count       = 0
    base_octave = int(state["base_octave"])
    pan_offset  = int(state["pan_offset"])
    step = 1 if direction >= 0 else -1
    while count < limit:
        if not can_display_window(state, base_octave, pan_offset + step * (count + 1)):
            break
        count += 1
    return count

def remaining_octave_steps(state: dict, direction: int, surface_mode: str = MODE_NOTE, limit: int = 8) -> int:
    if surface_mode != MODE_NOTE:
        return 0
    count       = 0
    base_octave = int(state["base_octave"])
    pan_offset  = int(state["pan_offset"])
    step = 1 if direction >= 0 else -1
    while count < limit:
        if not can_display_window(state, base_octave + step * (count + 1), pan_offset):
            break
        count += 1
    return count

def is_window_valid(state: dict) -> bool:
    return can_display_window(state, int(state["base_octave"]), int(state["pan_offset"]))

def _snap_to_valid_pan(state: dict) -> None:
    base_octave = int(state["base_octave"])
    pan_offset  = int(state["pan_offset"])
    step = -1 if pan_offset > 0 else 1
    while pan_offset and not can_display_window(state, base_octave, pan_offset):
        pan_offset += step
    state["pan_offset"] = pan_offset

def _snap_to_valid_octave(state: dict) -> None:
    base_octave = int(state["base_octave"])
    pan_offset  = int(state["pan_offset"])
    step = -1 if base_octave > 0 else 1
    while base_octave and not can_display_window(state, base_octave, pan_offset):
        base_octave += step
    state["base_octave"] = base_octave

def step_pan(state: dict, direction: int) -> None:
    if not is_window_valid(state):
        _snap_to_valid_pan(state)
        return
    step      = 1 if direction >= 0 else -1
    candidate = int(state["pan_offset"]) + step
    if can_display_window(state, int(state["base_octave"]), candidate):
        state["pan_offset"] = candidate

def step_octave(state: dict, direction: int) -> None:
    if not is_window_valid(state):
        _snap_to_valid_octave(state)
        return
    step      = 1 if direction >= 0 else -1
    candidate = int(state["base_octave"]) + step
    if can_display_window(state, candidate, int(state["pan_offset"])):
        state["base_octave"] = candidate

# Arrow button colours (top-row navigation feedback)
def arrow_color(
    surface_mode: str,
    remaining_steps: int,
    active_color: int = LP3_ARROW_ACTIVE,
    out_of_range: bool = False,
) -> int:
    if surface_mode != MODE_NOTE:
        return LP3_ARROW_INACTIVE
    if out_of_range:
        return active_color
    if remaining_steps <= 0:
        return LP3_ARROW_INACTIVE
    return min(0x7F, active_color + max(0, 4 - remaining_steps))
# gargoyles rule