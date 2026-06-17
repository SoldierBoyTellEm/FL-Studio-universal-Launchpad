# modulators.py
# Pad-based MIDI modulator primitives: fader engine, page selector, and XY pad.
#
#   PadFader       – firmware-style stepped fader (4 microsteps/pad, unipolar or
#                    bipolar) used by XY pages, Gross Beat, and custom modes.
#   SELECTOR_PADS  – canonical right-column pad order (top→bottom, slot 0 first)
#   pad_to_slot()  – right-column hit-test
#   selector_lighting() / handle_press() / step_page() – page selector helpers
#   pad_to_xy()    – XY grid hit-test (excludes side column)
#   vert_fader_defs() / horiz_fader_defs() / modwheel_pads() – CC-layout tables
#   vert_bipolar_fader_defs() / horiz_bipolar_fader_defs() – bipolar CC-layout tables
#   xy_values()    – position → CC pair
#   xy_grid_lighting() – crosshair display for the positional XY page
#   grid_fader_cc() – which CC a grid pad drives on a fader page

from __future__ import annotations
import math
from constants import (
    CUSTOM_MODE_SELECTOR_PADS,
    PAD_DISABLED,
    LP3_MENU_ACTIVE,
    LP3_MENU_INACTIVE,
    LedColor,
    XY_PAD_X_CC,
    XY_PAD_Y_CC,
    XY_VERT_FADER_CCS,
    XY_HORIZ_FADER_CCS,
    XY_VERT_BIPOLAR_FADER_CCS,
    XY_HORIZ_BIPOLAR_FADER_CCS,
    performance_modwheel_CC,
    XY_PAGE_VERT,
    XY_PAGE_HORIZ,
    XY_PAGE_VERT_BIPOLAR,
    XY_PAGE_HORIZ_BIPOLAR,
    FADER_MICROVALUES_PER_PAD,
    FADER_DEFAULT_MICROVALUE,
    FADER_DEFAULT_INTERPOLATE_SECONDS,
    XY_BIPOLAR_CENTER_CHORD_SECONDS,
)

# PadFader

def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))

