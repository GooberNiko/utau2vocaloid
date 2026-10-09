"""WAV I/O, resampling and the frame features used to place phoneme boundaries (numpy only)."""
import struct
import wave

import numpy as np

HOP = 0.005      # feature hop in seconds


def read_wav(path):
    """Return (mono float32 samples in [-1,1], sample rate). Handles PCM 8/16/24/32 and float32."""
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError('not a RIFF/WAVE file: %s' % path)
    pos, fmt, raw = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack('<I', data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b'fmt ':
            fmt = struct.unpack('<HHIIHH', body[:16])
        elif cid == b'data':
            raw = body
        pos += 8 + size + (size & 1)
    if fmt is None or raw is None:
        raise ValueError('missing fmt/data chunk: %s' % path)
    tag, ch, sr, _, align, bits = fmt
    if tag == 0xFFFE:
        tag = 3 if bits == 32 and b'\x03\x00\x00\x00\x00\x00\x10\x00' in data[:200] else 1
    n = len(raw) // align
    raw = raw[:n * align]
    if tag == 3 and bits == 32:
        x = np.frombuffer(raw, '<f4').astype(np.float32)
    elif tag == 3 and bits == 64:
        x = np.frombuffer(raw, '<f8').astype(np.float32)
    elif bits == 8:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    elif bits == 16:
        x = np.frombuffer(raw, '<i2').astype(np.float32) / 32768
    elif bits == 24:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float32) / (1 << 23)
    elif bits == 32:
        x = np.frombuffer(raw, '<i4').astype(np.float32) / 2147483648
    else:
        raise ValueError('unsupported wav format (%d bit, tag %d): %s' % (bits, tag, path))
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def resample(x, sr_from, sr_to):
    if sr_from == sr_to:
        return x
    n_out = int(round(len(x) * sr_to / sr_from))
    # FFT resampling with a short fade to avoid wrap-around clicks
    X = np.fft.rfft(x)
    n_bins = n_out // 2 + 1
    Y = np.zeros(n_bins, dtype=complex)
    k = min(len(X), n_bins)
    Y[:k] = X[:k]
    y = np.fft.irfft(Y, n_out) * (n_out / len(x))
    return y.astype(np.float32)


def cut(x, a, b, dither=1e-4):
    """x[a:b] where indices outside the signal become very quiet noise (padded silence)."""
    out = (np.random.default_rng(a & 0xffff).standard_normal(b - a) * dither).astype(np.float32)
    lo, hi = max(a, 0), min(b, len(x))
    if hi > lo:
        out[lo - a:hi - a] = x[lo:hi]
    return out


def write_wav(path, x, sr):
    y = np.clip(np.round(x * 32767), -32768, 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())


