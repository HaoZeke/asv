The documentation, the ``asv quickstart`` template and the JSON schema now
show the actual default ``install_command``, which passes
``--force-reinstall``, and the documentation explains that a custom
``install_command`` needs it too when consecutive commits build wheels with
the same version.
