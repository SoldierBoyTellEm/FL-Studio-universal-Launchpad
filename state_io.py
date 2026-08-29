# state_io.py
# Handles reading and writing the persistent JSON state file and FLP-embedded state.
import json
from copy import deepcopy
from fl_stubs import playlist as _playlist
from constants import (
    DEFAULT_STATE,
    FLP_STATE_KEYS,
    SCALES,
    STEP_SEQUENCER_MAX_STEPS,
    MODE_NOTE, MODE_FPC, MODE_PERFORMANCE, MODE_CUSTOM,
    MODE_XY_PAD, MODE_STEP_SEQ, MODE_MIXER, MODE_BLANK,
)

_VALID_MODES = frozenset({
    MODE_NOTE, MODE_FPC, MODE_PERFORMANCE, MODE_CUSTOM,
    MODE_XY_PAD, MODE_STEP_SEQ, MODE_MIXER, MODE_BLANK,
})

_FLP_TRACK = 500
_FLP_PREFIX = "LP:"
_warnings: list[str] = []
_LEGACY_JSON_KEYS = frozenset({"fpc_bank_channels", "fpc_quadrant_channels", "fpc_quadrant_banks"})


def default_state() -> dict:
    """Return an independent state object for one surface instance.

    DEFAULT_STATE intentionally documents the schema, but it contains lists
    and dictionaries.  A shallow copy would let two newly-created surfaces
    share channel locks, routes, or XY cursor positions.
    """
    return deepcopy(DEFAULT_STATE)

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
    state = default_state()
    try:
        root = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return state, True
    except Exception:
        return state, False
    loaded = root.get(str(port), root) if isinstance(root, dict) else {}
    if not isinstance(loaded, dict):
        _warnings.append("json: port state is not an object; using defaults")
        loaded = {}
    known = frozenset(DEFAULT_STATE) | _LEGACY_JSON_KEYS
    for key in loaded:
        if key not in known:
            _warnings.append(f"json: unrecognised key {key!r} discarded")
    for key in DEFAULT_STATE:
        if key in loaded:
            state[key] = loaded[key]
    normalize_state(state, has_xy_cursor_positions="xy_cursor_positions" in loaded)
    return state, False


