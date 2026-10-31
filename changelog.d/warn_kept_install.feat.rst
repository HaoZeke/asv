``asv`` now warns when pip reports that a wheel was "already installed with
the same version" during ``install_command``, since pip then keeps the
previously installed build and that build would be benchmarked for the new
commit.
