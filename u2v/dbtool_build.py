"""Drive VocaloidDBTool3.exe to build a singer DB from a converted folder (Windows, needs pywinauto).

Runs a private copy of the DBTool with its own VocaloidDBTool.ini, so the devkit's ini is never
touched: New singer database (from dictionary.txt) -> Automatic Segmentation -> add stationaries ->
optimize EpR guides -> add articulations -> exit.
"""
import os
import re
import shutil
import tempfile
import time
import warnings

warnings.filterwarnings('ignore')   # pywinauto: 32-bit target from 64-bit Python is fine for this

# DBTool menu command ids and Automatic Database Creation dialog controls
CMD_NEW_DB, CMD_AUTO_SEG, CMD_EXIT = 470, 1136, 484
LIST, ADD_STAT, OPT_EPR, ADD_ART = 1201, 1206, 1212, 1216
COL_STAT_TO_ADD, COL_STAT_ADDED, COL_ART_TO_ADD, COL_ART_ADDED = 6, 7, 9, 13
AUTO_DLG = 'Automatic Database Creation Dialog'


class DBToolError(RuntimeError):
    pass


def _decode(name):
    return re.sub(r'%(\d+)%', lambda m: chr(int(m.group(1))), name)


def expected_units(seg_folder):
    """(articulation pairs, stationary phonemes) the converted folder will add, from its .as files."""
    from . import dbtool_io
    art, stat = set(), set()
    for fn in os.listdir(seg_folder):
        if re.search(r'\.as\d+$', fn):
            try:
                a = dbtool_io.read_as(os.path.join(seg_folder, fn))
            except Exception:                     # noqa: BLE001 - validate reports broken files
                continue
            if len(a.phns) == 1:
                stat.add(a.phns[0])
            else:
                art.add(tuple(a.phns))
    return art, stat


def _finished(cell):
    """An "added" cell is done once it says YES (or an error): while the DBTool is still working on a row
    it shows "...1", and closing then cut the last articulation off on slower PCs."""
    t = cell.strip()
    return bool(t) and not t.startswith('...')


class DBToolHandoff(DBToolError):
    """The automation got stuck; the DBTool is left open with its files so the user can finish by hand."""


HANDOFF = '''The DBTool got stuck at "%s", so it was left open for you to finish by hand:
  in the "Automatic Segmentation" window select every row (click the first, shift+click the last), then press
  1. Add Stationaries To Database   (wait until the "added" column says Yes everywhere)
  2. Optimize EpR Guides
  3. Add Articulations To Database   (this one takes a few minutes)
  then press Exit and close the DBTool. Your DB is: %s
(Its working files are in %s; delete that folder afterwards.)'''


