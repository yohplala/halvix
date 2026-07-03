"""
Jinja2 templates for HTML generation.

This module provides a template loader and renderer for HTML pages.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# Template directory
TEMPLATE_DIR = Path(__file__).parent

# Create Jinja2 environment
_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_template(template_name: str, **context) -> str:
    """
    Render a Jinja2 template with the given context.

    Args:
        template_name: Name of the template file (e.g., "composition_viewer.html")
        **context: Template variables

    Returns:
        Rendered HTML string
    """
    template = _env.get_template(template_name)
    return template.render(**context)
