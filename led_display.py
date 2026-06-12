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
    color_saturation=FPC_COLOR_SATURATION_MK2,
    color_gamma=FPC_COLOR_GAMMA_MK2,
) -> None:
    _config["sysex_prefix"] = tuple(int(value) & 0xFF for value in sysex_prefix)
    _config["top_ccs"] = tuple(int(value) for value in top_ccs)
    _config["side_column_is_cc"] = bool(side_column_is_cc)
    _config["mode"] = mode
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

def clear_surface(grid_led_cache: dict, top_led_cache: dict) -> None:
    """Blank every pad and CC LED; flush both caches."""
    if _config["mode"] == "mk1":
        # MK1: individual note-on messages, velocity=12 (red=0,green=0,flags=12=off).
        for pad in PLAYABLE_PADS:
            device.midiOutMsg(midi.MIDI_NOTEON, SESSION_CHANNEL, pad_to_mk1_note(pad), 12)
        for cc in _config["top_ccs"]:
            device.midiOutMsg(midi.MIDI_CONTROLCHANGE, SESSION_CHANNEL, cc, 12)
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
    for pad in PLAYABLE_PADS:
        if pulse_grid_pads and pad in pulse_grid_pads:
            grid_led_cache.pop(pad, None)
            continue
        led = _as_led_color(grid_lighting_fn(pad))
        if grid_led_cache.get(pad) == led:
            continue
        grid_led_cache[pad] = led
        palette_color, rgb_color = led.index, led.rgb
        # For RGB pads on MK2/LP3 the batched RGB sysex (flushed once after the
        # loop) is the authoritative update.  Sending the per-pad palette
        # approximation first paints a brighter, wrong colour that the RGB batch
        # then overwrites — on MK2 that interim shows as a one-frame flash across
        # a freshly-scrolled grid.  Only MK1 (no sysex) needs the RGB fallback.
        if rgb_color is None or _config["mode"] == "mk1":
            send_grid_led_fallback(pad, fallback_palette(palette_color, rgb_color), rgb_color, led.mk1)
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
        color = led.index
        send_top_led_fallback(cc, color, led.mk1)
        if _config["mode"] == "lp3":
            pairs.extend(_lp3_colorspec(cc, color, None))
        else:
            pairs.extend((cc, color))
    # MK1 has no SysEx LED commands; all output already went through send_grid_led_fallback above.
    if _config["mode"] == "mk1":
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
    fallback_color = fallback_palette(palette_color, rgb_color)
    send_grid_led_fallback(pad, fallback_color, rgb_color, led.mk1)
    # MK1 has no SysEx LED commands; fallback already sent the note-on.
    if _config["mode"] == "mk1":
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