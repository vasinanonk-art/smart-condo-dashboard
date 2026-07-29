from tests.frontend_runtime import preview_chart_data_behavior


def test_preview_charts_have_deterministic_endpoint_samples():
    result = preview_chart_data_behavior()

    assert result["sensorCount"] == 24
    assert result["electricityCount"] == 24
    assert result["uniqueSensorTimestamps"] == 24
    assert result["uniqueElectricityTimestamps"] == 24
    assert result["sensorFirst"]["temperature"] == 20
    assert result["sensorLast"]["temperature"] == 31.5
    assert result["sensorFirst"]["humidity"] == 40
    assert result["sensorLast"]["humidity"] == 63
    assert result["sensorFirst"]["pm25_living_room"] == 5
    assert result["sensorLast"]["pm25_living_room"] == 28
    assert result["electricityFirst"]["energy_kwh"] == 0.1
    assert result["electricityLast"]["energy_kwh"] == 1.25


def test_preview_gaps_are_internal_and_requests_do_not_reach_backend():
    result = preview_chart_data_behavior()

    assert result["sensorNulls"] >= 3
    assert result["electricityNulls"] == 1
    assert result["condoCount"] == 24
    assert result["historyCount"] == 24
    assert result["originalRequestCount"] == 0


def test_preview_labels_cover_every_chart_family():
    result = preview_chart_data_behavior()

    assert set(result["labels"]) == {
        "overviewChart",
        "overviewPmChart",
        "airChart",
        "electricityHistoryChart",
    }


def test_preview_data_is_inert_without_explicit_query_parameter():
    result = preview_chart_data_behavior()

    assert result["productionApiInstalled"] is False
    assert result["productionFetchUnchanged"] is True
