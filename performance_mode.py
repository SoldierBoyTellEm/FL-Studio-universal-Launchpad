# performance_mode.py
# Performance / live-clip mode: track+block grid rendering, clip triggering,
# page-hotkey navigation, direct-audio FPC hybrid, and launch map integration.
#
# All FL Studio API objects are imported from fl_stubs so the file can be
# read and unit-tested outside FL Studio.
from fl_stubs import (
    channels,
    launchMapPages,
    playlist,
    ui,
)
from constants import (
    SETTINGS_GRID_PADS,
    SIDE_COLUMN_PADS,
    PLAYABLE_PADS,
    PERFORMANCE_PAGE_HOTKEY_PADS,
    PERFORMANCE_PAGE_WIDTH,
    PERFORMANCE_PAGE_HEIGHT,
    PERFORMANCE_PAD_STRIDE,
    PERFORMANCE_LAUNCH_MAP_WIDTH,
    PERFORMANCE_LAUNCH_MAP_HEIGHT,
    PERFORMANCE_MAX_BLOCKS,
    PERFORMANCE_DIRECT_AUDIO_EXPERIMENTAL,
    PERFORMANCE_LAUNCH_MAP_NAME,
    CHANNEL_TYPE_AUDIO_CLIP,
    WID_PLAYLIST,
    LB_STATUS_SIMPLEST,
    LB_STATUS_FILLED,
    TLC_MUTE_OTHERS,
    TLC_FILL,
    MODE_PERFORMANCE,
    PAD_DISABLED,
    PAD_OFF,
    LP3_BACKGROUND_OFF,
    LP3_MENU_ACTIVE,
    LP3_PERFORMANCE_READY,
    LP3_ARROW_INACTIVE,
    LedColor,
)
from led_display import rgb6_from_color, rgb_max_value
# Internal log helper (same prefix as the rest of the script)

def _log(message: str) -> None:
    print(f"[NovLPd unofficial universal] {message}")

def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))

def performance_available() -> bool:
    try:
        return int(playlist.getPerformanceModeState()) == 1
    except Exception:
        return False

def performance_track_count() -> int:
    try:
        return max(0, int(playlist.getTrackCount()))
    except Exception:
        try:
            return max(0, int(playlist.trackCount()))
        except Exception:
            return 0