class DBTool:
    def __init__(self, exe, seg_folder, log=print):
        from pywinauto import Application
        import win32con
        import win32gui
        self._gui, self._con = win32gui, win32con
        self.log = log
        self.seg_folder = os.path.abspath(seg_folder)
        self.work = tempfile.mkdtemp(prefix='dbtool_')
        shutil.copy(exe, self.work)
        with open(os.path.join(self.work, 'VocaloidDBTool.ini'), 'w', newline='\r\n') as f:
            f.write('[Segmentate Folder]\nWavefilePath=%s\nToolkitPath=%s\n[External Tool]\n'
                    'ExtWaveEditorPath=\nUseExtWaveEditor=0\nMasterVolume=0\n'
                    % (os.path.abspath(seg_folder), self.work))
        self.app = Application(backend='win32').start(os.path.join(self.work, os.path.basename(exe)),
                                                      work_dir=self.work)
        self.main = self._wait(lambda: [w for w in self.app.windows() if w.class_name().startswith('Afx')
                                        and w.menu() and w.menu().item_count()], 30, 'main window')[0]

    # ------------------------------------------------------------ plumbing
    def _alive(self):
        return self.app.is_process_running()

    def _wait(self, cond, timeout, what, poll=0.5):
        t = time.time()
        while time.time() - t < timeout:
            if not self._alive():
                raise DBToolError('the DBTool exited while waiting for %s' % what)
            r = cond()
            if r:
                return r
            time.sleep(poll)
        raise DBToolError('timed out waiting for %s. On screen: %s' % (what, self._describe()))

    def _describe(self):
        out = []
        for w in self.app.windows():
            if w.is_visible():
                t = [c.window_text() for c in w.children() if c.class_name() in ('Static', 'Button')
                     and c.window_text()]
                out.append('[%s] %s' % (w.window_text(), ' | '.join(t)[:300]))
        return '; '.join(out)

    def _dialog(self, title, timeout=30):
        w = self._wait(lambda: [w for w in self.app.windows()
                                if w.is_visible() and w.window_text() == title], timeout, title)[0]
        return self.app.window(handle=w.handle)

    def _menu(self, cmd):
        self._gui.PostMessage(self.main.handle, self._con.WM_COMMAND, cmd, 0)

    def _message_box(self):
        """Text of a visible message box, if the DBTool popped one."""
        for w in self.app.windows():
            if w.is_visible() and w.class_name() == '#32770' and w.window_text() != AUTO_DLG:
                texts = [c.window_text() for c in w.children() if c.class_name() == 'Static']
                if any(texts):
                    return w, ' '.join(t for t in texts if t)
        return None

    def _ok(self, dlg, what):
        """Press OK (IDOK) until the dialog is gone."""
        h = dlg.handle
        for _ in range(5):
            self._gui.PostMessage(h, self._con.WM_COMMAND, 1, 0)
            try:
                self._wait(lambda: not self._gui.IsWindow(h), 5, what + ' to close')
                return
            except DBToolError:
                if not self._alive():
                    raise
        raise DBToolError('%s does not close. On screen: %s' % (what, self._describe()))

    def _file_dialog(self, title, path):
        dlg = self._dialog(title)
        time.sleep(1)
        edit = [c for c in dlg.descendants() if c.class_name() == 'Edit' and c.is_visible()][0]
        edit.set_edit_text(path)
        time.sleep(0.3)
        self._ok(dlg, 'dialog "%s"' % title)

    # ------------------------------------------------------------ steps
    def new_db(self, dictionary, db_path, language='Japanese'):
        """db_path: folder\\Name -> creates folder\\Name.dat, Name.tree and Name\\singer.inf."""
        self._menu(CMD_NEW_DB)
        self._file_dialog('Load Phonetic Dictionary file', os.path.abspath(dictionary))
        self._file_dialog('Singer database Path & Name', os.path.abspath(db_path))
        dlg = self._dialog('Select language')
        box = dlg.child_window(class_name='ComboBox').wrapper_object()
        for _ in range(40):                     # the list fills a moment after the dialog shows
            items = box.item_texts()
            match = [s for s in items if s.strip().lower().startswith(language.lower())]
            if match:
                break
            time.sleep(0.25)
        else:
            raise DBToolError('language %r not offered by the DBTool (it lists %s)' % (language, items))
        box.select(match[0])
        self._ok(dlg, 'Select language')
        self._wait(lambda: os.path.exists(os.path.abspath(db_path) + '.tree'), 120, 'the new DB files')
        self.db_path = os.path.abspath(db_path)

    def load_db(self, db_path):
        """File > Load singer database (singer.inf), quick load."""
        self._menu(471)
        self.db_path = os.path.abspath(db_path)
        self._file_dialog('Open singer database', os.path.join(os.path.abspath(db_path), 'singer.inf'))
        dlg = self._dialog('Rebuild Database Tree Structure', timeout=60)
        time.sleep(1)
        self._ok(dlg, 'Rebuild Database Tree Structure')
        time.sleep(3)

    def quit(self):
        """Exit the DBTool (with no dialog open)."""
        self._menu(CMD_EXIT)
        try:
            self._wait(lambda: not self._alive() or None, 30, 'the DBTool to exit')
        except DBToolError:
            self.app.kill()
        shutil.rmtree(self.work, ignore_errors=True)

    def _confirm_folder(self, dlg, path, what):
        """OK a "Browse for Files or Folders" window. On some PCs a posted OK is ignored (no folder
        selected yet): type the path in, press OK every way we can, and in the end wait for the user to
        press it (the build used to give up after 25 s and close the DBTool)."""
        h = dlg.handle
        try:
            edits = [c for c in dlg.descendants() if c.class_name() == 'Edit' and c.is_visible()]
            if edits:
                edits[0].set_edit_text(path)
        except Exception:                         # noqa: BLE001 - no edit box in this version
            pass
        tries = [lambda: self._gui.PostMessage(h, self._con.WM_COMMAND, 1, 0),
                 lambda: dlg.child_window(control_id=1).click(),
                 lambda: dlg.type_keys('{ENTER}', set_foreground=True)]
        for k, press in enumerate(tries * 2):
            try:
                press()
            except Exception:                     # noqa: BLE001
                pass
            for _ in range(10):
                time.sleep(0.5)
                if not self._gui.IsWindow(h) or not self._gui.IsWindowVisible(h):
                    return
        self.log('>>> please press OK (or Enter) in the DBTool\'s "Browse for Files or Folders" window (%s: %s)'
                 % (what, path))
        self._wait(lambda: not self._gui.IsWindow(h) or not self._gui.IsWindowVisible(h), 600,
                   'you to press OK in the folder window')

    def open_auto_segmentation(self):
        self._menu(CMD_AUTO_SEG)
        for k in range(2):                       # wavefile folder, then toolkit folder (both from the ini)
            dlg = self._dialog('Browse for Files or Folders', timeout=60)
            time.sleep(1)
            self._confirm_folder(dlg, (self.seg_folder, self.work)[k], ('wave files', 'toolkit')[k])
        self.dlg = self._dialog(AUTO_DLG, timeout=600)
        self.dlg_handle = self.dlg.handle
        self.lv = self.dlg.child_window(control_id=LIST).wrapper_object()
        self.rows = self.lv.item_count()
        self.log('DBTool lists %d files' % self.rows)

    def _col(self, col, tries=10):
        """One list column. Reading another process's list view can fail while the DBTool redraws it."""
        for k in range(tries):
            try:
                return [self.lv.get_item(i, col).text() for i in range(self.rows)]
            except Exception:                     # noqa: BLE001 - pywinauto raises several kinds
                if not self._alive() or k == tries - 1:
                    raise
                time.sleep(2)

    def _select_all_keys(self):
        """Select every row with the keyboard (Home, Shift+End): needs no access to the list's memory."""
        try:
            self.lv.set_focus()
            self.lv.type_keys('{HOME}+{END}', set_foreground=True)
            time.sleep(1)
        except Exception as ex:                   # noqa: BLE001
            raise DBToolError('could not select the rows (%s)' % ex)
        return list(range(self.rows))

    def _select(self, col):
        try:
            rows = [i for i, t in enumerate(self._col(col)) if t]
        except Exception as ex:                   # noqa: BLE001 - some PCs can't read another program's list
            self.log('  could not read the list (%s): selecting every row' % ex)
            return self._select_all_keys()
        for attempt in range(3):
            for i in range(self.rows):
                self.lv.deselect(i)
            for i in rows:
                self.lv.select(i)
            time.sleep(0.5)
            try:
                n = self.lv.get_selected_count()
            except Exception:                     # noqa: BLE001 - can't tell: trust it
                return rows
            if n == len(rows):
                return rows
            self.log('  selected %d of %d rows, selecting again' % (n, len(rows)))
            time.sleep(2)
        self.log('  selecting rows one by one did not work: selecting every row')
        return self._select_all_keys()

    def _press(self, button):
        """Press a dialog button with its WM_COMMAND: works whether or not the window has focus (a
        simulated click sometimes never reached the DBTool and the build waited forever)."""
        h = self._gui.GetDlgItem(self.dlg_handle, button)
        self._gui.PostMessage(self.dlg_handle, self._con.WM_COMMAND, button & 0xFFFF, h)

    def _run(self, button, done, timeout, what, progress=None, stall=180):
        """Press button and wait for done(). progress() (a count) tells whether anything is happening:
        nothing for `stall` seconds after a press -> press again, twice, then hand over to the user."""
        self._press(button)
        t = t_press = time.time()
        presses, last = 1, (progress() if progress else None)
        while True:
            time.sleep(3)
            if not self._alive():
                raise DBToolError('the DBTool exited (crashed?) during: %s' % what)
            mb = self._message_box()
            if mb and 'currently running tasks' in mb[1]:
                # the previous step is still finishing (saving): dismiss, wait, press the button again
                self._gui.PostMessage(mb[0].handle, self._con.WM_COMMAND, 1, 0)       # OK
                time.sleep(5)
                self._press(button)
                t_press = time.time()
                continue
            if mb and 'want to continue' in mb[1]:
                # "...already been segmented. Previous segmentation ... will be lost. Do you want to
                # continue?" - our .as files are the segmentation we want, so: Yes
                self.log('  DBTool asks: %s -> Yes' % mb[1][:90])
                self._gui.PostMessage(mb[0].handle, self._con.WM_COMMAND, 6, 0)       # IDYES
                time.sleep(2)
            elif mb:
                raise DBToolError('%s: the DBTool says: %s' % (what, mb[1]))
            n = done()
            if n is True:
                return
            if progress:
                now = progress()
                if now != last:
                    last, t_press = now, time.time()
                elif time.time() - t_press > stall:
                    if presses >= 3:
                        raise DBToolError('nothing happened after pressing "%s" %d times' % (what, presses))
                    self.log('  no progress on "%s" for %d s, pressing it again' % (what, stall))
                    self._press(button)
                    presses += 1
                    t_press = time.time()
            if time.time() - t > timeout:
                raise DBToolError('timed out during: %s' % what)

    def _db_units(self, kind):
        """Units already written to the DB folder: {pair or phoneme: (bytes, mtime)}. The DBTool writes one
        file per unit (voice/articulation/<p1>/<p2>, voice/stationary/normal/<p>/<n>), so this shows
        progress without reading its window (which fails on some PCs and is slow for big banks)."""
        base = os.path.join(self.db_path, 'voice', kind)
        out = {}
        for dp, _, files in os.walk(base):
            for fn in files:
                if fn.endswith('.dat'):
                    continue
                f = os.path.join(dp, fn)
                parts = [_decode(x) for x in os.path.relpath(f, base).split(os.sep)]
                key = tuple(parts) if kind == 'articulation' else parts[1] if len(parts) > 2 else parts[0]
                try:
                    st = os.stat(f)
                except OSError:
                    continue
                b, m = out.get(key, (0, 0))
                out[key] = (b + st.st_size, max(m, st.st_mtime))
        return out

    def _files_done(self, kind, expected, quiet=20):
        """Every expected unit is in the DB and nothing has been written for `quiet` seconds."""
        got = self._db_units(kind)
        if any(u not in got for u in expected):
            return None
        last = max((m for _, m in got.values()), default=0)
        return (time.time() - last > quiet) or None

    def _files_progress(self, kind):
        got = self._db_units(kind)
        return (len(got), sum(b for b, _ in got.values()))

    def add_stationaries(self):
        rows = self._select(COL_STAT_TO_ADD)
        self.log('adding %d stationaries' % len(rows))
        if rows:
            exp = self.expected_stat
            self._run(ADD_STAT, lambda: self._files_done('stationary', exp, quiet=10), 3 * 3600,
                      'Add Stationaries To Database', progress=lambda: self._files_progress('stationary'), stall=300)
            time.sleep(5)                        # it keeps saving for a moment after the last file

    def optimize_epr(self):
        btn = self.dlg.child_window(control_id=OPT_EPR)
        self.log('optimizing EpR guides')
        self._run(OPT_EPR, lambda: btn.is_enabled() or None, 600, 'Optimize EpR Guides')
        time.sleep(3)

    def add_articulations(self):
        rows = self._select(COL_ART_TO_ADD)
        exp = self.expected_art
        self.log('adding %d articulation files (%d units)' % (len(rows), len(exp)))
        last = [0, time.time()]

        def done():
            n = sum(1 for u in self._db_units('articulation') if u in exp)
            if n != last[0] and time.time() - last[1] > 30:
                self.log('  %d/%d units' % (n, len(exp)))
                last[1] = time.time()
            last[0] = n
            return self._files_done('articulation', exp)
        if rows:
            # big banks on slow PCs take hours: no overall limit, only "nothing written for 10 minutes"
            self._run(ADD_ART, done, 24 * 3600, 'Add Articulations To Database',
                      progress=lambda: self._files_progress('articulation'), stall=600)
        have = self._db_units('articulation')
        return sorted(' '.join(u) for u in exp if u not in have)

    def close(self):
        try:
            if self._alive():
                h = getattr(self, 'dlg_handle', None)
                if h and self._gui.IsWindow(h):
                    self._gui.PostMessage(h, self._con.WM_COMMAND, 1, 0)       # Exit (IDOK)
                    self._wait(lambda: not self._gui.IsWindow(h), 30, 'the segmentation dialog to close')
                self._menu(CMD_EXIT)
                try:
                    self._wait(lambda: not self._alive() or None, 30, 'the DBTool to exit')
                except DBToolError:
                    self.app.kill()
        finally:
            shutil.rmtree(self.work, ignore_errors=True)


