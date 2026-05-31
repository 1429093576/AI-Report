"""图表 schema 的测试。"""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from src.schemas import ChartDataPoint, ChartSpec, ChartType


class ChartSchemaTests(unittest.TestCase):
    def test_chart_data_point_strips_label_and_accepts_number(self) -> None:
        point = ChartDataPoint(label=" AI models ", value=3)

        self.assertEqual(point.label, "AI models")
        self.assertEqual(point.value, 3.0)

    def test_chart_spec_parses_nested_data_and_chart_type(self) -> None:
        spec = ChartSpec(
            id="topic-distribution",
            title="Topic Distribution",
            chart_type="bar",
            data=[
                {"label": "Model release", "value": 3},
                {"label": "Policy", "value": 1},
            ],
            output_path="outputs/charts/topic_distribution.png",
            x_label=" Topic ",
            y_label=" Count ",
        )

        self.assertEqual(spec.chart_type, ChartType.BAR)
        self.assertIsInstance(spec.data[0], ChartDataPoint)
        self.assertEqual(spec.x_label, "Topic")
        self.assertEqual(spec.y_label, "Count")

    def test_chart_spec_requires_at_least_one_data_point(self) -> None:
        with self.assertRaises(ValidationError):
            ChartSpec(
                id="topic-distribution",
                title="Topic Distribution",
                chart_type="bar",
                data=[],
                output_path="outputs/charts/topic_distribution.png",
            )

    def test_chart_spec_rejects_unknown_chart_type(self) -> None:
        with self.assertRaises(ValidationError):
            ChartSpec(
                id="topic-distribution",
                title="Topic Distribution",
                chart_type="heatmap",
                data=[{"label": "Model release", "value": 3}],
                output_path="outputs/charts/topic_distribution.png",
            )

    def test_chart_spec_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            ChartSpec(
                id="topic-distribution",
                title="Topic Distribution",
                chart_type="bar",
                data=[{"label": "Model release", "value": 3}],
                output_path="outputs/charts/topic_distribution.png",
                unexpected="field",
            )


if __name__ == "__main__":
    unittest.main()
