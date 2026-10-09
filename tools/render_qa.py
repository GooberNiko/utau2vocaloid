"""Check a rendered song (wav + .msd from the VOCALOID4 Editor for Developer) for audible glitches.

    python -I tools/render_qa.py song.wav [song.wav ...]

Uses the .msd next to each wav to know which unit plays where, and reports:
  * doubled bursts: a plosive (k, t, p, g, d, b, ts, ch...) whose release plays twice across [V C]+[C V]
  * join jumps: level or timbre jumps where two units meet inside the same vowel (incl. the stationary)
  * clicks: single-sample jumps far above the local level
"""
import collections
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from u2v import audio  # noqa: E402
from msd import read_msd  # noqa: E402

PLOSIVES = {'k', "k'", 'g', "g'", 't', "t'", 'd', "d'", 'p', "p'", 'b', "b'", 'ts', 'tS', 'dz', 'dZ'}
VOWELS = {'a', 'i', 'M', 'e', 'o', 'N\\'}


def bursts(x, sr, t0, t1, min_rise=12.0):
    """Times of sharp high-frequency onsets in [t0, t1] (plosive releases). A voice coming in after the
    aspiration also raises the high band, but less than the whole level: only rises that are mostly
    high-frequency count."""
    hp = np.diff(x, prepend=0)
    w, hop = int(0.004 * sr), 0.002
    ts = np.arange(t0, t1, hop)
    en = np.array([10 * np.log10(np.mean(hp[int(t * sr):int(t * sr) + w] ** 2) + 1e-12) for t in ts])
    raw = np.array([10 * np.log10(np.mean(x[int(t * sr):int(t * sr) + w] ** 2) + 1e-12) for t in ts])
    if len(en) < 6:
        return []
    rise = en[3:] - en[:-3]
    rise_raw = raw[3:] - raw[:-3]
    out, last = [], -1.0
    for k in np.where((rise > min_rise) & (rise > rise_raw + 3))[0]:
        t = ts[k + 3]
        if t - last > 0.012 and en[k + 3] > np.max(en) - 30:
            out.append(t)
        last = t
    return out


def check(wav):
    x, sr = audio.read_wav(wav)
    ft = audio.Features(x, sr)
    recs = read_msd(os.path.splitext(wav)[0] + '.msd')
    issues = []
    # doubled bursts: consecutive records of one plosive phoneme coming from two different units
    k = 0
    while k < len(recs):
        t0, t1, ph, unit = recs[k]
        j = k
        while j + 1 < len(recs) and recs[j + 1][2] == ph and abs(recs[j + 1][0] - recs[j][1]) < 1e-3:
            j += 1
        if ph.split('#')[0] in PLOSIVES and j > k:
            units = {recs[q][3] for q in range(k, j + 1)}
            b = bursts(x, sr, t0, recs[j][1] + 0.01)
            # doubled only if the releases come from different units (one unit's own k can have an
            # aspiration + voice onset that looks like a second release)
            src = {recs[min(range(k, j + 1), key=lambda q: 0 if recs[q][0] <= t < recs[q][1] + 0.01 else 1)][3]
                   for t in b}
            if len(b) >= 2 and len(units) >= 2 and len(src) >= 2:
                issues.append((t0, 'double burst', ph, ' + '.join('[%s %s]' % u for u in sorted(units)),
                               '%d releases at %s' % (len(b), ', '.join('%.3f' % v for v in b))))
        k = j + 1
    # join jumps inside a vowel
    for (a0, a1, pa, ua), (b0, b1, pb, ub) in zip(recs, recs[1:]):
        if pa != pb or ua == ub or pa.split('#')[0] not in VOWELS or abs(b0 - a1) > 1e-3:
            continue
        if a1 - a0 < 0.03 or b1 - b0 < 0.03:
            continue
        ea = audio.rms_db(x[int((b0 - 0.025) * sr):int((b0 - 0.005) * sr)])
        eb = audio.rms_db(x[int((b0 + 0.005) * sr):int((b0 + 0.025) * sr)])
        # timbre change across the join, relative to the vowel's own motion just before and after it
        def jump(t):
            return float(np.linalg.norm(ft.spec[ft.t2f(t + 0.01), 4:] - ft.spec[ft.t2f(t - 0.01), 4:]))
        ds = jump(b0)
        around = np.median([jump(b0 + o) for o in (-0.04, -0.03, 0.03, 0.04)]) + 0.5
        if abs(eb - ea) > 3 or ds > 2.5 * around:
            kind = 'stationary join' if ub[0] == ub[1] or ua[0] == ua[1] else 'unit join'
            issues.append((b0, kind, pa, '[%s %s] -> [%s %s]' % (ua + ub),
                           '%+.1f dB, timbre jump %.1f (%.1fx the motion around it)' % (eb - ea, ds, ds / around)))
    # clicks
    dx = np.abs(np.diff(x))
    w = int(0.01 * sr)
    loc = np.convolve(dx, np.ones(w) / w, 'same') + 1e-4
    last = -1
    for i in np.where((dx > 15 * loc) & (np.abs(x[1:]) > 0.02))[0]:
        if i - last > sr * 0.05:
            t = i / sr
            u = next(('[%s %s]' % r[3] for r in recs if r[0] <= t < r[1]), '-')
            issues.append((t, 'click', '', u, ''))
        last = i
    return recs, sorted(issues)


def main():
    for wav in sys.argv[1:]:
        recs, issues = check(wav)
        print('== %s: %d unit records, %d issues' % (os.path.basename(wav), len(recs), len(issues)))
        kinds = collections.Counter(i[1] for i in issues)
        print('   ' + ', '.join('%s %d' % kv for kv in kinds.most_common()))
        units = collections.Counter(i[3] for i in issues if i[1] != 'click')
        if units:
            print('   units most involved: ' + ', '.join('%s x%d' % kv for kv in units.most_common(12)))
        for t, kind, ph, unit, info in issues[:60]:
            print('   %7.3fs  %-16s %-4s %-24s %s' % (t, kind, ph, unit, info))
    return 0


if __name__ == '__main__':
    sys.exit(main())
