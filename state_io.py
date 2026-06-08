# state_io.py
# Handles reading and writing the persistent JSON state file and FLP-embedded state.
import json
from constants import (
    DEFAULT_STATE,
    FLP_STATE_KEYS,
    SCALES,
    STEP_SEQUENCER_MAX_STEPS,
    MODE_NOTE, MODE_FPC, MODE_PERFORMANCE, MODE_CUSTOM,
    MODE_XY_PAD, MODE_STEP_SEQ, MODE_BLANK,
)

_VALID_MODES = frozenset({
    MODE_NOTE, MODE_FPC, MODE_PERFORMANCE, MODE_CUSTOM,
    MODE_XY_PAD, MODE_STEP_SEQ, MODE_BLANK,
})
from fl_stubs import playlist as _playlist

_FLP_TRACK = 500
_FLP_PREFIX = "LP:"
_warnings: list[str] = []
_LEGACY_JSON_KEYS = frozenset({"fpc_bank_channels", "fpc_quadrant_channels", "fpc_quadrant_banks"})

def load_state(state_path, port: int) -> tuple[dict, bool]:
    """Read persisted state for *port* from *state_path* and return a validated copy
    plus a bool indicating whether the file was absent (True = file was missing).
    Keys missing from the file fall back to DEFAULT_STATE values.
    Out-of-range values are clamped/sanitised so the rest of the script
    never has to guard against corrupt data.
    Unknown keys found in the file are warned about and discarded.
    """
    global _warnings
    _warnings = []
    state = dict(DEFAULT_STATE)
    try:
        root = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return state, True
    except Exception:
        return state, False
    loaded = root.get(str(port), root) if isinstance(root, dict) else {}
    known = frozenset(DEFAULT_STATE) | _LEGACY_JSON_KEYS
    for key in loaded:
        if key not in known:
            _warnings.append(f"json: unrecognised key {key!r} discarded")
    for key in DEFAULT_STATE:
        if key in loaded:
            state[key] = loaded[key]
    state["root"] = _clamp_warn(int(state["root"]), 0, 11, "root")
    state["scale_index"] = _clamp_warn(int(state["scale_index"]), 0, len(SCALES) - 1, "scale_index")
    if int(state["row_stride"]) not in (2, 3, 4, 5, 8):
        _warnings.append(f"row_stride={state['row_stride']} invalid, reset to 5")
        state["row_stride"] = 5
    else:
        state["row_stride"] = int(state["row_stride"])
    state["base_octave"] = _clamp_warn(int(state["base_octave"]), 0, 7, "base_octave")
    state["chromatic"] = bool(state["chromatic"])
    state["axis_flip"] = bool(state["axis_flip"])
    state["midi_channel"] = _clamp_warn(int(state["midi_channel"]), 0, 15, "midi_channel")
    # locked_channel: -1 = unlocked, >= 0 = locked to that channel index.
    # Upper bound is not validated here (channel count unknown at load time)
    # but a nonsensical large value would cause _channel_lock_enabled to spin
    # on channelCount() every idle tick; clamp to a safe ceiling.
    locked = int(state.get("locked_channel", -1))
    if locked < -1:
        _warnings.append(f"locked_channel={locked} below -1, reset to -1")
        locked = -1
    elif locked > 255:
        _warnings.append(f"locked_channel={locked} unreasonably large, reset to -1")
        locked = -1
    state["locked_channel"] = locked
    state["performance_track_offset"] = max(1, int(state.get("performance_track_offset", 1)))
    state["performance_block_offset"] = max(0, int(state.get("performance_block_offset", 0)))
    state["performance_direct_audio"] = bool(state.get("performance_direct_audio", False))
    state["step_channel_offset"] = max(0, int(state.get("step_channel_offset", 0)))
    step_offset = max(0, int(state.get("step_offset", 0)))
    if step_offset >= STEP_SEQUENCER_MAX_STEPS:
        _warnings.append(f"step_offset={step_offset} >= max {STEP_SEQUENCER_MAX_STEPS}, reset to 0")
        step_offset = 0
    state["step_offset"] = step_offset
    state["lights_out"] = bool(state.get("lights_out", False))
    state["gross_beat_slot_mode"] = (
        "volume"
        if str(state.get("gross_beat_slot_mode", "time")).lower() == "volume"
        else "time"
    )
    state["fpc_page"] = _clamp(int(state.get("fpc_page", 0)), 0, 3)
    state["fpc_slot_channels"] = _int_list(
        state.get("fpc_slot_channels", state.get("fpc_bank_channels")),
        [-1] * 16,
    )
    state["fpc_slot_banks"] = _int_list(
        state.get("fpc_slot_banks"),
        [-1] * 16,
        allowed_values=(-1, 0, 16),
        fallback_value=-1,
    )
    state["fpc_quadrant_channels"] = _int_list(
        state.get("fpc_quadrant_channels"),
        [-1, -1, -1, -1],
    )
    state["fpc_quadrant_banks"] = _int_list(
        state.get("fpc_quadrant_banks"),
        [0, 16, -1, -1],
        allowed_values=(-1, 0, 16),
        fallback_value=-1,
    )
    if all(int(v) < 0 for v in state["fpc_slot_channels"]):
        for slot_index, legacy_channel in zip((0, 1, 4, 5), state["fpc_quadrant_channels"]):
            state["fpc_slot_channels"][slot_index] = int(legacy_channel)
    if all(int(v) < 0 for v in state["fpc_slot_banks"]):
        for slot_index, legacy_bank in zip((0, 1, 4, 5), state["fpc_quadrant_banks"]):
            state["fpc_slot_banks"][slot_index] = int(legacy_bank) if int(legacy_bank) in (-1, 0, 16) else -1
    return state, False

