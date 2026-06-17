# led_display.py
# All LED output: sysex batching, RGB colour conversion, palette fallback,
# and the per-pad / per-CC refresh helpers.
#
# The surface object is passed in on every call so this module stays
# stateless and has no circular imports.
from fl_stubs import device, midi
from constants import (
    PALETTE_RGB_BY_FAMILY,
    LP3_PALETTE_RGB,
    mk1_velocity_from_rgb,
    mk1_velocity_from_rg,
    mk1_fold_blue,
    LedColor,
    SYSEX_PREFIX,
    SYSEX_LED_SET,
    SYSEX_LED_SET_RGB,
    SYSEX_LIGHT_ALL,
    SYSEX_SCROLL,
    LP3_SYSEX_SCROLL,
    LP3_SCROLL_SPEED,
    SYSEX_LAYOUT,
    SESSION_CHANNEL,
    USER2_FALLBACK_CHANNELS,
    SETTINGS_GRID_PADS,
    PLAYABLE_PADS,
    TOP_CCS,
    PAD_OFF,
    PAD_ROOT,
    PAD_ACTION,
    LP3_MENU_LOCKED,
    FPC_COLOR_SATURATION_MK2,
    FPC_COLOR_GAMMA_MK2,
    pad_to_mk1_note,
)
_config = {
    "sysex_prefix": SYSEX_PREFIX,
    "top_ccs": TOP_CCS,
    "side_column_is_cc": False,
    "mode": "mk2",
    "mk1_double_buffer": False,
    "mk1_double_buffer_threshold": 12,
    "lp3_programmer_toggle": 0x0E,
    "lp3_led_command": 0x03,
    "color_saturation": FPC_COLOR_SATURATION_MK2,
    "color_gamma": FPC_COLOR_GAMMA_MK2,
}

# SysEx helpers
def _sysex_bytes(data) -> bytes:
    return bytes(byte & 0xFF for byte in data)

def configure_surface(
    *,
    sysex_prefix=SYSEX_PREFIX,
    top_ccs=TOP_CCS,
    side_column_is_cc=False,
    mode="mk2",
    mk1_double_buffer=False,
    mk1_double_buffer_threshold=12,
    color_saturation=FPC_COLOR_SATURATION_MK2,
    color_gamma=FPC_COLOR_GAMMA_MK2,
) -> None:
    _config["sysex_prefix"] = tuple(int(value) & 0xFF for value in sysex_prefix)
    _config["top_ccs"] = tuple(int(value) for value in top_ccs)
    _config["side_column_is_cc"] = bool(side_column_is_cc)
    _config["mode"] = mode
    _config["mk1_double_buffer"] = bool(mk1_double_buffer)
    _config["mk1_double_buffer_threshold"] = max(1, int(mk1_double_buffer_threshold))
    _config["color_saturation"] = max(0.0, float(color_saturation))
    _config["color_gamma"] = max(0.01, float(color_gamma))

def rgb_max_value() -> int:
    return 127 if _config["mode"] == "lp3" else 63

def surface_mode() -> str:
    return str(_config["mode"])

def palette_rgb_from_index(palette_index: int) -> tuple[int, int, int]:
    """Return the active surface's native RGB triple for a palette index."""
    return _native_palette_rgb(palette_index)

def dim_palette_rgb(palette_index: int, brightness: float) -> tuple[int, int, int]:
    """Resolve a palette index to RGB, then scale it for the active surface."""
    brightness = max(0.0, min(1.0, float(brightness)))
    red, green, blue = _native_palette_rgb(palette_index)
    return (

        int(round(red * brightness)),
        int(round(green * brightness)),
        int(round(blue * brightness)),
    )

def _native_palette_rgb(palette_index: int) -> tuple[int, int, int]:
    mode = surface_mode()
    palette = PALETTE_RGB_BY_FAMILY.get(mode, PALETTE_RGB_BY_FAMILY["mk2"])
    idx = int(palette_index)
    red, green, blue = palette[idx] if 0 <= idx < len(palette) else (0, 0, 0)
    source_max = 255 if _config["mode"] == "lp3" else 63
    target_max = rgb_max_value()
    if source_max != target_max:
        red, green, blue = (
            int(round(red * target_max / source_max)),
            int(round(green * target_max / source_max)),
            int(round(blue * target_max / source_max)),
        )
    return tuple(max(0, min(target_max, value)) for value in (red, green, blue))

