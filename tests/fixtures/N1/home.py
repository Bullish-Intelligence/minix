from minix_config import *

home = Home(
    user="learner",
    programs=[Neovim(default_editor=True), Files(), TerminalWorkspace(), Git(name="Ada Lovelace", email="ada@example.org")],
    files=[File(".local/bin/hi", "#!/bin/sh\necho hi\n", executable=True)],
    services=[Service("web", program=pkgs.python3, args="-m http.server 8080", environment={"PORT": "8080"}, restart_delay="3s")],
)
