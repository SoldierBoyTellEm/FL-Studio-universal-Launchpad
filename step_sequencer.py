# step_sequencer.py
# Session-button fallback when FL Studio Performance Mode is disabled.

from fl_stubs import channels, general, ui, midi, transport, mixer, patterns
import channel_lock as cl

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
    LP3_MENU_LOCKED,
    LP3_SETTING_DIM,
    LP3_SETTING_ON,
    LP3_STEP_OFF,
    LP3_STEP_ON,
    LP3_STEP_SELECTED,
    PAD_DISABLED,
    SIDE_COLUMN_PADS,
    STEP_SEQUENCER_HEIGHT,
    STEP_SEQUENCER_MAX_STEPS,
    STEP_SEQ_DUAL_PAGE_SETTING_PAD,
    XY_FADER_OFF_COLOR,
    XY_FADER_ON_COLOR,
    DEFAULT_FL_CHANNEL_RGB,
    LedColor,
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
    if state.get("step_dual_page", False) and row_from_top >= STEP_SEQUENCER_HEIGHT // 2:
        row_from_top -= STEP_SEQUENCER_HEIGHT // 2
    return int(state.get("step_channel_offset", 0)) + row_from_top

def pads_for_step(step: int, state: dict) -> list[int]:
    """Grid pads whose currently-displayed step index equals *step* — one per
    visible channel row, two when step_dual_page shows a second page of steps
    in the bottom half. Returns an empty list for step < 0 (no playhead)."""
    if step < 0:
        return []
    page_offset = int(state.get("step_offset", 0))
    dual = state.get("step_dual_page", False)
    cols_per_page = step_columns_per_page()
    half = STEP_SEQUENCER_HEIGHT // 2
    pads: list[int] = []
    for row in range(1, STEP_SEQUENCER_HEIGHT + 1):
        row_from_top = STEP_SEQUENCER_HEIGHT - row
        page = page_offset + cols_per_page if dual and row_from_top >= half else page_offset
        col_index = step - page
        if 0 <= col_index < cols_per_page:
            pads.append(row * 10 + (col_index + 1))
    return pads

def pads_for_channel(channel_index: int, state: dict) -> list[int]:
    """All grid pads (steps + the channel-toggle pad) in the row(s) currently
    showing *channel_index* — two rows when step_dual_page mirrors it."""
    offset = int(state.get("step_channel_offset", 0))
    dual = state.get("step_dual_page", False)
    half = STEP_SEQUENCER_HEIGHT // 2
    pads: list[int] = []
    for row in range(1, STEP_SEQUENCER_HEIGHT + 1):
        row_from_top = STEP_SEQUENCER_HEIGHT - row
        adjusted = row_from_top - half if dual and row_from_top >= half else row_from_top
        if offset + adjusted == channel_index:
            pads.extend(row * 10 + col for col in range(1, 10))
    return pads

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
    max_offset = max(0, count - visible_channel_count(state))
    current = int(state.get("step_channel_offset", 0))
    state["step_channel_offset"] = _clamp(current + (1 if direction >= 0 else -1), 0, max_offset)
    sync_channel_rack_view(state)

def step_steps(direction: int, state: dict) -> None:
    numerator = time_signature_numerator()
    visible_steps = visible_step_count(state)
    multiplier = step_jump_multiplier(state)
    current = int(state.get("step_offset", 0))
    bar_base = current_bar_base(state)

    if direction >= 0:
        if should_split_bar(numerator) and current < second_page_start(bar_base, numerator, visible_steps):
            state["step_offset"] = second_page_start(bar_base, numerator, visible_steps)
        else:
            state["step_offset"] = next_bar_start(bar_base, numerator, multiplier)
    else:
        if should_split_bar(numerator) and current > bar_base:
            state["step_offset"] = bar_base
        else:
            previous_bar = previous_bar_start(bar_base, numerator, multiplier)
            if should_split_bar(numerator):
                overflow_start = second_page_start(previous_bar, numerator, visible_steps)
                if overflow_start < next_bar_start(previous_bar, numerator, multiplier):
                    state["step_offset"] = overflow_start
                else:
                    state["step_offset"] = previous_bar
            else:
                state["step_offset"] = previous_bar

    state["step_offset"] = _clamp(state["step_offset"], 0, STEP_SEQUENCER_MAX_STEPS - 1)
    sync_channel_rack_view(state)

