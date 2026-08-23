import pytest

from scripts import preview_xiaomi_camera as preview


def test_preview_upstreams_are_loopback_only():
    assert preview.loopback_base("http://127.0.0.1:8090") == "http://127.0.0.1:8090"
    for value in (
        "https://127.0.0.1:8090",
        "http://192.168.1.61:8090",
        "http://user:password@127.0.0.1:8090",
        "http://127.0.0.1:8090/api",
    ):
        with pytest.raises(ValueError, match="preview_upstream_must_be_loopback"):
            preview.loopback_base(value)


def test_preview_is_read_only_and_capability_gated():
    source = open(preview.__file__, encoding="utf-8").read()
    assert '"ptz_move": False' in source
    assert '"ptz_stop": False' in source
    assert 'self._json_error(405, "read_only_preview")' in source
    assert "/command" not in source


def test_preview_uses_h264_live_compatibility_stream():
    source = open(preview.__file__, encoding="utf-8").read()
    assert preview.SNAPSHOT_STREAM_NAME == "living_room_xiaomi"
    assert preview.LIVE_STREAM_NAME == "living_room_xiaomi_h264"
    assert preview.PREVIEW_ASSET_VERSION == "v1022-xiaomi-livefix1"
    assert 'time.monotonic() + 15.0' in source