def _lp3_colorspec(pad: int, palette_color: int, rgb_color: tuple[int, int, int] | None) -> list[int]:
    if rgb_color is not None:
        red, green, blue = rgb_color
        return [0x03, pad, red, green, blue]
    return [0x00, pad, palette_color]

def set_layout(layout: int) -> None:
    """Send a layout-switch sysex to the hardware."""
    if _config["mode"] == "lp3":
        data = list(_config["sysex_prefix"]) + [_config["lp3_programmer_toggle"], layout, 0xF7]
    else:
        data = list(_config["sysex_prefix"]) + [SYSEX_LAYOUT, layout, 0xF7]
    device.midiOutSysex(_sysex_bytes(data))

def supports_text_scroll() -> bool:
    """True on surfaces with the native scrolling-text SysEx (MK2, LPX, LPM3).

    MK1-protocol hardware has no SysEx LED commands at all, so it has no
    scroll either.
    """
    return _config["mode"] != "mk1"

def scroll_text(text: str, color: int = 0, *, loop: bool = False) -> None:
    """Scroll ASCII *text* across the pads using the native scroll SysEx.

    The two device families speak different scroll dialects:
      MK2:  prefix + 14h + <colour> + <loop> + <text> + F7        (PRM p.14)
      MK3:  prefix + 07h + <loop> + <speed> + <colourspec> + <text> + F7
            where <colourspec> = 00h <palette>                    (LPX PRM p.23)
    *color* is a palette index in both cases.  When *loop* is False the
    hardware plays the text once and then restores the LEDs on its own.
    Non-ASCII bytes and the reserved speed codes (1-7) are dropped so they
    can't corrupt the stream / change scroll speed mid-text.
    """
    if not supports_text_scroll():
        return
    payload = [byte for byte in text.encode("ascii", "ignore") if 0x20 <= byte <= 0x7E]
    prefix = list(_config["sysex_prefix"])
    color = int(color) & 0x7F
    loop_byte = 0x01 if loop else 0x00
    if _config["mode"] == "lp3":
        # MK3: loop, speed, then a palette colourspec (type 0 + index).
        data = prefix + [
            LP3_SYSEX_SCROLL,
            loop_byte,
            LP3_SCROLL_SPEED,
            0x00, color,
        ] + payload + [0xF7]
    else:
        data = prefix + [SYSEX_SCROLL, color, loop_byte] + payload + [0xF7]
    device.midiOutSysex(_sysex_bytes(data))

def stop_scroll() -> None:
    """Stop any in-progress scroll by sending the empty scroll command.

    A no-op on surfaces without native scroll.  Note the hardware does NOT
    repaint the grid afterwards, so callers must follow this with a cache
    invalidation + full refresh if a looping scroll might have been running.
    """
    if not supports_text_scroll():
        return
    command = LP3_SYSEX_SCROLL if _config["mode"] == "lp3" else SYSEX_SCROLL
    data = list(_config["sysex_prefix"]) + [command, 0xF7]
    device.midiOutSysex(_sysex_bytes(data))

def clear_surface(grid_led_cache: dict, top_led_cache: dict) -> None:
    """Blank every pad and CC LED; flush both caches."""
    if _config["mode"] == "mk1":
        # MK1/S reset command: all LEDs off, layout defaults restored.
        device.midiOutMsg(midi.MIDI_CONTROLCHANGE, SESSION_CHANNEL, 0, 0)
    elif _config["mode"] != "lp3":
        data = list(_config["sysex_prefix"]) + [SYSEX_LIGHT_ALL, 0x00, 0xF7]
        device.midiOutSysex(_sysex_bytes(data))
        for channel in (SESSION_CHANNEL,) + USER2_FALLBACK_CHANNELS:
            for pad in PLAYABLE_PADS:
                if _config["side_column_is_cc"] and pad not in SETTINGS_GRID_PADS:
                    device.midiOutMsg(midi.MIDI_CONTROLCHANGE, channel, pad, 0)
                else:
                    device.midiOutMsg(midi.MIDI_NOTEON, channel, pad, 0)
            for cc in _config["top_ccs"]:
                device.midiOutMsg(midi.MIDI_CONTROLCHANGE, channel, cc, 0)
    grid_led_cache.clear()
    top_led_cache.clear()