def save_state(state_path, port: int, state: dict) -> None:
    """Serialise non-FLP keys of *state* for *port* into *state_path* as pretty-printed JSON."""
    try:
        root = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(root, dict) or not any(k.isdigit() for k in root):
            root = {}
    except Exception:
        root = {}
    root[str(port)] = {k: v for k, v in state.items() if k not in FLP_STATE_KEYS}
    state_path.write_text(json.dumps(root, indent=2), encoding="utf-8")

def load_flp_state(port: int, state: dict) -> None:
    """Merge FLP-embedded state for *port* from playlist track 500 into *state* in-place."""
    raw = _playlist.getTrackName(_FLP_TRACK)
    if not raw.startswith(_FLP_PREFIX):
        return
    try:
        root = json.loads(raw[len(_FLP_PREFIX):])
    except Exception:
        return
    loaded = root.get(str(port), root) if isinstance(root, dict) else {}
    for key in loaded:
        if key not in FLP_STATE_KEYS:
            _warnings.append(f"flp: unrecognised key {key!r} discarded")
    for key in FLP_STATE_KEYS:
        if key in loaded:
            state[key] = loaded[key]
    if state.get("surface_mode") not in _VALID_MODES:
        state["surface_mode"] = MODE_NOTE

def save_flp_state(port: int, state: dict) -> None:
    """Write FLP-specific keys for *port* into playlist track 500's name."""
    raw = _playlist.getTrackName(_FLP_TRACK)
    try:
        parsed = json.loads(raw[len(_FLP_PREFIX):]) if raw.startswith(_FLP_PREFIX) else {}
        root = {k: v for k, v in parsed.items() if isinstance(parsed, dict) and k.isdigit()}
    except Exception:
        root = {}
    root[str(port)] = {k: state[k] for k in FLP_STATE_KEYS if k in state}
    _playlist.setTrackName(_FLP_TRACK, _FLP_PREFIX + json.dumps(root, separators=(",", ":")))

# Internal helpers
def pop_warnings() -> list[str]:
    """Return and clear any warnings generated by the last load_state call."""
    global _warnings
    result = list(_warnings)
    _warnings = []
    return result

def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))

def _clamp_warn(value: int, minimum: int, maximum: int, key: str) -> int:
    clamped = max(minimum, min(maximum, value))
    if clamped != value:
        _warnings.append(f"{key}={value} out of range [{minimum},{maximum}], clamped to {clamped}")
    return clamped

def _int_list(
    value,
    default: list[int],
    allowed_values: tuple[int, ...] | None = None,
    fallback_value: int = -1,
) -> list[int]:
    if not isinstance(value, list):
        value = list(default)
    values = (list(value) + list(default))[:len(default)]
    coerced = [int(item) for item in values]
    if allowed_values is None:
        return coerced
    return [item if item in allowed_values else fallback_value for item in coerced]
# gargoyles rule