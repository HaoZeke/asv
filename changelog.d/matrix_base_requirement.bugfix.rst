A matrix entry that overrides a base requirement, such as
``"pip+asv_runner": ["==0.3.1"]``, is now part of the environment name and of
``requirements`` and ``params`` in result files. Previously it was installed
but dropped from the recorded results, so runs with different ``asv_runner``
versions could not be told apart.
