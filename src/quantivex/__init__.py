"""Quantivex: an LLM agent for noise-aware quantum chemistry on real calibration data."""

import logging

__version__ = "1.3.0"

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = ["__version__"]
