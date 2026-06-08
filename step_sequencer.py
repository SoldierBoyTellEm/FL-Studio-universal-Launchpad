# step_sequencer.py
# Session-button fallback when FL Studio Performance Mode is disabled.

from fl_stubs import channels, general, ui, midi, transport, mixer

from constants import (
    CHANNEL_RACK_COLOR_GAMMA_LP3,
    CHANNEL_RACK_COLOR_GAMMA_MK2,
    CHANNEL_RACK_COLOR_SATURATION_LP3,
    CHANNEL_RACK_COLOR_SATURATION_MK2,
    CHANNEL_RACK_DIM_INACTIVE_STEP,
    CHANNEL_RACK_DIM_MUTED_STEP,
    CHANNEL_RACK_DIM_MUTED_TOGGLE,
    CHANNEL_RACK_DIM_PLAYHEAD_STEP,
    LP3_ARROW_INACTIVE,
    LP3_ARROW_PAN_ACTIVE,
    LP3_ARROW_OCTAVE_ACTIVE,
    LP3_MENU_ACTIVE,
    LP3_MENU_INACTIVE,
    LP3_STEP_OFF,
    LP3_STEP_ON,
    LP3_STEP_SELECTED,
    PAD_DISABLED,
    STEP_SEQUENCER_HEIGHT,
    STEP_SEQUENCER_MAX_STEPS,
    DEFAULT_FL_CHANNEL_RGB,
)
from led_display import rgb6_from_color, rgb6_from_rgb, rgb_max_value, surface_mode

_last_channel_rack_rect: tuple[int, int, int, int] | None = None

def _channel_rack_tuning() -> tuple[float, float]:
    if surface_mode() == "lp3":
        return CHANNEL_RACK_COLOR_SATURATION_LP3, CHANNEL_RACK_COLOR_GAMMA_LP3
    return CHANNEL_RACK_COLOR_SATURATION_MK2, CHANNEL_RACK_COLOR_GAMMA_MK2

def pad_to_cell(pad: int) -> tuple[int, int] | None:
    row = pad // 10
    col = pad % 10
    if not 1 <= row <= STEP_SEQUENCER_HEIGHT:
        return None
    if not 1 <= col <= 9:
        return None
    return STEP_SEQUENCER_HEIGHT - row, col - 1

def channel_for_pad(pad: int, state: dict) -> int:
    cell = pad_to_cell(pad)
    if cell is None:
        return -1
    row_from_top, _col = cell
    return int(state.get("step_channel_offset", 0)) + row_from_top

def toggle_pad(pad: int, state: dict) -> bool:
    channel_index = channel_for_pad(pad, state)
    if channel_index < 0 or channel_index >= channel_count():
        return False

    if is_channel_toggle_pad(pad):
        channels.muteChannel(channel_index, 0 if is_channel_muted(channel_index) else 1)
        return True

    step = step_for_pad(pad, state)
    if step < 0 or step >= STEP_SEQUENCER_MAX_STEPS:
        return False
    set_step(channel_index, step, not get_step(channel_index, step))
    return True

def step_channels(direction: int, state: dict) -> None:
    count = channel_count()
    max_offset = max(0, count - STEP_SEQUENCER_HEIGHT)
    current = int(state.get("step_channel_offset", 0))
    state["step_channel_offset"] = _clamp(current + (1 if direction >= 0 else -1), 0, max_offset)
    sync_channel_rack_view(state)

def step_steps(direction: int, state: dict) -> None:
    numerator = time_signature_numerator()
    visible_step_count = step_columns_per_page()
    current = int(state.get("step_offset", 0))
    bar_base = current_bar_base(state)

    if direction >= 0:
        if should_split_bar(numerator) and current < second_page_start(bar_base, numerator, visible_step_count):
            state["step_offset"] = second_page_start(bar_base, numerator, visible_step_count)
        else:
            state["step_offset"] = next_bar_start(bar_base, numerator)
    else:
        if should_split_bar(numerator) and current > bar_base:
            state["step_offset"] = bar_base
        else:
            previous_bar = previous_bar_start(bar_base, numerator)
            if should_split_bar(numerator):
                overflow_start = second_page_start(previous_bar, numerator, visible_step_count)
                if overflow_start < next_bar_start(previous_bar, numerator):
                    state["step_offset"] = overflow_start
                else:
                    state["step_offset"] = previous_bar
            else:
                state["step_offset"] = previous_bar

    state["step_offset"] = _clamp(state["step_offset"], 0, STEP_SEQUENCER_MAX_STEPS - 1)
    sync_channel_rack_view(state)

def remaining_channel_steps(direction: int, state: dict) -> int:
    current = int(state.get("step_channel_offset", 0))
    max_offset = max(0, channel_count() - STEP_SEQUENCER_HEIGHT)
    if direction < 0:
        return current
    return max(0, max_offset - current)

