from tests.frontend_runtime import electricity_settings_navigation_behavior


def test_settings_navigation_has_one_authoritative_owner_and_hydrates_once():
    result = electricity_settings_navigation_behavior()

    assert result["handlerPreserved"] is True
    assert result["first"]["mount_count"] == 1
    assert result["first"]["settings_fetch_count"] == 1
    assert result["first"]["name"] == "Tariff 1"

    # Re-activating an already-active page is idempotent.
    assert result["duplicate"]["mount_count"] == 1
    assert result["duplicate"]["settings_fetch_count"] == 1

    # Leaving and returning performs one new fetch without overwriting a draft.
    assert result["returned"]["mount_count"] == 1
    assert result["returned"]["settings_fetch_count"] == 2
    assert result["returned"]["name"] == "Unsaved draft"
    assert result["settingsRequests"] == 2

    # dashboard_v3 owns transitions and does not reactivate the current page.
    assert result["canonicalNavigation"] == {
        "activated": 1,
        "deactivated": 1,
        "billingActivated": 1,
        "billingDeactivated": 1,
    }
