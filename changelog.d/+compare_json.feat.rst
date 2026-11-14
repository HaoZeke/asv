``asv compare --format=json`` writes schema version 1, one record per
benchmark and parameter combination. Each record has the name,
parameters, base and head medians and units, the ratio and its 95%
CI, rounds used, the verdict, and the A/A band. Markdown and LaTeX
are rendered from those same records.