def band_edges(nfft):
    """FFT bin edges of the 24 log-spaced bands of Features.spec."""
    return np.unique(np.geomspace(4, nfft // 2, 25).astype(int))


def band_centres_hz(sr, win=0.040):
    nfft = 1 << int(np.ceil(np.log2(2 * int(round(win * sr)))))
    e = band_edges(nfft)
    return np.sqrt(np.maximum(e[:-1], 1) * e[1:]) * sr / nfft


class Features:
    """Frame features over a whole file: energy (dB), periodicity (voicing), spectral frames."""

    def __init__(self, x, sr):
        self.sr = sr
        hop = int(round(HOP * sr))
        win = int(round(0.040 * sr))
        n = max(1, (len(x) - win) // hop + 1) if len(x) >= win else 1
        xp = np.pad(x, (0, max(0, win - len(x))))
        idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
        frames = xp[idx] * np.hanning(win)[None, :]
        e = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
        self.energy = 20 * np.log10(e + 1e-9)
        # normalized autocorrelation peak in 70..900 Hz
        nfft = 1 << int(np.ceil(np.log2(2 * win)))
        F = np.fft.rfft(frames, nfft)
        ac = np.fft.irfft(np.abs(F) ** 2, nfft)[:, :win]
        ac0 = ac[:, :1] + 1e-12
        lo, hi = int(sr / 900), min(win - 1, int(sr / 70))
        acn = ac[:, lo:hi] / ac0
        # compensate the window's own decay
        wac = np.correlate(np.hanning(win), np.hanning(win), 'full')[win - 1:][lo:hi]
        acn = acn / (wac / wac[0])
        self.period = np.clip(np.max(acn, axis=1), 0, 1)
        self.lag = (np.argmax(acn, axis=1) + lo).astype(float)
        # log spectra (24 bands) for spectral change
        mag = np.abs(F[:, :nfft // 2])
        edges = band_edges(nfft)
        bands = np.stack([mag[:, a:b].mean(axis=1) for a, b in zip(edges[:-1], edges[1:])], axis=1)
        self.spec = np.log(bands + 1e-6)
        hf = mag[:, int(3000 * nfft / sr):].mean(axis=1)
        lf = mag[:, int(80 * nfft / sr):int(1000 * nfft / sr)].mean(axis=1)
        self.hf_ratio = np.log((hf + 1e-9) / (lf + 1e-9))
        peak = np.percentile(self.energy, 99)
        floor = np.percentile(self.energy, 5)
        # silence must also be well below the peak: some banks (formant synths) never go quiet
        self.sil_thr = min(max(floor + 8, peak - 45), peak - 35)
        self.peak = peak
        k = 30                                         # +-150 ms local peak
        ep = np.pad(self.energy, k, mode='edge')
        local = np.lib.stride_tricks.sliding_window_view(ep, 2 * k + 1).max(axis=1)
        self.voiced = (self.period > 0.45) & (self.energy > np.maximum(peak - 40, local - 20)) &                       (self.hf_ratio < 0)
        self.n = n

    def t2f(self, t):
        return int(np.clip(round((t - 0.020) / HOP), 0, self.n - 1))

    def f2t(self, f):
        return f * HOP + 0.020          # centre of the 40 ms window

    def silent(self, f):
        return self.energy[f] < self.sil_thr

    def smooth_energy(self, f0, f1, k=3):
        e = self.energy[f0:f1 + 1]
        if len(e) < 2 * k + 1:
            return e
        return np.convolve(np.pad(e, k, mode='edge'), np.ones(2 * k + 1) / (2 * k + 1), 'valid')


def _nearest(cands, h):
    return min(cands, key=lambda c: abs(c - h)) if cands else None


def find_boundary(ft, x_cls, y_cls, hint, lo, hi, y_ph=None):
    """Place the transition between phoneme classes x_cls -> y_cls near `hint` inside [lo, hi] (seconds).

    Returns (time, detected?)."""
    f0, f1, fh = ft.t2f(lo), ft.t2f(hi), ft.t2f(hint)
    if f1 - f0 < 2:
        return hint, False
    rng = range(f0 + 1, f1 + 1)
    v = ft.voiced
    s = ft.energy < ft.sil_thr
    unv = ('uplos', 'ufric')
    cand = None
    if y_cls == 'sil':
        cand = _nearest([f for f in rng if not s[f - 1] and s[f] and s[f:f + 4].all()], fh)
    elif x_cls == 'sil':
        ons = [f for f in rng if s[f - 1] and not s[f] and not s[f:f + 3].any()]
        cand = _nearest(ons, fh)
        if cand is not None and y_cls == 'uplos':
            cand = max(f0, cand - 4)          # closure sits in silence: start 20 ms before the burst
    elif x_cls in ('vowel', 'vcons', 'vplos') and y_cls in unv:
        cand = _nearest([f for f in rng if v[f - 1] and not v[f] and not v[f:f + 3].any()], fh)
        # voicing can bleed on into a closure: the vowel really ends where its level falls away
        # (closures only: a breathy h/f/hy is meant to carry some of the vowel's energy)
        closure = y_cls == 'uplos' or y_ph in ('ts', 'tS')
        c0 = cand if cand is not None else fh
        pl = ft.energy[max(f0, c0 - 25):max(f0 + 1, c0 - 8)]
        if closure and len(pl) >= 4:
            plateau = float(np.median(pl))
            drop = [f for f in range(max(f0 + 1, c0 - 25), min(f1 + 1, c0 + 1)) if ft.energy[f] < plateau - 6]
            if drop and (cand is None or drop[0] < cand):
                cand = drop[0]
    elif x_cls in unv and y_cls in ('vowel', 'vcons', 'vplos'):
        cand = _nearest([f for f in rng if not v[f - 1] and v[f] and v[f:f + 3].all()], fh)
    elif x_cls == 'vowel' and y_cls == 'vowel':
        sp = ft.spec[f0:f1 + 1]
        if len(sp) > 8:
            dist = np.linalg.norm(sp[4:] - sp[:-4], axis=1)
            w = np.exp(-0.5 * ((np.arange(len(dist)) + 2 + f0 - fh) / 12.0) ** 2)
            cand = f0 + 2 + int(np.argmax(dist * w))
    if cand is None and x_cls != y_cls and 'sil' not in (x_cls, y_cls):
        # energy slope: falling into a consonant, rising into a vowel
        e = ft.smooth_energy(f0, f1)
        d = np.diff(e)
        if len(d):
            w = np.exp(-0.5 * ((np.arange(len(d)) + 1 + f0 - fh) / 8.0) ** 2)
            falling = x_cls == 'vowel' or (x_cls != 'vowel' and y_cls != 'vowel' and x_cls == 'vcons')
            score = (-d if falling else d) * w
            if score.max() > 0.5:
                cand = f0 + int(np.argmax(score)) + 1
    if cand is None:
        return hint, False
    return ft.f2t(cand) - HOP / 2, True


def stable_region(ft, t0, t1):
    """Most pitch-stable sub-span (>=40% of the segment) of a sustained segment, as (begin, end)."""
    f0, f1 = ft.t2f(t0), ft.t2f(t1)
    if f1 - f0 < 10:
        return t0 + 0.25 * (t1 - t0), t1 - 0.15 * (t1 - t0)
    lag = ft.lag[f0:f1]
    w = max(8, int(0.4 * (f1 - f0)))
    best, bi = None, 0
    for i in range(0, len(lag) - w + 1, 2):
        seg = lag[i:i + w]
        score = np.std(seg / np.median(seg)) - 0.002 * np.mean(ft.energy[f0 + i:f0 + i + w])
        if best is None or score < best:
            best, bi = score, i
    return ft.f2t(f0 + bi), ft.f2t(f0 + bi + w)


def sustained_extent(ft, t0, max_len=2.5, max_dist=2.2, max_drop=4.0, backward=False):
    """How far a sustained sound starting at t0 really lasts in the recording (it usually runs past the
    oto region). Stops where the spectrum moves away from its start (next syllable, other vowel),
    the level drops or voicing ends. backward=True walks towards earlier times from t0 instead.
    Returns (begin, end) in seconds."""
    f0 = ft.t2f(t0)
    step = -1 if backward else 1
    if not backward:
        # a CV sample's vowel is still swelling right after the consonant: start at full level
        top = float(ft.energy[f0:min(ft.n, f0 + 20)].max(initial=-200))
        while f0 < min(ft.n - 1, ft.t2f(t0) + 20) and ft.energy[f0] < top - 3:
            f0 += 1
    lo, hi = (max(0, f0 - 11), f0 + 1) if backward else (f0, min(ft.n, f0 + 12))
    if hi - lo < 4:
        return t0, t0
    spec = ft.spec[:, 4:]                  # the lowest bands sit below the voice: mostly room noise
    ref = spec[lo:hi].mean(axis=0)
    eref = float(np.median(ft.energy[lo:hi]))
    # how much this recording wobbles frame to frame on its own: the limit adapts to it
    lim = max(max_dist, 2.5 * float(np.median([np.linalg.norm(spec[k] - ref) for k in range(lo, hi)])))
    last, bad, f = f0, 0, f0
    while 0 <= f < ft.n and abs(f - f0) < int(max_len / HOP):
        # compared with the last ~60 ms (slow natural drift is fine, a new syllable is not) and,
        # loosely, with the start (so it can never glide into another vowel)
        recent = spec[f - 12 * step:f - 2 * step:step] if abs(f - f0) > 12 else spec[lo:hi]
        near = np.linalg.norm(spec[f] - recent.mean(axis=0)) if len(recent) else 0.0
        steady = near < lim and np.linalg.norm(spec[f] - ref) < 2 * lim
        if ft.voiced[f] and ft.energy[f] > eref - max_drop and steady:
            last, bad = f, 0
            eref = max(eref, float(ft.energy[f]) - 2) if abs(f - f0) < 20 else eref
        else:
            bad += 1
            if bad >= 3:
                break
        f += step
    if backward:
        return ft.f2t(last) + 0.02, ft.f2t(f0)
    return ft.f2t(f0), ft.f2t(last) - 0.02


def burst_time(x, sr, b, e, min_rise=10.0):
    """Release burst of a plosive/affricate inside [b, e]: the sharpest rise of high-passed energy.
    None if there is no clear burst."""
    w, hop = int(0.006 * sr), 0.002
    ts = np.arange(b, e, hop)
    if len(ts) < 4:
        return None
    hp = np.diff(cut(x, int(b * sr) - 1, int(e * sr) + w), prepend=0)
    en = np.array([10 * np.log10(np.mean(hp[int((t - b) * sr):int((t - b) * sr) + w] ** 2) + 1e-12) for t in ts])
    rise = en[2:] - en[:-2]
    k = int(np.argmax(rise))
    return float(ts[k + 2]) if rise[k] >= min_rise else None


def f0_track(x, sr, t0, t1, hop=0.005, win=0.03):
    """Pitch of a sustained sound, searched only near its own period (no octave jumps).
    Returns (times, f0 Hz)."""
    seg = x[int(t0 * sr):int(t1 * sr)]
    n = min(len(seg), 8192)
    if n < int(0.05 * sr):
        return np.array([]), np.array([])
    ac = np.correlate(seg[:n], seg[:n], 'full')[n - 1:]
    lo, hi = int(sr / 800), int(sr / 70)
    L0 = lo + int(np.argmax(ac[lo:hi]))
    w, l1, l2 = int(win * sr), int(0.8 * L0), int(1.25 * L0)
    ts, out = [], []
    for t in np.arange(t0, t1, hop):
        a = x[int(t * sr):int(t * sr) + w + l2 + 1]
        if len(a) < w + l2 + 1:
            break
        r = np.array([np.dot(a[:w], a[L:L + w]) / (np.linalg.norm(a[:w]) * np.linalg.norm(a[L:L + w]) + 1e-9)
                      for L in range(l1, l2)])
        k = int(np.argmax(r))
        L = l1 + k
        if 0 < k < len(r) - 1:
            L += 0.5 * (r[k - 1] - r[k + 1]) / (r[k - 1] - 2 * r[k] + r[k + 1] + 1e-12)
        ts.append(t)
        out.append(sr / L)
    return np.array(ts), np.array(out)


def clean_span(x, sr, t0, t1, max_dev=30.0, max_step=15.0, max_dip=2.5):
    """Longest stretch of [t0, t1] with no seam: a pitch wobble/jump or level dip such as a re-attacked
    syllable glued into a held vowel. VOCALOID loops stationaries, so a seam would repeat audibly.
    Natural drift (measured up to ~26 cents off the 100 ms mean, ~10 cents per 5 ms) is not a seam."""
    ts, f = f0_track(x, sr, t0, t1)
    if len(f) < 25:
        return t0, t1
    c = 1200 * np.log2(f / np.median(f))
    dev = np.abs(c - np.convolve(c, np.ones(21) / 21, 'same'))
    dev[:10] = dev[-10:] = 0
    step = np.abs(np.diff(c, prepend=c[0]))
    env = np.array([rms_db(x[int(t * sr):int(t * sr) + int(0.02 * sr)]) for t in ts])
    dip = np.convolve(env, np.ones(21) / 21, 'same') - env
    dip[:10] = dip[-10:] = 0
    bad = (dev > max_dev) | (step > max_step) | (dip > max_dip)
    best, start = (t0, t0), None
    for k in range(len(ts) + 1):
        if k < len(ts) and not bad[k]:
            start = k if start is None else start
        elif start is not None:
            a, b = ts[start] + 0.03, ts[k - 1] - 0.03
            if b - a > best[1] - best[0]:
                best = (a, b)
            start = None
    return best


def flat_span(x, sr, t0, t1, tol=3.0, hop=0.01, win=0.02):
    """Longest stretch of [t0, t1] whose level stays within tol dB of the stretch's median: a loop cut
    from a swelling or fading vowel jumps in level at every repeat."""
    ts = np.arange(t0, t1 - win, hop)
    if len(ts) < 5:
        return t0, t1
    lv = np.array([rms_db(x[int(t * sr):int(t * sr) + int(win * sr)]) for t in ts])
    med = np.median(lv)
    best, start = (t0, t0), None
    ok = np.abs(lv - med) <= tol
    for k in range(len(ts) + 1):
        if k < len(ts) and ok[k]:
            start = k if start is None else start
        elif start is not None:
            a, b = ts[start], ts[k - 1] + win
            if b - a > best[1] - best[0]:
                best = (a, b)
            start = None
    return best


def devoice(y, sr, a, b, fade=0.008, n=512, hop=128, smooth_hz=700, max_db=None):
    """Replace y[a:b] with aperiodic noise of the same short-time spectral envelope. VOCALOID plays
    unvoiced phonemes (h, hy, f) back without resynthesising them at the note's pitch, so a breathy,
    half-voiced recorded "h" would keep the harmonics of the pitch it was recorded at."""
    f = int(fade * sr)
    a0, b0 = max(0, a - f), min(len(y), b + f)
    seg = y[a0:b0].astype(np.float64)
    if len(seg) < n:
        return y
    win = np.hanning(n)
    starts = range(0, len(seg) - n + 1, hop)
    k = max(1, int(smooth_hz / (sr / n)))
    rng = np.random.default_rng(a)
    out, norm = np.zeros(len(seg)), np.zeros(len(seg))
    for s0 in starts:
        mag = np.abs(np.fft.rfft(seg[s0:s0 + n] * win))
        env = np.exp(np.convolve(np.log(mag + 1e-9), np.ones(k) / k, 'same'))   # harmonics smoothed away
        frame = np.fft.irfft(env * np.exp(2j * np.pi * rng.random(len(env))), n) * win
        out[s0:s0 + n] += frame
        norm[s0:s0 + n] += win ** 2
    out /= np.maximum(norm, 1e-3)
    rms_in, rms_out = np.sqrt(np.mean(seg ** 2)) + 1e-12, np.sqrt(np.mean(out ** 2)) + 1e-12
    out *= rms_in / rms_out
    if max_db is not None and rms_db(out) > max_db:
        out *= 10 ** ((max_db - rms_db(out)) / 20)      # a voiced "h" is vowel-loud; breath is not
    w = np.ones(len(seg))
    ramp = np.linspace(0, 1, f) if f else np.array([])
    if f:
        w[:f], w[-f:] = ramp, ramp[::-1]
    y = y.copy()
    y[a0:b0] = (seg * (1 - w) + out * w).astype(np.float32)
    return y


def band_eq(x, sr, centres_hz, gains_db):
    """Zero-phase EQ: gains (dB) at band centres, interpolated over log frequency."""
    n = 1 << int(np.ceil(np.log2(max(len(x), 2))))
    X = np.fft.rfft(x, n)
    f = np.maximum(np.fft.rfftfreq(n, 1 / sr), 1.0)
    g = np.interp(np.log(f), np.log(centres_hz), gains_db)
    return np.fft.irfft(X * 10 ** (g / 20), n)[:len(x)].astype(np.float32)


def boost_fundamental(x, sr, f0, gain_db=6.0, width_oct=0.8):
    """Raise the first harmonic (a bell around f0), same overall level. The DBTool analyses some units
    whose 2nd harmonic dominates an octave too high, and VOCALOID then renders them with a buzzing
    half-pitch undertone; +6 dB on the fundamental fixed its analysis (Niko's "e n": 274 -> 137 Hz)."""
    n = 1 << int(np.ceil(np.log2(max(len(x), 2))))
    X = np.fft.rfft(x, n)
    f = np.maximum(np.fft.rfftfreq(n, 1 / sr), 1.0)
    g = gain_db * np.exp(-0.5 * (np.log2(f / f0) / (width_oct / 2.355)) ** 2)
    y = np.fft.irfft(X * 10 ** (g / 20), n)[:len(x)]
    y *= np.sqrt(np.mean(np.square(x, dtype=np.float64)) / (np.mean(np.square(y, dtype=np.float64)) + 1e-20))
    return y.astype(np.float32)


def high_shelf(x, sr, gain_db, fc=2000.0):
    """Zero-phase high shelf (smooth 1-octave transition around fc)."""
    n = 1 << int(np.ceil(np.log2(max(len(x), 2))))
    X = np.fft.rfft(x, n)
    f = np.fft.rfftfreq(n, 1 / sr)
    w = np.clip((np.log2(np.maximum(f, 1) / fc) + 0.5), 0, 1)
    return np.fft.irfft(X * 10 ** (gain_db * w / 20), n)[:len(x)].astype(np.float32)


def dip_at(x, sr, t, reach=0.08, ctx=0.07, hop=0.005, win=0.02):
    """Level envelope around t: how far (dB) it sinks below the line joining the levels just before
    and after. Returns (depth_db, times, gain_db) where gain_db would lift the envelope onto that line."""
    n, w = int(hop * sr), int(win * sr)
    ts = np.arange(t - reach - ctx, t + reach + ctx, hop)
    env = np.array([rms_db(cut(x, int(u * sr) - w // 2, int(u * sr) + w // 2)) for u in ts])
    pre, post = ts < t - reach, ts > t + reach
    if not pre.any() or not post.any():
        return 0.0, ts, np.zeros_like(ts)
    a, b = np.median(env[pre]), np.median(env[post])
    line = np.interp(ts, [t - reach, t + reach], [a, b])
    gain = np.where(pre | post, 0.0, np.clip(line - env, 0, None))
    return float(gain.max()), ts, np.convolve(gain, np.ones(5) / 5, 'same')


def fill_dip(x, sr, t, max_db=12.0):
    """Lift a level dip at t (a re-attacked vowel in a VCV recording) onto the surrounding level."""
    depth, ts, gain = dip_at(x, sr, t)
    if depth < 1.5:
        return x, depth
    g = 10 ** (np.clip(np.interp(np.arange(len(x)) / sr, ts, gain, left=0, right=0), 0, max_db) / 20)
    return (x * g).astype(np.float32), depth


def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9) if len(x) else -120.0


def mean_f0(ft, t0, t1):
    f0, f1 = ft.t2f(t0), ft.t2f(t1)
    lag = ft.lag[f0:f1 + 1][ft.voiced[f0:f1 + 1]]
    return float(ft.sr / np.median(lag)) if len(lag) else 0.0
