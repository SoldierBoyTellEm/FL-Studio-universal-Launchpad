# gestures.py
# Tap / double-tap / long-hold gesture tracking for the top mode buttons.

from collections import namedtuple

from constants import TAP_AND_HOLD_DURATION_SECONDS

ReleasedGesture = namedtuple("ReleasedGesture", "was_pressed hold_fired context")


class ButtonGesture:
    """One button's press lifecycle.

    Usage pattern in a press/release handler:

        if pressed:
            if gesture.tap_tap(now):    # only when double_tap_seconds > 0
                <double-tap action>
                return
            gesture.press(now, context=...)
            return
        released = gesture.release()
        if not released.was_pressed or released.hold_fired:
            return
        <tap action>

    Long-holds are polled from on_idle: poll_hold(now) returns True exactly
    once per press after the button has been held past hold_seconds. Callers
    may gate it behind extra conditions (`if cond and gesture.poll_hold(now)`)
    — the hold stays armed until it fires or the button is released, matching
    the previous per-button behaviour.

    `context` carries press-time information (e.g. "entered from another
    surface") to the matching release.
    """

    def __init__(
        self,
        hold_seconds: float = TAP_AND_HOLD_DURATION_SECONDS,
        double_tap_seconds: float = 0.0,
    ) -> None:
        self.hold_seconds = float(hold_seconds)
        self.double_tap_seconds = float(double_tap_seconds)
        self.pressed = False
        self.hold_started = 0.0
        self.hold_fired = False
        self.last_tap = 0.0
        self.context = None

    def press(self, now: float, context=None) -> None:
        self.pressed = True
        self.hold_started = now
        self.hold_fired = False
        self.last_tap = now
        self.context = context

    def tap_tap(self, now: float) -> bool:
        """Check at press time whether this press completes a double tap.
        Firing consumes the gesture: the upcoming release becomes a no-op and
        no hold can fire from it."""
        if self.double_tap_seconds <= 0.0:
            return False
        if self.last_tap <= 0.0 or now - self.last_tap > self.double_tap_seconds:
            return False
        self.reset()
        return True

    def poll_hold(self, now: float) -> bool:
        if not self.pressed or self.hold_fired:
            return False
        if now - self.hold_started < self.hold_seconds:
            return False
        self.hold_fired = True
        self.last_tap = 0.0  # a hold never arms a double tap
        return True

    def release(self) -> ReleasedGesture:
        released = ReleasedGesture(self.pressed, self.hold_fired, self.context)
        self.pressed = False
        self.hold_started = 0.0
        self.hold_fired = False
        self.context = None
        return released

    def reset(self) -> None:
        self.pressed = False
        self.hold_started = 0.0
        self.hold_fired = False
        self.last_tap = 0.0
        self.context = None
# ~gargoyles rule~