def build(exe, seg_folder, db_path, name=None, dictionary=None, language='Japanese', log=print):
    """Create db_path (folder\\Name) from seg_folder's dictionary.txt and add every unit. Afterwards the
    pitch the DBTool analysed for each unit is checked; units it read an octave off get their
    fundamental raised and the DB is built once more."""
    dictionary = os.path.abspath(dictionary or os.path.join(seg_folder, 'dictionary.txt'))
    if os.path.exists(os.path.abspath(db_path) + '.tree'):
        raise DBToolError('%s already exists; pick a new DB name' % db_path)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    boosted = {}
    for attempt in range(2):
        # The DBTool rewrites files in the folder it builds from (stationary .wav/.as0, new .db0), which
        # would break a second build: give it a copy and keep the converted folder as it was.
        work = tempfile.mkdtemp(prefix='dbtool_seg_')
        try:
            for fn in os.listdir(seg_folder):
                shutil.copy(os.path.join(seg_folder, fn), work)
            _boost(work, boosted)
            try:
                failed = _build(exe, work, db_path, name, dictionary, language, log)
            except DBToolHandoff:
                work = None                  # the DBTool still uses it: keep it for the user
                raise
            except DBToolError:
                raise
            except Exception as ex:          # noqa: BLE001 - pywinauto loses a window now and then
                # a dialog that closed while it was being read ("Invalid window handle"): start over once
                log('the DBTool automation tripped (%s); starting the build again' % ex)
                time.sleep(3)                # _build already closed its DBTool (its own private copy)
                _remove_db(db_path)
                shutil.rmtree(work, ignore_errors=True)
                work = tempfile.mkdtemp(prefix='dbtool_seg_')
                for fn in os.listdir(seg_folder):
                    shutil.copy(os.path.join(seg_folder, fn), work)
                _boost(work, boosted)
                failed = _build(exe, work, db_path, name, dictionary, language, log)
        finally:
            if work:
                shutil.rmtree(work, ignore_errors=True)
        bad = octave_errors(db_path, seg_folder)
        new = {f: f0 for _, files in bad.values() for f, f0 in files if f not in boosted}
        if bad and (not new or attempt == 1):
            log('the DBTool still analyses these units an octave off: %s'
                % ', '.join('[%s] %.1fx' % (' '.join(k[1:]), r) for k, (r, _) in sorted(bad.items())))
        if not new or attempt == 1:
            return failed
        log('the DBTool analysed %d unit(s) an octave off (%s): raising their fundamental and rebuilding'
            % (len(bad), ', '.join('[%s]' % ' '.join(k[1:]) for k in sorted(bad))))
        log('>>> pass 2 of 2: the DBTool closes and opens again and repeats every step. This is normal, '
            "it's not a crash: let it finish.")
        boosted.update(new)
        _remove_db(db_path)


