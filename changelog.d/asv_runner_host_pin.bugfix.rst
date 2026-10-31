Benchmark environments install the ``asv_runner`` release that is installed
next to ``asv`` (``asv_runner==<version>``) instead of the newest release on
the index, so timing semantics do not change when a new ``asv_runner`` is
published. Development and local versions are not pinned.
