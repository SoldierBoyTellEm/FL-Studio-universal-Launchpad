# constants.py
# Every magic number, colour constant, pad layout, and default state value
# used across the script lives here.  Import with `from constants import *`
# or name-specifically; nothing in this file has side-effects.

# MK1 LED velocity encoding — Launchpad / Launchpad S / Mini MK1 / Mini MK2.
#
# The MK1 protocol encodes LED colour directly in the velocity byte:
#   velocity = (16 * green_brightness) + red_brightness + flags
# where green_brightness and red_brightness are each 0–3 (off/low/med/full)
# and flags = 12 (0x0C) for normal write mode.
#
# Convenience constructor — avoids scattering the formula everywhere:
def _mk1_vel(red: int, green: int) -> int:
    """Return MK1 LED velocity for red and green brightness values (each 0–3)."""
    return (16 * green) + red + 12

# MK1 OFF constant (red=0, green=0):
MK1_OFF = _mk1_vel(0, 0)   # 12

# Named mappings for the palette indices actually used by this script.
# Any index not listed here maps to MK1_OFF via MK1_VELOCITY_TABLE below.
#
# Semantic intent → (red 0-3, green 0-3):
#   off / disabled              → (0, 0)  vel=12
#   dim inactive                → (0, 1)  vel=28   low green
#   note in scale / dim green   → (0, 2)  vel=44   med green
#   note root / bright green    → (0, 3)  vel=60   full green
#   arrow active / action       → (3, 0)  vel=15   full red
#   menu active / selected      → (0, 3)  vel=60   full green  (no blue on MK1)
#   menu locked / pan arrow     → (3, 3)  vel=63   full amber
#   octave arrow active         → (1, 3)  vel=61   full green + low red (closest to teal)
#   setting dim                 → (1, 0)  vel=13   low red
#   setting on                  → (0, 3)  vel=60   full green
#   chromatic dim               → (0, 1)  vel=28   low green
#   chromatic on                → (3, 1)  vel=31   low green + full red (orange/amber)
#   step off                    → (0, 1)  vel=28   low green
#   step on                     → (3, 3)  vel=63   full amber
#   step selected               → (0, 3)  vel=60   full green
#   performance ready           → (3, 0)  vel=15   full red
#   performance hybrid          → (3, 3)  vel=63   full amber
#
# Indices reference the palette constants defined below (PAD_OFF=0x00, etc.).
_MK1_SEMANTIC: dict[int, int] = {
    0x00: _mk1_vel(0, 0),   # PAD_OFF / PAD_DISABLED / LP3_BACKGROUND_OFF
    0x01: _mk1_vel(0, 1),   # PAD_AVAILABLE / LP3_UNUSED_GREY / LP3_MENU_INACTIVE / LP3_STEP_OFF
    0x0A: _mk1_vel(1, 0),   # LP3_SETTING_DIM
    0x0D: _mk1_vel(3, 0),   # PAD_ACTION / LP3_ARROW_ACTIVE / LP3_CHANNEL_ON / LP3_PERFORMANCE_READY
    0x0F: _mk1_vel(1, 0),   # LP3_CHANNEL_DIM
    0x07: _mk1_vel(0, 1),   # LP3_CHROMATIC_DIM
    0x1C: _mk1_vel(0, 3),   # PAD_SELECTED / LP3_MENU_ACTIVE / LP3_STEP_SELECTED
    0x1D: _mk1_vel(3, 1),   # LP3_CHROMATIC_ON
    0x24: _mk1_vel(0, 2),   # PAD_IN_SCALE / LP3_NOTE_IN_SCALE / LP3_ROOT_DIM
    0x27: _mk1_vel(0, 1),   # LP3_SCALE_DIM
    0x28: _mk1_vel(1, 3),   # LP3_ARROW_OCTAVE_ACTIVE
    0x34: _mk1_vel(3, 3),   # LP3_MENU_LOCKED / LP3_ARROW_PAN_ACTIVE / LP3_ROOT_ON / LP3_STEP_ON
    0x49: _mk1_vel(3, 3),   # LP3_PERFORMANCE_HYBRID
    0x5E: _mk1_vel(0, 3),   # PAD_ROOT / LP3_NOTE_ROOT
    0x71: _mk1_vel(0, 3),   # LP3_SCALE_ON / LP3_SETTING_ON
    # MK2 palette aliases also used at runtime via rgb6_from_color / FPC lighting:
    0x15: _mk1_vel(1, 1),   # PAD_AUX / PAD_HELD  — low amber
}

