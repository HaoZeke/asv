``asv continuous --interleave-rounds N`` alternates the base and head
commits for N rounds and reports each ratio with a bootstrap 95% CI.
``--aa-band`` flags a change only when that interval lies entirely
outside an A/A band measured on the base commit.
