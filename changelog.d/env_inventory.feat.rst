Result files record the Python distributions installed in the benchmark
environment (``asv_runner``, build tools, resolved versions of unpinned
matrix entries) under the new optional ``env_inventory`` key, exposed as
``Results.env_inventory`` and listed by ``asv show COMMIT --details``.
Older result files without the key still load, and older asv versions
ignore it, so the result ``api_version`` is unchanged.