def _build_mk1_velocity_table() -> tuple:
    table = [MK1_OFF] * 128
    for idx, vel in _MK1_SEMANTIC.items():
        if 0 <= idx < 128:
            table[idx] = vel
    return tuple(table)

MK1_VELOCITY_TABLE: tuple = _build_mk1_velocity_table()

# SysEx / layout IDs
LAYOUT_SESSION = 0x00
LAYOUT_USER_2  = 0x02
SYSEX_PREFIX    = (0xF0, 0x00, 0x20, 0x29, 0x02, 0x18)
SYSEX_LED_SET   = 0x0A
SYSEX_LED_SET_RGB = 0x0B
SYSEX_LED_PULSE = 0x28
SYSEX_LIGHT_ALL = 0x0E
SYSEX_LAYOUT    = 0x22
# Top-row CC numbers
TOP_CC_START    = 104
TOP_CC_END      = 111
TOP_OCTAVE_UP   = 104
TOP_OCTAVE_DOWN = 105
TOP_PAN_LEFT    = 106
TOP_PAN_RIGHT   = 107
TOP_PERFORMANCE = 108
TOP_NOTE_MODE   = 109
TOP_FPC_MODE    = 110
TOP_RECORD_ARM  = 111
# MIDI routing
SESSION_CHANNEL          = 0
USER2_FALLBACK_CHANNELS  = (13, 14, 15)
# XY pad generated CCs. 102/103 are in the undefined high CC range, avoiding
# common controls like mod wheel, volume, pan, expression, and sustain.
XY_PAD_X_CC = 102
XY_PAD_Y_CC = 103
# Fader page CCs: 8 vertical (col 0-7) and 8 horizontal (col 0-7)
XY_VERT_FADER_CCS  = tuple(range(104, 112))   # 104–111
XY_HORIZ_FADER_CCS = tuple(range(112, 120))   # 112–119
# Mod-wheel CC (standard MIDI CC 1)
performance_modwheel_CC = 1
# XY pad sub-page indices
XY_PAGE_XY      = 0
XY_PAGE_VERT    = 1
XY_PAGE_HORIZ   = 2
XY_PAGE_COUNT   = 3
# XY fader lighting (palette indices; micro-brightness applied via dim_palette_rgb)
XY_FADER_ON_COLOR   = 0x1C   # filled steps
XY_FADER_OFF_COLOR  = 0x01   # active-step dim track
performance_modwheel_COLOR   = 0x25   # modwheel column fill

# Surface mode names
MODE_NOTE        = "note"
MODE_FPC         = "fpc"
MODE_PERFORMANCE = "performance"
MODE_CUSTOM      = "custom"
MODE_XY_PAD      = "xy_pad"
MODE_STEP_SEQ    = "step_seq"
MODE_BLANK       = "blank"