def _mk1_velocity(palette_index: int) -> int:
    """Convert a palette index to a MK1 LED velocity (red/green 2-bit encoding).

    The index is looked up in the MK3 (LP3) palette to get its true RGB, then
    run through the same blue-fold + saturation/gamma pipeline as RGB pads
    (rgb6_from_rgb in mk1 mode) and quantized.  This keeps indexed pads and
    RGB pads consistent on first-gen hardware, instead of relying on a separate
    hand-tuned per-index table.
    """
    idx = int(palette_index)
    if 0 <= idx < len(LP3_PALETTE_RGB):
        red, green, blue = LP3_PALETTE_RGB[idx]
    else:
        red, green, blue = (0, 0, 0)
    # rgb6_from_rgb (mk1 mode) folds blue into red/green, applies the configured
    # MK1 saturation/gamma, and scales to the 0-63 range mk1_velocity expects.
    tuned_r, tuned_g, _tuned_b = rgb6_from_rgb(red, green, blue)
    return mk1_velocity_from_rgb(tuned_r, tuned_g, maximum=rgb_max_value())

def _mk1_velocity_for(color: int, rgb_color: tuple[int, int, int] | None, mk1: int | None) -> int:
    """Resolve a MK1 LED velocity, honouring an explicit page-level mk1 value.

    An explicit *mk1* (two-digit RG) always wins.  Otherwise an RGB colour is
    folded down to red/green, and a bare palette index uses the reverse-map.
    """
    if mk1 is not None:
        return mk1_velocity_from_rg(mk1)
    if rgb_color is not None:
        red, green, _blue = rgb_color
        return mk1_velocity_from_rgb(red, green, maximum=rgb_max_value())
    return _mk1_velocity(color)

def _mk1_ignore_velocity_for(color: int, rgb_color: tuple[int, int, int] | None, mk1: int | None) -> int:
    return _mk1_velocity_for(color, rgb_color, mk1) & 0x33

def _send_mk1_buffer_mode(value: int) -> None:
    device.midiOutMsg(midi.MIDI_CONTROLCHANGE, SESSION_CHANNEL, 0, int(value) & 0x7F)

def send_grid_led_fallback(
    pad: int,
    color: int,
    rgb_color: tuple[int, int, int] | None = None,
    mk1: int | None = None,
) -> None:
    if _config["mode"] == "lp3":
        return
    if _config["mode"] == "mk1":
        velocity = _mk1_velocity_for(color, rgb_color, mk1)
        device.midiOutMsg(midi.MIDI_NOTEON, SESSION_CHANNEL, pad_to_mk1_note(pad), velocity)
        return
    if _config["side_column_is_cc"] and pad not in SETTINGS_GRID_PADS:
        device.midiOutMsg(midi.MIDI_CONTROLCHANGE, SESSION_CHANNEL, pad, color)
    else:
        device.midiOutMsg(midi.MIDI_NOTEON, SESSION_CHANNEL, pad, color)
    for channel in USER2_FALLBACK_CHANNELS:
        if _config["side_column_is_cc"] and pad not in SETTINGS_GRID_PADS:
            device.midiOutMsg(midi.MIDI_CONTROLCHANGE, channel, pad, color)
        else:
            device.midiOutMsg(midi.MIDI_NOTEON, channel, pad, color)

def send_top_led_fallback(cc: int, color: int, mk1: int | None = None) -> None:
    if _config["mode"] == "lp3":
        return
    if _config["mode"] == "mk1":
        velocity = _mk1_velocity_for(color, None, mk1)
        device.midiOutMsg(midi.MIDI_CONTROLCHANGE, SESSION_CHANNEL, cc, velocity)
        return
    device.midiOutMsg(midi.MIDI_CONTROLCHANGE, SESSION_CHANNEL, cc, color)
    for channel in USER2_FALLBACK_CHANNELS:
        device.midiOutMsg(midi.MIDI_CONTROLCHANGE, channel, cc, color)

def software_pulse_brightness(phase: float) -> float:
    """Return a brightness scalar in [0.25, 1.0] for the software pulse.
    Matches the LP3 hardware pulse waveform: skewed triangle that rises from
    25% to 100% over the first 25% of the cycle then falls back to 25% over
    the remaining 75%.  phase is in [0.0, 1.0).
    """
    if phase < 0.25:
        return 0.25 + 0.75 * (phase / 0.25)
    return 1.0 - 0.75 * ((phase - 0.25) / 0.75)

def send_top_led_pulse(cc: int, color: int) -> None:
    """Pulse a top-row LED at the given palette colour (LP3 only).
    LP3 (LPX, Mini MK3): CC on MIDI channel 3 engages the hardware pulse mode.
    MK2 software pulse is driven separately via send_top_led_rgb.
    """
    if _config["mode"] == "lp3":
        device.midiOutMsg(midi.MIDI_CONTROLCHANGE, 2, cc, color)

