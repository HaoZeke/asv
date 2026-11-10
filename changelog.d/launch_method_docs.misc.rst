Document the ``launch_method`` key of ``asv.conf.json``. Since 0.6.6,
``auto`` selects ``spawn`` on all platforms. Earlier, ``auto`` selected
``forkserver`` when ``fork`` was available, except on macOS.
``--launch-method`` takes precedence over the key.