def remaining_channel_steps(direction: int, state: dict) -> int:
    current = int(state.get("step_channel_offset", 0))
    max_offset = max(0, channel_count() - visible_channel_count(state))
    if direction < 0:
        return current
    return max(0, max_offset - current)

def remaining_step_pages(direction: int, state: dict) -> int:
    current = int(state.get("step_offset", 0))
    numerator = time_signature_numerator()
    visible_steps = visible_step_count(state)
    multiplier = step_jump_multiplier(state)
    bar_base = current_bar_base(state)

    if direction < 0:
        if should_split_bar(numerator) and current > bar_base:
            return 1
        if bar_base <= 0:
            return 0
        return 1

    if should_split_bar(numerator) and current < second_page_start(bar_base, numerator, visible_steps):
        overflow_start = second_page_start(bar_base, numerator, visible_steps)
        if overflow_start < next_bar_start(bar_base, numerator, multiplier) and overflow_start < STEP_SEQUENCER_MAX_STEPS:
            return 1

    if next_bar_start(bar_base, numerator, multiplier) < STEP_SEQUENCER_MAX_STEPS:
        return 1
    return 0

def arrow_color(remaining_steps: int, active_color: int) -> int:
    if remaining_steps <= 0:
        return LP3_ARROW_INACTIVE
    return active_color

def lighting(pad: int, state: dict) -> LedColor:
    channel_index = channel_for_pad(pad, state)
    if channel_index < 0 or channel_index >= channel_count():
        return LedColor(PAD_DISABLED)

    if is_channel_toggle_pad(pad):
        return channel_toggle_lighting(channel_index)

    step = step_for_pad(pad, state)
    if step < 0 or step >= STEP_SEQUENCER_MAX_STEPS:
        return LedColor(PAD_DISABLED)

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
        return LedColor(PAD_DISABLED, rgb)

    if channel_index == selected_channel():
        return LedColor(LP3_STEP_SELECTED)
    return LedColor(LP3_STEP_ON if is_on else LP3_STEP_OFF)

def settings_lighting(pad: int, state: dict) -> LedColor:
    """Return the LedColor for *pad* while the step-sequencer settings pane
    is shown (long-hold the step-seq mode key)."""
    if pad == STEP_SEQ_DUAL_PAGE_SETTING_PAD:
        return LedColor(LP3_SETTING_ON if state.get("step_dual_page", False) else LP3_SETTING_DIM)
    return LedColor(PAD_DISABLED)

def handle_settings_pad(pad: int, state: dict) -> bool:
    """Mutate *state* in-place for a step-sequencer settings-pane pad press.
    Returns True if the state was changed (caller should save + refresh)."""
    if pad == STEP_SEQ_DUAL_PAGE_SETTING_PAD:
        state["step_dual_page"] = not state.get("step_dual_page", False)
        return True
    return False

def sync_channel_rack_view(state: dict) -> None:
    global _last_channel_rack_rect
    try:
        duration = int(getattr(midi, "MaxInt"))
    except Exception:
        duration = 2147483647

    left = int(state.get("step_offset", 0))
    width = visible_step_count(state)
    top = int(state.get("step_channel_offset", 0))
    height = STEP_SEQUENCER_HEIGHT // 2 if state.get("step_dual_page", False) else STEP_SEQUENCER_HEIGHT
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
    row_from_top, col = cell
    if col >= step_columns_per_page():
        return -1
    page_offset = int(state.get("step_offset", 0))
    if state.get("step_dual_page", False) and row_from_top >= STEP_SEQUENCER_HEIGHT // 2:
        page_offset += step_columns_per_page()
    return page_offset + col

def visible_steps_in_page(state: dict) -> int:
    numerator = time_signature_numerator()
    page_start = int(state.get("step_offset", 0))
    bar_base = current_bar_base(state)
    offset_within_bar = max(0, page_start - bar_base)
    return max(0, min(step_columns_per_page(), numerator - offset_within_bar))

def step_columns_per_page() -> int:
    return 9 if uses_ninth_step_column() else 8

def visible_step_count(state: dict) -> int:
    """Number of steps spanned by the displayed/navigable window. Doubled
    when step_dual_page is enabled, since the bottom half of the grid then
    shows a second page of steps for the same channels."""
    count = step_columns_per_page()
    if state.get("step_dual_page", False):
        count *= 2
    return count

