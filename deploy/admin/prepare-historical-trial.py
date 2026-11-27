#!/usr/bin/env python3
"""Provision/repair ONLY the isolated history tree. Stop historical writers first."""
import argparse
import grp
import os
from pathlib import Path
import pwd
import stat


def prepare(root, user='kiya', group='kiya'):
    root = Path(os.path.abspath(root))
    if root.name != 'history' or root == Path('/history'):
        raise ValueError('expected an explicit acceptance directory ending in /history')
    for path in (root, *root.parents):
        if path.is_symlink():
            raise ValueError(f'symlink ancestor rejected: {path}')
    uid, gid = pwd.getpwnam(user).pw_uid, grp.getgrnam(group).gr_gid
    if not root.parent.is_dir():
        raise ValueError('acceptance parent must already exist; it is never modified by this tool')
    if not root.exists():
        root.mkdir(mode=0o770)
    # Validate the complete tree before changing any existing entry. Never follow links.
    entries = [root]
    for directory, names, files in os.walk(root, followlinks=False):
        for name in names + files:
            path = Path(directory) / name
            info = path.lstat()
            mode = info.st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f'non-regular historical entry rejected: {path}')
            if info.st_dev != root.stat().st_dev or os.path.ismount(path):
                raise ValueError(f'nested mount rejected: {path}')
            if stat.S_ISREG(mode) and info.st_nlink != 1:
                raise ValueError(f'hard-linked historical file rejected: {path}')
            entries.append(path)
    for path in entries:
        os.chown(path, uid, gid, follow_symlinks=False)
        os.chmod(path, 0o2770 if path.is_dir() else 0o640, follow_symlinks=False)
    for name in ('queue', 'status', 'production', 'locks', 'ledger'):
        path = root / name
        path.mkdir(mode=0o770, exist_ok=True)
        os.chown(path, uid, gid, follow_symlinks=False)
        os.chmod(path, 0o2770, follow_symlinks=False)
    print(f'Prepared {root}: owner={user}:{group}, dirs=2770, files=0640; changed only history tree')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--user', default='kiya')
    parser.add_argument('--group', default='kiya')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('run as root while historical writers are stopped')
    prepare(args.root, args.user, args.group)
