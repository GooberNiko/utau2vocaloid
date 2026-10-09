"""Render a test song with a converted DB in a *copy* of the VOCALOID4 Editor for Developer.

    python -I tools/render.py <editor copy folder> <DB folder\\Name> <out.wav> [--pitch 64] [--song test]

The editor copy needs its own DB_Dev.ini (it is rewritten here with one voice: the DB under test), so
the installed editor and its ini are never touched. The editor writes <out>.msd next to the wav: the
list of units it used, see tools/msd.py.
"""
import argparse
import os
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import phonemes as P  # noqa: E402

warnings.filterwarnings('ignore')
TPQ = 480                          # ticks per quarter note
ROMA_TO_KANA = {}
for k, v in P.KANA.items():
    if len(k) <= 2 and v not in ROMA_TO_KANA:
        ROMA_TO_KANA[v] = k

# phrases of romaji; '-' is a rest, ':' after a syllable makes it long (held notes test stationaries)
SONGS = {
    'test': [
        'ka ki ku ke ko sa shi su se so -', 'ta chi tsu te to na ni nu ne no -',
        'ha hi fu he ho ma mi mu me mo -', 'ya yu yo ra ri ru re ro wa n -',
        'ga gi gu ge go za ji zu ze zo -', 'da de do ba bi bu be bo pa pi pu pe po -',
        'kya kyu kyo sha shu sho cha chu cho -', 'nya nyu nyo hya hyu hyo mya myu myo rya ryu ryo -',
        'a i u e o a: -', 'o o a a i i e e u u -', 'a: i: u: e: o: n: -',
        'ka n ka n na n ma n ta n -', 'sa ka na ta be ta i -', 'o n ga ku ga su ki -',
        'n: a -', 'a n: ba n: ta n: ka n: ni -', 'o n: ma a n: na a n: ga -',
    ],
    # extras: breaths (br1.. on their own note), a breath out after a vowel (brE), a breath in (brI),
    # rolled r (rr) alone and before a vowel (rra)
    'extras': [
        'br1 sa ku ra brE -', 'br2 a o i so ra brI -', 'br3 ka ze ga fu ku -', 'a: brE -',
        'rr a rra rri rre -', 'ko rro ga ru -',
    ],
}


# English: (lyric, VOCALOID English phonemes) per note; '-' is a rest, a trailing ' ~' makes the note long
SONGS_EN = {
    'en': [
        [('hel', 'h e'), ('lo', 'l @U'), ('world', 'w @r l d'), '-'],
        [('this', 'D I s'), ('is', 'I z'), ('a', '@'), ('test', 't e s t'), '-'],
        [('star', 's t Q'), ('light', 'l aI t'), ('blue', 'b l u:'), ('sky', 's k aI ~'), '-'],
        [('cat', 'k { t'), ('cut', 'k V t'), ('book', 'b U k'), ('boy', 'b OI'), ('now', 'n aU ~'), '-'],
        [('sing', 's I N'), ('ing', 'I N'), ('the', 'D @'), ('song', 's O: N'), ('for', 'f O:'), ('you', 'j u:'), '-'],
        [('day', 'd eI ~'), ('and', '{ n d'), ('night', 'n aI t'), ('we', 'w i:'), ('go', 'g @U ~'), '-'],
        [('spring', 's p r I N'), ('think', 'T I N k'), ('judge', 'dZ V dZ'), ('church', 'tS @r tS'), '-'],
        [('a', 'Q ~'), ('e', 'i: ~'), ('o', '@U ~'), ('u', 'u: ~'), '-'],
    ],
}


def syllable_phonemes(rom):
    if rom in P.EXTRAS:                       # br1, brE, rr ... sung as their own note
        return [rom]
    if rom.startswith('rr') and P.ja_syllable(rom[2:]) and len(P.ja_syllable(rom[2:])) == 1:
        return ['rr'] + P.ja_syllable(rom[2:])          # "rra": rolled r into a vowel
    if rom == 'n':
        return ['N\\']
    ph = P.ja_syllable(rom)
    return ph


