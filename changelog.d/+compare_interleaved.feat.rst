``asv continuous --interleave-rounds`` stores each round's samples, the
ratio CI, and the A/A band in the results file. ``asv compare`` reads
them back. ``asv compare --format=latex --columns`` can add those
columns. A benchmark that fails in one round is reported, and the other
rounds, benchmarks, and environments still run.
