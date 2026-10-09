"""Let the dev editor sing a test DB through one of its existing voice slots, without admin rights.

The dev editor only knows the voices in its installed DB_Dev.ini (writing that file needs admin). This
points one slot's DB folder at a test DB with a directory junction instead; the slot's own DB is parked
next to it as <folder>__orig and put back by `restore`.

    python -I tools/slot.py mount <pc> <test DB parent folder>   # folder holding <Name>.tree and <Name>/
    python -I tools/slot.py restore <pc>

The test DB must be built under the slot's DB name (e.g. build "...\\mytest\\GooberNiko2\\GooberNiko2").
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render import read_slots  # noqa: E402


def slot_folder(pc):
    s = [s for s in read_slots() if s[0] == pc][0]
    return os.path.dirname(s[2]), os.path.splitext(s[3])[0]     # folder holding <Name>.tree, Name


def is_junction(path):
    return os.path.isdir(path) and os.path.realpath(path) != os.path.abspath(path)


def mount(pc, test_parent):
    folder, name = slot_folder(pc)
    if not os.path.exists(os.path.join(test_parent, name + '.tree')):
        raise SystemExit('%s has no %s.tree: build the test DB under the name %s' % (test_parent, name, name))
    orig = folder + '__orig'
    if is_junction(folder):
        os.rmdir(folder)                       # removes the link only
    elif os.path.exists(folder):
        if os.path.exists(orig):
            raise SystemExit('%s and %s both exist; restore first' % (folder, orig))
        os.rename(folder, orig)
    subprocess.run(['cmd', '/c', 'mklink', '/J', folder, os.path.abspath(test_parent)], check=True,
                   capture_output=True)
    return folder


def restore(pc):
    folder, _ = slot_folder(pc)
    orig = folder + '__orig'
    if is_junction(folder):
        os.rmdir(folder)
    if os.path.exists(orig) and not os.path.exists(folder):
        os.rename(orig, folder)
    return folder


if __name__ == '__main__':
    if sys.argv[1] == 'mount':
        print('slot %s -> %s' % (sys.argv[2], mount(int(sys.argv[2]), sys.argv[3])))
    else:
        print('restored', restore(int(sys.argv[2])))
