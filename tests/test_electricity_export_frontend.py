from frontend_runtime import electricity_export_behavior


def test_selected_csv_contract_and_duplicate_prevention():
    result = electricity_export_behavior()
    assert result["duplicateCount"] == 1
    assert result["start"] == "2026-07-26T00:00:00+07:00"
    assert result["end"] == "2026-07-27T00:00:00+07:00"
    assert result["bucket"] == "30m"
    assert result["format"] == "csv"
    assert result["credentials"] == "same-origin"
    assert result["csrf"] is False


def test_csv_error_and_browser_download_lifecycle():
    result = electricity_export_behavior()
    assert result["displayedError"] == "history temporarily unavailable"
    assert result["clicks"] == 1
    assert result["appends"] == 1
    assert result["removes"] == 1
    assert result["revokes"] == 1


def test_axis_label_density_matches_bucket_resolution():
    result = electricity_export_behavior()
    assert result["strides"] == [8, 4, 2, 4, 3]
