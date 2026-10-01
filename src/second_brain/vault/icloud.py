"""Let this process download evicted iCloud files ("dataless" placeholders) on read.

The vault lives in iCloud Drive, which keeps some files as placeholders whose
content is still in the cloud — e.g. a note clipped on the phone that the Mac
hasn't downloaded yet. Processes started by launchd begin with on-demand
download ("materialization") turned off, so reading such a file fails with
``OSError: [Errno 11] Resource deadlock avoided`` (EDEADLK) instead of fetching
it. A process may turn it back on for itself, which is what this does.
"""

from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger(__name__)

# <sys/resource.h>
_IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES = 3
_IOPOL_SCOPE_PROCESS = 0
_IOPOL_MATERIALIZE_DATALESS_FILES_ON = 2


def enable_dataless_materialization() -> None:
    """Make reads of iCloud placeholders block until downloaded instead of failing.

    No-op outside macOS; a failure is logged, never raised (reads of files that
    are already downloaded work either way).
    """
    if sys.platform != "darwin":
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        rc = libc.setiopolicy_np(
            _IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES,
            _IOPOL_SCOPE_PROCESS,
            _IOPOL_MATERIALIZE_DATALESS_FILES_ON,
        )
    except (OSError, AttributeError) as exc:
        logger.warning("Could not enable iCloud on-demand downloads: %s", exc)
        return
    if rc != 0:
        logger.warning(
            "Could not enable iCloud on-demand downloads: setiopolicy_np errno %d",
            ctypes.get_errno(),
        )