class PadFader:
    """Firmware-style pad fader with 4 microsteps per pad."""

    def __init__(
        self,
        pads: tuple[int, ...],
        *,
        minimum: float = 0.0,
        maximum: float = 1.0,
        bipolar: bool = False,
        tension: float = 0.0,
        interpolate_seconds: float = FADER_DEFAULT_INTERPOLATE_SECONDS,
    ) -> None:
        self.pads = tuple(pads)
        self.minimum = float(minimum)
        self.maximum = float(maximum)
        self.bipolar = bool(bipolar)
        self.tension = float(tension)
        self.interpolate_seconds = float(interpolate_seconds)
        self._pad_to_index = {pad: index for index, pad in enumerate(self.pads)}
        self._center_press_time: dict[int, float] = {}
        self._ramp_start_value: float | None = None
        self._ramp_target_value: float | None = None
        self._ramp_start_time: float = 0.0

    def contains(self, pad: int) -> bool:
        return pad in self._pad_to_index

    def clamp_value(self, value: float) -> float:
        return _clamp(float(value), self.minimum, self.maximum)

    def center_pads(self) -> tuple[int, ...]:
        """Return the pad(s) nearest the bipolar centre point: one for an odd
        pad count, two for an even one."""
        midpoint = (len(self.pads) - 1) / 2.0
        return tuple(p for i, p in enumerate(self.pads) if abs(i - midpoint) < 1.0)

    def center_value(self) -> float:
        """Return the value representing the bipolar centre (zero deviation)."""
        return (self.minimum + self.maximum) / 2.0

    def value_for_pad(self, pad: int) -> float | None:
        """Return the exact fader value that pad's bottom microstep represents, or None."""
        if pad not in self._pad_to_index:
            return None
        pad_index = self._pad_to_index[pad]
        if self.bipolar:
            return None
        total_steps = len(self.pads) * FADER_MICROVALUES_PER_PAD
        step = pad_index * FADER_MICROVALUES_PER_PAD
        return self._value_for_unipolar_step(step, total_steps)

    def next_value_for_pad(self, pad: int, current_value: float) -> float:
        """Cycle through the pad's 4 microvalues on repeated presses."""
        if pad not in self._pad_to_index:
            return self.clamp_value(current_value)
        if self.bipolar:
            return self._next_bipolar_value(pad, current_value)
        return self._next_unipolar_value(pad, current_value)

    def apply_press(self, pad: int, current_value: float, now: float) -> float:
        """Return the value after pressing `pad`, applying the bipolar centre-chord
        reset: pressing both centre pads within XY_BIPOLAR_CENTER_CHORD_SECONDS of
        each other resets to centre instead of stepping, regardless of press/release
        ordering. Unipolar faders and out-of-range pads just step normally.

        Tracks its own press timestamps, so every fader using this gets the
        chord behaviour for free."""
        if pad not in self._pad_to_index:
            return self.clamp_value(current_value)
        if not self.bipolar:
            return self.next_value_for_pad(pad, current_value)
        centers = self.center_pads()
        if pad not in centers:
            return self.next_value_for_pad(pad, current_value)
        other_pads = [p for p in centers if p != pad]
        if any(
            now - self._center_press_time.get(p, -1.0) <= XY_BIPOLAR_CENTER_CHORD_SECONDS
            for p in other_pads
        ):
            for p in centers:
                self._center_press_time.pop(p, None)
            return self.center_value()
        self._center_press_time[pad] = now
        return self.next_value_for_pad(pad, current_value)

    # Interpolation (gliding to a newly pressed value rather than jumping)

    def start_ramp(self, current_value: float, target_value: float, now: float) -> None:
        """Begin gliding from `current_value` (which may itself be mid-glide —
        callers should pass current_ramped_value()) to `target_value`. A
        no-op if interpolate_seconds is 0."""
        if self.interpolate_seconds <= 0.0:
            self._ramp_start_value = None
            self._ramp_target_value = None
            return
        self._ramp_start_value = self.clamp_value(current_value)
        self._ramp_target_value = self.clamp_value(target_value)
        self._ramp_start_time = now

    def current_ramped_value(self, now: float) -> float | None:
        """Return the in-progress interpolated value, or None if no ramp is
        active. Does not clear the ramp — call advance_ramp() to do that."""
        if self._ramp_target_value is None or self._ramp_start_value is None:
            return None
        elapsed = now - self._ramp_start_time
        duration = self.interpolate_seconds
        if duration <= 0.0 or elapsed >= duration:
            return self._ramp_target_value
        fraction = elapsed / duration
        return self._ramp_start_value + (self._ramp_target_value - self._ramp_start_value) * fraction

    def advance_ramp(self, now: float) -> float | None:
        """Return the in-progress interpolated value and clear the ramp once
        it reaches its target. Returns None if no ramp is active."""
        value = self.current_ramped_value(now)
        if value is None:
            return None
        if value == self._ramp_target_value:
            self._ramp_start_value = None
            self._ramp_target_value = None
        return value

    def is_ramping(self) -> bool:
        return self._ramp_target_value is not None

    def palette_for_pad(
        self,
        pad: int,
        current_value: float,
        *,
        dim_palette: int,
        bright_palettes: tuple[int, int, int, int],
        off_palette: int,
    ) -> int:
        if pad not in self._pad_to_index:
            return off_palette
        if self.bipolar:
            return self._bipolar_palette_for_pad(
                pad, current_value,
                dim_palette=dim_palette,
                bright_palettes=bright_palettes,
                off_palette=off_palette,
            )
        return self._unipolar_palette_for_pad(
            pad, current_value,
            dim_palette=dim_palette,
            bright_palettes=bright_palettes,
            off_palette=off_palette,
        )

    def progress_for_pad(self, pad: int, current_value: float) -> tuple[str, int | None]:
        """Return pad lighting state and microstep for the current value.

        States:
        - "off"  : pad is beyond the active step
        - "dim"  : centered/inactive bipolar state
        - "full" : pad is fully behind the active step
        - "micro": pad owns the active step; second item is microstep 0..3
        """
        if pad not in self._pad_to_index:
            return "off", None
        if self.bipolar:
            return self._bipolar_progress_for_pad(pad, current_value)
        return self._unipolar_progress_for_pad(pad, current_value)

    # unipolar internals

    def _next_unipolar_value(self, pad: int, current_value: float) -> float:
        pad_index = self._pad_to_index[pad]
        total_steps = len(self.pads) * FADER_MICROVALUES_PER_PAD
        target_group = pad_index
        current_step = self._unipolar_step_for_value(current_value)
        if current_step // FADER_MICROVALUES_PER_PAD == target_group:
            micro = (current_step % FADER_MICROVALUES_PER_PAD + 1) % FADER_MICROVALUES_PER_PAD
        else:
            micro = FADER_DEFAULT_MICROVALUE
        step = target_group * FADER_MICROVALUES_PER_PAD + micro
        return self._value_for_unipolar_step(step, total_steps)

    def _unipolar_palette_for_pad(
        self,
        pad: int,
        current_value: float,
        *,
        dim_palette: int,
        bright_palettes: tuple[int, int, int, int],
        off_palette: int,
    ) -> int:
        current_step = self._unipolar_step_for_value(current_value)
        current_group = current_step // FADER_MICROVALUES_PER_PAD
        current_micro = current_step % FADER_MICROVALUES_PER_PAD
        pad_index = self._pad_to_index[pad]
        if pad_index < current_group:
            return bright_palettes[-1]
        if pad_index > current_group:
            return off_palette
        return bright_palettes[current_micro]

    def _unipolar_progress_for_pad(self, pad: int, current_value: float) -> tuple[str, int | None]:
        current_step = self._unipolar_step_for_value(current_value)
        current_group = current_step // FADER_MICROVALUES_PER_PAD
        current_micro = current_step % FADER_MICROVALUES_PER_PAD
        pad_index = self._pad_to_index[pad]
        if pad_index < current_group:
            return "full", None
        if pad_index > current_group:
            return "off", None
        return "micro", current_micro

    def _unipolar_step_for_value(self, value: float) -> int:
        total_steps = max(1, len(self.pads) * FADER_MICROVALUES_PER_PAD)
        if total_steps == 1:
            return 0
        normalized = (self.clamp_value(value) - self.minimum) / (self.maximum - self.minimum)
        normalized = self._inverse_tension(normalized)
        return int(round(normalized * (total_steps - 1)))

    def _value_for_unipolar_step(self, step: int, total_steps: int) -> float:
        if total_steps <= 1:
            return self.minimum
        normalized = step / float(total_steps - 1)
        normalized = self._apply_tension(normalized)
        return self.minimum + normalized * (self.maximum - self.minimum)

    # bipolar internals

    def _next_bipolar_value(self, pad: int, current_value: float) -> float:
        segments = self._bipolar_segments()
        if segments <= 0:
            return self.clamp_value(current_value)
        pad_index = self._pad_to_index[pad]
        midpoint = (len(self.pads) - 1) / 2.0
        distance = abs(pad_index - midpoint)
        group = int(distance)
        current_group, current_micro, current_sign = self._bipolar_state_for_value(current_value)
        sign = -1 if pad_index < midpoint else 1
        if current_group == group and current_sign == sign:
            micro = (current_micro + 1) % FADER_MICROVALUES_PER_PAD
        else:
            micro = FADER_DEFAULT_MICROVALUE
        # Steps 0..total_steps-1 map to magnitudes (1/total_steps)..1 — the
        # centre pads' (group 0) microsteps are the smallest non-zero
        # deviations on their side, never the exact centre. Centre is reached
        # only via the dedicated centre_value() reset, not by stepping.
        step = group * FADER_MICROVALUES_PER_PAD + micro
        total_steps = segments * FADER_MICROVALUES_PER_PAD
        magnitude = (step + 1) / float(total_steps)
        if sign < 0:
            magnitude *= -1.0
        center = (self.minimum + self.maximum) / 2.0
        half_range = (self.maximum - self.minimum) / 2.0
        return self.clamp_value(center + magnitude * half_range)

    def _bipolar_palette_for_pad(
        self,
        pad: int,
        current_value: float,
        *,
        dim_palette: int,
        bright_palettes: tuple[int, int, int, int],
        off_palette: int,
    ) -> int:
        midpoint = (len(self.pads) - 1) / 2.0
        pad_index = self._pad_to_index[pad]
        current_group, current_micro, current_sign = self._bipolar_state_for_value(current_value)
        if current_sign == 0:
            return dim_palette if abs(pad_index - midpoint) < 1.0 else off_palette
        sign = -1 if pad_index < midpoint else 1
        if sign != current_sign:
            return off_palette
        group = int(abs(pad_index - midpoint))
        if group < current_group:
            return bright_palettes[-1]
        if group > current_group:
            return off_palette
        return bright_palettes[current_micro]

    def _bipolar_progress_for_pad(self, pad: int, current_value: float) -> tuple[str, int | None]:
        midpoint = (len(self.pads) - 1) / 2.0
        pad_index = self._pad_to_index[pad]
        current_group, current_micro, current_sign = self._bipolar_state_for_value(current_value)
        if current_sign == 0:
            if abs(pad_index - midpoint) < 1.0:
                return "dim", None
            return "off", None
        sign = -1 if pad_index < midpoint else 1
        if sign != current_sign:
            return "off", None
        group = int(abs(pad_index - midpoint))
        if group < current_group:
            return "full", None
        if group > current_group:
            return "off", None
        return "micro", current_micro

    def _bipolar_state_for_value(self, value: float) -> tuple[int, int, int]:
        center = (self.minimum + self.maximum) / 2.0
        half_range = (self.maximum - self.minimum) / 2.0
        if half_range <= 0.0:
            return 0, 0, 0
        normalized = (self.clamp_value(value) - center) / half_range
        if abs(normalized) < 1e-9:
            return 0, 0, 0
        sign = -1 if normalized < 0.0 else 1
        magnitude = self._inverse_tension(abs(normalized))
        segments = self._bipolar_segments()
        total_steps = max(1, segments * FADER_MICROVALUES_PER_PAD)
        step = int(round(magnitude * total_steps)) - 1
        step = max(0, min(total_steps - 1, step))
        return step // FADER_MICROVALUES_PER_PAD, step % FADER_MICROVALUES_PER_PAD, sign

    def _bipolar_segments(self) -> int:
        return max(1, len(self.pads) // 2)

    # tension helpers

    def _apply_tension(self, normalized: float) -> float:
        normalized = _clamp(float(normalized), 0.0, 1.0)
        exponent = self._tension_exponent()
        if exponent == 1.0:
            return normalized
        return math.pow(normalized, exponent)

    def _inverse_tension(self, normalized: float) -> float:
        normalized = _clamp(float(normalized), 0.0, 1.0)
        exponent = self._tension_exponent()
        if exponent == 1.0:
            return normalized
        return math.pow(normalized, 1.0 / exponent)

    def _tension_exponent(self) -> float:
        return math.pow(2.0, self.tension / 3.0)

# Page selector (right column)
# Canonical order: slot 0 = top pad (89), slot 7 = bottom pad (19).
SELECTOR_PADS = CUSTOM_MODE_SELECTOR_PADS  # (89, 79, 69, 59, 49, 39, 29, 19)

def pad_to_slot(pad: int) -> int | None:
    """Return 0-based slot index for a right-column pad, or None."""
    try:
        return SELECTOR_PADS.index(pad)
    except ValueError:
        return None

def selector_lighting(
    pad: int,
    active_index: int,
    page_count: int,
    active_color: int = LP3_MENU_ACTIVE,
    inactive_color: int = LP3_MENU_INACTIVE,
) -> LedColor:
    """LED colour for a right-column pad given the active page and total count."""
    slot = pad_to_slot(pad)
    if slot is None or slot >= page_count:
        return LedColor(PAD_DISABLED)
    if slot == active_index:
        return LedColor(active_color)
    return LedColor(inactive_color)

def handle_press(pad: int, active_index: int, page_count: int) -> int:
    """Return the new active page index after pressing a selector pad."""
    slot = pad_to_slot(pad)
    if slot is None or slot >= page_count:
        return active_index
    return slot

def step_page(active_index: int, direction: int, page_count: int) -> int:
    """Return the new active page index after stepping ±1, clamped to valid range."""
    return max(0, min(page_count - 1, active_index + direction))

# XY pad geometry
def pad_to_xy(pad: int) -> tuple[int, int] | None:
    """Convert a grid pad to (col, row) in 0-7 range.
    col 0 = left, col 7 = right; row 0 = bottom, row 7 = top.
    Returns None for the side column or anything off-grid."""
    if pad % 10 == 9:
        return None
    row = (pad // 10) - 1
    col = (pad % 10) - 1
    if not (0 <= row <= 7 and 0 <= col <= 7):
        return None
    return col, 7 - row

def _vert_fader_pads(col: int) -> tuple[int, ...]:
    units = col + 1
    return tuple(row * 10 + units for row in range(1, 9))  # 1x (bottom) … 8x (top)

def _horiz_fader_pads(row: int) -> tuple[int, ...]:
    tens = 8 - row
    return tuple(tens * 10 + (col + 1) for col in range(8))  # left … right

def vert_fader_defs() -> tuple[tuple[int, tuple[int, ...]], ...]:
    """One (cc, pads) per column.  Pads ordered bottom→top (low→high value)."""
    return tuple((XY_VERT_FADER_CCS[col], _vert_fader_pads(col)) for col in range(8))

def horiz_fader_defs() -> tuple[tuple[int, tuple[int, ...]], ...]:
    """One (cc, pads) per row.  Pads ordered left→right (low→high value)."""
    return tuple((XY_HORIZ_FADER_CCS[row], _horiz_fader_pads(row)) for row in range(8))

def vert_bipolar_fader_defs() -> tuple[tuple[int, tuple[int, ...]], ...]:
    """One (cc, pads) per column, bipolar.  Pads ordered bottom→top
    (negative extreme → centre → positive extreme)."""
    return tuple((XY_VERT_BIPOLAR_FADER_CCS[col], _vert_fader_pads(col)) for col in range(8))

def horiz_bipolar_fader_defs() -> tuple[tuple[int, tuple[int, ...]], ...]:
    """One (cc, pads) per row, bipolar.  Pads ordered left→right
    (negative extreme → centre → positive extreme)."""
    return tuple((XY_HORIZ_BIPOLAR_FADER_CCS[row], _horiz_fader_pads(row)) for row in range(8))

def modwheel_pads() -> tuple[int, ...]:
    """Side-column pads ordered bottom→top for the modwheel fader."""
    return tuple(reversed(SELECTOR_PADS))   # 19 (bottom) … 89 (top)

def grid_fader_cc(pad: int, page: int) -> int | None:
    """Return the fader CC a grid pad drives on the given fader page, else None."""
    xy = pad_to_xy(pad)
    if xy is None:
        return None
    col, row = xy
    if page == XY_PAGE_VERT:
        return XY_VERT_FADER_CCS[col]
    if page == XY_PAGE_HORIZ:
        return XY_HORIZ_FADER_CCS[row]
    if page == XY_PAGE_VERT_BIPOLAR:
        return XY_VERT_BIPOLAR_FADER_CCS[col]
    if page == XY_PAGE_HORIZ_BIPOLAR:
        return XY_HORIZ_BIPOLAR_FADER_CCS[row]
    return None

def xy_values(x: int, y: int) -> tuple[int, int]:
    """Return (x_val, y_val) CC values (0-127) for a grid position."""
    return int(round(x / 7 * 127)), int(round(y / 7 * 127))

def xy_grid_lighting(
    pad: int,
    cursor_x: int | None,
    cursor_y: int | None,
) -> LedColor:
    """Crosshair lighting for the XY page: active row+column dim, intersection bright."""
    xy = pad_to_xy(pad)
    if xy is None:
        return LedColor(PAD_DISABLED)
    if cursor_x is not None and cursor_y is not None:
        pad_x, pad_y = xy
        cursor_col = int(round(cursor_x / 127 * 7))
        cursor_row = int(round(cursor_y / 127 * 7))
        on_col = pad_x == cursor_col
        on_row = pad_y == cursor_row
        if on_col and on_row:
            return LedColor(LP3_MENU_ACTIVE)
        if on_col or on_row:
            return LedColor(LP3_MENU_INACTIVE)
    return LedColor(PAD_DISABLED)
# ~gargoyles rule~