# channel_lock.py
# Per-context channel-lock manager.
#
# Each "context" keeps its own locked FL channel index:
#   - note mode      ("note")
#   - the step/channel-rack view ("step")
#   - each of the 16 custom-mode indices ("custom:0" … "custom:15")
#
# Other surface pages route their notes through resolve(): a page can be pinned
# to whatever plugin was selected in FL when the lock was set, so notes keep
# going to that plugin regardless of FL's current selection.  This lets a setup
# assign different pages to different plugins without further mouse clicks.

from fl_stubs import channels

NOTE_CONTEXT = "note"
STEP_CONTEXT = "step"
CUSTOM_COUNT = 16

_STATE_KEY = "channel_locks"

def custom_context(index: int) -> str:
    return f"custom:{int(index)}"

def _locks(state: dict) -> dict:
    locks = state.get(_STATE_KEY)
    if not isinstance(locks, dict):
        locks = {}
        state[_STATE_KEY] = locks
    return locks

def get(state: dict, context: str) -> int:
    """Return the locked channel index for a context, or -1 if unset."""
    try:
        return int(_locks(state).get(context, -1))
    except Exception:
        return -1

def set_lock(state: dict, context: str, channel: int) -> None:
    _locks(state)[context] = int(channel)

def clear(state: dict, context: str) -> None:
    _locks(state)[context] = -1

def is_locked(state: dict, context: str) -> bool:
    """True if the context is locked to a still-valid channel.  Stale locks
    (channel removed from the project) are cleared and report unlocked."""
    locked = get(state, context)
    if locked < 0:
        return False
    try:
        if locked >= channels.channelCount():
            clear(state, context)
            return False
    except Exception:
        pass
    return True

def toggle(state: dict, context: str, host_channel: int) -> bool:
    """Toggle the lock for a context.  Returns True if now locked."""
    if is_locked(state, context):
        clear(state, context)
        return False
    set_lock(state, context, host_channel)
    return True

def resolve(state: dict, context: str, host_channel: int) -> int:
    """Return the channel a context's notes should target."""
    if is_locked(state, context):
        return get(state, context)
    return host_channel

# Multi-channel routing
#
# A context may additionally fan its notes out to a *set* of channels, so one
# page can drive several plugins at once.  This is layered on top of the
# single-channel lock above rather than replacing it:
#
#   routes set    -> notes go to every routed channel (the lock is bypassed)
#   routes empty  -> falls back to resolve(), i.e. the lock or FL's selection
#
# Routes are per-context, so note mode and each of the 16 custom modes keep
# independent sets, matching how their locks already behave.

_ROUTES_KEY = "channel_routes"
ROUTING_PAGE_CHANNELS = 64
ROUTING_PAGE_ROW = 8


def _routes(state: dict) -> dict:
    routes = state.get(_ROUTES_KEY)
    if not isinstance(routes, dict):
        routes = {}
        state[_ROUTES_KEY] = routes
    return routes


def get_routes(state: dict, context: str) -> list[int]:
    """Return the routed channel indices for a context, lowest first.

    Stale entries (channels removed from the project) are filtered out on read
    rather than mutating state here, so a temporarily short channel count
    can't permanently destroy a routing set.
    """
    raw = _routes(state).get(context)
    if not isinstance(raw, (list, tuple)):
        return []
    try:
        count = channels.channelCount()
    except Exception:
        count = None
    out = []
    for value in raw:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index < 0 or index in out:
            continue
        if count is not None and index >= count:
            continue
        out.append(index)
    out.sort()
    return out


def has_routes(state: dict, context: str) -> bool:
    return bool(get_routes(state, context))


def is_routed(state: dict, context: str, channel: int) -> bool:
    return int(channel) in get_routes(state, context)


def toggle_route(state: dict, context: str, channel: int) -> bool:
    """Add/remove a channel from a context's routing set.  Returns True if the
    channel is routed afterwards."""
    channel = int(channel)
    current = get_routes(state, context)
    if channel in current:
        current.remove(channel)
        routed = False
    else:
        current.append(channel)
        current.sort()
        routed = True
    _routes(state)[context] = current
    return routed


def clear_routes(state: dict, context: str) -> None:
    _routes(state)[context] = []


def route_targets(state: dict, context: str, host_channel: int) -> list[int]:
    """Every channel a context's notes should reach.

    Falls back to the single-channel resolve() (lock, else FL's selection) when
    no explicit routes are set, so untouched pages behave exactly as before.
    """
    routed = get_routes(state, context)
    if routed:
        return routed
    target = resolve(state, context, host_channel)
    return [target] if target >= 0 else []


def routing_page_channel_for_pad(pad: int, offset: int = 0) -> int | None:
    """Map an 8x8 grid pad to a channel index, top-left first then row by row.

    Pad ids are row*10+col with row 8 at the top, so at offset 0 channel 0 is
    pad 81, channel 7 is pad 88, channel 8 is pad 71, and channel 63 is pad 18.
    *offset* scrolls the window, in channels.
    """
    row = pad // 10
    col = pad % 10
    if not 1 <= row <= 8 or not 1 <= col <= 8:
        return None
    return int(offset) + (8 - row) * ROUTING_PAGE_ROW + (col - 1)


def routing_page_pad_for_channel(channel: int, offset: int = 0) -> int | None:
    local = int(channel) - int(offset)
    if not 0 <= local < ROUTING_PAGE_CHANNELS:
        return None
    return (8 - local // ROUTING_PAGE_ROW) * 10 + (local % ROUTING_PAGE_ROW) + 1


def routing_page_max_offset(channel_count: int) -> int:
    """Highest scroll offset that still reveals new channels.

    Rounded up to a whole row so scrolling always lands on a row boundary, and
    0 whenever the rack fits on one page (nothing to scroll to).
    """
    remainder = int(channel_count) - ROUTING_PAGE_CHANNELS
    if remainder <= 0:
        return 0
    return ((remainder + ROUTING_PAGE_ROW - 1) // ROUTING_PAGE_ROW) * ROUTING_PAGE_ROW


def migrate_legacy(state: dict) -> None:
    """Fold a pre-existing single `locked_channel` value into the note context."""
    legacy = int(state.get("locked_channel", -1))
    if legacy >= 0:
        locks = _locks(state)
        locks.setdefault(NOTE_CONTEXT, legacy)
    state["locked_channel"] = -1
# ~gargoyles rule~