def normalize_state(state: dict, *, has_xy_cursor_positions: bool) -> None:
    """Normalize state in-place after *any* persistence source is merged.

    JSON and FLP state use the same schema.  Keeping their validation here
    prevents a project load from bypassing the checks applied to the JSON
    file at startup.
    """
    state["root"] = _clamp_warn(_int(state.get("root"), 0), 0, 11, "root")
    state["scale_index"] = _clamp_warn(_int(state.get("scale_index"), 1), 0, len(SCALES) - 1, "scale_index")
    row_stride = _int(state.get("row_stride"), 5)
    if row_stride not in (2, 3, 4, 5, 6, 7, 8, 9):
        _warnings.append(f"row_stride={state.get('row_stride')} invalid, reset to 5")
        state["row_stride"] = 5
    else:
        state["row_stride"] = row_stride
    state["base_octave"] = _clamp_warn(_int(state.get("base_octave"), 2), 0, 7, "base_octave")
    state["chromatic"] = bool(state.get("chromatic", False))
    state["axis_flip"] = bool(state.get("axis_flip", False))
    state["midi_channel"] = _clamp_warn(_int(state.get("midi_channel"), 0), 0, 15, "midi_channel")
    # locked_channel: -1 = unlocked, >= 0 = locked to that channel index.
    # Upper bound is not validated here (channel count unknown at load time)
    # but a nonsensical large value would cause _channel_lock_enabled to spin
    # on channelCount() every idle tick; clamp to a safe ceiling.
    locked = _int(state.get("locked_channel"), -1)
    if locked < -1:
        _warnings.append(f"locked_channel={locked} below -1, reset to -1")
        locked = -1
    elif locked > 255:
        _warnings.append(f"locked_channel={locked} unreasonably large, reset to -1")
        locked = -1
    state["locked_channel"] = locked
    state["performance_track_offset"] = max(1, _int(state.get("performance_track_offset"), 1))
    state["performance_block_offset"] = max(0, _int(state.get("performance_block_offset"), 0))
    state["performance_direct_audio"] = bool(state.get("performance_direct_audio", False))
    state["step_channel_offset"] = max(0, _int(state.get("step_channel_offset"), 0))
    step_offset = max(0, _int(state.get("step_offset"), 0))
    if step_offset >= STEP_SEQUENCER_MAX_STEPS:
        _warnings.append(f"step_offset={step_offset} >= max {STEP_SEQUENCER_MAX_STEPS}, reset to 0")
        step_offset = 0
    state["step_offset"] = step_offset
    state["step_dual_page"] = bool(state.get("step_dual_page", False))
    state["lights_out"] = bool(state.get("lights_out", False))
    shortcuts = state.get("view_shortcuts", [None] * 8)
    if not isinstance(shortcuts, list):
        _warnings.append("view_shortcuts invalid, reset")
        shortcuts = [None] * 8
    state["view_shortcuts"] = [
        item if isinstance(item, dict) and item.get("mode") in _VALID_MODES else None
        for item in (shortcuts + [None] * 8)[:8]
    ]
    state["gross_beat_slot_mode"] = (
        "volume"
        if str(state.get("gross_beat_slot_mode", "time")).lower() == "volume"
        else "time"
    )
    if has_xy_cursor_positions:
        state["xy_cursor_positions"] = _xy_cursor_positions(state.get("xy_cursor_positions"))
    else:
        # Pre-existing file from before per-pad memory: seed all 4 pads from
        # the single shared position they used to share, so upgrading
        # doesn't reset anyone's XY cursor back to the corner.
        legacy_x = _clamp(_int(state.get("xy_cursor_x"), 0), 0, 127)
        legacy_y = _clamp(_int(state.get("xy_cursor_y"), 127), 0, 127)
        state["xy_cursor_positions"] = [[legacy_x, legacy_y] for _ in range(4)]
    state["xy_fader_values"] = _float_dict(state.get("xy_fader_values"), 0.0, 127.0)
    state["fpc_page"] = _clamp(_int(state.get("fpc_page"), 0), 0, 3)
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
    state["channel_locks"] = _channel_locks(state.get("channel_locks"))
    state["channel_routes"] = _channel_routes(state.get("channel_routes"))
    state["routing_page_offset"] = max(0, _int(state.get("routing_page_offset"), 0))
    state["surface_mode"] = state.get("surface_mode") if state.get("surface_mode") in _VALID_MODES else MODE_NOTE
    state["custom_mode_index"] = max(0, _int(state.get("custom_mode_index"), 0))

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
    if not isinstance(loaded, dict):
        _warnings.append("flp: port state is not an object; ignoring it")
        return
    for key in loaded:
        if key not in FLP_STATE_KEYS:
            _warnings.append(f"flp: unrecognised key {key!r} discarded")
    for key in FLP_STATE_KEYS:
        if key in loaded:
            state[key] = loaded[key]
    normalize_state(state, has_xy_cursor_positions="xy_cursor_positions" in loaded)

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


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_dict(value, minimum: float, maximum: float) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        try:
            result[str(int(key))] = max(minimum, min(maximum, float(item)))
        except (TypeError, ValueError):
            continue
    return result


def _channel_locks(value) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(context): _clamp(_int(channel, -1), -1, 255)
        for context, channel in value.items()
    }


def _channel_routes(value) -> dict[str, list[int]]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for context, channels in value.items():
        if not isinstance(channels, (list, tuple)):
            continue
        valid = sorted({_int(channel, -1) for channel in channels if _int(channel, -1) >= 0})
        if valid:
            result[str(context)] = valid
    return result

def _xy_cursor_positions(value) -> list[list[int]]:
    """Validate the 4 [x, y] pairs behind the parallel XY pads. Any pair
    that isn't a clean, in-range [x, y] falls back to the pad's default
    rather than discarding the whole list, so one corrupt entry can't cost
    the other 3 pads their remembered position."""
    default = [[0, 127] for _ in range(4)]
    if not isinstance(value, list):
        return default
    out = []
    for i in range(4):
        pair = value[i] if i < len(value) else None
        try:
            x = _clamp(int(pair[0]), 0, 127)
            y = _clamp(int(pair[1]), 0, 127)
            out.append([x, y])
        except (TypeError, ValueError, IndexError):
            out.append(list(default[i]))
    return out

def _int_list(
    value,
    default: list[int],
    allowed_values: tuple[int, ...] | None = None,
    fallback_value: int = -1,
) -> list[int]:
    if not isinstance(value, list):
        value = list(default)
    values = (list(value) + list(default))[:len(default)]
    coerced = [_int(item, fallback_value) for item in values]
    if allowed_values is None:
        return coerced
    return [item if item in allowed_values else fallback_value for item in coerced]
# ~gargoyles rule~
