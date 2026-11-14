"""The installed version of this package, for diagnostics. It is not what a run is pinned to: that is the agent's own ``version``."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("loamz-runtime")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.1.1"

__all__ = ["__version__"]