def send_top_led_rgb(cc: int, rgb: tuple[int, int, int]) -> None:
    """Set a top-row LED to an explicit 6-bit RGB value (MK2 only)."""
    r, g, b = rgb
    message = list(_config["sysex_prefix"]) + [SYSEX_LED_SET_RGB, cc, r, g, b, 0xF7]
    device.midiOutSysex(_sysex_bytes(message))

def send_top_led_mk1_pulse(cc: int, rgb: tuple[int, int, int]) -> None:
    """Software-pulse a top-row CC LED (MK1 only).

    MK1 has no SysEx LED commands, so the pulse brightness is folded down to
    a red/green velocity (same pipeline as indexed pads) and sent as a CC
    message each frame.
    """
    if _config["mode"] != "mk1":
        return
    red, green, blue = rgb
    tuned_r, tuned_g, _tuned_b = rgb6_from_rgb(red, green, blue)
    velocity = mk1_velocity_from_rgb(tuned_r, tuned_g, maximum=rgb_max_value())
    device.midiOutMsg(midi.MIDI_CONTROLCHANGE, SESSION_CHANNEL, cc, velocity)

def _as_led_color(value) -> LedColor:
    """Normalise a lighting-fn return into a LedColor.

    Accepts a LedColor, a legacy (index, rgb) pair, or a bare palette index.
    """
    if isinstance(value, LedColor):
        return value
    if isinstance(value, tuple):
        if len(value) >= 3:
            return LedColor(value[0], value[1], value[2])
        if len(value) == 2:
            return LedColor(value[0], value[1], None)
        return LedColor(value[0], None, None)
    return LedColor(int(value), None, None)

# Batch surface refresh
def refresh_surface(
    grid_lighting_fn,
    top_color_fn,
    grid_led_cache: dict,
    top_led_cache: dict,
    pulse_top_ccs: set | None = None,
    pulse_grid_pads: set | None = None,
) -> None:
    """Recompute every LED and flush changed values via sysex.
    *grid_lighting_fn(pad)* → LedColor (index, rgb | None, mk1 | None)
    *top_color_fn(cc)*      → LedColor or bare palette index
    *pulse_top_ccs*         → CCs that should pulse; excluded from the static
                              batch so a subsequent send_top_led_pulse call wins
                              cleanly with no static override to cancel it.
    *pulse_grid_pads*       → grid/side-column pads that should pulse; excluded
                              from the static batch for the same reason.
    """
    pairs: list[int] = []
    rgb_entries: list[int] = []
    mk1_grid_entries: list[tuple[int, LedColor]] = []
    mk1_top_entries: list[tuple[int, LedColor]] = []
    for pad in PLAYABLE_PADS:
        if pulse_grid_pads and pad in pulse_grid_pads:
            grid_led_cache.pop(pad, None)
            continue
        led = _as_led_color(grid_lighting_fn(pad))
        if grid_led_cache.get(pad) == led:
            continue
        grid_led_cache[pad] = led
        if _config["mode"] == "mk1":
            mk1_grid_entries.append((pad, led))
            continue
        palette_color, rgb_color = led.index, led.rgb
        # MK2/LP3 light every LED through the batched SysEx (flushed once after
        # the loop), which addresses pads by Session-layout index regardless of
        # the active layout or channel — so no per-pad note/CC fallback is
        # needed.  (The MK1 branch above handles MK1, which has no SysEx.)
        if rgb_color is not None:
            if _config["mode"] == "lp3":
                rgb_entries.extend(_lp3_colorspec(pad, palette_color, rgb_color))
            else:
                red, green, blue = rgb_color
                rgb_entries.extend((pad, red, green, blue))
        else:
            if _config["mode"] == "lp3":
                pairs.extend(_lp3_colorspec(pad, palette_color, None))
            else:
                pairs.extend((pad, palette_color))
    for cc in _config["top_ccs"]:
        if pulse_top_ccs and cc in pulse_top_ccs:
            top_led_cache.pop(cc, None)
            continue
        led = _as_led_color(top_color_fn(cc))
        if top_led_cache.get(cc) == led:
            continue
        top_led_cache[cc] = led
        if _config["mode"] == "mk1":
            mk1_top_entries.append((cc, led))
            continue
        color = led.index
        if _config["mode"] == "lp3":
            pairs.extend(_lp3_colorspec(cc, color, None))
        else:
            pairs.extend((cc, color))
    if _config["mode"] == "mk1":
        total = len(mk1_grid_entries) + len(mk1_top_entries)
        if total <= 0:
            return
        if _config["mk1_double_buffer"] and total >= _config["mk1_double_buffer_threshold"]:
            # Launchpad S: stage a large frame invisibly, swap, then return to
            # simple mode so one-off pad updates still light immediately.
            _send_mk1_buffer_mode(0x34)
            for pad, led in mk1_grid_entries:
                device.midiOutMsg(
                    midi.MIDI_NOTEON,
                    SESSION_CHANNEL,
                    pad_to_mk1_note(pad),
                    _mk1_ignore_velocity_for(led.index, led.rgb, led.mk1),
                )
            for cc, led in mk1_top_entries:
                device.midiOutMsg(
                    midi.MIDI_CONTROLCHANGE,
                    SESSION_CHANNEL,
                    cc,
                    _mk1_ignore_velocity_for(led.index, led.rgb, led.mk1),
                )
            _send_mk1_buffer_mode(0x31)
            _send_mk1_buffer_mode(0x20)
            return
        for pad, led in mk1_grid_entries:
            send_grid_led_fallback(pad, fallback_palette(led.index, led.rgb), led.rgb, led.mk1)
        for cc, led in mk1_top_entries:
            send_top_led_fallback(cc, led.index, led.mk1)
        return
    if pairs:
        if _config["mode"] == "lp3":
            message = list(_config["sysex_prefix"]) + [_config["lp3_led_command"]] + pairs + [0xF7]
        else:
            message = list(_config["sysex_prefix"]) + [SYSEX_LED_SET] + pairs + [0xF7]
        device.midiOutSysex(_sysex_bytes(message))
    if rgb_entries:
        if _config["mode"] == "lp3":
            message = list(_config["sysex_prefix"]) + [_config["lp3_led_command"]] + rgb_entries + [0xF7]
        else:
            message = list(_config["sysex_prefix"]) + [SYSEX_LED_SET_RGB] + rgb_entries + [0xF7]
        device.midiOutSysex(_sysex_bytes(message))

