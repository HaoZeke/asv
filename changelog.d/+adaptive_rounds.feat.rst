``asv continuous --min-rounds M`` stops a benchmark early once its
ratio CI lies outside the A/A band, or is narrower than ``--ci-tol``
without lying outside it. At most four looks spend the 5% tail in
the O'Brien-Fleming way. The last look is an ordinary 95% interval,
and an early stop stores the wider interval from that look.
