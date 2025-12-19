"""
Data processors for TOTAL2 index calculations.

This module re-exports classes from the processor submodules:

- processor_base.py: BaseTotal2Processor with shared algorithms
- processor_total2.py: Total2Processor for legacy TOTAL2 calculation
- processor_total2b.py: Total2bProcessor for new TOTAL2b calculation
"""

from config import (
    TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS,
    TOTAL2B_MIN_COINS_FOR_SCALING,
)
from data.processor_base import (
    MIN_VOLUME_FOR_OUTLIER_CHECK,
    OUTLIER_WINDOW_DAYS,
    VOLUME_OUTLIER_THRESHOLD,
    BaseTotal2Processor,
    ProcessorError,
    Total2Result,
)
from data.processor_total2 import (
    MAX_DOD_DECREASE,
    MAX_DOD_INCREASE,
    Total2Processor,
)
from data.processor_total2b import Total2bProcessor


def get_processor(
    index_type: str = "total2b",
    **kwargs,
) -> BaseTotal2Processor:
    """
    Factory function to get the appropriate processor for an index type.

    Args:
        index_type: "total2" for legacy, "total2b" for new methodology
        **kwargs: Additional arguments passed to processor constructor

    Returns:
        Processor instance (Total2Processor or Total2bProcessor)

    Raises:
        ValueError: If index_type is not recognized
    """
    if index_type == "total2":
        return Total2Processor(**kwargs)
    elif index_type == "total2b":
        return Total2bProcessor(**kwargs)
    else:
        raise ValueError(f"Unknown index type: {index_type}. Use 'total2' or 'total2b'.")


__all__ = [
    # Base classes and types
    "BaseTotal2Processor",
    "ProcessorError",
    "Total2Result",
    # Processor implementations
    "Total2Processor",
    "Total2bProcessor",
    # Factory function
    "get_processor",
    # Constants (volume outliers - shared)
    "VOLUME_OUTLIER_THRESHOLD",
    "MIN_VOLUME_FOR_OUTLIER_CHECK",
    "OUTLIER_WINDOW_DAYS",
    # Constants (TOTAL2 series smoothing)
    "MAX_DOD_INCREASE",
    "MAX_DOD_DECREASE",
    # Constants (TOTAL2b specific)
    "TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS",
    "TOTAL2B_MIN_COINS_FOR_SCALING",
]