def octave_errors(db_path, seg_folder):
    """{unit key: (analysed / recorded pitch, [(seg file, recorded f0)])} for transition units whose
    analysed pitch is about an octave (or more) away from the pitch they were recorded at."""
    import csv
    from collections import defaultdict
    from . import dbtool_io, phonemes as P
    got = dbtool_io.db_pitches(db_path)
    want = defaultdict(list)
    with open(os.path.join(seg_folder, 'manifest.csv'), encoding='utf-8-sig', newline='') as fh:
        for r in csv.DictReader(fh):
            ph = r['unit'].split()
            f0 = float(r['f0_hz'] or 0)
            # stationaries read an octave off still rendered clean (Teto's i, M, o): only transitions
            if r['kind'] != 'art' or f0 <= 0 or not any(p.split('#')[0] in P.SUSTAINED for p in ph):
                continue
            key = ('art',) + tuple(ph)
            want[key].append((r['file'], f0))
    import numpy as np
    bad = {}
    for k, files in want.items():
        if k in got:
            ratio = got[k] / float(np.median([f0 for _, f0 in files]))
            if ratio > 1.6 or ratio < 0.62:
                bad[k] = (ratio, files)
    return bad


def _boost(folder, boosted):
    from . import audio
    for f, f0 in boosted.items():
        p = os.path.join(folder, f + '.wav')
        x, sr = audio.read_wav(p)
        audio.write_wav(p, audio.boost_fundamental(x, sr, f0), sr)


