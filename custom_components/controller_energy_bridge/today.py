"""Today's energy per meter (protocol §14): statistics baseline plus live meter readings.

Pure logic, no Home Assistant imports, so it is testable standalone.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TodayMeter:
    """Energy since local midnight for one cumulative meter, in kWh.

    The recorder knows how much the meter advanced since midnight (`change`) up to its last
    compiled five-minute period; the live state only knows the running total. `rebase` pins
    the two together through the meter reading at that period's end, after which each live
    reading yields today's value without a database query. A meter without a live state
    (external statistic) keeps the last statistics value.
    """

    baseline: float | None = None
    last_live: float | None = None
    fixed: float | None = None

    def rebase(self, change_today: float, reference: float | None, live: float | None) -> float:
        """Adopt the statistics; returns today's energy.

        `change_today` is the meter's advance from midnight up to the end of the last
        compiled statistics period, `reference` the meter reading at that end and `live`
        the current state (None while the sensor is unavailable). Without a reference
        (external statistic) the statistics value stands alone.
        """
        change_today = max(0.0, change_today)
        if reference is None:
            self.baseline = None
            self.last_live = live
            self.fixed = change_today
            return change_today
        if live is not None and live < reference and self.baseline is not None:
            # Reset after the compiled period: the running value already counted the energy
            # before it, which the recorder has not seen yet. Keep it until it has.
            return self.value if self.value is not None else change_today
        self.baseline = reference - change_today
        self.fixed = None
        # The reference is the last known reading, so `update` sees a reset since then.
        self.last_live = reference
        if live is None:
            return change_today
        today = self.update(live)
        return change_today if today is None else today

    def update(self, live: float) -> float | None:
        """Today's energy after a live reading; None until the first rebase."""
        if self.baseline is None:
            self.last_live = live
            return self.fixed
        if self.last_live is not None and live < self.last_live:
            # Meter reset (§9): the new count starts from zero on top of what today already had.
            self.baseline -= self.last_live
        self.last_live = live
        return max(0.0, live - self.baseline)

    @property
    def value(self) -> float | None:
        if self.baseline is None:
            return self.fixed
        return None if self.last_live is None else max(0.0, self.last_live - self.baseline)
