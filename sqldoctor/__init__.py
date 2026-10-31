"""SQL Query Doctor: find missing indexes and measure the speedup."""
from .analyzer import analyze, suggest_indexes, explain, full_scans, Report

__all__ = ["analyze", "suggest_indexes", "explain", "full_scans", "Report"]
__version__ = "0.1.0"
