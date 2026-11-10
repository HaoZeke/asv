Build requirements are read from ``pyproject.toml`` of the commit being
built (in ``{build_dir}``, honouring ``repo_subdir``) and installed before
``build_command``, instead of from ``pyproject.toml`` in the directory
``asv`` is run from. Requirement strings are passed to pip unchanged, so
``setuptools >= 77`` is no longer turned into ``==77``. Matrix entries for
the same packages take precedence (see #1388).
