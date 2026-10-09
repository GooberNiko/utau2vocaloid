"""Drive VocaloidDBTool3.exe to build a singer DB from a converted folder (Windows, needs pywinauto).

Runs a private copy of the DBTool with its own VocaloidDBTool.ini, so the devkit's ini is never
touched: New singer database (from dictionary.txt) -> Automatic Segmentation -> add stationaries ->
optimize EpR guides -> add articulations -> exit.
"""
import os
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


class DBTool:
    def __init__(self, exe, seg_folder, log=print):
        from pywinauto import Application
        import win32con
        import win32gui
        self._gui, self._con = win32gui, win32con
        self.log = log
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
        self._wait(lambda: os.path.exists(os.path.abspath(db_path) + '.tree'), 30, 'the new DB files')

    def load_db(self, db_path):
        """File > Load singer database (singer.inf), quick load."""
        self._menu(471)
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

    def open_auto_segmentation(self):
        self._menu(CMD_AUTO_SEG)
        for _ in range(2):                       # wavefile folder, then toolkit folder (both from the ini)
            dlg = self._dialog('Browse for Files or Folders')
            time.sleep(1)
            self._ok(dlg, 'folder dialog')
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

    def _select(self, col):
        rows = [i for i, t in enumerate(self._col(col)) if t]
        for i in range(self.rows):
            self.lv.deselect(i)
        for i in rows:
            self.lv.select(i)
        return rows

    def _run(self, button, done, timeout, what):
        self.dlg.child_window(control_id=button).click()
        t = time.time()
        while True:
            time.sleep(3)
            if not self._alive():
                raise DBToolError('the DBTool exited (crashed?) during: %s' % what)
            mb = self._message_box()
            if mb and 'currently running tasks' in mb[1]:
                # the previous step is still finishing (saving): dismiss, wait, press the button again
                self._gui.PostMessage(mb[0].handle, self._con.WM_COMMAND, 1, 0)       # OK
                time.sleep(5)
                self.dlg.child_window(control_id=button).click()
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
            if time.time() - t > timeout:
                raise DBToolError('timed out during: %s' % what)

    def add_stationaries(self):
        rows = self._select(COL_STAT_TO_ADD)
        self.log('adding %d stationaries' % len(rows))
        if rows:
            self._run(ADD_STAT, lambda: all(self._col(COL_STAT_ADDED)[i] for i in rows), 1800,
                      'Add Stationaries To Database')
            time.sleep(5)                        # it keeps saving for a moment after the list says YES

    def optimize_epr(self):
        btn = self.dlg.child_window(control_id=OPT_EPR)
        self.log('optimizing EpR guides')
        self._run(OPT_EPR, lambda: btn.is_enabled() or None, 600, 'Optimize EpR Guides')
        time.sleep(3)

    def add_articulations(self):
        rows = self._select(COL_ART_TO_ADD)
        self.log('adding %d articulation files' % len(rows))
        last = [0, time.time()]

        def done():
            added = self._col(COL_ART_ADDED)
            n = sum(1 for i in rows if added[i])
            if n != last[0] and time.time() - last[1] > 30:
                self.log('  %d/%d' % (n, len(rows)))
                last[1] = time.time()
            last[0] = n
            return n >= len(rows) or None
        if rows:
            self._run(ADD_ART, done, 4 * 3600, 'Add Articulations To Database')
        failed = [i for i in rows if not self._col(COL_ART_ADDED)[i].upper().startswith('YES')]
        return failed

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
    try:
        if evec:
            t.load_db(db_path)
        else:
            t.new_db(dictionary, db_path, language)
        t.open_auto_segmentation()
        t.add_stationaries()
        t.optimize_epr()
        failed = t.add_articulations()
    finally:
        t.close()
    inf = os.path.join(os.path.abspath(db_path), 'singer.inf')
    if name and os.path.exists(inf):
        txt = open(inf, encoding='latin-1').read()
        txt = txt.replace('Name = "Undefined"', 'Name = "%s"' % name)
        open(inf, 'w', encoding='latin-1').write(txt)
    return failed