# Note-range limits and timing constants
LOWEST_NOTE               = 0
HIGHEST_NOTE              = 131
TAP_AND_HOLD_DURATION_SECONDS = 0.4
NOTE_DOUBLE_TAP_SECONDS       = 0.35
PERFORMANCE_DOUBLE_TAP_SECONDS = 0.35
# Performance-mode geometry
PERFORMANCE_PAGE_WIDTH   = 8
PERFORMANCE_PAGE_HEIGHT  = 8
PERFORMANCE_PAD_STRIDE   = 10
PERFORMANCE_LAUNCH_MAP_WIDTH  = PERFORMANCE_PAD_STRIDE
PERFORMANCE_LAUNCH_MAP_HEIGHT = PERFORMANCE_PAGE_HEIGHT
PERFORMANCE_MAX_BLOCKS   = 512
PERFORMANCE_DIRECT_AUDIO_EXPERIMENTAL = True
PERFORMANCE_LAUNCH_MAP_NAME = "Novation Launchpad"
# Step-sequencer fallback geometry
STEP_SEQUENCER_WIDTH = 9
STEP_SEQUENCER_HEIGHT = 8
STEP_SEQUENCER_MAX_STEPS = 128
# Playlist / channel type IDs
CHANNEL_TYPE_AUDIO_CLIP = 4
WID_PLAYLIST            = 2
WID_PLUGIN              = 5
WID_PLUGIN_EFFECT       = 6
WID_PLUGIN_GENERATOR    = 7
LB_STATUS_SIMPLEST      = 2
LB_STATUS_FILLED        = 1
TLC_MUTE_OTHERS         = 1
TLC_FILL                = 2
# FPC colour tuning
# Keep separate values for MK2 and LP3-generation devices. The LEDs differ
# enough that one set of numbers is not a good universal fit.
FPC_COLOR_SATURATION_MK2 = 1.25
FPC_COLOR_GAMMA_MK2      = 0.5
FPC_COLOR_SATURATION_LP3 = 1.25
FPC_COLOR_GAMMA_LP3      = 0.5
# Channel-rack fallback colour tuning
# Keep this separate from FPC, and split it by hardware generation as well, so
# the Session fallback can be tuned independently on MK2 vs LP3 devices.
CHANNEL_RACK_COLOR_SATURATION_MK2 = 2
CHANNEL_RACK_COLOR_GAMMA_MK2      = 0.6
CHANNEL_RACK_COLOR_SATURATION_LP3 = 1.3
CHANNEL_RACK_COLOR_GAMMA_LP3      = 1
# Channel-rack tick dimming divisors (higher = dimmer; each RGB component is
# integer-divided by the value, floored at 1).
CHANNEL_RACK_DIM_INACTIVE_STEP    = 4   # off step on an unmuted channel
CHANNEL_RACK_DIM_MUTED_STEP       = 6   # step on a muted channel
CHANNEL_RACK_DIM_MUTED_TOGGLE     = 6  # channel toggle pad on a muted channel
CHANNEL_RACK_DIM_PLAYHEAD_STEP    = 0.5  # step under the playhead while playing (<1 brightens)
# Pad groups
SETTINGS_GRID_PADS = tuple(
    row * 10 + col
    for row in range(1, 9)
    for col in range(1, 9)
)
SIDE_COLUMN_PADS = tuple(row * 10 + 9 for row in range(1, 9))

# Right-column pads used as custom-mode slot selectors (top=slot 0, bottom=slot 7)
CUSTOM_MODE_SELECTOR_PADS = tuple(row * 10 + 9 for row in range(8, 0, -1))
PERFORMANCE_PAGE_HOTKEY_PADS = (89, 79, 69, 59)
PLAYABLE_PADS = SETTINGS_GRID_PADS + SIDE_COLUMN_PADS
TOP_CCS = tuple(range(TOP_CC_START, TOP_CC_END + 1))
GROSS_BEAT_SLOT_PADS = (
    81, 82, 83, 84,
    71, 72, 73, 74,
    61, 62, 63, 64,
    51, 52, 53, 54,
    41, 42, 43, 44,
    31, 32, 33, 34,
    21, 22, 23, 24,
    11, 12, 13, 14,
    85, 86, 87, 88,
)
GROSS_BEAT_TOGGLE_PAD = 17
GROSS_BEAT_FADER_PADS = (18, 28, 38, 48, 58, 68, 78)
GROSS_BEAT_TIME_COLOR = 0x1D
GROSS_BEAT_VOLUME_COLOR = 0x08
GROSS_BEAT_FADER_DIM_COLOR = 0x01
GROSS_BEAT_FADER_MICRO_COLORS = (0x0E, 0x1E, 0x2E, 0x3F)

