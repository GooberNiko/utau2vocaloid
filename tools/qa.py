"""Quality check for converted seg folders (UTAU -> VOCALOID3 DBTool).

Usage: python -I tools/qa.py <seg folder> [<seg folder> ...]
Prints one section per folder with a FLAGS list. Always exits 0.
"""
import csv
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

import numpy as np  # noqa: E402

from u2v import audio, phonemes as P  # noqa: E402
from u2v.dbtool_io import find_occurrence, read_as, read_seg, read_trans  # noqa: E402

PLOSIVES = {'k', "k'", 'g', "g'", 't', "t'", 'd', "d'", 'p', "p'", 'b', "b'", 'ts', 'tS', 'dz', 'dZ'}
WIN = 0.020

_wav = {}   # wav base path -> [samples, sr, Features or None]


def load(base):
    if base not in _wav:
        x, sr = audio.read_wav(base + '.wav')
        _wav[base] = [x, sr, None]
    return _wav[base]


def feats(base):
    e = load(base)
    if e[2] is None:
        e[2] = audio.Features(e[0], e[1])
    return e[2]


def rms_frames(x, sr, a, b):
    """Non-overlapping 20 ms RMS levels (dB) between a and b seconds."""
    n = int((b - a) / WIN)
    return np.array([audio.rms_db(x[int((a + i * WIN) * sr):int((a + (i + 1) * WIN) * sr)]) for i in range(n)])


def pct(v, q):
    return float(np.percentile(v, q)) if len(v) else float('nan')


def load_unit(folder, row, cnt):
    """Segments (absolute times), mark times (absolute, from .as<k>) and samples of one manifest row."""
    base = os.path.join(folder, row['file'])
    x, sr, _ = load(base)
    phs, dirs = read_trans(base + '.trans')
    unit = row['unit'].split()
    key = (row['file'], row['unit'])
    j = cnt.get(key, 0)              # n-th manifest row with this unit in this file -> n-th directive
    cnt[key] = j + 1
    ks = [k for k, (u, _) in enumerate(dirs) if u == unit]
    if not ks:
        raise ValueError('unit not in .trans')
    k = ks[min(j, len(ks) - 1)]
    start = find_occurrence(phs, unit, dirs[k][1])
    if start < 0:
        raise ValueError('directive not in transcription')
    segs = read_seg(base + '.seg')[0][start:start + len(unit)]
    a = read_as('%s.as%d' % (base, k))
    off = a.cut_offset / sr
    return {'file': row['file'], 'unit': unit, 'seg': segs, 'x': x, 'sr': sr, 'base': base,
            'bounds': [off + b for b in a.boundaries]}


def check_stationary(r, flags):
    x, sr = r['x'], r['sr']
    _, b, e = r['seg'][0]
    _, f = audio.f0_track(x, sr, b, e)
    if len(f) >= 25:
        c = 1200 * np.log2(f / np.median(f))
        dev = np.abs(c - np.convolve(c, np.ones(21) / 21, 'same'))
        dev[:10] = dev[-10:] = 0
        sd, mdev, step = float(np.std(c)), float(dev.max()), float(np.abs(np.diff(c)).max())
    else:
        sd = mdev = step = float('nan')
    body = rms_frames(x, sr, b, e)
    start = audio.rms_db(x[int(b * sr):int((b + WIN) * sr)])
    drop = float(start - np.median(body)) if len(body) else float('nan')
    name = ' '.join(r['unit'])
    if e - b < 0.3:
        flags.append((r['file'], name, 'stationary length %.2f s' % (e - b)))
    if mdev > 12:
        flags.append((r['file'], name, 'pitch local deviation %.1f c' % mdev))
    if step > 6:
        flags.append((r['file'], name, 'pitch step %.1f c per 5 ms' % step))
    if drop < -3:
        flags.append((r['file'], name, 'start level %.1f dB below body' % drop))
    return [r['file'], name, '%.2f' % (e - b), '%.1f' % sd, '%.1f' % mdev, '%.1f' % step, '%.1f' % drop]


