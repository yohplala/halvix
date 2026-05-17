"""
Public facade for the TOTAL2b processor.

This module is the stable import surface for callers (main.py, tests,
downstream consumers). It re-exports only the names that have external
consumers:

- get_processor: factory function (used by the CLI)
- Total2bProcessor: the concrete processor class
- Total2Result: the result dataclass
- ProcessorError: the package's error type

Internal helpers (BaseTotal2Processor, Total2bConfig, NoDataError) remain
importable from their defining submodules (processor_base, processor_total2b)
but are deliberately not surfaced here to keep the facade narrow.
"""

from data.processor_base import ProcessorError, Total2Result
from data.processor_total2b import Total2bProcessor


def get_processor(**kwargs) -> Total2bProcessor:
    """
    Factory function to create a Total2bProcessor.

    Args:
        **kwargs: Arguments passed to Total2bProcessor constructor

    Returns:
        Total2bProcessor instance
    """
    return Total2bProcessor(**kwargs)


__all__ = [
    "ProcessorError",
    "Total2Result",
    "Total2bProcessor",
    "get_processor",
]