# Plugin pad-override IDs
PLUGIN_PAD_OVERRIDE_GROSS_BEAT = "gross_beat"
PLUGIN_PAD_OVERRIDE_IDS = {
    "gross beat": PLUGIN_PAD_OVERRIDE_GROSS_BEAT,
}
FPC_QUADRANT_PADS = (
    (
        51, 52, 53, 54,
        61, 62, 63, 64,
        71, 72, 73, 74,
        81, 82, 83, 84,
    ),
    (
        55, 56, 57, 58,
        65, 66, 67, 68,
        75, 76, 77, 78,
        85, 86, 87, 88,
    ),
    (
        11, 12, 13, 14,
        21, 22, 23, 24,
        31, 32, 33, 34,
        41, 42, 43, 44,
    ),
    (
        15, 16, 17, 18,
        25, 26, 27, 28,
        35, 36, 37, 38,
        45, 46, 47, 48,
    ),
)
FPC_BANK_A_SELECTORS = (89, 79, 69, 59)
FPC_BANK_B_SELECTORS = (49, 39, 29, 19)
FPC_SELECTORS = FPC_BANK_A_SELECTORS + FPC_BANK_B_SELECTORS
FPC_PAGE_COUNT = 4
FPC_BANKS_PER_ROW = 4
# Palette colour constants
PAD_OFF       = 0x00
PAD_ROOT      = 0x5E
PAD_IN_SCALE  = 0x24
PAD_AUX       = 0x15
PAD_ACTION    = 0x0D
PAD_AVAILABLE = 0x01
PAD_SELECTED  = 0x1C
PAD_HELD      = 0x15
PAD_DISABLED  = 0x00
# LP3-style named colour indices
LP3_NOTE_ROOT       = 0x5E
LP3_NOTE_IN_SCALE   = 0x24
LP3_NOTE_OFF        = 0x00
LP3_BACKGROUND_OFF  = 0x00
LP3_UNUSED_GREY     = 0x01
LP3_SETTING_DIM     = 0x0A
LP3_SETTING_ON      = 0x71
LP3_ARROW_ACTIVE    = 0x0D
LP3_ARROW_INACTIVE  = 0x00
LP3_ARROW_OCTAVE_ACTIVE = 0x28
LP3_ARROW_PAN_ACTIVE    = 0x34
LP3_MENU_INACTIVE   = 0x01
LP3_MENU_ACTIVE     = 0x1C
LP3_MENU_LOCKED     = 0x34
LP3_PERFORMANCE_READY  = 0x0D
LP3_PERFORMANCE_HYBRID = 0x49
LP3_CHROMATIC_DIM   = 0x07
LP3_CHROMATIC_ON    = 0x1D
LP3_ROOT_DIM        = 0x24
LP3_ROOT_ON         = 0x34
LP3_SCALE_DIM       = 0x27
LP3_SCALE_ON        = 0x71
LP3_CHANNEL_DIM     = 0x0F
LP3_CHANNEL_ON      = 0x0D
LP3_STEP_OFF        = 0x01
LP3_STEP_ON         = 0x34
LP3_STEP_SELECTED   = 0x1C
# Full-brightness 6-bit RGB for the channel-lock pulse on the Note key (#e530ff).
NOTE_LOCK_PULSE_RGB = (57, 12, 63)
# FL Studio's default channel colour (warm tan) — used to detect unset channels
# and substitute white instead of rendering the bland default.
DEFAULT_FL_CHANNEL_RGB = (0xA5, 0x95, 0x78)
# Scale definitions  (name, semitone intervals from root)
SCALES = (
    ("Minor",             (0, 2, 3, 5, 7, 8, 10)),
    ("Major",             (0, 2, 4, 5, 7, 9, 11)),
    ("Dorian",            (0, 2, 3, 5, 7, 9, 10)),
    ("Phrygian",          (0, 1, 3, 5, 7, 8, 10)),
    ("Mixolydian",        (0, 2, 4, 5, 7, 9, 10)),
    ("Melodic Minor",     (0, 2, 3, 5, 7, 9, 11)),
    ("Harmonic Minor",    (0, 2, 3, 5, 7, 8, 11)),
    ("Bebop Dorian",      (0, 2, 3, 4, 5, 7, 9, 10)),
    ("Blues",             (0, 3, 5, 6, 7, 10)),
    ("Minor Pentatonic",  (0, 3, 5, 7, 10)),
    ("Hungarian Minor",   (0, 2, 3, 6, 7, 8, 11)),
    ("Ukrainian Dorian",  (0, 2, 3, 6, 7, 9, 10)),
    ("Marva",             (0, 1, 4, 6, 7, 9, 11)),
    ("Todi",              (0, 1, 3, 6, 7, 8, 11)),
    ("Whole",             (0, 2, 4, 6, 8, 10)),
    ("Hirajoshi",         (0, 2, 3, 7, 8)),
    ("Aeolian Dominant",  (0, 2, 4, 5, 7, 8, 10)),
    ("All Notes",         tuple(range(12))),
)

