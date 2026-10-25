from minix_config import *

home = Home(
    user="learner",
    programs=[],  # Program(pkgs.htop)
    files=[],  # File(".config/app/app.conf", "key = value\n")
    services=[Service("web", program=pkgs.coreutils, file="sleep", args="5")],
)
