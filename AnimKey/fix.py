"""Retired development helper.

Older work-in-progress builds shipped this module with an absolute local path
and modified source code merely by importing it.  It remains as an inert module
so any legacy reference to ``AnimKey.fix`` is safe in every supported Maya
release.
"""


def apply_legacy_fix():
    """Kept for legacy callers; current AnimKey releases need no source patch."""
    return False
