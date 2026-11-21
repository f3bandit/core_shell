#!/usr/bin/env python3
"""Coreshell - retired: tools/update_db.py makes profiles from source now.

gen_profiles.py took a core's newest release from its repo and wrote systems/<system>.json from it. It didn't record
the core file's md5 (so Coreshell couldn't tell the build it was made from), named systems differently from the rest,
and wrote over profiles read from the running core. tools/update_db.py does the job properly: the exact build Update
All installs, its md5, and the menu worked out as Quartus would (coreshell/vconst.py), with anything it can't read left
for a dump on a MiSTer (docs/MANUAL_DUMPS.md).

    python3 tools/update_db.py --dry-run --only <core>     what it would do for that core
    python3 tools/update_db.py --only <core>               do it

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import sys

if __name__ == '__main__':
    print(__doc__)
    sys.exit(2)
