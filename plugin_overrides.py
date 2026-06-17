from __future__ import annotations

import re

from fl_stubs import midi, plugins, ui
import fpc_mode as fm
import led_display
from constants import (
    GROSS_BEAT_FADER_DIM_COLOR,
    GROSS_BEAT_FADER_MICRO_COLORS,
    GROSS_BEAT_FADER_PADS,
    GROSS_BEAT_SLOT_PADS,
    GROSS_BEAT_TIME_COLOR,
    GROSS_BEAT_TOGGLE_PAD,
    GROSS_BEAT_VOLUME_COLOR,
    LP3_BACKGROUND_OFF,
    MODE_FPC,
    PAD_ACTION,
    PAD_DISABLED,
    PLUGIN_PAD_OVERRIDE_GROSS_BEAT,
    PLUGIN_PAD_OVERRIDE_IDS,
    SETTINGS_GRID_PADS,
    WID_PLUGIN,
    WID_PLUGIN_EFFECT,
    WID_PLUGIN_GENERATOR,
    LedColor,
)


def active_override(surface) -> str | None:
    if surface.surface_mode != MODE_FPC or surface.settings_visible:
        return None
    override_id = focused_override_id()
    if override_id == surface._suppressed_plugin_override_id:
        return None
    return override_id


def focused_override_id() -> str | None:
    return PLUGIN_PAD_OVERRIDE_IDS.get(focused_plugin_name())


def focused_plugin_name() -> str:
    if not plugin_window_focused():
        return ""
    try:
        return str(ui.getFocusedPluginName() or "").strip().lower()
    except Exception:
        return ""


def plugin_window_focused() -> bool:
    for wid in (WID_PLUGIN_EFFECT, WID_PLUGIN_GENERATOR, WID_PLUGIN):
        try:
            if int(ui.getFocused(wid)) == 1:
                return True
        except Exception:
            continue
    return False


def handle_pad(surface, override_id: str, event, pad: int, velocity: int, pressed: bool) -> bool:
    if override_id == PLUGIN_PAD_OVERRIDE_GROSS_BEAT:
        return handle_gross_beat_pad(surface, event, pad, pressed)
    return False


def lighting(surface, override_id: str, pad: int) -> LedColor:
    if override_id != PLUGIN_PAD_OVERRIDE_GROSS_BEAT:
        return LedColor(PAD_DISABLED)
    return gross_beat_lighting(surface, pad)


def handle_gross_beat_pad(surface, _event, pad: int, pressed: bool) -> bool:
    if fm.is_fpc_selector(pad):
        return False
    if pressed:
        surface._plugin_override_held_pads.add(pad)
    else:
        surface._plugin_override_held_pads.discard(pad)
        return True
    if pad == GROSS_BEAT_TOGGLE_PAD:
        surface.state["gross_beat_slot_mode"] = (
            "volume"
            if gross_beat_slot_mode(surface) == "time"
            else "time"
        )
        surface._save_state()
        surface._refresh_grid_pads((GROSS_BEAT_TOGGLE_PAD, *GROSS_BEAT_SLOT_PADS))
        surface._refresh_needed = False
        return True
    target = focused_plugin_target()
    if target is None:
        return True
    spec = gross_beat_spec(surface, target)
    if spec is None:
        return True
    if pad in GROSS_BEAT_SLOT_PADS:
        slot_index = GROSS_BEAT_SLOT_PADS.index(pad)
        gross_beat_trigger_slot(target, spec, gross_beat_slot_mode(surface), slot_index)
        surface._refresh_grid_pads(GROSS_BEAT_SLOT_PADS)
        surface._refresh_needed = False
        return True
    if surface._gross_beat_fader.contains(pad):
        current_value = plugin_param_value(target, spec["mix_param"])
        new_value = surface._gross_beat_fader.next_value_for_pad(pad, current_value)
        plugin_set_param_value(target, spec["mix_param"], new_value)
        surface._refresh_grid_pads(surface._gross_beat_fader.pads)
        surface._refresh_needed = False
        return True
    return True


