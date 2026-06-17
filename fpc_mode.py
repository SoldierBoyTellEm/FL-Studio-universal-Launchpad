# fpc_mode.py
# FPC drum-pad mode: quadrant/bank assignment, selector-pad handling,
# pad→note/colour mapping, and lighting.
#
# All functions that need the FL Studio `channels` / `plugins` API receive
# them as arguments so this module remains unit-testable with stubs.
from fl_stubs import channels, plugins
from constants import (
    FPC_QUADRANT_PADS,
    FPC_BANK_A_SELECTORS,
    FPC_BANK_B_SELECTORS,
    FPC_SELECTORS,
    FPC_PAGE_COUNT,
    FPC_BANKS_PER_ROW,
    SETTINGS_GRID_PADS,
    PERFORMANCE_PAGE_HEIGHT,
    PAD_ROOT,
    PAD_AUX,
    PAD_SELECTED,
    PAD_AVAILABLE,
    PAD_DISABLED,
    PAD_OFF,
    LedColor,
)
from led_display import rgb6_from_color, rgb_max_value

def is_fpc_selector(pad: int) -> bool:
    return pad in FPC_SELECTORS

def fpc_selector_mapping(pad: int) -> tuple[int, int] | None:
    """Return (quadrant_index, bank_offset) for *pad*, or None."""
    if pad in FPC_BANK_A_SELECTORS:
        return FPC_BANK_A_SELECTORS.index(pad), 0
    if pad in FPC_BANK_B_SELECTORS:
        return FPC_BANK_B_SELECTORS.index(pad), 16
    return None

def handle_fpc_selector(
    pad: int,
    state: dict,
    selected_channel: int,
    page_index: int | None = None,
) -> None:
    """Assign the currently selected channel to the quadrant for *pad*."""
    mapping = fpc_selector_mapping(pad)
    if mapping is None:
        return
    quadrant, bank_offset = mapping
    target_page = current_fpc_page(state) if page_index is None else int(page_index)
    slot_index = bank_index_for_page_quadrant(target_page, quadrant)
    slot_channels = list(state.get("fpc_slot_channels", [-1] * 16))
    slot_banks = list(state.get("fpc_slot_banks", [-1] * 16))
    slot_channels[slot_index] = selected_channel
    slot_banks[slot_index] = bank_offset
    state["fpc_slot_channels"] = slot_channels
    state["fpc_slot_banks"] = slot_banks

def clear_fpc_selector(
    pad: int,
    state: dict,
    page_index: int | None = None,
) -> None:
    """Unassign the quadrant mapped to *pad* (hold-to-clear gesture)."""
    mapping = fpc_selector_mapping(pad)
    if mapping is None:
        return
    quadrant, bank_offset = mapping
    target_page = current_fpc_page(state) if page_index is None else int(page_index)
    slot_index = bank_index_for_page_quadrant(target_page, quadrant)
    slot_channels = list(state.get("fpc_slot_channels", [-1] * 16))
    slot_banks = list(state.get("fpc_slot_banks", [-1] * 16))
    if slot_banks[slot_index] == bank_offset:
        slot_channels[slot_index] = -1
        slot_banks[slot_index] = -1
    state["fpc_slot_channels"] = slot_channels
    state["fpc_slot_banks"] = slot_banks

def bank_index_for_page_quadrant(page_index: int, quadrant: int) -> int:
    page = max(0, min(FPC_PAGE_COUNT - 1, int(page_index)))
    if not 0 <= quadrant < 4:
        return 0
    page_row = page // 2
    page_col = page % 2
    local_row = 0 if quadrant < 2 else 1
    local_col = quadrant % 2
    slot_row = page_row * 2 + local_row
    slot_col = page_col * 2 + local_col
    return slot_row * FPC_BANKS_PER_ROW + slot_col

def slot_assignment(state: dict, page_index: int, quadrant: int) -> tuple[int, int] | None:
    slot_index = bank_index_for_page_quadrant(page_index, quadrant)
    return slot_assignment_for_index(state, slot_index)