def visible_channel_count(state: dict) -> int:
    """Number of distinct channel rows visible in the current step-grid view."""
    if state.get("step_dual_page", False):
        return STEP_SEQUENCER_HEIGHT // 2
    return STEP_SEQUENCER_HEIGHT

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

def next_bar_start(bar_base: int, numerator: int, multiplier: int = 1) -> int:
    return bar_base + max(1, numerator) * bars_per_step_press(numerator) * multiplier

def previous_bar_start(bar_base: int, numerator: int, multiplier: int = 1) -> int:
    return max(0, bar_base - max(1, numerator) * bars_per_step_press(numerator) * multiplier)

def step_jump_multiplier(state: dict) -> int:
    """Arrow-key bar jumps cover twice the ground when step_dual_page is
    enabled, matching the doubled-width display window."""
    return 2 if state.get("step_dual_page", False) else 1

def second_page_start(bar_base: int, numerator: int, visible_steps: int) -> int:
    if not should_split_bar(numerator):
        return bar_base
    overflow = max(0, numerator - visible_steps)
    if overflow <= 0:
        return bar_base
    return bar_base + visible_steps

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

# FL's step-velocity parameter (parameterIndex=1) ranges 0-128, where 128
# means "use the channel default". STEP_VELOCITY_DEFAULT is that default
# expressed in the 0.0-1.0 range used by the velocity PadFader.
STEP_VELOCITY_MAX = 128
STEP_VELOCITY_DEFAULT = 100 / STEP_VELOCITY_MAX

def _current_pattern() -> int:
    try:
        return int(patterns.patternNumber())
    except Exception:
        return 1

def get_step_velocity(channel_index: int, step: int) -> float:
    if channel_index < 0 or step < 0:
        return STEP_VELOCITY_DEFAULT
    try:
        value = int(channels.getStepParameterByIndex(channel_index, _current_pattern(), step, 1))
    except Exception:
        return STEP_VELOCITY_DEFAULT
    if value >= STEP_VELOCITY_MAX:
        return STEP_VELOCITY_DEFAULT
    return _clamp(value, 0, STEP_VELOCITY_MAX) / STEP_VELOCITY_MAX

def set_step_velocity(channel_index: int, step: int, value: float) -> None:
    if channel_index < 0 or step < 0:
        return
    scaled = _clamp(round(value * STEP_VELOCITY_MAX), 0, STEP_VELOCITY_MAX - 1)
    try:
        channels.setStepParameterByIndex(channel_index, _current_pattern(), step, 1, scaled)
    except Exception:
        return

def channel_toggle_lighting(channel_index: int) -> LedColor:
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
        return LedColor(PAD_DISABLED, rgb)

    if muted:
        return LedColor(LP3_MENU_INACTIVE)
    if selected:
        return LedColor(LP3_STEP_SELECTED)
    return LedColor(LP3_MENU_ACTIVE)

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

# Lock routing page (step sequencer channel hold)
LOCK_PAGE_NOTE_PAD = 81
LOCK_PAGE_TEST_NOTE = 72
LOCK_PAGE_CUSTOM_ROW0 = tuple(range(61, 69))
LOCK_PAGE_CUSTOM_ROW1 = tuple(range(51, 59))
LOCK_PAGE_VOLUME_ROW = tuple(range(21, 29))
LOCK_PAGE_PAN_ROW = tuple(range(11, 19))

# Velocity fader: while a step pad is held, the right side column shows the
# held step's velocity (most-recently-pressed step if several are held).
# Only available in normal 8-step-column mode — the side column doubles as
# the 9th step column when the time signature's numerator is 9.
VELOCITY_FADER_PADS = SIDE_COLUMN_PADS

def held_step(surface) -> tuple[int, int] | None:
    """The (channel_index, step) for the most-recently-pressed held step pad,
    or None if no step is currently held."""
    if not surface._step_held_pads:
        return None
    return surface._step_held_pads[-1][1]

def velocity_fader_active(surface) -> bool:
    return not uses_ninth_step_column() and held_step(surface) is not None