def gross_beat_lighting(surface, pad: int) -> LedColor:
    slot_mode = gross_beat_slot_mode(surface)
    slot_color = GROSS_BEAT_TIME_COLOR if slot_mode == "time" else GROSS_BEAT_VOLUME_COLOR
    selected_slot_color = GROSS_BEAT_VOLUME_COLOR if slot_mode == "time" else GROSS_BEAT_TIME_COLOR
    if pad == GROSS_BEAT_TOGGLE_PAD:
        if pad in surface._plugin_override_held_pads:
            return LedColor(PAD_ACTION)
        return LedColor(GROSS_BEAT_VOLUME_COLOR)
    if surface._gross_beat_fader.contains(pad):
        target = focused_plugin_target()
        spec = gross_beat_spec(surface, target) if target is not None else None
        current_value = 0.0
        if spec is not None:
            current_value = plugin_param_value(target, spec["mix_param"])
        return LedColor(
            surface._gross_beat_fader.palette_for_pad(
                pad,
                current_value,
                dim_palette=GROSS_BEAT_FADER_DIM_COLOR,
                bright_palettes=GROSS_BEAT_FADER_MICRO_COLORS,
                off_palette=LP3_BACKGROUND_OFF,
            )
        )
    if pad in GROSS_BEAT_SLOT_PADS:
        target = focused_plugin_target()
        spec = gross_beat_spec(surface, target) if target is not None else None
        slot_index = GROSS_BEAT_SLOT_PADS.index(pad)
        active_slot = gross_beat_active_slot(target, spec, slot_mode)
        if active_slot == slot_index:
            return LedColor(selected_slot_color)
        if pad in surface._plugin_override_held_pads:
            return LedColor(PAD_ACTION)
        return LedColor(slot_color)
    if fm.is_fpc_selector(pad):
        return LedColor(fm.fpc_selector_color(
            pad,
            surface.state,
            lambda: fm.selected_channel_is_fpc(surface._selected_channel()),
            surface._selected_channel,
            hide_if_not_fpc=True,
        ))
    if pad in SETTINGS_GRID_PADS:
        return LedColor(LP3_BACKGROUND_OFF)
    return LedColor(PAD_DISABLED)


def gross_beat_slot_mode(surface) -> str:
    mode = str(surface.state.get("gross_beat_slot_mode", "time")).lower()
    return "volume" if mode == "volume" else "time"


def focused_plugin_target() -> tuple[int, int, int] | None:
    try:
        focused_id = int(ui.getFocusedFormID())
    except Exception:
        return None
    try:
        if int(ui.getFocused(WID_PLUGIN_EFFECT)) == 1:
            encoded = (focused_id >> 16) & 0xFFFFFFFF
            return encoded >> 6, encoded & 0x3F, focused_id
    except Exception:
        pass
    try:
        if int(ui.getFocused(WID_PLUGIN_GENERATOR)) == 1 and focused_id >= 0:
            return focused_id, -1, focused_id
    except Exception:
        pass
    try:
        if int(ui.getFocused(WID_PLUGIN)) == 1 and focused_id >= 0:
            return focused_id, -1, focused_id
    except Exception:
        pass
    return None


def gross_beat_spec(surface, target: tuple[int, int, int] | None) -> dict | None:
    if target is None:
        return None
    index, slot_index, target_id = target
    key = (PLUGIN_PAD_OVERRIDE_GROSS_BEAT, index, slot_index, target_id)
    cached = surface._plugin_param_specs.get(key)
    if cached is not None:
        return cached
    param_names: list[str] = []
    try:
        param_count = int(plugins.getParamCount(index, slot_index))
    except Exception:
        return None
    for param_index in range(max(0, param_count)):
        try:
            param_names.append(str(plugins.getParamName(param_index, index, slot_index) or ""))
        except Exception:
            param_names.append("")
    spec = discover_gross_beat_spec(param_names)
    if spec is None:
        return None
    surface._plugin_param_specs[key] = spec
    return spec