def check_folder(folder):
    flags, out = [], ['== %s' % folder]
    _wav.clear()
    rep = os.path.join(folder, 'report.txt')
    if os.path.exists(rep):
        lines = open(rep, encoding='utf-8-sig', errors='replace').read().splitlines()
        out += ['  ' + s.strip() for s in lines if 'coverage' in s.lower() or 'missing' in s.lower()]
    else:
        out.append('  (no report.txt)')
    with open(os.path.join(folder, 'manifest.csv'), encoding='utf-8-sig', newline='') as fh:
        rows = list(csv.DictReader(fh))
    cnt, stat, arts, stat_spec = {}, [], [], {}
    for row in rows:
        try:
            r = load_unit(folder, row, cnt)
        except Exception as ex:  # noqa: BLE001 - keep going, report it
            flags.append((row['file'], row['unit'], 'load error: %s' % ex))
            continue
        (stat if row['kind'] == 'stationary' else arts).append(r)

    out.append('stationaries: %d  (file | unit | len s | sd c | maxdev c | step c | start dB)' % len(stat))
    for r in stat:
        out.append('  ' + ' | '.join(check_stationary(r, flags)))
        f, (_, b, e) = feats(r['base']), r['seg'][0]
        stat_spec[r['unit'][0]] = f.spec[f.t2f(b):f.t2f(e) + 1].mean(axis=0)

    vv_dips, nplos, nnoburst, nplos_flag, joins, loud, join_by = [], 0, 0, 0, [], {}, {}
    for r in arts:
        ph, seg, x, sr, fn = r['unit'], r['seg'], r['x'], r['sr'], r['file']
        name = ' '.join(ph)
        if len(ph) == 2:
            p0, p1 = ph
            if p0 in P.VOWELS and p1 in P.VOWELS:
                d = float(audio.dip_at(x, sr, seg[0][2])[0])
                vv_dips.append(d)
                if d > 3:
                    flags.append((fn, name, 'vowel-vowel dip %.1f dB' % d))
            mark = pseg = None
            if p0 in P.VOWELS and p1 in PLOSIVES:
                mark, pseg = r['bounds'][2], seg[1]
            elif p0 in PLOSIVES and p1 in P.VOWELS:
                mark, pseg = r['bounds'][0], seg[0]
            if mark is not None:
                nplos += 1
                burst = audio.burst_time(x, sr, pseg[1], pseg[2])
                if burst is None:
                    nnoburst += 1
                elif mark > burst:
                    nplos_flag += 1
                    flags.append((fn, name, 'mark %.1f ms after burst' % ((mark - burst) * 1000)))
            if p0 not in P.VOWELS and p1 in stat_spec:            # [C V]: end mark vs stationary
                V, mk = p1, r['bounds'][2]
            elif p1 not in P.VOWELS and p0 in stat_spec:          # [V C]: start mark vs stationary
                V, mk = p0, r['bounds'][0]
            else:
                V = None
            if V is not None:
                f = feats(r['base'])
                joins.append(float(np.linalg.norm(f.spec[f.t2f(mk)] - stat_spec[V])))
                join_by.setdefault(V, []).append(joins[-1])
        for p, (_, b, e) in zip(ph, seg):
            if p in P.VOWELS:
                loud.setdefault(p, []).extend(rms_frames(x, sr, b + 0.2 * (e - b), e - 0.2 * (e - b)))

    out.append('vowel-vowel: %d units, dip median %.2f dB, p90 %.2f dB, flagged %d'
               % (len(vv_dips), pct(vv_dips, 50), pct(vv_dips, 90), sum(d > 3 for d in vv_dips)))
    out.append('plosive marks: %d checked, %d no burst found, %d flagged'
               % (nplos, nnoburst, nplos_flag))
    out.append('join (stationary vs unit spectrum): n=%d median %.2f p90 %.2f'
               % (len(joins), pct(joins, 50), pct(joins, 90)))
    out.append('  per vowel (median): ' + ', '.join('%s %.2f' % (v, pct(d, 50)) for v, d in sorted(join_by.items())))
    out.append('loudness spread, 10-90% of 20 ms RMS in middle 60% of vowels:')
    for v in sorted(loud):
        if len(loud[v]) >= 2:
            a = np.array(loud[v])
            out.append('  %-4s n=%-5d spread %.1f dB' % (v, len(a), pct(a, 90) - pct(a, 10)))
    out.append('FLAGS (%d):' % len(flags))
    out += ['  %s | %s | %s' % fl for fl in sorted(flags)]
    return out


def main(argv):
    if not argv:
        print('usage: python -I tools/qa.py <seg folder> [<seg folder> ...]')
        return 0
    for folder in argv:
        try:
            print('\n'.join(check_folder(folder)))
        except Exception as ex:  # noqa: BLE001
            print('== %s: failed: %s' % (folder, ex))
        print()
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(errors='replace')
    sys.exit(main(sys.argv[1:]))
