``asv continuous --min-rounds M`` stops a benchmark early only when
its ratio CI lies entirely outside the A/A band. A no-change verdict
waits for the last look. ``--min-effect T`` opts in to an earlier
no-change stop when the interval lies entirely inside ``1 ± T``.
At most four looks spend the 5% tail in the O'Brien-Fleming way.
The last look is an ordinary 95% interval, and an early stop stores
the wider interval from that look.
