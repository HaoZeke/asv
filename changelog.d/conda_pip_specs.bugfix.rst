The ``conda`` plugin writes ``pip+`` matrix entries into the ``pip:`` section
of the generated ``environment.yml`` as valid requirements (e.g.
``package==1.0`` instead of ``package 1.0``), so ``conda env create`` no longer
fails with an invalid requirement when a pip dependency has a version.
Declarations with flags or paths are left to the follow-up ``pip install``.
