"""
Data processors for TOTAL2b index calculations.

This module re-exports classes from the processor submodules:

- processor_base.py: BaseTotal2Processor with shared algorithms
- processor_total2b.py: Total2bProcessor for TOTAL2b calculation
"""

from data.processor_base import (
    BaseTotal2Processor,
    ProcessorError,
    Total2Result,
)
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
    "BaseTotal2Processor",
    "ProcessorError",
    "Total2Result",
    "Total2bProcessor",
    "get_processor",
]
