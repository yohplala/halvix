"""Visualization module for Halvix charts and reports."""

from visualization.charts import (
    _get_base_css,
    _get_footer_css,
    _get_footer_html,
    _get_header_css,
    _get_header_html,
    create_btc_combined_chart,
    create_btc_usd_halving_chart,
    create_btc_usd_normalized_chart,
    create_composition_viewer_html,
    create_total2_combined_chart,
    create_total2_dual_chart,
    create_total2_halving_chart,
    generate_all_charts,
)

__all__ = [
    "create_total2_halving_chart",
    "create_btc_usd_halving_chart",
    "create_btc_usd_normalized_chart",
    "create_total2_dual_chart",
    "create_btc_combined_chart",
    "create_total2_combined_chart",
    "create_composition_viewer_html",
    "generate_all_charts",
    # HTML helper functions
    "_get_base_css",
    "_get_header_css",
    "_get_footer_css",
    "_get_header_html",
    "_get_footer_html",
]
