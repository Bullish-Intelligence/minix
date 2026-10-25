from minix_config import *

home = Home(
    user="learner",
    programs=[Program(pkgs.ripgrep)],  # Program(pkgs.ripgrep), Git(name="Ada Lovelace", email="ada@example.org"), Neovim(default_editor=True)
    files=[],  # File(".config/demo/demo.conf", "greeting = hello\n")
    services=[],  # Service("web", Run(pkgs.python3, "-m", "http.server", "8080"))
    options=[],  # advanced: Home Manager options, written as classes of pynix_hm
)