def build_notes(song, pitch=64, step=TPQ // 2, suffix=''):
    notes, t = [], TPQ * 4                      # one bar of lead-in
    melody = [0, 2, 4, 5, 7, 5, 4, 2]
    if song in SONGS_EN:
        for line in SONGS_EN[song]:
            for k, tok in enumerate(line):
                if tok == '-':
                    t += TPQ * 2
                    continue
                lyric, ph = tok
                long_ = ph.endswith(' ~')
                ph = ph[:-2] if long_ else ph
                dur = TPQ * 3 if long_ else step
                notes.append((t, dur, pitch + melody[k % len(melody)] - 5, lyric, ph))
                t += dur
        return notes, t + TPQ * 4
    for line in SONGS[song]:
        for k, tok in enumerate(line.split()):
            if tok == '-':
                t += TPQ * 2
                continue
            long_ = tok.endswith(':')
            rom = tok.rstrip(':')
            ph = syllable_phonemes(rom)
            if ph is None:
                continue
            dur = TPQ * 4 if long_ else step
            ph = [p + suffix if p != 'Sil' else p for p in ph]          # EVEC colour: "a#1"
            notes.append((t, dur, pitch + melody[k % len(melody)], ROMA_TO_KANA.get(rom, rom), ' '.join(ph)))
            t += dur
    return nasal_variants(notes, suffix), t + TPQ * 4


def nasal_variants(notes, suffix=''):
    """ん sung before a consonant is m / n / N / N' (as the editor converts it): "a n ba" -> [a m] [m b]."""
    out = list(notes)
    plain = 'N' + chr(92) + suffix
    for k, (t, dur, n, lyric, ph) in enumerate(out[:-1]):
        nt, _, _, _, nph = out[k + 1]
        if ph == plain and nt == t + dur:                      # the next note follows without a rest
            nxt = nph.split()[0][:len(nph.split()[0]) - len(suffix)] if suffix else nph.split()[0]
            out[k] = (t, dur, n, lyric, P.nasal_before(nxt) + suffix)
    return out


def write_vsqx(path, notes, end, pc=0, name='Test', comp_id=None):
    note_xml = []
    for t, dur, n, lyric, ph in notes:
        note_xml.append(
            '<note><t>%d</t><dur>%d</dur><n>%d</n><v>64</v><y><![CDATA[%s]]></y><p lock="1"><![CDATA[%s]]></p>'
            '<nStyle><v id="accent">50</v><v id="bendDep">0</v><v id="bendLen">0</v><v id="decay">50</v>'
            '<v id="fallPort">0</v><v id="opening">127</v><v id="risePort">0</v><v id="vibLen">0</v>'
            '<v id="vibType">0</v></nStyle></note>' % (t, dur, n, lyric, ph))
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
           '<vsq4 xmlns="http://www.yamaha.co.jp/vocaloid/schema/vsq4/" '
           'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
           'xsi:schemaLocation="http://www.yamaha.co.jp/vocaloid/schema/vsq4/ vsq4.xsd">'
           '<vender><![CDATA[Yamaha corporation]]></vender><version><![CDATA[4.0.0.3]]></version>'
           '<vVoiceTable><vVoice><bs>0</bs><pc>%d</pc><id><![CDATA[%s]]></id><name><![CDATA[%s]]></name>'
           '<vPrm><bre>0</bre><bri>0</bri><cle>0</cle><gen>0</gen><ope>0</ope></vPrm></vVoice></vVoiceTable>'
           '<mixer><masterUnit><oDev>0</oDev><rLvl>0</rLvl><vol>0</vol></masterUnit>'
           '<vsUnit><tNo>0</tNo><iGin>0</iGin><sLvl>-898</sLvl><sEnable>0</sEnable><m>0</m><s>0</s>'
           '<pan>64</pan><vol>0</vol></vsUnit>'
           '<monoUnit><iGin>0</iGin><sLvl>-898</sLvl><sEnable>0</sEnable><m>0</m><s>0</s><pan>64</pan>'
           '<vol>0</vol></monoUnit><stUnit><iGin>0</iGin><m>0</m><s>0</s><vol>-129</vol></stUnit></mixer>'
           '<masterTrack><seqName><![CDATA[utau2vocaloid test]]></seqName><comment><![CDATA[test]]></comment>'
           '<resolution>480</resolution><preMeasure>1</preMeasure>'
           '<timeSig><m>0</m><nu>4</nu><de>4</de></timeSig><tempo><t>0</t><v>12000</v></tempo></masterTrack>'
           '<vsTrack><tNo>0</tNo><name><![CDATA[Test]]></name><comment><![CDATA[Track]]></comment>'
           '<vsPart><t>1920</t><playTime>%d</playTime><name><![CDATA[Test]]></name>'
           '<comment><![CDATA[test]]></comment>'
           '<sPlug><id><![CDATA[ACA9C502-A04B-42b5-B2EB-5CEA36D16FCE]]></id>'
           '<name><![CDATA[VOCALOID2 Compatible Style]]></name><version><![CDATA[3.0.0.1]]></version></sPlug>'
           '<pStyle><v id="accent">50</v><v id="bendDep">8</v><v id="bendLen">0</v><v id="decay">50</v>'
           '<v id="fallPort">0</v><v id="opening">127</v><v id="risePort">0</v></pStyle>'
           '<singer><t>0</t><bs>0</bs><pc>%d</pc></singer>%s<plane>0</plane></vsPart></vsTrack>'
           '<monoTrack></monoTrack><stTrack></stTrack>'
           '<aux><id><![CDATA[AUX_VST_HOST_CHUNK_INFO]]></id>'
           '<content><![CDATA[VlNDSwAAAAADAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=]]></content></aux></vsq4>'
           % (pc, comp_id or '%016d' % (pc + 1), name, end, pc, ''.join(note_xml)))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(xml)