# Persistent state
STATE_FILE = "launchpad_unofficial_universal_state.json"

# Keys stored per-FLP in playlist track 500's name rather than the JSON file.
FLP_STATE_KEYS = frozenset({
    "channel_locks",
    "pan_offset",
    "fpc_page",
    "performance_modwheel",
    "fpc_slot_channels",
    "fpc_slot_banks",
    "fpc_quadrant_channels",
    "fpc_quadrant_banks",
    "surface_mode",
    "custom_mode_index",
    "xy_page",
    "xy_cursor_x",
    "xy_cursor_y",
    "xy_fader_values",
})

DEFAULT_STATE = {
    "surface_mode": MODE_NOTE,
    "custom_mode_index": 0,
    "xy_cursor_x": 0,
    "xy_cursor_y": 127,
    "xy_fader_values": {},
    "root": 0,
    "scale_index": 1,
    "chromatic": False,
    "row_stride": 5,
    "base_octave": 2,
    "axis_flip": False,
    "midi_channel": 0,
    "locked_channel": -1,
    "channel_locks": {},
    "pan_offset": 0,
    "performance_track_offset": 1,
    "performance_block_offset": 0,
    "performance_direct_audio": False,
    "step_channel_offset": 0,
    "step_offset": 0,
    "lights_out": False,
    "gross_beat_slot_mode": "time",
    "fpc_page": 0,
    "xy_page": 0,
    "performance_modwheel": False,
    "fpc_slot_channels": [-1] * 16,
    "fpc_slot_banks": [-1] * 16,
    "fpc_quadrant_channels": [-1, -1, -1, -1],
    "fpc_quadrant_banks": [0, 16, -1, -1],
}

# Settings-screen pad maps
OVERLAP_SETTING_PADS = {
    81: 8,  # sequential
    82: 2,
    83: 3,
    84: 4,
    85: 5,
}
AXIS_SETTING_PAD     = 87
CHROMATIC_SETTING_PAD = 88
INACTIVE_SETTINGS_PADS = {
    86,
    71, 72, 73, 74, 75, 76, 77, 78,
    61, 64, 68,
    58,
}
ROOT_SETTING_PADS = {
    62: 1,  63: 3,  65: 6,  66: 8,  67: 10,
    51: 0,  52: 2,  53: 4,  54: 5,  55: 7,  56: 9,  57: 11,
}
SCALE_SETTING_PADS = {
    41: 0,  42: 1,  43: 2,  44: 3,  45: 4,  46: 5,  47: 6,  48: 7,
    31: 8,  32: 9,  33: 10, 34: 11, 35: 12, 36: 13, 37: 14, 38: 15,
    49: 16, 39: 17,
}
MIDI_CHANNEL_SETTING_PADS = {
    21: 0,  22: 1,  23: 2,  24: 3,  25: 4,  26: 5,  27: 6,  28: 7,
    11: 8,  12: 9,  13: 10, 14: 11, 15: 12, 16: 13, 17: 14, 18: 15,
}

