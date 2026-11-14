``asv continuous --min-rounds M`` stops a benchmark early once its
ratio CI lies outside the A/A band, or is narrower than ``--ci-tol``
without lying outside it. The interval is a Bonferroni widening over
the planned looks, and that widened interval is what is stored and
printed.