def _cached_step_velocity(surface, channel_index: int, step: int) -> float:
    """The fader's authoritative velocity for a held step. Seeded once from FL
    the first time the step is held (or pressed), then owned locally so the
    fader holds its position instead of snapping back to FL's reported default
    every frame — FL reports the channel default for any step whose velocity it
    treats as unset, which otherwise made the ramp restart from default."""
    key = (channel_index, step)
    if key not in surface._step_velocity_cache:
        surface._step_velocity_cache[key] = get_step_velocity(channel_index, step)
    return surface._step_velocity_cache[key]


def handle_velocity_fader_press(surface, pad: int) -> None:
    target = held_step(surface)
    if target is None:
        return
    channel_index, step = target
    now = surface._now()
    current = _cached_step_velocity(surface, channel_index, step)
    new_value = surface._step_velocity_fader.apply_press(pad, current, now)
    surface._step_velocity_cache[(channel_index, step)] = new_value
    # Adjusting velocity always (re)creates the note, even when the step is
    # currently off — a fader press that would otherwise "delete" the note
    # instead restores it at the chosen velocity.
    if not get_step(channel_index, step):
        set_step(channel_index, step, True)
        surface._refresh_grid_pads(pads_for_step(step, surface.state))
    surface._begin_channel_param_ramp(
        surface._step_velocity_fader, ("step_velocity", channel_index, step), now, current, new_value,
        emit=lambda value, ch=channel_index, st=step: set_step_velocity(ch, st, value),
    )


def velocity_fader_lighting(surface, pad: int) -> LedColor:
    target = held_step(surface)
    if target is None:
        return LedColor(PAD_DISABLED)
    channel_index, step = target
    current = _cached_step_velocity(surface, channel_index, step)
    return surface._fader_pad_lighting(
        surface._step_velocity_fader, XY_FADER_ON_COLOR, XY_FADER_OFF_COLOR, pad, current
    )


def handle_surface_pad(surface, event, pad: int, velocity: int, pressed: bool) -> bool:
    if not pressed and any(entry[0] == pad for entry in surface._step_held_pads):
        _track_step_hold(surface, pad, pressed=False)
    if surface._step_seq_settings_visible:
        if pressed and handle_settings_pad(pad, surface.state):
            surface._save_state()
        surface._refresh_needed = True
        return True
    if (
        surface._step_lock_page_channel is None
        and pad in VELOCITY_FADER_PADS
        and velocity_fader_active(surface)
    ):
        if pressed:
            handle_velocity_fader_press(surface, pad)
        surface._refresh_grid_pads(VELOCITY_FADER_PADS)
        surface._refresh_needed = False
        return True
    if is_channel_toggle_pad(pad):
        if pressed:
            surface._step_toggle_pad_pressed = pad
            surface._step_toggle_hold_started = surface._now()
            surface._step_toggle_hold_fired = False
            return True
        channel_index = channel_for_pad(pad, surface.state)
        toggled = False
        if surface._step_toggle_pad_pressed == pad and not surface._step_toggle_hold_fired:
            if toggle_pad(pad, surface.state):
                toggled = True
                surface._save_state()
        if surface._step_toggle_pad_pressed == pad:
            surface._step_toggle_pad_pressed = None
        surface._step_toggle_hold_started = 0.0
        surface._step_toggle_hold_fired = False
        exiting_lock_page = surface._step_lock_page_channel is not None
        if surface._step_lock_page_test_note_sent:
            midi_channel = int(surface.state["midi_channel"]) & 0x0F
            channels.midiNoteOn(surface._step_lock_page_channel, LOCK_PAGE_TEST_NOTE, 0, midi_channel)
            surface._step_lock_page_test_note_sent = False
        surface._step_lock_page_channel = None
        if exiting_lock_page:
            surface._refresh_surface()
        elif toggled:
            surface._refresh_grid_pads(pads_for_channel(channel_index, surface.state))
        surface._refresh_needed = False
        return True
    if surface._step_lock_page_channel is not None:
        if pressed:
            surface._refresh_grid_pads(handle_lock_page_press(surface, pad))
        surface._refresh_needed = False
        return True
    # Seed the held-step velocity cache before toggling, so a press that turns
    # the note off still captures its real velocity (used if the fader later
    # restores it).
    if pressed:
        _track_step_hold(surface, pad, pressed=True)
    if pressed and toggle_pad(pad, surface.state):
        surface._save_state()
        surface._refresh_grid_pad(pad)
    surface._refresh_needed = False
    return True


