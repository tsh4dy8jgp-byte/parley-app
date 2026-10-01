"""Entry point of the packaged Parley app (see parley.spec).

`Parley --self-test REPORT [--online]` checks the build instead of opening the window.
"""
import sys
from pathlib import Path

from parley import frozen

frozen.prepare()   # before anything imports pydub

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--self-test" in args:
        rest = [a for a in args if not a.startswith("--")]
        report = Path(rest[0] if rest else "parley-self-test.txt")
        sys.exit(frozen.self_test(report, online="--online" in args))

    from parley.app import main

    main()
