"""samplemeter: count the samples a differential harness actually produced.

A harness that loops 2,000 times over one seeded document reports 2,000 samples
and exercises two code paths.  ``samplemeter`` measures the gap instead of
trusting the loop counter.
"""

from .core import Auditor, CallRecord, Report, SeedReset, fingerprint, scan_seed_resets

__all__ = [
    "Auditor",
    "CallRecord",
    "Report",
    "SeedReset",
    "fingerprint",
    "scan_seed_resets",
]
__version__ = "1.0.0"