# Grid coordinate helpers
def track_for_pad(pad: int, state: dict) -> int:
    row_from_top = 8 - (pad // 10)
    return int(state["performance_track_offset"]) + row_from_top

def block_for_pad(pad: int, state: dict) -> int:
    col = (pad % 10) - 1
    return int(state["performance_block_offset"]) + col

# Page hotkey helpers
def page_hotkey_target(pad: int) -> tuple[int, int] | None:
    try:
        index = PERFORMANCE_PAGE_HOTKEY_PADS.index(pad)
    except ValueError:
        return None
    row = index // 2
    col = index % 2
    return (1 + row * PERFORMANCE_PAGE_HEIGHT, col * PERFORMANCE_PAGE_WIDTH)

def selected_page_hotkey(state: dict) -> int | None:
    current = (
        int(state["performance_track_offset"]),
        int(state["performance_block_offset"]),
    )
    for pad in PERFORMANCE_PAGE_HOTKEY_PADS:
        if page_hotkey_target(pad) == current:
            return pad
    return None

def trigger_page_hotkey(pad: int, state: dict) -> None:
    target = page_hotkey_target(pad)
    if target is None:
        return
    state["performance_track_offset"] = target[0]
    state["performance_block_offset"]  = target[1]
def live_block_status(track: int, block: int) -> int:
    try:
        return int(playlist.getLiveBlockStatus(track, block, LB_STATUS_SIMPLEST))
    except Exception:
        try:
            return int(playlist.getLiveBlockStatus(track, block))
        except Exception:
            return 0

def pad_has_live_clip(pad: int, state: dict) -> bool:
    if pad not in SETTINGS_GRID_PADS:
        return False
    track = track_for_pad(pad, state)
    block = block_for_pad(pad, state)
    if track < 1 or track > performance_track_count():
        return False
    return live_block_status(track, block) >= LB_STATUS_FILLED

def trigger_pad(pad: int, velocity: int, state: dict) -> None:
    if not performance_available():
        return
    if pad in SIDE_COLUMN_PADS:
        trigger_page_hotkey(pad, state)
        return
    if pad not in SETTINGS_GRID_PADS:
        return
    track = track_for_pad(pad, state)
    block = block_for_pad(pad, state)
    if track < 1 or track > performance_track_count():
        return
    flags = TLC_MUTE_OTHERS | TLC_FILL
    try:
        if velocity > 0:
            playlist.triggerLiveClip(track, block, flags, velocity * (1 / 127))
        else:
            playlist.triggerLiveClip(track, block, flags)
    except Exception:
        return
def performance_lighting(
    pad: int,
    state: dict,
    fpc_lighting_fn,
) -> LedColor:
    """Return the LedColor for *pad* in performance mode.
    *fpc_lighting_fn(pad)* is called for empty-pad hybrid fallback.
    """
    if pad not in PLAYABLE_PADS:
        return LedColor(PAD_DISABLED)
    if not performance_available():
        return LedColor(PAD_DISABLED)
    if pad in SIDE_COLUMN_PADS:
        target = page_hotkey_target(pad)
        if target is None:
            return LedColor(LP3_BACKGROUND_OFF)
        sel = selected_page_hotkey(state)
        if sel is None:
            return LedColor(LP3_BACKGROUND_OFF)
        return LedColor(LP3_MENU_ACTIVE if pad == sel else LP3_BACKGROUND_OFF)
    track = track_for_pad(pad, state)
    block = block_for_pad(pad, state)
    if track < 1 or track > performance_track_count():
        return LedColor(PAD_DISABLED)
    status = live_block_status(track, block)
    if status <= 0:
        if bool(state.get("performance_direct_audio", False)):
            return fpc_lighting_fn(pad)
        return LedColor(LP3_BACKGROUND_OFF)
    try:
        color = int(playlist.getLiveBlockColor(track, block))
    except Exception:
        color = 0
    rgb = rgb6_from_color(color)
    if status >= 2:
        maximum = rgb_max_value()
        rgb = tuple(min(maximum, c + 16) for c in rgb)
    return LedColor(PAD_OFF, rgb)

# Direct-audio / hybrid mode
def try_empty_pad_fallback(
    pad: int,
    velocity: int,
    pressed: bool,
    state: dict,
    performance_direct_pads: dict,
    active_notes: dict,
    fpc_assignment_fn,
    fpc_pad_note_fn,
    midi_channel: int,
) -> bool:
    """Handle an empty-pad press in hybrid mode (direct FPC audio).
    Returns True if the event was consumed.
    """
    if not bool(state.get("performance_direct_audio", False)):
        return False
    if pad not in SETTINGS_GRID_PADS:
        return False
    if pad_has_live_clip(pad, state):
        return False
    if pressed:
        assignment = fpc_assignment_fn(pad)
        if assignment is None:
            return True
        channel_index, pad_index = assignment
        note = fpc_pad_note_fn(channel_index, pad_index)
        if channel_index < 0 or note < 0:
            return True
        performance_direct_pads[pad] = (channel_index, note)
        key = (channel_index, note)
        active_notes[key] = active_notes.get(key, 0) + 1
        channels.midiNoteOn(channel_index, note, max(1, velocity or 127), midi_channel)
        return True
    direct_note = performance_direct_pads.pop(pad, None)
    if direct_note is None:
        return True
    channel_index, note = direct_note
    count = active_notes.get((channel_index, note), 0)
    if count <= 1:
        active_notes.pop((channel_index, note), None)
    else:
        active_notes[(channel_index, note)] = count - 1
    channels.midiNoteOn(channel_index, note, 0, midi_channel)
    return True

def release_performance_direct_notes(performance_direct_pads: dict, midi_channel: int) -> None:
    for channel_index, note in performance_direct_pads.values():
        try:
            channels.midiNoteOn(channel_index, note, 0, midi_channel)
        except Exception:
            pass
    performance_direct_pads.clear()

# Launch-map integration
def init_launch_map(script_dir) -> bool:
    """Create and configure the overlay launch map.  Returns True on success."""
    if not PERFORMANCE_DIRECT_AUDIO_EXPERIMENTAL:
        return False
    try:
        launchMapPages.createOverlayMap(
            1, 8,
            PERFORMANCE_LAUNCH_MAP_WIDTH,
            PERFORMANCE_LAUNCH_MAP_HEIGHT,
        )
        for y in range(PERFORMANCE_PAGE_HEIGHT):
            for x in range(PERFORMANCE_PAGE_WIDTH):
                launchMapPages.setMapItemTarget(
                    -1,
                    y * PERFORMANCE_LAUNCH_MAP_WIDTH + x,
                    y * PERFORMANCE_PAD_STRIDE + x + 1,
                )
        launchMapPages.init(
            PERFORMANCE_LAUNCH_MAP_NAME,
            PERFORMANCE_LAUNCH_MAP_WIDTH,
            PERFORMANCE_LAUNCH_MAP_HEIGHT,
        )
        return True
    except Exception as exc:
        _log(f"launch map unavailable: {exc}")
        return False

def update_launch_map(launch_map_ready: bool) -> bool:
    """Call updateMap; returns False if the map is no longer usable."""
    if not launch_map_ready:
        return False
    try:
        launchMapPages.updateMap(-1)
        return True
    except Exception as exc:
        _log(f"launch map update failed: {exc}")
        return False

def clear_performance_view() -> None:
    """Clear the playlist performance display zone when FL leaves live mode."""
    try:
        zone_index = int(playlist.getDisplayZone())
        if zone_index > 0:
            try:
                playlist.lockDisplayZone(zone_index, 0)
            except Exception:
                pass
    except Exception:
        pass
    for args in ((0, 0, 0, 0, 1), (0, 0, 0, 0), (-1, -1, -1, -1, 1), (-1, -1, -1, -1)):
        try:
            playlist.liveDisplayZone(*args)
            return
        except Exception:
            continue
def item_indexes_for_pad(pad: int) -> tuple[int, ...]:
    if pad not in SETTINGS_GRID_PADS:
        return ()
    row = (pad // 10) - 1
    col = (pad % 10) - 1
    row_from_top = PERFORMANCE_PAGE_HEIGHT - 1 - row
    return (row_from_top * PERFORMANCE_LAUNCH_MAP_WIDTH + col,)

def launch_map_channel(item_index: int) -> int:
    if item_index < 0:
        return -1
    try:
        return int(launchMapPages.getMapItemChannel(-1, item_index))
    except Exception:
        return -1

def channel_is_audio_clip(channel_index: int) -> bool:
    for args in ((channel_index,), (channel_index, False), (channel_index, True)):
        try:
            if int(channels.getChannelType(*args)) == CHANNEL_TYPE_AUDIO_CLIP:
                return True
        except Exception:
            continue
    return False

# Navigation (track / block offset stepping)

def step_tracks(direction: int, state: dict) -> None:
    if not performance_available():
        return
    current    = int(state["performance_track_offset"])
    max_offset = max(1, performance_track_count() - PERFORMANCE_PAGE_HEIGHT + 1)
    state["performance_track_offset"] = _clamp(current + direction, 1, max_offset)

def step_blocks(direction: int, state: dict) -> None:
    if not performance_available():
        return
    current    = int(state["performance_block_offset"])
    max_offset = max(0, PERFORMANCE_MAX_BLOCKS - PERFORMANCE_PAGE_WIDTH)
    state["performance_block_offset"] = _clamp(current + direction, 0, max_offset)

def remaining_track_steps(direction: int, state: dict, surface_mode: str) -> int:
    if surface_mode != MODE_PERFORMANCE:
        return 0
    current    = int(state["performance_track_offset"])
    max_offset = max(1, performance_track_count() - PERFORMANCE_PAGE_HEIGHT + 1)
    if direction < 0:
        return max(0, current - 1)
    return max(0, max_offset - current)

def remaining_block_steps(direction: int, state: dict, surface_mode: str) -> int:
    if surface_mode != MODE_PERFORMANCE:
        return 0
    current    = int(state["performance_block_offset"])
    max_offset = max(0, PERFORMANCE_MAX_BLOCKS - PERFORMANCE_PAGE_WIDTH)
    if direction < 0:
        return current
    return max(0, max_offset - current)

def sync_performance_view(state: dict, launch_map_ready: bool) -> bool:
    """Push the current viewport to FL Studio's playlist and launch map.
    Returns the (possibly-updated) launch_map_ready flag.
    """
    if not performance_available():
        return launch_map_ready
    left   = int(state["performance_block_offset"])
    top    = int(state["performance_track_offset"])
    right  = left + PERFORMANCE_PAGE_WIDTH
    bottom = top  + PERFORMANCE_PAGE_HEIGHT
    try:
        playlist.liveDisplayZone(left, top, right, bottom)
    except Exception:
        pass
    launch_map_ready = update_launch_map(launch_map_ready)
    try:
        ui.showWindow(WID_PLAYLIST)
        ui.setFocused(WID_PLAYLIST)
        ui.scrollWindow(WID_PLAYLIST, top, 0)
        ui.scrollWindow(WID_PLAYLIST, left, 1)
    except Exception:
        pass
    return launch_map_ready

def performance_arrow_color(remaining_steps: int, surface_mode: str) -> int:
    if surface_mode != MODE_PERFORMANCE:
        return LP3_ARROW_INACTIVE
    if remaining_steps <= 0:
        return LP3_ARROW_INACTIVE
    return min(0x7F, LP3_PERFORMANCE_READY + max(0, 4 - remaining_steps))
# ~gargoyles rule~