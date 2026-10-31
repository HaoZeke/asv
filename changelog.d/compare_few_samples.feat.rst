``asv compare`` and ``asv continuous`` append ``?`` to the ratio of results
beyond the factor whose recorded samples are too few for the Mann-Whitney U
significance test to reach the threshold (e.g. fewer than 7 per side), and
print a note on how to record more. ``--no-mark-few-samples`` turns this off.
