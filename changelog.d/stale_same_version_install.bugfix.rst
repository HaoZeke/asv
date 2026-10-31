The default ``uninstall_command`` now also uninstalls the distribution of the
wheel that was last installed, whose name can differ from ``project``.
Before, nothing was uninstalled in that case, so a custom ``install_command``
without ``--force-reinstall`` kept the previous commit's build when two
commits had the same version, and that build was benchmarked again.
