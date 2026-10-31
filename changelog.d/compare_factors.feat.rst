New ``compare_factors`` option in ``asv.conf.json``: a dictionary that maps
benchmark-name regular expressions to factors. ``asv compare`` and
``asv continuous`` use the largest matching factor instead of ``--factor``
for those benchmarks, so noisy benchmark types such as memory or tracked
values can use a looser threshold than timings.
