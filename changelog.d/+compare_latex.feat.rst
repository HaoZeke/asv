``asv compare --format=latex`` writes a ``booktabs`` table from the
schema version 1 records: benchmark, parameters, before, after, ratio,
and the significance marker. Each row scales its own unit, including
bytes, with ``siunitx``. ``--only-body`` emits only the tabular rows.