def remaining_step_pages(direction: int, state: dict) -> int:
    current = int(state.get("step_offset", 0))
    numerator = time_signature_numerator()
    visible_step_count = step_columns_per_page()
    bar_base = current_bar_base(state)

    if direction < 0:
        if should_split_bar(numerator) and current > bar_base:
            return 1
        if bar_base <= 0:
            return 0
        return 1

    if should_split_bar(numerator) and current < second_page_start(bar_base, numerator, visible_step_count):
        overflow_start = second_page_start(bar_base, numerator, visible_step_count)
        if overflow_start < next_bar_start(bar_base, numerator) and overflow_start < STEP_SEQUENCER_MAX_STEPS:
            return 1

    if next_bar_start(bar_base, numerator) < STEP_SEQUENCER_MAX_STEPS:
        return 1
    return 0

def arrow_color(remaining_steps: int, active_color: int) -> int:
    if remaining_steps <= 0:
        return LP3_ARROW_INACTIVE
    return active_color

def lighting(pad: int, state: dict) -> tuple[int, tuple[int, int, int] | None]:
    channel_index = channel_for_pad(pad, state)
    if channel_index < 0 or channel_index >= channel_count():
        return PAD_DISABLED, None

    if is_channel_toggle_pad(pad):
        return channel_toggle_lighting(channel_index)

    step = step_for_pad(pad, state)
    if step < 0 or step >= STEP_SEQUENCER_MAX_STEPS:
        return PAD_DISABLED, None

    is_on = get_step(channel_index, step)
    under_playhead = step == playhead_step()
    rgb = _channel_rgb_uncorrected(channel_index)
    if rgb is not None:
        if is_channel_muted(channel_index):
            rgb = tuple(max(1, component // CHANNEL_RACK_DIM_MUTED_STEP) for component in rgb)
        elif under_playhead:
            rgb = tuple(max(1, int(component // CHANNEL_RACK_DIM_PLAYHEAD_STEP)) for component in rgb)
        elif not is_on:
            rgb = tuple(max(1, component // CHANNEL_RACK_DIM_INACTIVE_STEP) for component in rgb)
        saturation, gamma = _channel_rack_tuning()
        rgb = rgb6_from_rgb(
            rgb[0],
            rgb[1],
            rgb[2],
            saturation=saturation,
            gamma=gamma,
        )
        return PAD_DISABLED, rgb

    if channel_index == selected_channel():
        return LP3_STEP_SELECTED, None
    return (LP3_STEP_ON if is_on else LP3_STEP_OFF), None

def sync_channel_rack_view(state: dict) -> None:
    global _last_channel_rack_rect
    try:
        duration = int(getattr(midi, "MaxInt"))
    except Exception:
        duration = 2147483647

    left = int(state.get("step_offset", 0))
    width = step_columns_per_page()
    top = int(state.get("step_channel_offset", 0))
    height = STEP_SEQUENCER_HEIGHT
    try:
        if _last_channel_rack_rect is not None:
            prev_left, prev_top, prev_width, prev_height = _last_channel_rack_rect
            ui.crDisplayRect(prev_left, prev_top, prev_width, prev_height, 0)
        ui.crDisplayRect(left, top, width, height, duration)
        _last_channel_rack_rect = (left, top, width, height)
    except Exception:
        return

def clear_channel_rack_view() -> None:
    global _last_channel_rack_rect
    try:
        if _last_channel_rack_rect is not None:
            left, top, width, height = _last_channel_rack_rect
            ui.crDisplayRect(left, top, width, height, 0)
            _last_channel_rack_rect = None
    except Exception:
        return

def is_channel_toggle_pad(pad: int) -> bool:
    cell = pad_to_cell(pad)
    if cell is None:
        return False
    _row_from_top, col = cell
    return col == 8 and not uses_ninth_step_column()

def step_for_pad(pad: int, state: dict) -> int:
    cell = pad_to_cell(pad)
    if cell is None:
        return -1
    _row_from_top, col = cell
    if col >= step_columns_per_page():
        return -1
    return int(state.get("step_offset", 0)) + col

def visible_steps_in_page(state: dict) -> int:
    numerator = time_signature_numerator()
    page_start = int(state.get("step_offset", 0))
    bar_base = current_bar_base(state)
    offset_within_bar = max(0, page_start - bar_base)
    return max(0, min(step_columns_per_page(), numerator - offset_within_bar))

def step_columns_per_page() -> int:
    return 9 if uses_ninth_step_column() else 8

def uses_ninth_step_column() -> bool:
    return time_signature_numerator() == 9

def should_split_bar(numerator: int | None = None) -> bool:
    if numerator is None:
        numerator = time_signature_numerator()
    return 10 <= numerator <= 16

def time_signature_numerator() -> int:
    try:
        ppb = int(general.getRecPPB())
        ppq = int(general.getRecPPQ())
    except Exception:
        return 4
    if ppq <= 0:
        return 4
    return _clamp(max(1, int(round(ppb / float(ppq)))), 1, 16)

def current_bar_base(state: dict) -> int:
    numerator = time_signature_numerator()
    current = max(0, int(state.get("step_offset", 0)))
    return (current // numerator) * numerator if numerator > 0 else 0

def bars_per_step_press(numerator: int) -> int:
    """How many bars a pan arrow jumps per press.  Short bars cover little
    ground per bar, so step through several at once: numerator 1-2 jumps four
    bars, 3-4 jumps two, and 5+ jumps a single bar (split-bar 10-16 is handled
    separately and ignores this)."""
    if numerator <= 2:
        return 4
    if numerator <= 4:
        return 2
    return 1

def next_bar_start(bar_base: int, numerator: int) -> int:
    return bar_base + max(1, numerator) * bars_per_step_press(numerator)

def previous_bar_start(bar_base: int, numerator: int) -> int:
    return max(0, bar_base - max(1, numerator) * bars_per_step_press(numerator))

def second_page_start(bar_base: int, numerator: int, visible_step_count: int) -> int:
    if not should_split_bar(numerator):
        return bar_base
    overflow = max(0, numerator - visible_step_count)
    if overflow <= 0:
        return bar_base
    return bar_base + visible_step_count

def channel_count() -> int:
    try:
        return max(0, int(channels.channelCount()))
    except Exception:
        return 0

def selected_channel() -> int:
    try:
        selected = int(channels.selectedChannel(1))
        if selected >= 0:
            return selected
        return int(channels.channelNumber(0, 0))
    except Exception:
        return -1

def is_channel_muted(channel_index: int) -> bool:
    try:
        return int(channels.isChannelMuted(channel_index)) != 0
    except Exception:
        return False

def get_step(channel_index: int, step: int) -> bool:
    if channel_index < 0 or step < 0:
        return False
    try:
        return int(channels.getGridBit(channel_index, step)) != 0
    except Exception:
        return False

def playhead_step() -> int:
    """The pattern step the playhead is currently over, or -1 when stopped.

    FL exposes the playing step on the *mixer* module (mixer.getSongStepPos),
    which already returns an integer absolute pattern step (or -1)."""
    try:
        if not transport.isPlaying():
            return -1
        return int(mixer.getSongStepPos())
    except Exception:
        return -1

def set_step(channel_index: int, step: int, value: bool) -> None:
    if channel_index < 0 or step < 0:
        return
    try:
        channels.setGridBit(channel_index, step, 1 if value else 0)
    except Exception:
        return

def channel_toggle_lighting(channel_index: int) -> tuple[int, tuple[int, int, int] | None]:
    rgb = _channel_rgb_uncorrected(channel_index)
    muted = is_channel_muted(channel_index)
    selected = channel_index == selected_channel()
    if rgb is not None:
        if muted:
            rgb = tuple(max(1, component // CHANNEL_RACK_DIM_MUTED_TOGGLE) for component in rgb)
        elif selected:
            maximum = rgb_max_value()
            rgb = tuple(min(maximum, component + 18) for component in rgb)
        saturation, gamma = _channel_rack_tuning()
        rgb = rgb6_from_rgb(
            rgb[0],
            rgb[1],
            rgb[2],
            saturation=saturation,
            gamma=gamma,
        )
        return PAD_DISABLED, rgb

    if muted:
        return LP3_MENU_INACTIVE, None
    if selected:
        return LP3_STEP_SELECTED, None
    return LP3_MENU_ACTIVE, None

def _channel_rgb_uncorrected(channel_index: int) -> tuple[int, int, int] | None:
    try:
        color = int(channels.getChannelColor(channel_index))
    except Exception:
        return None
    if color == 0:
        return None
    red = color & 0xFF
    green = (color >> 8) & 0xFF
    blue = (color >> 16) & 0xFF
    if (red, green, blue) == DEFAULT_FL_CHANNEL_RGB:
        return (0xFF, 0xFF, 0xFF)
    return (red, green, blue)

def _channel_rgb(channel_index: int) -> tuple[int, int, int] | None:
    rgb = _channel_rgb_uncorrected(channel_index)
    if rgb is None:
        return None
    saturation, gamma = _channel_rack_tuning()
    return rgb6_from_rgb(
        rgb[0],
        rgb[1],
        rgb[2],
        saturation=saturation,
        gamma=gamma,
    )

def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
# gargoyles rule