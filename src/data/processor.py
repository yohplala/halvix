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


def get_processor(
    index_type: str = "total2b",
    **kwargs,
) -> BaseTotal2Processor:
    """
    Factory function to get the appropriate processor for an index type.

    Args:
        index_type: Must be "total2b"
        **kwargs: Additional arguments passed to processor constructor

    Returns:
        Total2bProcessor instance

    Raises:
        ValueError: If index_type is not recognized
    """
    if index_type == "total2b":
        return Total2bProcessor(**kwargs)
    else:
        raise ValueError(f"Unknown index type: {index_type}. Use 'total2b'.")


__all__ = [
    # Base classes and types
    "BaseTotal2Processor",
    "ProcessorError",
    "Total2Result",
    # Processor implementation
    "Total2bProcessor",
    # Factory function
    "get_processor",
]
