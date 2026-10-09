"""One test cycle: convert an UTAU bank, build the DB, render the test song in the dev editor, check it.

    python -I tools/cycle.py <UTAU bank> <tag> [--groups REGEX] [--pc 1] [--work DIR] [--language English]
                             [--song en] [--suffixes ",#1"] [--seg CONVERTED] [convert options...]

Everything goes to <work>/<tag>/: seg/ (converted folder), db/<Name>/ (the DB, built under the name of
dev-editor voice slot <pc>, which is pointed at it with tools/slot.py), song.wav/.msd and qa.txt.
Run `python -I tools/slot.py restore <pc>` when done testing.
"""
import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import render  # noqa: E402
import render_qa  # noqa: E402
import slot  # noqa: E402
from u2v import dbtool_build  # noqa: E402

DEVKIT = os.path.dirname(ROOT)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument('bank')
    ap.add_argument('tag')
    ap.add_argument('--pc', type=int, default=1)
    ap.add_argument('--work', default=os.path.join(os.path.dirname(DEVKIT), 'render_tests'))
    ap.add_argument('--skip-convert', action='store_true')
    ap.add_argument('--seg', help='use this already converted folder instead of converting the bank')
    ap.add_argument('--language', default='Japanese', help='DB language for the DBTool')
    ap.add_argument('--song', default='test', help='test song (test, en)')
    ap.add_argument('--suffixes', default='', help='comma list of phoneme suffixes to render, e.g. ",#1"')
    a, rest = ap.parse_known_args()
    out = os.path.join(a.work, a.tag)
    seg, dbdir = a.seg or os.path.join(out, 'seg'), os.path.join(out, 'db')
    _, name = slot.slot_folder(a.pc)
    t0 = time.time()
    if not a.skip_convert and not a.seg:
        cmd = [sys.executable, '-I', os.path.join(ROOT, 'utau2vocaloid.py'), 'convert', a.bank, seg] + rest
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        tail = [ln for ln in r.stdout.splitlines() if 'coverage' in ln or 'validation' in ln or 'diphones' in ln]
        print('\n'.join(tail) or r.stdout[-2000:] + r.stderr[-2000:])
        if r.returncode:
            raise SystemExit('convert failed')
    if os.path.exists(dbdir):
        slot.restore(a.pc) if slot.is_junction(slot.slot_folder(a.pc)[0]) else None
        shutil.rmtree(dbdir)
    failed = dbtool_build.build(os.path.join(DEVKIT, 'VocaloidDBTool3.exe'), seg,
                                os.path.join(dbdir, name, name), name=a.tag, language=a.language,
                                log=lambda *m: print(*m) if 'octave' in str(m) else None)
    print('built in %.0fs%s' % (time.time() - t0, '' if not failed else ', %d files NOT added' % len(failed)))
    slot.mount(a.pc, os.path.join(dbdir, name))
    for suffix in a.suffixes.split(","):
        wav = os.path.join(out, 'song%s.wav' % suffix.replace('#', '_'))
        render.render(a.pc, wav, song=a.song, suffix=suffix, log=lambda *m: None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sys.argv = ['render_qa', wav]
            render_qa.main()
        open(wav[:-4] + '_qa.txt', 'w', encoding='utf-8').write(buf.getvalue())
        print(os.path.basename(wav), '\n'.join(buf.getvalue().splitlines()[:4]))
    print('total %.0fs -> %s' % (time.time() - t0, out))


if __name__ == '__main__':
    main()
