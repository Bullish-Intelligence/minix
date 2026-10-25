# The compiler: `minix-compile home.py home.json`. It holds its own Python (with Pydantic from nixpkgs, 008-Q7), the
# `pynix` source, and `minix_config`. It needs nothing from PATH.
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
  export PYTHONPATH=$out/lib:${pynix}/src PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1
  exec ${python}/bin/python -P -m minix_config.compile "\$@"
  SH
  chmod +x $out/bin/minix-compile
''