def write_db_ini(editor, db_path, name='Test'):
    folder, base = os.path.split(os.path.abspath(db_path))
    ini = ['[VVoice 0]', 'BankSelect = 0', 'ProgramChange = 0', 'ComponentID = "0000000000000001"',
           'DBPath = "%s"' % os.path.abspath(db_path), 'DBName = "%s.tree"' % base, 'VVoiceName = "%s"' % name,
           'Breathiness = 0', 'Brightness = 0', 'Clearness = 0', 'GenderFactor = 0', 'Opening = 0', '',
           '[VExp 0]', 'ID_H = 0', 'ID_L = 0', 'DBPath = "%s"' % os.path.join(editor, 'ExpDB', 'expa2'),
           'DBName = "expa2"', 'VExpName = "expa2"', '']
    with open(os.path.join(editor, 'DB_Dev.ini'), 'w', encoding='cp1252', newline='\r\n') as f:
        f.write('\n'.join(ini))


# ---------------------------------------------------------------- driving the editor (pywinauto)
CMD_OPEN, CMD_EXPORT_WAV, CMD_NEW = 57601, 33299, 57600
TITLE = 'VOCALOID4 Editor for Developer'


class Editor:
    def __init__(self, editor_dir, log=print):
        from pywinauto import Application
        import win32con
        import win32gui
        self.gui, self.con, self.log = win32gui, win32con, log
        exe = os.path.join(editor_dir, 'VOCALOID4_Dev.exe')
        self.app = Application(backend='win32').start(exe, work_dir=editor_dir)
        self.main = self.wait(lambda: [w for w in self.app.windows() if w.is_visible()
                                       and w.window_text().startswith(TITLE)], 60, 'editor window')[0].handle

    def wait(self, cond, timeout, what, poll=0.5):
        t = time.time()
        while time.time() - t < timeout:
            if not self.app.is_process_running():
                raise RuntimeError('editor exited while waiting for %s' % what)
            r = cond()
            if r:
                return r
            time.sleep(poll)
        raise RuntimeError('timed out waiting for %s; on screen: %s' % (what, self.describe()))

    def dialogs(self):
        return [w for w in self.app.windows() if w.is_visible() and w.class_name() == '#32770']

    def describe(self):
        out = []
        for w in self.app.windows():
            if w.is_visible():
                t = [c.window_text() for c in w.children() if c.window_text() and c.class_name() in ('Static', 'Button')]
                out.append('[%s|%s] %s' % (w.window_text(), w.class_name(), ' | '.join(t)[:200]))
        return '; '.join(out)

    def command(self, cmd):
        self.gui.PostMessage(self.main, self.con.WM_COMMAND, cmd, 0)

    def file_dialog(self, path, timeout=20):
        dlg = self.wait(lambda: [d for d in self.dialogs() if [c for c in d.descendants()
                                                              if c.class_name() == 'Edit' and c.is_visible()]],
                        timeout, 'file dialog')[0]
        time.sleep(0.8)
        edit = [c for c in dlg.descendants() if c.class_name() == 'Edit' and c.is_visible()][0]
        edit.set_edit_text(path)
        time.sleep(0.3)
        h = dlg.handle
        for _ in range(5):
            self.gui.PostMessage(h, self.con.WM_COMMAND, 1, 0)
            try:
                self.wait(lambda: not self.gui.IsWindow(h), 5, 'file dialog to close')
                return
            except RuntimeError:
                pass
        raise RuntimeError('file dialog did not close: %s' % self.describe())

    def answer_boxes(self, reply_id=7):
        """Dismiss message boxes ("save changes?" -> No)."""
        for d in self.dialogs():
            texts = ' '.join(c.window_text() for c in d.children() if c.class_name() == 'Static')
            if 'synthesizing' in texts:
                continue                                           # progress box: leave it alone
            if texts and not [c for c in d.descendants() if c.class_name() == 'Edit']:
                self.log('  editor says: %s' % texts[:120])
                self.gui.PostMessage(d.handle, self.con.WM_COMMAND, reply_id, 0)
                time.sleep(1)

    def open(self, vsqx):
        self.command(CMD_OPEN)
        time.sleep(1.5)
        self.answer_boxes(7)
        self.file_dialog(os.path.abspath(vsqx))
        time.sleep(3)
        self.answer_boxes(1)

    def close(self):
        if self.app.is_process_running():
            self.app.kill()

    def export(self, wav, timeout=1800):
        """File > Export > Wave: current track, mono, with the .msd analysis file."""
        if os.path.exists(wav):
            os.remove(wav)
        self.command(CMD_EXPORT_WAV)
        dlg = self.wait(lambda: [d for d in self.dialogs() if d.window_text() == 'Export Wave File'], 20,
                        'export dialog')[0]
        time.sleep(0.8)
        spec = self.app.window(handle=dlg.handle)
        spec.child_window(control_id=1408).click()                 # current track
        msd = spec.child_window(control_id=1503).wrapper_object()
        if not msd.get_check_state():
            msd.click()
        self.gui.PostMessage(dlg.handle, self.con.WM_COMMAND, 1, 0)
        self.file_dialog(os.path.abspath(wav))
        time.sleep(2)
        self.answer_boxes(6)                                       # "overwrite?" -> Yes
        last, t0 = -1, time.time()
        while time.time() - t0 < timeout:                          # rendering: wait for the file to settle
            time.sleep(3)
            size = os.path.getsize(wav) if os.path.exists(wav) else -1
            busy = self.dialogs()                                  # progress box still up
            if size > 0 and size == last and not busy:
                return wav
            last = size
        raise RuntimeError('render did not finish: %s' % self.describe())