def _track_step_hold(surface, pad: int, pressed: bool) -> None:
    """Maintain `surface._step_held_pads`, the press-order stack of held step
    pads, and refresh the velocity fader column when its target step or
    visibility changes."""
    was_active = velocity_fader_active(surface)
    if pressed:
        channel_index = channel_for_pad(pad, surface.state)
        step = step_for_pad(pad, surface.state)
        if channel_index < 0 or step < 0:
            return
        surface._step_held_pads = [entry for entry in surface._step_held_pads if entry[0] != pad]
        surface._step_held_pads.append((pad, (channel_index, step)))
        # Seed the fader's cached velocity from FL while the note still exists
        # (handle_surface_pad seeds before toggling the step, so an on-note's
        # real velocity is captured before a press can switch it off).
        _cached_step_velocity(surface, channel_index, step)
    else:
        released = [entry[1] for entry in surface._step_held_pads if entry[0] == pad]
        surface._step_held_pads = [entry for entry in surface._step_held_pads if entry[0] != pad]
        for key in released:
            if not any(entry[1] == key for entry in surface._step_held_pads):
                surface._step_velocity_cache.pop(key, None)
    is_active = velocity_fader_active(surface)
    if was_active or is_active:
        surface._refresh_grid_pads(VELOCITY_FADER_PADS)


def lock_page_context_for_pad(pad: int) -> str | None:
    if pad == LOCK_PAGE_NOTE_PAD:
        return cl.NOTE_CONTEXT
    if pad in LOCK_PAGE_CUSTOM_ROW0:
        return cl.custom_context(pad - 61)
    if pad in LOCK_PAGE_CUSTOM_ROW1:
        return cl.custom_context(pad - 51 + 8)
    return None


def handle_lock_page_press(surface, pad: int) -> list[int]:
    channel = surface._step_lock_page_channel
    if channel is None:
        return []
    if surface._lock_page_volume_fader.contains(pad):
        now = surface._now()
        current = float(channels.getChannelVolume(channel))
        new_value = surface._lock_page_volume_fader.apply_press(pad, current, now)
        surface._begin_channel_param_ramp(
            surface._lock_page_volume_fader, ("lock_volume", channel), now, current, new_value,
            emit=lambda value, ch=channel: channels.setChannelVolume(ch, value),
        )
        return list(surface._lock_page_volume_fader.pads)
    if surface._lock_page_pan_fader.contains(pad):
        now = surface._now()
        current = float(channels.getChannelPan(channel))
        new_value = surface._lock_page_pan_fader.apply_press(pad, current, now)
        surface._begin_channel_param_ramp(
            surface._lock_page_pan_fader, ("lock_pan", channel), now, current, new_value,
            emit=lambda value, ch=channel: channels.setChannelPan(ch, value),
        )
        return list(surface._lock_page_pan_fader.pads)
    ctx = lock_page_context_for_pad(pad)
    if ctx is None:
        return []
    if cl.is_locked(surface.state, ctx) and cl.get(surface.state, ctx) == channel:
        cl.clear(surface.state, ctx)
    else:
        cl.set_lock(surface.state, ctx, channel)
    surface._save_state()
    return [pad]


def lock_page_lighting(surface, pad: int) -> LedColor:
    channel = surface._step_lock_page_channel
    if surface._lock_page_volume_fader.contains(pad):
        if channel is None:
            return LedColor(PAD_DISABLED)
        current = float(channels.getChannelVolume(channel))
        return surface._fader_pad_lighting(
            surface._lock_page_volume_fader, XY_FADER_ON_COLOR, XY_FADER_OFF_COLOR, pad, current
        )
    if surface._lock_page_pan_fader.contains(pad):
        if channel is None:
            return LedColor(PAD_DISABLED)
        current = float(channels.getChannelPan(channel))
        return surface._fader_pad_lighting(
            surface._lock_page_pan_fader, XY_FADER_ON_COLOR, XY_FADER_OFF_COLOR, pad, current
        )
    ctx = lock_page_context_for_pad(pad)
    if ctx is None:
        return LedColor(PAD_DISABLED)
    if channel is not None and cl.is_locked(surface.state, ctx) and cl.get(surface.state, ctx) == channel:
        return LedColor(LP3_MENU_LOCKED)
    return LedColor(LP3_MENU_INACTIVE)
# ~gargoyles rule~