def slot_assignment_for_index(state: dict, slot_index: int) -> tuple[int, int] | None:
    slot_channels = state.get("fpc_slot_channels", [-1] * 16)
    slot_banks = state.get("fpc_slot_banks", [-1] * 16)
    if not 0 <= slot_index < len(slot_channels) or not 0 <= slot_index < len(slot_banks):
        return None
    channel_index = int(slot_channels[slot_index])
    bank_offset = int(slot_banks[slot_index])
    if channel_index < 0 or bank_offset not in (0, 16):
        return None
    if not selected_channel_is_fpc(channel_index):
        return None
    return channel_index, bank_offset

def current_fpc_page(state: dict) -> int:
    return max(0, min(FPC_PAGE_COUNT - 1, int(state.get("fpc_page", 0))))

def step_fpc_page(direction: int, state: dict) -> None:
    state["fpc_page"] = max(0, min(FPC_PAGE_COUNT - 1, current_fpc_page(state) + int(direction)))

def remaining_fpc_page_steps(direction: int, state: dict) -> int:
    page = current_fpc_page(state)
    if direction < 0:
        return page
    if direction > 0:
        return (FPC_PAGE_COUNT - 1) - page
    return 0

# Quadrant / pad assignment
def fpc_assignment_for_quadrant(
    quadrant: int,
    state: dict,
    selected_channel_fn,
    page_index: int | None = None,
    allow_fallback_to_selected: bool = False,
) -> tuple[int, int] | None:
    """Return (channel_index, bank_offset) for *quadrant*, or None if unassigned."""
    if not 0 <= quadrant < 4:
        return None
    target_page = current_fpc_page(state) if page_index is None else int(page_index)
    assigned = slot_assignment(state, target_page, quadrant)
    if assigned is None and allow_fallback_to_selected:
        channel_index = selected_channel_fn()
        bank_offset = 0
        return channel_index, bank_offset
    return assigned

def fpc_assignment_for_grid_position(
    x: int,
    y: int,
    state: dict,
    selected_channel_fn,
    allow_fallback_to_selected: bool = False,
) -> tuple[int, int] | None:
    """Return the FPC pad assignment at absolute 16x16 grid position *(x, y)*."""
    if not 0 <= x < 16 or not 0 <= y < 16:
        return None
    bank_col = int(x) // 4
    bank_row = int(y) // 4
    slot_index = bank_row * FPC_BANKS_PER_ROW + bank_col
    assigned = slot_assignment_for_index(state, slot_index)
    if assigned is None and allow_fallback_to_selected:
        assigned = (selected_channel_fn(), 0)
    if assigned is None:
        return None
    channel_index, bank_offset = assigned
    local_x = int(x) % 4
    local_y_from_top = int(y) % 4
    pad_index = bank_offset + ((3 - local_y_from_top) * 4) + local_x
    return channel_index, pad_index