def refresh_grid_pad(
    pad: int,
    grid_lighting_fn,
    grid_led_cache: dict,
) -> None:
    """Refresh a single grid pad, skipping if the cache is still valid."""
    led = _as_led_color(grid_lighting_fn(pad))
    if grid_led_cache.get(pad) == led:
        return
    grid_led_cache[pad] = led
    palette_color, rgb_color = led.index, led.rgb
    # MK1 has no SysEx LED commands, so the note-on fallback is the only way to
    # light the pad.  MK2/LP3 light it through SysEx below, which addresses the
    # pad by index regardless of layout/channel — no fallback needed.
    if _config["mode"] == "mk1":
        fallback_color = fallback_palette(palette_color, rgb_color)
        send_grid_led_fallback(pad, fallback_color, rgb_color, led.mk1)
        return
    if _config["mode"] == "lp3":
        message = list(_config["sysex_prefix"]) + [_config["lp3_led_command"]] + _lp3_colorspec(
            pad,
            palette_color,
            rgb_color,
        ) + [0xF7]
    elif rgb_color is None:
        message = list(_config["sysex_prefix"]) + [SYSEX_LED_SET, pad, palette_color, 0xF7]
    else:
        red, green, blue = rgb_color
        message = list(_config["sysex_prefix"]) + [
            SYSEX_LED_SET_RGB,
            pad,
            red,
            green,
            blue,
            0xF7,
    ]
    device.midiOutSysex(_sysex_bytes(message))