# Palette RGB tables
# MK2: 64-step (0-63 per channel) palette from factory dump.
MK2_PALETTE_RGB = (
    (122, 186, 250),
    (0, 0, 0),
    (16, 16, 16),
    (32, 32, 32),
    (63, 63, 63),
    (63, 15, 15),
    (63, 0, 0),
    (32, 0, 0),
    (16, 0, 0),
    (63, 46, 26),
    (63, 15, 0),
    (32, 8, 0),
    (16, 4, 0),
    (63, 43, 11),
    (63, 63, 0),
    (32, 32, 0),
    (16, 16, 0),
    (33, 63, 12),
    (20, 63, 0),
    (10, 32, 0),
    (5, 16, 0),
    (18, 63, 18),
    (0, 63, 0),
    (0, 32, 0),
    (0, 16, 0),
    (18, 63, 23),
    (0, 63, 6),
    (0, 32, 3),
    (0, 16, 1),
    (18, 63, 22),
    (0, 63, 21),
    (0, 32, 11),
    (0, 16, 6),
    (18, 63, 45),
    (0, 63, 37),
    (0, 32, 18),
    (0, 16, 9),
    (18, 48, 63),
    (0, 41, 63),
    (0, 21, 32),
    (0, 11, 16),
    (18, 33, 63),
    (0, 21, 63),
    (0, 11, 32),
    (0, 6, 16),
    (11, 9, 63),
    (0, 0, 63),
    (0, 0, 32),
    (0, 0, 16),
    (26, 13, 62),
    (11, 0, 63),
    (6, 0, 32),
    (3, 0, 16),
    (63, 15, 63),
    (63, 0, 63),
    (32, 0, 32),
    (16, 0, 16),
    (63, 16, 27),
    (63, 0, 20),
    (32, 0, 10),
    (16, 0, 5),
    (63, 3, 0),
    (37, 13, 0),
    (29, 20, 0),
    (8, 13, 1),
    (0, 14, 0),
    (0, 18, 6),
    (0, 5, 27),
    (0, 0, 63),
    (0, 17, 19),
    (4, 0, 50),
    (31, 31, 31),
    (7, 7, 7),
    (63, 0, 0),
    (46, 63, 11),
    (43, 58, 1),
    (24, 63, 2),
    (3, 34, 0),
    (0, 63, 23),
    (0, 41, 63),
    (0, 10, 63),
    (6, 0, 63),
    (22, 0, 63),
    (43, 6, 30),
    (10, 4, 0),
    (63, 12, 0),
    (33, 55, 1),
    (28, 63, 5),
    (0, 63, 0),
    (14, 63, 9),
    (21, 63, 27),
    (13, 63, 50),
    (22, 34, 63),
    (12, 20, 48),
    (26, 20, 57),
    (52, 7, 63),
    (63, 0, 22),
    (63, 17, 0),
    (45, 41, 0),
    (35, 63, 0),
    (32, 22, 1),
    (14, 10, 0),
    (0, 18, 3),
    (3, 19, 8),
    (5, 5, 10),
    (5, 7, 22),
    (25, 14, 6),
    (32, 0, 0),
    (54, 16, 10),
    (53, 18, 4),
    (63, 47, 9),
    (39, 55, 11),
    (25, 44, 3),
    (5, 5, 11),
    (54, 52, 26),
    (31, 58, 34),
    (38, 37, 63),
    (35, 25, 63),
    (15, 15, 15),
    (28, 28, 28),
    (55, 63, 63),
    (39, 0, 0),
    (13, 0, 0),
    (6, 51, 0),
    (1, 16, 0),
    (45, 43, 0),
    (15, 12, 0),
    (44, 20, 0),
)