def _remove_db(db_path):
    db = os.path.abspath(db_path)
    for ext in ('.dat', '.tree'):
        if os.path.exists(db + ext):
            os.remove(db + ext)
    shutil.rmtree(db, ignore_errors=True)


def _build(exe, seg_folder, db_path, name, dictionary, language, log):
    from . import dbtool_io
    evec = dbtool_io.EVEC_STANDIN + '1' in open(dictionary, encoding='latin-1').read()
    t = DBTool(exe, seg_folder, log)
    if evec:
        # EVEC colours: create the DB with stand-in names ("aX1"), rename them to "a#1" in its phoneme
        # table, then reopen it (the DBTool's dictionary reader treats "#" as a comment)
        try:
            t.new_db(dictionary, db_path, language)
            time.sleep(2)
        finally:
            t.quit()
        log('EVEC: renamed %d phoneme entries' % dbtool_io.patch_evec_names(os.path.abspath(db_path)))
        t = DBTool(exe, seg_folder, log)
    t.expected_art, t.expected_stat = expected_units(seg_folder)
    step = 'creating the DB'
    try:
        if evec:
            t.load_db(db_path)
        else:
            t.new_db(dictionary, db_path, language)
        step = 'opening Automatic Segmentation'
        t.open_auto_segmentation()
        step = 'Add Stationaries To Database'
        t.add_stationaries()
        step = 'Optimize EpR Guides'
        t.optimize_epr()
        step = 'Add Articulations To Database'
        failed = t.add_articulations()
    except DBToolError as ex:
        if step.startswith(('Add', 'Optimize', 'opening')) and t._alive():
            # Leave the DBTool open on its list (and its files in place): the user can finish by hand.
            # Closing it here used to delete the folder it was building from, so that failed too.
            log('%s' % ex)
            raise DBToolHandoff(HANDOFF % (step, os.path.abspath(db_path), seg_folder)) from ex
        t.close()
        raise
    except Exception as ex:
        if step.startswith(('Add', 'Optimize', 'opening')) and t._alive():
            log('%s' % ex)                       # same: don't pull the DBTool away mid-way
            raise DBToolHandoff(HANDOFF % (step, os.path.abspath(db_path), seg_folder)) from ex
        t.close()
        raise
    except BaseException:
        t.close()
        raise
    t.close()
    inf = os.path.join(os.path.abspath(db_path), 'singer.inf')
    if name and os.path.exists(inf):
        txt = open(inf, encoding='latin-1').read()
        txt = txt.replace('Name = "Undefined"', 'Name = "%s"' % name)
        open(inf, 'w', encoding='latin-1').write(txt)
    return failed