def discover_gross_beat_spec(param_names: list[str]) -> dict | None:
    time_slots: dict[int, int] = {}
    volume_slots: dict[int, int] = {}
    time_selector: int | None = None
    volume_selector: int | None = None
    mix_param: int | None = None
    for param_index, param_name in enumerate(param_names):
        normalized = normalize_param_name(param_name)
        slot_number = extract_slot_number(normalized)
        if mix_param is None and "volume mix" in normalized:
            mix_param = param_index
        if "time" in normalized and "slot" in normalized:
            if slot_number is None and time_selector is None:
                time_selector = param_index
            elif slot_number is not None and 1 <= slot_number <= 36:
                time_slots[slot_number - 1] = param_index
            continue
        if "volume" in normalized and "slot" in normalized:
            if slot_number is None and volume_selector is None:
                volume_selector = param_index
            elif slot_number is not None and 1 <= slot_number <= 36:
                volume_slots[slot_number - 1] = param_index
    if mix_param is None:
        for param_index, param_name in enumerate(param_names):
            normalized = normalize_param_name(param_name)
            if normalized in ("mix", "mix level", "vol mix"):
                mix_param = param_index
                break
    if mix_param is None:
        return None
    if len(time_slots) == 36 and len(volume_slots) == 36:
        return {
            "mode": "button_matrix",
            "time_slots": [time_slots[index] for index in range(36)],
            "volume_slots": [volume_slots[index] for index in range(36)],
            "mix_param": mix_param,
        }
    if time_selector is not None and volume_selector is not None:
        return {
            "mode": "selector_pair",
            "time_selector": time_selector,
            "volume_selector": volume_selector,
            "mix_param": mix_param,
        }
    return None


def normalize_param_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").strip().lower()).strip()


def extract_slot_number(normalized_name: str) -> int | None:
    match = re.search(r"\bslot\s+(\d{1,2})\b", normalized_name)
    if match is not None:
        return int(match.group(1))
    match = re.search(r"\b(\d{1,2})\b", normalized_name)
    if match is not None:
        return int(match.group(1))
    return None


def gross_beat_trigger_slot(
    target: tuple[int, int, int],
    spec: dict,
    slot_mode: str,
    slot_index: int,
) -> None:
    if spec["mode"] == "button_matrix":
        if slot_mode == "time":
            param_index = spec["time_slots"][slot_index]
        else:
            param_index = spec["volume_slots"][slot_index]
        plugin_set_param_value(target, param_index, 1.0)
        return
    selector_param = spec["time_selector"] if slot_mode == "time" else spec["volume_selector"]
    plugin_set_param_value(target, selector_param, slot_index / 35.0)


def gross_beat_active_slot(
    target: tuple[int, int, int] | None,
    spec: dict | None,
    slot_mode: str,
) -> int | None:
    if target is None or spec is None:
        return None
    if spec["mode"] == "button_matrix":
        slot_params = spec["time_slots"] if slot_mode == "time" else spec["volume_slots"]
        strongest_index = 0
        strongest_value = -1.0
        for slot_index, param_index in enumerate(slot_params):
            value = plugin_param_value(target, param_index)
            if value > strongest_value:
                strongest_index = slot_index
                strongest_value = value
        return strongest_index
    selector_param = spec["time_selector"] if slot_mode == "time" else spec["volume_selector"]
    value = plugin_param_value(target, selector_param)
    return _clamp(int(round(value * 35.0)), 0, 35)


def plugin_param_value(target: tuple[int, int, int], param_index: int) -> float:
    index, slot_index, _target_id = target
    try:
        return float(plugins.getParamValue(param_index, index, slot_index))
    except Exception:
        return 0.0


def plugin_set_param_value(
    target: tuple[int, int, int],
    param_index: int,
    value: float,
) -> None:
    index, slot_index, _target_id = target
    try:
        plugins.setParamValue(float(value), param_index, index, slot_index, midi.PIM_None)
    except Exception:
        return


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
# ~gargoyles rule~