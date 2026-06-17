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

def migrate_legacy(state: dict) -> None:
    """Fold a pre-existing single `locked_channel` value into the note context."""
    legacy = int(state.get("locked_channel", -1))
    if legacy >= 0:
        locks = _locks(state)
        locks.setdefault(NOTE_CONTEXT, legacy)
    state["locked_channel"] = -1
# ~gargoyles rule~