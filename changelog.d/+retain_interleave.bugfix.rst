Interleaved rounds install each of base and head once and reuse
those installs. A later round only reruns the benchmarks, instead
of checking the tree out and reinstalling the wheel on every switch.