INSTALLED = r'C:\Program Files (x86)\VOCALOID4\Editor'


def read_slots(editor_dir=INSTALLED):
    """Voices the dev editor knows: it always reads DB_Dev.ini from its *installed* folder (ROOT_PATH in
    HKLM), so these are the only DBs it can sing. [(pc, component id, DB path, tree name, voice name)]"""
    import configparser
    cp = configparser.ConfigParser()
    cp.read(os.path.join(editor_dir, 'DB_Dev.ini'), encoding='cp1252')
    out = []
    for sec in cp.sections():
        if sec.startswith('VVoice'):
            g = lambda k: cp.get(sec, k).strip().strip('"')
            out.append((int(g('ProgramChange')), g('ComponentID'), g('DBPath'), g('DBName'), g('VVoiceName')))
    return out


def render(pc, out_wav, song='test', pitch=64, editor_dir=INSTALLED, log=print, suffix=''):
    """Render the test song with dev-editor voice number pc. Returns (wav, msd)."""
    slot = [s for s in read_slots(editor_dir) if s[0] == pc][0]
    vsqx = os.path.splitext(out_wav)[0] + '.vsqx'
    notes, end = build_notes(song, pitch, suffix=suffix)
    write_vsqx(vsqx, notes, end, pc=pc, comp_id=slot[1], name=slot[4])
    ed = Editor(editor_dir, log)
    try:
        ed.open(vsqx)
        ed.export(out_wav)
    finally:
        ed.close()
    return out_wav, os.path.splitext(out_wav)[0] + '.msd'


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pc', type=int, help='voice number in the dev editor (ProgramChange in DB_Dev.ini); '
                                         'run with --list to see them')
    ap.add_argument('out', help='output .wav (a .msd and .vsqx are written next to it)')
    ap.add_argument('--song', default='test', choices=sorted(SONGS) + sorted(SONGS_EN))
    ap.add_argument('--pitch', type=int, default=64, help='base MIDI note (64 = E4)')
    ap.add_argument('--suffix', default='', help='EVEC colour suffix added to every phoneme, e.g. "#1"')
    a = ap.parse_args()
    print(render(a.pc, a.out, a.song, a.pitch, suffix=a.suffix))
