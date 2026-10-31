Tracking, memory and peak memory benchmarks run with more than one round (for
example ``-a rounds=3``) now keep the value of every round as a sample, store
the median as the result and compute statistics, so that ``asv compare`` and
``asv continuous`` apply their significance test to them. Previously only the
value of the last round was stored. ``--append-samples`` and
``--record-samples`` work for these benchmarks as for timing benchmarks.
