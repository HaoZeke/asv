``asv continuous --interleave-rounds N`` alternates commits for N rounds
and reports each ratio with a bootstrap 95% CI. ``--aa-band`` runs a
second copy of the base commit in that rotation and flags a change only
when the interval lies entirely outside the base-vs-base' band.
``--aa-band-split`` keeps the older half-split estimate.
