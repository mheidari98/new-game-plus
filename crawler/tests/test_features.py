"""Decoding PSN `compatibilityNotices` into filterable feature flags.

Every fixture below is a real payload observed on the live US store on
2026-08-10 via metGetProductById.
"""

from ngp.features import decode_features

# --- real observed payloads -------------------------------------------------

GANG_BEASTS = [
    {"type": "NO_OF_PLAYERS", "value": "4"},
    {"type": "ONLINE_PLAY_MODE", "value": "OPTIONAL"},
    {"type": "PS5_VIBRATION", "value": "OPTIONAL"},
]

SPIDER_MAN_2 = [
    {"type": "NO_OF_PLAYERS", "value": "1"},
    {"type": "PS5_VIBRATION", "value": "OPTIONAL"},
    {"type": "PS5_TRIGGER_EFFECT", "value": "OPTIONAL"},
    {"type": "GRAPHICS_ENHANCED", "value": "true"},
    {"type": "GAME_HELP_SUPPORTED", "value": "true"},
    {"type": "STREAMING_SUPPORTED", "value": "true"},
]

# Single-player game that is nonetheless playable offline. This pairing is the
# reason OFFLINE_PLAY_MODE must never be read as a multiplayer signal.
LAST_OF_US_PART_I = [
    {"type": "NO_OF_PLAYERS", "value": "1"},
    {"type": "OFFLINE_PLAY_MODE", "value": "ENABLED"},
]

SEA_OF_THIEVES = [
    {"type": "ONLINE_PLAY_MODE", "value": "REQUIRED"},
    {"type": "PS_PLUS_REQUIRED", "value": "true"},
]

ARIZONA_SUNSHINE_VR2 = [
    {"type": "CAESAR_HEADSET", "value": "REQUIRED"},
    {"type": "ASTON_CONTROLLER", "value": "REQUIRED"},
    {"type": "PLAYER_MODES_SITTING", "value": "true"},
    {"type": "NO_OF_PLAYERS", "value": "1"},
]

# VR2 supported but not mandatory (flat-screen game with an optional VR mode).
VR2_OPTIONAL = [
    {"type": "CAESAR_HEADSET", "value": "OPTIONAL"},
    {"type": "NO_OF_PLAYERS", "value": "1"},
]


class TestLocalPlayers:
    """NO_OF_PLAYERS counts players on one console, so >= 2 is couch co-op.
    This is the whole local-multiplayer filter and it needs no third party."""

    def test_reads_player_count(self):
        assert decode_features(GANG_BEASTS).local_players == 4

    def test_four_player_game_is_local_multiplayer(self):
        assert decode_features(GANG_BEASTS).is_local_multiplayer is True

    def test_two_player_game_is_local_multiplayer(self):
        notices = [{"type": "NO_OF_PLAYERS", "value": "2"}]
        assert decode_features(notices).is_local_multiplayer is True

    def test_single_player_game_is_not_local_multiplayer(self):
        assert decode_features(SPIDER_MAN_2).is_local_multiplayer is False

    def test_offline_play_mode_does_not_imply_local_multiplayer(self):
        # The Last of Us Part I is 1 player AND offline-enabled. Reading
        # OFFLINE_PLAY_MODE as a co-op signal would wrongly list it.
        f = decode_features(LAST_OF_US_PART_I)
        assert f.local_players == 1
        assert f.is_local_multiplayer is False

    def test_missing_player_count_is_none_not_zero(self):
        # Online-only games omit the field. None means "unknown", which must
        # not be confused with "1 player".
        assert decode_features(SEA_OF_THIEVES).local_players is None

    def test_unknown_player_count_is_not_local_multiplayer(self):
        assert decode_features(SEA_OF_THIEVES).is_local_multiplayer is False


class TestControllerAndGraphicsFlags:
    def test_detects_dualsense_haptics(self):
        assert decode_features(SPIDER_MAN_2).dualsense_haptics is True

    def test_detects_adaptive_triggers(self):
        assert decode_features(SPIDER_MAN_2).adaptive_triggers is True

    def test_detects_ps5_pro_enhanced(self):
        assert decode_features(SPIDER_MAN_2).ps5_pro_enhanced is True

    def test_absent_flags_are_false(self):
        f = decode_features(SEA_OF_THIEVES)
        assert f.dualsense_haptics is False
        assert f.adaptive_triggers is False
        assert f.ps5_pro_enhanced is False


class TestPsvr2:
    """Sony ships VR2 under internal codenames: CAESAR is the headset,
    ASTON the Sense controllers. There is no literal 'PSVR2' key."""

    def test_required_headset_means_vr2_required(self):
        assert decode_features(ARIZONA_SUNSHINE_VR2).psvr2 == "required"

    def test_optional_headset_means_vr2_supported(self):
        assert decode_features(VR2_OPTIONAL).psvr2 == "optional"

    def test_no_headset_key_means_no_vr2(self):
        assert decode_features(SPIDER_MAN_2).psvr2 is None


class TestOnlineAndSubscription:
    def test_detects_online_required(self):
        assert decode_features(SEA_OF_THIEVES).online_play == "required"

    def test_detects_online_optional(self):
        assert decode_features(GANG_BEASTS).online_play == "optional"

    def test_detects_ps_plus_required(self):
        assert decode_features(SEA_OF_THIEVES).ps_plus_required is True


class TestRobustness:
    def test_empty_notices_yield_all_unknown(self):
        f = decode_features([])
        assert f.local_players is None
        assert f.is_local_multiplayer is False
        assert f.psvr2 is None

    def test_none_input_does_not_raise(self):
        assert decode_features(None).local_players is None

    def test_unparseable_player_count_is_none(self):
        notices = [{"type": "NO_OF_PLAYERS", "value": "not a number"}]
        assert decode_features(notices).local_players is None

    def test_unknown_notice_types_are_ignored(self):
        notices = [
            {"type": "SOME_FUTURE_SONY_FLAG", "value": "true"},
            {"type": "NO_OF_PLAYERS", "value": "2"},
        ]
        assert decode_features(notices).local_players == 2
