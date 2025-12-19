"""
Data processor for TOTAL2 index calculation.

This module provides backward compatibility by re-exporting classes
from the refactored processor modules:

- processor_base.py: BaseTotal2Processor with shared algorithms
- processor_total2.py: Total2Processor for legacy TOTAL2 calculation
- processor_total2b.py: Total2bProcessor for new TOTAL2b calculation

For new code, prefer importing directly from the specific modules.
"""

# Re-export all public classes for backward compatibility
from data.processor_base import (
    BaseTotal2Processor,
    ProcessorError,
    Total2Result,
    VOLUME_OUTLIER_THRESHOLD,
    MIN_VOLUME_FOR_OUTLIER_CHECK,
    OUTLIER_WINDOW_DAYS,
)

from data.processor_total2 import (
    Total2Processor,
    MAX_DOD_INCREASE,
    MAX_DOD_DECREASE,
    PRICE_OUTLIER_WINDOW_DAYS,
)

from data.processor_total2b import (
    Total2bProcessor,
    FREEZE_PERIOD_DAYS,
    MIN_COINS_FOR_SCALING,
)


# Factory function for getting the appropriate processor
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


# Keep legacy constants for backward compatibility
PRICE_OUTLIER_THRESHOLD = 5
MIN_PRICE_FOR_OUTLIER_CHECK = 0.001
TOTAL2_OUTLIER_THRESHOLD = 2


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
    # Constants (price outliers - TOTAL2 specific)
    "MAX_DOD_INCREASE",
    "MAX_DOD_DECREASE",
    "PRICE_OUTLIER_WINDOW_DAYS",
    "PRICE_OUTLIER_THRESHOLD",
    "MIN_PRICE_FOR_OUTLIER_CHECK",
    "TOTAL2_OUTLIER_THRESHOLD",
    # Constants (TOTAL2b specific)
    "FREEZE_PERIOD_DAYS",
    "MIN_COINS_FOR_SCALING",
]
