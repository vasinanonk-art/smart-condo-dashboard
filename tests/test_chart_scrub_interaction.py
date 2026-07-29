import unittest

from frontend_runtime import chart_scrub_behavior


class ChartScrubInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = chart_scrub_behavior()

    def test_visible_samples_are_ordered_deduplicated_and_shared_with_renderer(self):
        self.assertEqual(self.result["canonicalTs"], [1, 2, 3, 4, 5])
        self.assertEqual(self.result["duplicateValue"], 24)
        self.assertEqual(self.result["visibleTs"], [1, 2, 3])
        self.assertEqual(self.result["renderedTs"], [1, 2, 3, 4, 5])

    def test_missing_values_are_not_coerced_to_zero(self):
        self.assertIsNone(self.result["nullNumeric"])
        self.assertIsNone(self.result["emptyNumeric"])
        self.assertNotIn(4, self.result["visibleTs"])
        self.assertNotIn(5, self.result["visibleTs"])

    def test_left_to_right_scrub_is_monotonic_and_clamped(self):
        selected = self.result["sweep"]
        self.assertEqual(selected, sorted(selected))
        self.assertEqual(selected[0], 0)
        self.assertEqual(selected[-1], 2)

    def test_plot_margins_and_responsive_svg_scaling_are_compensated(self):
        self.assertEqual(self.result["marginLeft"], 0)
        self.assertEqual(self.result["marginRight"], 2)
        self.assertEqual(self.result["resized"], 1)

    def test_first_last_and_single_sample_boundaries_are_stable(self):
        self.assertEqual(self.result["sweep"][0], 0)
        self.assertEqual(self.result["sweep"][-1], 2)
        self.assertEqual(self.result["singleSelections"], [0, 0, 0])

    def test_interaction_surface_includes_full_rendered_width(self):
        self.assertEqual(
            self.result["interactionBounds"],
            {"left": 0, "right": 900, "top": 18, "bottom": 275},
        )

    def test_all_chart_configurations_reach_both_endpoints(self):
        names = {
            "temperature-portrait",
            "humidity-landscape",
            "pm25-landscape",
            "electricity-portrait",
        }
        self.assertEqual(
            {configuration["name"] for configuration in self.result["endpointConfigurations"]},
            names,
        )
        for configuration in self.result["endpointConfigurations"]:
            self.assertEqual(configuration["indices"], [0, 0, 47, 47])
            self.assertEqual(configuration["timestamps"], [1, 1, 48, 48])
            self.assertEqual(
                configuration["markerPositions"][0],
                configuration["markerPositions"][1],
            )
            self.assertEqual(
                configuration["markerPositions"][2],
                configuration["markerPositions"][3],
            )
            self.assertEqual(
                configuration["touchIndices"],
                configuration["indices"],
            )

    def test_aspect_ratio_letterbox_is_included_in_coordinate_conversion(self):
        conversion = self.result["wideConversion"]

        self.assertEqual(conversion["offsetX"], 100)
        self.assertEqual(conversion["scaleX"], 1)
        self.assertAlmostEqual(conversion["oldFirstX"], 121.09, places=2)
        self.assertAlmostEqual(conversion["oldLastX"], 803.45, places=2)
        self.assertEqual(conversion["convertedFirst"], 48)
        self.assertEqual(conversion["convertedLast"], 882)

    def test_endpoint_events_render_matching_marker_and_tooltip(self):
        rendered = self.result["renderedEndpoints"]

        self.assertTrue(rendered["rootPointerBound"])
        self.assertEqual(
            rendered["left"],
            {"marker": "marker:48", "tooltip": "timestamp:1", "crosshair": 48},
        )
        self.assertEqual(
            rendered["right"],
            {"marker": "marker:882", "tooltip": "timestamp:3", "crosshair": 882},
        )

    def test_selection_uses_downsampled_visible_series(self):
        self.assertEqual(self.result["downsampledTs"], [1, 3, 5])

    def test_temperature_and_pm25_selection_state_is_independent(self):
        self.assertTrue(self.result["independent"])

    def test_mouse_and_touch_use_identical_coordinates(self):
        self.assertTrue(self.result["mouseTouchParity"])


if __name__ == "__main__":
    unittest.main()
