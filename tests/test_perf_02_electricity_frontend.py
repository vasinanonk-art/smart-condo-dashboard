from frontend_runtime import electricity_summary_store_behavior


def test_summary_store_deduplicates_consumers_without_polling_timer():
    result = electricity_summary_store_behavior()
    assert result["requestsBeforeCleanup"] == 1
    assert result["sameResult"] is True
    assert result["timers"] == 0


def test_summary_store_cleanup_removes_lifecycle_listener():
    result = electricity_summary_store_behavior()
    assert result["listenerAdds"] == 1
    assert result["listenerRemoves"] == 1
    assert result["requestsTotal"] == 2
    assert result["aborted"] is True
    assert result["notifications"] == 0
