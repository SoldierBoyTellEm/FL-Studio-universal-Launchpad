"""Launchpad Pro mixer overview: pads 1..64 address mixer tracks 0..63.

Mixer track 0 is the Master track and is intentionally included as the first
selectable slot.
"""
from fl_stubs import mixer
from constants import LedColor, LED_OFF, LP3_MENU_ACTIVE, LP3_MENU_INACTIVE, MIXER_PAGE_LAYOUTS, SIDE_COLUMN_PADS, CHANNEL_RACK_DIM_INACTIVE_STEP
from led_display import rgb6_from_color, rgb_max_value

MIXER_INSERT_COUNT = 64

def _track_for_pad(pad: int) -> int | None:
    row, col = divmod(int(pad), 10)
    return (8 - row) * 8 + col - 1 if 1 <= row <= 8 and 1 <= col <= 8 else None

def _track_count() -> int:
    try:
        return max(0, int(mixer.getTrackCount()))
    except Exception:
        try:
            return max(0, int(mixer.trackCount()) - 2)
        except Exception:
            return 0

def _track_rgb(track: int) -> tuple[int, int, int]:
    try:
        color = int(mixer.getTrackColor(track))
        if color:
            return rgb6_from_color(color)
    except Exception:
        pass
    maximum = rgb_max_value()
    return (maximum, maximum, maximum)

def is_armed(track: int) -> bool:
    try:
        return bool(mixer.isTrackArmed(track))
    except Exception:
        return False

def armed_tracks() -> list[int]:
    return [track for track in range(min(MIXER_INSERT_COUNT, _track_count()) + 1)
            if is_armed(track)]

def toggle_arm(track: int) -> bool:
    if track < 0 or track > min(MIXER_INSERT_COUNT, _track_count()):
        return False
    try:
        mixer.armTrack(track)
        return True
    except Exception:
        return False

def lighting(pad: int, subpage: int = 0, pulse: float = 1.0) -> LedColor:
    if pad in SIDE_COLUMN_PADS:
        slot = 8 - pad // 10
        return LedColor(LP3_MENU_ACTIVE if slot == subpage else LP3_MENU_INACTIVE)
    track = _track_for_pad(pad)
    if track is None or track > min(MIXER_INSERT_COUNT, _track_count()):
        return LED_OFF
    rgb = _track_rgb(track)
    try:
        selected = bool(mixer.isTrackSelected(track))
    except Exception:
        selected = False
    if not selected:
        rgb = tuple(max(1, int(c // CHANNEL_RACK_DIM_INACTIVE_STEP)) for c in rgb)
    if is_armed(track):
        rgb = tuple(max(1, int(round(c * pulse))) for c in rgb)
    return LedColor(0, rgb)

def arm_control_lighting(pulse: float = 1.0) -> LedColor:
    if not is_armed(int(mixer.trackNumber())):
        return LedColor(LP3_MENU_INACTIVE)
    maximum = rgb_max_value()
    return LedColor(0, (max(1, int(round(maximum * pulse))), 0, 0))

def select(pad: int) -> bool:
    track = _track_for_pad(pad)
    if track is None or track > min(MIXER_INSERT_COUNT, _track_count()):
        return False
    try:
        mixer.setActiveTrack(track)
    except Exception:
        try:
            mixer.selectTrack(track)
        except Exception:
            return False
    return True

def subpage_for_pad(pad: int) -> int | None:
    if pad not in SIDE_COLUMN_PADS:
        return None
    slot = 8 - pad // 10
    return slot if slot < len(MIXER_PAGE_LAYOUTS) else None
