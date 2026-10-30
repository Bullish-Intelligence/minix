# The compiler: `minix-compile home.py home.json`. It holds its own Python (with Pydantic from nixpkgs, 008-Q7), the
# `pynix` source, and `minix_config`. It needs nothing from PATH.
#
# The wrapper clears `NIX_PYTHONPATH`, `NIX_PYTHONPREFIX`, and `NIX_PYTHONEXECUTABLE`. A devenv shell sets them, and the
# Python of nixpkgs would put the packages of that shell on the search path of the compile.
#
# `pynix` is the source of the locked tag (`flake = false`): `${pynix}/src` holds `nixdantic`, `pynix_hm`, `pynix_pkgs`,
# and `pynix_native`. `source` is the folder `minix_config`.
{ pkgs, pynix, source }:
let
  python = pkgs.python313.withPackages (p: [ p.pydantic ]);
in
pkgs.runCommand "minix-compiler" { } ''
  mkdir -p $out/bin $out/lib
  cp -r ${source} $out/lib/minix_config
  cat > $out/bin/minix-compile <<SH
  #!${pkgs.runtimeShell}
  unset NIX_PYTHONPATH NIX_PYTHONPREFIX NIX_PYTHONEXECUTABLE
  export PYTHONPATH=$out/lib:${pynix}/src PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1
  exec ${python}/bin/python -P -m minix_config.compile "\$@"
  SH
  chmod +x $out/bin/minix-compile
''