def fpc_assignment_for_performance_pad(
    pad: int,
    state: dict,
    selected_channel_fn,
    allow_fallback_to_selected: bool = False,
) -> tuple[int, int] | None:
    """Map a performance pad through the smoothly panned 16x16 FPC grid."""
    if pad not in SETTINGS_GRID_PADS:
        return None
    col = (pad % 10) - 1
    row_from_top = PERFORMANCE_PAGE_HEIGHT - (pad // 10)
    x = int(state.get("performance_block_offset", 0)) + col
    y = max(0, int(state.get("performance_track_offset", 1)) - 1) + row_from_top
    return fpc_assignment_for_grid_position(
        x,
        y,
        state,
        selected_channel_fn,
        allow_fallback_to_selected=allow_fallback_to_selected,
    )

def fpc_assignment_for_pad(
    pad: int,
    state: dict,
    selected_channel_fn,
    page_index: int | None = None,
    allow_fallback_to_selected: bool = False,
) -> tuple[int, int] | None:
    """Return (channel_index, pad_index) for *pad*, or None."""
    for quadrant, quadrant_pads in enumerate(FPC_QUADRANT_PADS):
        if pad in quadrant_pads:
            assignment = fpc_assignment_for_quadrant(
                quadrant,
                state,
                selected_channel_fn,
                page_index=page_index,
                allow_fallback_to_selected=allow_fallback_to_selected,
            )
            if assignment is None:
                return None
            channel_index, bank_offset = assignment
            return channel_index, bank_offset + quadrant_pads.index(pad)
    return None

# Pad note / colour via plugins API
def fpc_pad_count(channel_index: int) -> int:
    if channel_index < 0:
        return 16
    try:
        return max(0, int(plugins.getPadInfo(channel_index, -1, 0, 0, True)))
    except TypeError:
        try:
            return max(0, int(plugins.getPadInfo(channel_index, -1, 0, 0)))
        except Exception:
            return 16
    except Exception:
        return 16

def fpc_pad_note(channel_index: int, pad_index: int) -> int:
    if channel_index < 0:
        return -1
    if not 0 <= pad_index < fpc_pad_count(channel_index):
        return -1
    try:
        return int(plugins.getPadInfo(channel_index, -1, 1, pad_index, True))
    except TypeError:
        try:
            return int(plugins.getPadInfo(channel_index, -1, 1, pad_index))
        except Exception:
            return -1
    except Exception:
        return -1

def fpc_pad_color(channel_index: int, pad_index: int) -> tuple[int, int, int]:
    if channel_index < 0:
        return (0, 0, 0)
    if not 0 <= pad_index < fpc_pad_count(channel_index):
        return (0, 0, 0)
    try:
        color = int(plugins.getPadInfo(channel_index, -1, 2, pad_index, True))
    except TypeError:
        try:
            color = int(plugins.getPadInfo(channel_index, -1, 2, pad_index))
        except Exception:
            color = 0
    except Exception:
        color = 0
    return rgb6_from_color(color)

def fpc_selector_color(
    pad: int,
    state: dict,
    selected_channel_is_fpc_fn,
    selected_channel_fn=None,
    *,
    hide_if_not_fpc: bool = False,
) -> int:
    if hide_if_not_fpc and not selected_channel_is_fpc_fn():
        return PAD_DISABLED
    selected_channel = selected_channel_fn() if selected_channel_fn is not None else None
    page_index = current_fpc_page(state)
    if pad in FPC_BANK_A_SELECTORS:
        quadrant = FPC_BANK_A_SELECTORS.index(pad)
        assigned = slot_assignment(state, page_index, quadrant)
        if (
            assigned is not None
            and assigned[1] == 0
            and (selected_channel is None or assigned[0] == selected_channel)
        ):
            return PAD_ROOT
        return PAD_AUX
    if pad in FPC_BANK_B_SELECTORS:
        quadrant = FPC_BANK_B_SELECTORS.index(pad)
        assigned = slot_assignment(state, page_index, quadrant)
        if (
            assigned is not None
            and assigned[1] == 16
            and (selected_channel is None or assigned[0] == selected_channel)
        ):
            return PAD_SELECTED
        return PAD_AVAILABLE
    return PAD_DISABLED

def has_any_fpc_slot_assignment(state: dict) -> bool:
    slot_channels = state.get("fpc_slot_channels", [-1] * 16)
    slot_banks = state.get("fpc_slot_banks", [-1] * 16)
    for i, ch in enumerate(slot_channels):
        bank = int(slot_banks[i]) if i < len(slot_banks) else -1
        if int(ch) >= 0 and bank in (0, 16):
            return True
    return False

def auto_assign_new_fpc(state: dict, channel_index: int) -> None:
    """Assign channel_index to the lower two quadrants (2 and 3) on page 0,
    bank A (offset 0) for quadrant 2 and bank B (offset 16) for quadrant 3."""
    slot_channels = list(state.get("fpc_slot_channels", [-1] * 16))
    slot_banks = list(state.get("fpc_slot_banks", [-1] * 16))
    for quadrant, bank_offset in ((2, 0), (3, 16)):
        slot_index = bank_index_for_page_quadrant(0, quadrant)
        slot_channels[slot_index] = channel_index
        slot_banks[slot_index] = bank_offset
    state["fpc_slot_channels"] = slot_channels
    state["fpc_slot_banks"] = slot_banks

def fpc_lighting(
    pad: int,
    state: dict,
    selected_channel_fn,
    selected_channel_is_fpc_fn,
    is_note_active_fn,
    is_pad_recently_active_fn,
    page_index: int | None = None,
    allow_fallback_to_selected: bool = False,
) -> LedColor:
    """Return the LedColor for *pad* in FPC mode."""
    if is_fpc_selector(pad):
        return LedColor(fpc_selector_color(
            pad, state, selected_channel_is_fpc_fn, selected_channel_fn, hide_if_not_fpc=True
        ))
    if pad not in SETTINGS_GRID_PADS:
        return LedColor(PAD_DISABLED)
    assignment = fpc_assignment_for_pad(
        pad,
        state,
        selected_channel_fn,
        page_index=page_index,
        allow_fallback_to_selected=allow_fallback_to_selected,
    )
    if assignment is None:
        return LedColor(PAD_DISABLED)
    return fpc_lighting_for_assignment(
        pad,
        assignment,
        is_note_active_fn,
        is_pad_recently_active_fn,
    )

def fpc_performance_lighting(
    pad: int,
    state: dict,
    selected_channel_fn,
    is_note_active_fn,
    is_pad_recently_active_fn,
    allow_fallback_to_selected: bool = False,
) -> LedColor:
    assignment = fpc_assignment_for_performance_pad(
        pad,
        state,
        selected_channel_fn,
        allow_fallback_to_selected=allow_fallback_to_selected,
    )
    if assignment is None:
        return LedColor(PAD_DISABLED)
    return fpc_lighting_for_assignment(
        pad,
        assignment,
        is_note_active_fn,
        is_pad_recently_active_fn,
    )

def fpc_lighting_for_assignment(
    pad: int,
    assignment: tuple[int, int],
    is_note_active_fn,
    is_pad_recently_active_fn,
) -> LedColor:
    channel_index, pad_index = assignment
    if not 0 <= pad_index < fpc_pad_count(channel_index):
        return LedColor(PAD_OFF, (0, 0, 0))
    note = fpc_pad_note(channel_index, pad_index)
    rgb  = fpc_pad_color(channel_index, pad_index)
    if (
        note >= 0
        and is_note_active_fn(channel_index, note)
        and is_pad_recently_active_fn(pad, channel_index, note)
    ):
        maximum = rgb_max_value()
        rgb = tuple(min(maximum, c + 18) for c in rgb)
    return LedColor(PAD_OFF, rgb)
# Selected-channel helpers (used by the main class)

def selected_plugin_name(channel_index: int) -> str:
    if channel_index < 0:
        return ""
    try:
        return str(plugins.getPluginName(channel_index, -1, 0, True) or "")
    except TypeError:
        try:
            return str(plugins.getPluginName(channel_index, -1, 0) or "")
        except Exception:
            return ""
    except Exception:
        return ""

def selected_channel_is_fpc(channel_index: int) -> bool:
    return selected_plugin_name(channel_index).strip().lower() == "fpc"

# All channel-rack queries below use the GLOBAL index space (useGlobalIndex=1),
# which is immune to the All/Audio/Unsorted rack filter. "Make unique from the
# sample" inserts channels into the global rack, shifting the global indices of
# channels below the insertion point while preserving their names and relative
# order — so a stored FPC slot index can come to point at the wrong channel.
# The helpers here re-locate a slot by its last-known channel name.

def global_channel_count() -> int:
    try:
        return max(0, int(channels.channelCount(1)))
    except TypeError:
        try:
            return max(0, int(channels.channelCount()))
        except Exception:
            return 0
    except Exception:
        return 0

def global_channel_name(channel_index: int) -> str:
    if channel_index < 0:
        return ""
    try:
        return str(channels.getChannelName(channel_index, 1) or "")
    except TypeError:
        try:
            return str(channels.getChannelName(channel_index) or "")
        except Exception:
            return ""
    except Exception:
        return ""

def rack_signature() -> tuple:
    """Cheap snapshot of the global channel rack (names in order). Used to
    detect when the rack actually changed so recovery only runs on real
    edits, not on every LED refresh."""
    return tuple(global_channel_name(i) for i in range(global_channel_count()))

def remember_slot_assignment_names(state: dict, last_known_names: dict[int, str]) -> None:
    """Record the current channel name for every valid FPC slot assignment so
    recover_shifted_slot_assignments() can search by it after a rack edit."""
    slot_channels = state.get("fpc_slot_channels", [-1] * 16)
    slot_banks = state.get("fpc_slot_banks", [-1] * 16)
    for index, channel_index in enumerate(slot_channels):
        bank_offset = int(slot_banks[index]) if index < len(slot_banks) else -1
        channel_index = int(channel_index)
        if channel_index >= 0 and bank_offset in (0, 16) and selected_channel_is_fpc(channel_index):
            last_known_names[index] = global_channel_name(channel_index)

def recover_shifted_slot_assignments(state: dict, last_known_names: dict[int, str]) -> bool:
    """Re-locate FPC slot assignments whose stored global index drifted (because
    a rack edit inserted/removed channels), matching by last-known channel name.

    Matching is done per name-group so duplicate-named FPC channels keep their
    relative order: all slots tracking a given name (taken in ascending slot
    order) are paired positionally with the current FPC channels of that name
    (taken in ascending global index). E.g. if two slots track "FPC" and the two
    "FPC" channels shifted from global indices 4,5 to 5,6, the first slot gets 5
    and the second gets 6.

    This deliberately does NOT keep a slot's stored index just because it still
    happens to point at *some* FPC channel — after a one-index shift that stale
    index can land on a different same-named FPC channel that really belongs to
    another slot, so the group pairing is the source of truth.

    A slot is preserved untouched (not cleared) when we have no last-known name
    for it, or when its name-group has fewer current channels than slots — the
    named channel may be transiently hidden/absent and could return, at which
    point a later recovery pass repairs it. Recovery never clears assignments.

    Returns True if any slot assignment changed (so the caller can persist).
    """
    slot_channels = list(state.get("fpc_slot_channels", [-1] * 16))
    slot_banks = list(state.get("fpc_slot_banks", [-1] * 16))
    count = global_channel_count()

    # Current FPC channels by name → ascending list of global indices.
    available: dict[str, list[int]] = {}
    for ci in range(count):
        if selected_channel_is_fpc(ci):
            available.setdefault(global_channel_name(ci), []).append(ci)

    # Slots needing placement, grouped by last-known name (ascending slot order).
    slots_by_name: dict[str, list[int]] = {}
    for slot_index, channel_index in enumerate(slot_channels):
        bank_offset = int(slot_banks[slot_index]) if slot_index < len(slot_banks) else -1
        if int(channel_index) < 0 or bank_offset not in (0, 16):
            continue
        name = last_known_names.get(slot_index)
        if not name:
            # No name on record — leave the stored index alone rather than risk
            # disturbing a mapping over a transient/unknown API state.
            continue
        slots_by_name.setdefault(name, []).append(slot_index)

    changed = False
    for name, slots in slots_by_name.items():
        channels_for_name = available.get(name, [])
        # Pair positionally; if fewer channels than slots, the surplus slots are
        # left as-is (their channel may reappear and be fixed on a later pass).
        for slot_index, new_index in zip(slots, channels_for_name):
            if slot_channels[slot_index] != new_index:
                slot_channels[slot_index] = new_index
                changed = True
            last_known_names[slot_index] = name

    if changed:
        state["fpc_slot_channels"] = slot_channels
        state["fpc_slot_banks"] = slot_banks
    return changed

# Note mapping helper used from the surface
def fpc_note_for_pad(pad: int, state: dict, selected_channel_fn, page_index: int | None = None) -> int:
    assignment = fpc_assignment_for_pad(
        pad,
        state,
        selected_channel_fn,
        page_index=page_index,
    )
    if assignment is None:
        return -1
    channel_index, pad_index = assignment
    return fpc_pad_note(channel_index, pad_index)
# ~gargoyles rule~