# LP3: 128-step (0-255 per channel) palette shared by LPX and Mini MK3.
LP3_PALETTE_RGB = (
    (0, 0, 0),
    (63, 63, 63),
    (127, 127, 127),
    (255, 255, 255),
    (63, 63, 255),
    (0, 0, 255),
    (0, 0, 127),
    (0, 0, 63),
    (111, 191, 255),
    (0, 63, 255),
    (0, 31, 127),
    (0, 15, 63),
    (47, 175, 255),
    (0, 255, 255),
    (0, 127, 127),
    (0, 63, 63),
    (47, 255, 127),
    (0, 255, 79),
    (0, 127, 47),
    (0, 63, 23),
    (63, 255, 79),
    (0, 255, 0),
    (0, 127, 0),
    (0, 63, 0),
    (79, 255, 79),
    (31, 255, 0),
    (15, 127, 0),
    (7, 63, 0),
    (95, 255, 79),
    (95, 255, 0),
    (47, 127, 0),
    (23, 63, 0),
    (191, 255, 79),
    (159, 255, 0),
    (79, 127, 0),
    (39, 63, 0),
    (255, 191, 79),
    (255, 175, 0),
    (127, 87, 0),
    (63, 47, 0),
    (255, 127, 79),
    (255, 87, 0),
    (127, 47, 0),
    (63, 23, 0),
    (255, 31, 47),
    (255, 0, 0),
    (127, 0, 0),
    (63, 0, 0),
    (255, 63, 95),
    (255, 0, 47),
    (127, 0, 23),
    (63, 0, 15),
    (255, 63, 255),
    (255, 0, 255),
    (127, 0, 127),
    (63, 0, 63),
    (111, 63, 255),
    (79, 0, 255),
    (47, 0, 127),
    (31, 0, 63),
    (0, 15, 255),
    (0, 63, 159),
    (0, 79, 127),
    (0, 47, 47),
    (0, 63, 0),
    (31, 63, 0),
    (111, 31, 0),
    (255, 0, 0),
    (63, 63, 0),
    (191, 0, 31),
    (79, 63, 95),
    (23, 15, 31),
    (0, 0, 255),
    (47, 255, 191),
    (0, 239, 175),
    (0, 255, 95),
    (0, 127, 15),
    (95, 255, 0),
    (255, 159, 0),
    (255, 47, 0),
    (255, 0, 31),
    (239, 0, 95),
    (127, 31, 175),
    (0, 15, 47),
    (0, 47, 255),
    (0, 223, 127),
    (31, 255, 111),
    (0, 255, 0),
    (47, 255, 63),
    (111, 239, 95),
    (207, 255, 63),
    (255, 143, 95),
    (207, 79, 47),
    (223, 79, 111),
    (255, 31, 223),
    (95, 0, 255),
    (0, 79, 255),
    (0, 175, 191),
    (0, 255, 143),
    (0, 95, 127),
    (0, 47, 63),
    (15, 71, 0),
    (31, 79, 15),
    (47, 23, 23),
    (95, 31, 23),
    (23, 55, 95),
    (0, 0, 127),
    (47, 63, 223),
    (15, 71, 223),
    (31, 191, 255),
    (47, 223, 159),
    (15, 175, 111),
    (47, 23, 23),
    (111, 223, 223),
    (143, 239, 127),
    (255, 159, 159),
    (255, 111, 143),
    (63, 63, 63),
    (111, 111, 111),
    (255, 255, 223),
    (0, 0, 159),
    (0, 0, 55),
    (0, 207, 23),
    (0, 63, 0),
    (0, 175, 191),
    (0, 47, 63),
    (0, 79, 175),
    (0, 15, 79),
)

PALETTE_RGB_BY_FAMILY: dict[str, tuple] = {
    "mk1":  MK2_PALETTE_RGB,
    "mk2":  MK2_PALETTE_RGB,
    "lp3":  LP3_PALETTE_RGB,
    "lpx":  LP3_PALETTE_RGB,
    "lpm3": LP3_PALETTE_RGB,
}

# User overrides — copy any constant from above into constants_user.py and
# change its value there.  That file is never touched by script updates, so
# your changes survive a drop-in replace of all other files.
try:
    from constants_user import *  # noqa: F401, F403
except ImportError:
    pass
