"""Decode PSN `compatibilityNotices` into filterable feature flags.

`metGetProductById` returns a flat list of ``{type, value}`` pairs. This
module turns it into typed fields. Pure: no I/O, no network, no clock.

Two things here are not obvious and were established by measurement:

* **`NO_OF_PLAYERS` counts players on one console**, so ``>= 2`` is the
  couch co-op signal. Sony publishes it for every product, which is why the
  local-multiplayer filter needs no third-party source and no title matching.
* **`OFFLINE_PLAY_MODE` does not mean local multiplayer.** It marks a game as
  playable offline and appears on single-player-only titles (The Last of Us
  Part I, Oblivion Remastered), while being *absent* from Gang Beasts, which
  is 4-player couch. Never combine the two fields.

Install size, frame-rate caps, VRR, ray tracing and HDR have no
representation anywhere in the store API or on the public product page, so
they are deliberately not modelled here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DIGITS = re.compile(r"\d+")

# Sony ships PS VR2 under internal codenames. There is no literal "PSVR2" key.
_VR2_HEADSET = "CAESAR_HEADSET"      # PS VR2 headset
_VR2_CONTROLLER = "ASTON_CONTROLLER"  # PS VR2 Sense controllers


@dataclass(frozen=True)
class Features:
    """Everything the store tells us about how a game plays."""

    local_players: int | None = None      # players on one console; None = unknown
    dualsense_haptics: bool = False
    adaptive_triggers: bool = False
    ps5_pro_enhanced: bool = False
    psvr2: str | None = None              # None | "optional" | "required"
    online_play: str | None = None        # None | "optional" | "required"
    ps_plus_required: bool = False

    @property
    def is_local_multiplayer(self) -> bool:
        """Two or more players on one console.

        ``None`` means the store did not say, which is not the same as one
        player, so it must not count as couch co-op.
        """
        return self.local_players is not None and self.local_players >= 2


def _mode(raw: str | None) -> str | None:
    """Map Sony's OPTIONAL/REQUIRED vocabulary to lowercase, or None."""
    if not raw:
        return None
    lowered = str(raw).strip().lower()
    return lowered if lowered in ("optional", "required") else None


# Real counts observed live are 1-11. Sony also publishes 99 on a couple of
# rows, which is a missing value wearing a number, not a 99-player couch.
_MAX_PLAYERS = 16


def _player_count(raw: str | None) -> int | None:
    if raw is None:
        return None
    found = _DIGITS.findall(str(raw))
    if not found:
        return None
    # Values observed so far are bare integers ("1", "2", "4"). Taking the
    # maximum keeps a hypothetical "1-4" correct rather than reading it as 1.
    count = max(int(n) for n in found)
    return count if count <= _MAX_PLAYERS else None


def decode_features(notices: list[dict] | None) -> Features:
    """Turn a raw `compatibilityNotices` list into typed flags.

    Unknown notice types are ignored, so a new Sony flag cannot break a crawl.
    """
    by_type: dict[str, str] = {}
    for notice in notices or []:
        kind = notice.get("type")
        if kind:
            by_type[kind] = notice.get("value")

    headset = _mode(by_type.get(_VR2_HEADSET))
    if headset is None and _VR2_CONTROLLER in by_type:
        # Controllers listed without a headset entry still imply VR2.
        headset = _mode(by_type.get(_VR2_CONTROLLER))

    return Features(
        local_players=_player_count(by_type.get("NO_OF_PLAYERS")),
        dualsense_haptics="PS5_VIBRATION" in by_type,
        adaptive_triggers="PS5_TRIGGER_EFFECT" in by_type,
        ps5_pro_enhanced="GRAPHICS_ENHANCED" in by_type,
        psvr2=headset,
        online_play=_mode(by_type.get("ONLINE_PLAY_MODE")),
        ps_plus_required="PS_PLUS_REQUIRED" in by_type,
    )