def refresh_grid_pads(
    pads,
    grid_lighting_fn,
    grid_led_cache: dict,
) -> None:
    """Refresh multiple grid pads, flushing them as one SysEx batch when possible."""
    if _config["mode"] == "mk1":
        for pad in pads:
            refresh_grid_pad(pad, grid_lighting_fn, grid_led_cache)
        return
    pairs: list[int] = []
    rgb_entries: list[int] = []
    for pad in pads:
        led = _as_led_color(grid_lighting_fn(pad))
        if grid_led_cache.get(pad) == led:
            continue
        grid_led_cache[pad] = led
        palette_color, rgb_color = led.index, led.rgb
        if _config["mode"] == "lp3":
            entry = _lp3_colorspec(pad, palette_color, rgb_color)
            if rgb_color is None:
                pairs.extend(entry)
            else:
                rgb_entries.extend(entry)
            continue
        fallback_color = fallback_palette(palette_color, rgb_color)
        if rgb_color is None:
            pairs.extend((pad, fallback_color))
        else:
            red, green, blue = rgb_color
            rgb_entries.extend((pad, red, green, blue))
    if pairs:
        if _config["mode"] == "lp3":
            message = list(_config["sysex_prefix"]) + [_config["lp3_led_command"]] + pairs + [0xF7]
        else:
            message = list(_config["sysex_prefix"]) + [SYSEX_LED_SET] + pairs + [0xF7]
        device.midiOutSysex(_sysex_bytes(message))
    if rgb_entries:
        if _config["mode"] == "lp3":
            message = list(_config["sysex_prefix"]) + [_config["lp3_led_command"]] + rgb_entries + [0xF7]
        else:
            message = list(_config["sysex_prefix"]) + [SYSEX_LED_SET_RGB] + rgb_entries + [0xF7]
        device.midiOutSysex(_sysex_bytes(message))

# Colour utilities
def fallback_palette(
    palette_color: int,
    rgb_color: tuple[int, int, int] | None,
) -> int:
    """Map an RGB colour to the nearest useful palette index.

    On MK1-protocol hardware (Launchpad S and similar) LEDs are red/green
    only — there is no blue element. Any blue contribution is folded into
    red and green so that, e.g., cyan/magenta/blue still light up rather
    than collapsing to a single hue.
    """
    if rgb_color is None:
        return palette_color
    red, green, blue = rgb_color
    # Fold blue into red and green equally, since MK1 hardware can't show it.
    red = red + blue // 2
    green = green + blue // 2
    brightness = red + green
    if brightness <= 6:
        return PAD_OFF
    # Roughly equal red/green -> amber/yellow (also covers white, which MK1
    # hardware can't distinguish from yellow without a blue element).
    if abs(red - green) <= max(red, green) // 4:
        return LP3_MENU_LOCKED
    if red > green:
        return PAD_ACTION
    return PAD_ROOT

def apply_fpc_color_tuning(red: int, green: int, blue: int) -> tuple[int, int, int]:
    """Saturation-boost + gamma-correct an 8-bit RGB triple."""
    return apply_color_tuning(

        red,
        green,
        blue,
        saturation=float(_config["color_saturation"]),
        gamma=float(_config["color_gamma"]),
    )

def apply_color_tuning(
    red: int,
    green: int,
    blue: int,
    *,
    saturation: float,
    gamma: float,
) -> tuple[int, int, int]:
    """Saturation-boost + gamma-correct an 8-bit RGB triple."""
    saturation = max(0.0, float(saturation))
    gamma = max(0.01, float(gamma))
    average = (red + green + blue) / 3.0
    tuned = []
    for component in (red, green, blue):
        saturated  = average + (component - average) * saturation
        normalized = max(0.0, min(1.0, saturated / 255.0))
        gamma_corrected = pow(normalized, 1.0 / gamma)
        tuned.append(int(round(gamma_corrected * 255.0)))
    return tuple(tuned)

def rgb6_from_rgb(
    red: int,
    green: int,
    blue: int,
    *,
    saturation: float | None = None,
    gamma: float | None = None,
) -> tuple[int, int, int]:
    if saturation is None:
        saturation = float(_config["color_saturation"])
    if gamma is None:
        gamma = float(_config["color_gamma"])
    if _config["mode"] == "mk1":
        # Fold blue into red/green before tuning, so saturation/gamma can
        # re-punch-up whatever brightness the fold dulled.
        red, green, blue = mk1_fold_blue(red, green, blue)
    red, green, blue = apply_color_tuning(
        red,
        green,
        blue,
        saturation=saturation,
        gamma=gamma,
    )
    maximum = rgb_max_value()
    return tuple((component * maximum + 127) // 255 for component in (red, green, blue))

def rgb6_from_color(
    color: int,
    *,
    saturation: float | None = None,
    gamma: float | None = None,
) -> tuple[int, int, int]:
    """Unpack a packed FL/FPC colour and convert it for the active Launchpad profile."""
    blue  = color & 0xFF
    green = (color >> 8) & 0xFF
    red   = (color >> 16) & 0xFF
    return rgb6_from_rgb(

        red,
        green,
        blue,
        saturation=saturation,
        gamma=gamma,
    )
# ~gargoyles rule~