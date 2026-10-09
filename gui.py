#!/usr/bin/env python3
"""utau2vocaloid GUI: pick an UTAU bank, convert it, build the singer DB with the DBTool and test it in
the VOCALOID4 Editor for Developer. Every step runs the command line tools, so the log shows exactly
what the CLI would print.

    python gui.py
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
DEVKIT = os.path.dirname(ROOT)
CLI = os.path.join(ROOT, 'utau2vocaloid.py')
TOOLS = os.path.join(ROOT, 'tools')
SETTINGS = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'utau2vocaloid', 'gui.json')
sys.path.insert(0, ROOT)
sys.path.insert(0, TOOLS)

SAVED = ('bank', 'out', 'dict', 'lang', 'colors', 'keep', 'splice', 'fill', 'normalize', 'derive', 'extend_dict', 'extras',
         'merge_groups', 'brighten', 'avoid', 'db_name', 'db_path', 'db_lang', 'dbtool', 'slot', 'song',
         'suffixes')


class Runner:
    """Runs one command at a time in the background and streams its output into the log."""

    def __init__(self, app):
        self.app, self.proc, self.q = app, None, queue.Queue()

    @property
    def busy(self):
        return self.proc is not None

    def start(self, cmd, title, done=None):
        if self.busy:
            messagebox.showinfo('Busy', 'Wait for the current step to finish (or press Stop).')
            return
        self.app.log('\n$ %s\n' % ' '.join('"%s"' % c if ' ' in c else c for c in cmd[1:]), 'cmd')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, encoding='utf-8',
                                     errors='replace', bufsize=1, creationflags=flags)
        self.done, self.title = done, title
        self.app.set_busy(title)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        self.app.after(100, self._poll)

    def _read(self, proc):
        for line in proc.stdout:
            if 'warnings.warn(' in line or '32-bit application should be automated' in line:
                continue                                    # pywinauto noise
            self.q.put(line)
        proc.wait()
        self.q.put(None)

    def _poll(self):
        try:
            while True:
                line = self.q.get_nowait()
                if line is None:
                    code = self.proc.returncode
                    self.proc = None
                    self.app.set_busy(None)
                    self.app.log('%s %s\n' % (self.title, 'finished' if code == 0 else 'failed (exit %s)' % code),
                                 'ok' if code == 0 else 'err')
                    if self.done:
                        self.done(code)
                    return
                tag = 'err' if re.search(r'Traceback|Error|failed|problems', line) else None
                self.app.log(line, tag)
        except queue.Empty:
            pass
        self.app.after(100, self._poll)

    def stop(self):
        if not self.proc:
            return
        # the build drives the DBTool and the render the dev editor: end the whole process tree
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(self.proc.pid)], capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.app.log('stopped\n', 'err')


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        from u2v import __version__
        self.title('utau2vocaloid ' + __version__)
        self.geometry('900x760')
        self.minsize(760, 600)
        self.runner = Runner(self)
        self.v = {}
        self.groups = []          # [(group, entries)]
        self.detected_lang = None
        self._vars()
        self._load()
        self._ui()
        self.protocol('WM_DELETE_WINDOW', self._close)
        if self.v['bank'].get() and os.path.isdir(self.v['bank'].get()):
            self.after(200, self.scan)

    # ------------------------------------------------------------------ state
    def _vars(self):
        S, B, I = tk.StringVar, tk.BooleanVar, tk.IntVar
        self.v.update(bank=S(), out=S(), dict=S(), lang=S(value='auto'), colors=S(value='main'), keep=I(value=1),
                      splice=B(value=True), fill=B(value=True), normalize=B(value=True), derive=B(value=True),
                      extend_dict=B(value=True), merge_groups=B(value=False), extras=B(value=True),
                      brighten=S(), avoid=S(),
                      db_name=S(), db_path=S(), db_lang=S(value='Japanese'),
                      dbtool=S(value=os.path.join(DEVKIT, 'VocaloidDBTool3.exe')),
                      slot=S(), song=S(value='Japanese test song'), suffixes=S())
        self.status = S(value='Ready')
        self.info = S(value='Choose an UTAU voicebank folder.')

    def _load(self):
        try:
            with open(SETTINGS, encoding='utf-8') as f:
                data = json.load(f)
            for k in SAVED:
                if k in data:
                    self.v[k].set(data[k])
        except (OSError, ValueError, tk.TclError):
            pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
            with open(SETTINGS, 'w', encoding='utf-8') as f:
                json.dump({k: self.v[k].get() for k in SAVED}, f, indent=1)
        except OSError:
            pass

    def _close(self):
        if self.runner.busy and not messagebox.askyesno('Quit', 'A step is still running. Stop it and quit?'):
            return
        self.runner.stop()
        self._save()
        self.destroy()

    # --------------------------------------------------------------------- UI
    def _ui(self):
        style = ttk.Style(self)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        style.configure('Head.TLabel', font=('Segoe UI', 10, 'bold'))
        style.configure('Big.TButton', padding=(12, 4))

        top = ttk.Frame(self, padding=(10, 8, 10, 0))
        top.pack(fill='x')
        ttk.Label(top, text='UTAU bank', style='Head.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Entry(top, textvariable=self.v['bank']).grid(row=0, column=1, sticky='ew', padx=6)
        ttk.Button(top, text='Browse…', command=self.pick_bank).grid(row=0, column=2)
        ttk.Label(top, textvariable=self.info, foreground='#555').grid(row=1, column=1, columnspan=2,
                                                                      sticky='w', padx=6, pady=(2, 0))
        ttk.Label(top, text='Phonetic dictionary', style='Head.TLabel').grid(row=2, column=0, sticky='w', pady=(6, 0))
        ttk.Entry(top, textvariable=self.v['dict']).grid(row=2, column=1, sticky='ew', padx=6, pady=(6, 0))
        ttk.Button(top, text='Browse…', command=self.pick_dict).grid(row=2, column=2, pady=(6, 0))
        ttk.Label(top, text='from the devkit: Japanese Dictionary\\Japanese_Dictionary.txt (English: English '
                            'Dictionary\\english_phonetic_dictionary_20061220.txt). Found automatically when it can be.',
                  foreground='#777').grid(row=3, column=1, columnspan=2, sticky='w', padx=6)
        top.columnconfigure(1, weight=1)

        nb = ttk.Notebook(self)
        nb.pack(fill='both', expand=False, padx=10, pady=8)
        nb.add(self._tab_convert(nb), text='  1. Convert  ')
        nb.add(self._tab_build(nb), text='  2. Build singer DB  ')
        nb.add(self._tab_test(nb), text='  3. Test in VOCALOID  ')

        bar = ttk.Frame(self, padding=(10, 0))
        bar.pack(fill='x')
        self.prog = ttk.Progressbar(bar, mode='indeterminate', length=160)
        self.prog.pack(side='left')
        ttk.Label(bar, textvariable=self.status).pack(side='left', padx=8)
        self.stop_btn = ttk.Button(bar, text='Stop', command=self.runner.stop, state='disabled')
        self.stop_btn.pack(side='right')
        ttk.Button(bar, text='Clear log', command=lambda: self.text.delete('1.0', 'end')).pack(side='right', padx=6)

        lf = ttk.Frame(self, padding=(10, 6, 10, 10))
        lf.pack(fill='both', expand=True)
        self.text = tk.Text(lf, wrap='word', font=('Consolas', 9), height=12, relief='flat',
                            background='#1e1e1e', foreground='#d4d4d4', insertbackground='#d4d4d4')
        sb = ttk.Scrollbar(lf, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.text.pack(fill='both', expand=True)
        self.text.tag_configure('cmd', foreground='#9cdcfe')
        self.text.tag_configure('ok', foreground='#6a9955')
        self.text.tag_configure('err', foreground='#f48771')

    def _row(self, parent, r, label, var, browse=None, width=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky='w', pady=3)
        e = ttk.Entry(parent, textvariable=var, width=width or 60)
        e.grid(row=r, column=1, sticky='ew', padx=6, pady=3)
        if browse:
            ttk.Button(parent, text='Browse…', command=browse).grid(row=r, column=2, pady=3)
        return e

    def _tab_convert(self, nb):
        f = ttk.Frame(nb, padding=10)
        f.columnconfigure(1, weight=1)
        left = ttk.Frame(f)
        left.grid(row=0, column=0, sticky='nsew')
        ttk.Label(left, text='Folders / suffixes to use', style='Head.TLabel').pack(anchor='w')
        ttk.Label(left, text='(none selected = all)', foreground='#777').pack(anchor='w')
        box = ttk.Frame(left)
        box.pack(fill='both', expand=True, pady=4)
        self.glist = tk.Listbox(box, selectmode='extended', height=9, width=30, exportselection=False,
                                activestyle='none')
        gsb = ttk.Scrollbar(box, command=self.glist.yview)
        self.glist.configure(yscrollcommand=gsb.set)
        self.glist.pack(side='left', fill='both', expand=True)
        gsb.pack(side='right', fill='y')

        right = ttk.Frame(f, padding=(14, 0, 0, 0))
        right.grid(row=0, column=1, sticky='nsew')
        right.columnconfigure(1, weight=1)
        self._row(right, 0, 'Output folder', self.v['out'], self.pick_out)
        right.rowconfigure(1, minsize=0)

        o = ttk.Frame(right)
        o.grid(row=1, column=0, columnspan=3, sticky='ew', pady=4)
        ttk.Label(o, text='Language').grid(row=0, column=0, sticky='w')
        ttk.Combobox(o, textvariable=self.v['lang'], values=('auto', 'ja', 'en'), width=6,
                     state='readonly').grid(row=0, column=1, sticky='w', padx=(4, 16))
        ttk.Label(o, text='Voice colours').grid(row=0, column=2, sticky='w')
        ttk.Combobox(o, textvariable=self.v['colors'], values=('main', 'evec', 'merge'), width=7,
                     state='readonly').grid(row=0, column=3, sticky='w', padx=(4, 16))
        ttk.Label(o, text='Samples per unit').grid(row=0, column=4, sticky='w')
        ttk.Spinbox(o, textvariable=self.v['keep'], from_=1, to=5, width=4).grid(row=0, column=5, padx=4)

        c = ttk.Frame(right)
        c.grid(row=2, column=0, columnspan=3, sticky='w', pady=2)
        checks = [('splice', 'Fake missing transitions (CV legato)'), ('fill', 'Fill missing units'),
                  ('normalize', 'Even out loudness'), ('derive', 'Units from silence'),
                  ('extend_dict', "Add b, b', p\\' to dictionary"), ('merge_groups', 'Treat folders as one set'),
                  ('extras', 'Extras: breaths, end breaths, rolled r')]
        for i, (k, t) in enumerate(checks):
            ttk.Checkbutton(c, text=t, variable=self.v[k]).grid(row=i // 2, column=i % 2, sticky='w', padx=(0, 18))

        self._row(right, 3, 'Brighten', self.v['brighten'])
        ttk.Label(right, text='e.g.  o=3   (dB of treble for a dull vowel; several: o=3, a=1)',
                  foreground='#777').grid(row=4, column=1, sticky='w', padx=6)
        self._row(right, 5, 'Avoid units', self.v['avoid'])
        ttk.Label(right, text="e.g.  g' i; k o   (rebuild these from other recordings)",
                  foreground='#777').grid(row=6, column=1, sticky='w', padx=6)

        b = ttk.Frame(right)
        b.grid(row=7, column=0, columnspan=3, sticky='w', pady=(10, 0))
        ttk.Button(b, text='Convert', style='Big.TButton', command=self.convert).pack(side='left')
        ttk.Button(b, text='Validate', command=self.validate).pack(side='left', padx=6)
        ttk.Button(b, text='Open report', command=lambda: self.open_file(os.path.join(self.v['out'].get(),
                                                                                       'report.txt'))).pack(side='left')
        ttk.Button(b, text='Open folder', command=lambda: self.open_file(self.v['out'].get())).pack(side='left', padx=6)
        return f

    def _tab_build(self, nb):
        f = ttk.Frame(nb, padding=10)
        f.columnconfigure(1, weight=1)
        self._row(f, 0, 'Singer name', self.v['db_name'], width=30)
        self._row(f, 1, 'New DB (folder\\Name)', self.v['db_path'], self.pick_db)
        ttk.Label(f, text='Language').grid(row=2, column=0, sticky='w', pady=3)
        ttk.Combobox(f, textvariable=self.v['db_lang'], values=('Japanese', 'English', 'Spanish'), width=12,
                     state='readonly').grid(row=2, column=1, sticky='w', padx=6)
        self._row(f, 3, 'VocaloidDBTool3.exe', self.v['dbtool'], self.pick_dbtool)
        ttk.Label(f, text='Builds from the converted output folder. Takes a few minutes; the DBTool window opens '
                          'and is driven automatically,\nso leave it alone until the log says the DB is built.',
                  foreground='#777').grid(row=4, column=1, sticky='w', padx=6, pady=(6, 0))
        b = ttk.Frame(f)
        b.grid(row=5, column=1, sticky='w', pady=(10, 0), padx=6)
        ttk.Button(b, text='Build DB', style='Big.TButton', command=self.build).pack(side='left')
        ttk.Button(b, text='Convert + Build', command=lambda: self.convert(then=self.build)).pack(side='left', padx=6)
        return f

    def _tab_test(self, nb):
        f = ttk.Frame(nb, padding=10)
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text='Voice slot').grid(row=0, column=0, sticky='w', pady=3)
        self.slot_box = ttk.Combobox(f, textvariable=self.v['slot'], width=48, state='readonly')
        self.slot_box.grid(row=0, column=1, sticky='w', padx=6)
        self.slot_box['values'] = self._slots()
        ttk.Label(f, text='Test song').grid(row=1, column=0, sticky='w', pady=3)
        ttk.Combobox(f, textvariable=self.v['song'], values=('Japanese test song', 'English test song'), width=22,
                     state='readonly').grid(row=1, column=1, sticky='w', padx=6)
        self._row(f, 2, 'Colour suffixes', self.v['suffixes'], width=20)
        ttk.Label(f, text='EVEC banks: also render other colours, e.g.  ,#1   (empty = main colour only)',
                  foreground='#777').grid(row=3, column=1, sticky='w', padx=6)
        ttk.Label(f, text='Builds a test DB under the slot\'s name, points the slot at it (your own DB is kept as '
                          '<folder>__orig),\nopens the dev editor, renders the test song and checks it. '
                          'Close the dev editor first.',
                  foreground='#777').grid(row=4, column=1, sticky='w', padx=6, pady=(6, 0))
        b = ttk.Frame(f)
        b.grid(row=5, column=1, sticky='w', pady=(10, 0), padx=6)
        ttk.Button(b, text='Build && render test', style='Big.TButton', command=self.test).pack(side='left')
        ttk.Button(b, text='Play render', command=self.play).pack(side='left', padx=6)
        ttk.Button(b, text='Restore my voice slot', command=self.restore).pack(side='left')
        return f

    # ---------------------------------------------------------------- helpers
    def log(self, s, tag=None):
        self.text.insert('end', s, tag)
        self.text.see('end')

    def set_busy(self, title):
        if title:
            self.status.set(title + '…')
            self.prog.start(12)
            self.stop_btn.configure(state='normal')
        else:
            self.status.set('Ready')
            self.prog.stop()
            self.stop_btn.configure(state='disabled')

    @staticmethod
    def open_file(path):
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showinfo('Not found', '%s does not exist yet.' % (path or 'That'))

    def _py(self, script, *args):
        return [sys.executable, '-I', '-u', script] + [str(a) for a in args]

    def _slots(self):
        try:
            from render import read_slots
            return ['%d: %s  (%s)' % (pc, voice, os.path.splitext(name)[0]) for pc, _, _, name, voice in read_slots()]
        except Exception:                                          # noqa: BLE001 - editor not installed
            return []

    def _slot_pc(self):
        m = re.match(r'(\d+):', self.v['slot'].get())
        return int(m.group(1)) if m else None

    def _tag(self):
        name = self.v['db_name'].get() or os.path.basename(self.v['bank'].get().rstrip('\\/')) or 'test'
        return re.sub(r'[^A-Za-z0-9_-]+', '_', name).strip('_') or 'test'

    # ------------------------------------------------------------------ pick
    def pick_bank(self):
        d = filedialog.askdirectory(title='UTAU voicebank folder', initialdir=self.v['bank'].get() or None)
        if d:
            self.v['bank'].set(os.path.normpath(d))
            name = os.path.basename(os.path.normpath(d))
            safe = re.sub(r'[^\w\- ]+', '_', name).strip() or 'bank'
            self.v['out'].set(os.path.join(DEVKIT, safe + '_seg'))
            ascii_name = re.sub(r'[^A-Za-z0-9]+', '', name)[:20] or 'Singer'
            self.v['db_name'].set(ascii_name)
            self.v['db_path'].set(os.path.join(DEVKIT, ascii_name, ascii_name))
            self.scan()

    def pick_out(self):
        d = filedialog.askdirectory(title='Output folder for the DBTool files')
        if d:
            self.v['out'].set(os.path.normpath(d))

    def pick_db(self):
        d = filedialog.askdirectory(title='Folder to create the singer DB in')
        if d:
            name = self.v['db_name'].get() or 'Singer'
            self.v['db_path'].set(os.path.join(os.path.normpath(d), name, name))

    def pick_dict(self):
        p = filedialog.askopenfilename(title='VOCALOID phonetic dictionary (.txt from the devkit)',
                                       filetypes=[('Phonetic dictionary', '*.txt'), ('All files', '*.*')])
        if p:
            self.v['dict'].set(os.path.normpath(p))

    def _find_dict(self, lang):
        """Fill in the devkit dictionary for lang unless the user chose one for that language."""
        from u2v import dbtool_io
        cur = self.v['dict'].get()
        if cur and os.path.exists(cur) and dbtool_io.dictionary_language(cur) in (lang, None):
            return
        roots = [DEVKIT, os.path.dirname(self.v['dbtool'].get()), os.path.dirname(DEVKIT)]
        found = dbtool_io.find_dictionary(lang, roots)
        self.v['dict'].set(found or '')

    def pick_dbtool(self):
        p = filedialog.askopenfilename(title='VocaloidDBTool3.exe', filetypes=[('DBTool', '*.exe')])
        if p:
            self.v['dbtool'].set(os.path.normpath(p))

    def scan(self):
        """List the bank's folders/suffixes and guess its language (reads oto.ini files only)."""
        bank = self.v['bank'].get()
        self.glist.delete(0, 'end')
        if not os.path.isdir(bank):
            return
        self.info.set('Reading oto.ini files…')
        self.update_idletasks()
        try:
            from u2v import oto, phonemes as P
            entries = oto.read_bank(bank)
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            self.info.set('Could not read the bank: %s' % ex)
            return
        if not entries:
            self.info.set('No oto.ini entries found in this folder.')
            return
        count = {}
        for e in entries:
            count[e.group] = count.get(e.group, 0) + 1
        self.groups = sorted(count.items())
        for g, n in self.groups:
            self.glist.insert('end', '%s   (%d)' % (g or '(main folder)', n))
        self.detected_lang = P.detect_language([e.alias for e in entries])
        lang = {'ja': 'Japanese', 'en': 'English'}.get(self.detected_lang, self.detected_lang)
        self.info.set('%d oto entries in %d folder/suffix group(s), looks %s' % (len(entries), len(count), lang))
        if self.detected_lang in ('ja', 'en'):
            self._find_dict(self.detected_lang)
        if self.detected_lang == 'en':
            self.v['db_lang'].set('English')
        elif self.detected_lang == 'ja':
            self.v['db_lang'].set('Japanese')

    # ---------------------------------------------------------------- actions
    def _convert_args(self):
        v = self.v
        args = ['convert', v['bank'].get(), v['out'].get(), '--lang', v['lang'].get(), '--colors', v['colors'].get(),
                '--keep', v['keep'].get()]
        if v['dict'].get():
            if not os.path.exists(v['dict'].get()):
                raise ValueError('The phonetic dictionary file does not exist:\n%s' % v['dict'].get())
            args += ['--dict', v['dict'].get()]
        sel = [self.groups[i][0] for i in self.glist.curselection()]
        if sel and len(sel) < len(self.groups):
            args += ['--groups', '^(%s)$' % '|'.join(re.escape(g) for g in sel)]
        for k, flag in (('splice', '--no-splice'), ('fill', '--no-fill'), ('normalize', '--no-normalize'),
                        ('derive', '--no-derive'), ('extend_dict', '--no-extend-dict'), ('extras', '--no-extras')):
            if not v[k].get():
                args.append(flag)
        if v['merge_groups'].get():
            args.append('--merge-groups')
        for b in re.split(r'[,;\s]+', v['brighten'].get().strip()):
            if b:
                if not re.fullmatch(r"[^=]+=-?\d+(\.\d+)?", b):
                    raise ValueError('Brighten: "%s" should look like o=3' % b)
                args += ['--brighten', b]
        for u in v['avoid'].get().split(';'):
            if u.strip():
                if len(u.split()) != 2:
                    raise ValueError('Avoid units: "%s" should be two phonemes, e.g. g\' i' % u.strip())
                args += ['--avoid', ' '.join(u.split())]
        return args

    def convert(self, then=None):
        if not os.path.isdir(self.v['bank'].get()):
            return messagebox.showwarning('Convert', 'Choose an UTAU voicebank folder first.')
        if not self.v['out'].get():
            return messagebox.showwarning('Convert', 'Choose an output folder.')
        if not self.v['dict'].get():
            return messagebox.showwarning(
                'Convert', 'No phonetic dictionary found.\n\nIt comes with the VOCALOID3 devkit: '
                '"Japanese Dictionary\\Japanese_Dictionary.txt" (English banks: "English Dictionary\\'
                'english_phonetic_dictionary_20061220.txt").\n\nPress Browse… next to "Phonetic dictionary" and pick it.')
        try:
            args = self._convert_args()
        except ValueError as ex:
            return messagebox.showwarning('Convert', str(ex))
        self._save()
        self.runner.start(self._py(CLI, *args), 'Converting',
                          done=lambda code: then() if (then and code == 0) else None)

    def validate(self):
        out = self.v['out'].get()
        if not os.path.isdir(out):
            return messagebox.showwarning('Validate', 'Convert the bank first.')
        lang = 'en' if (self.v['lang'].get() == 'en' or self.detected_lang == 'en') else 'ja'
        self.runner.start(self._py(CLI, 'validate', out, '--lang', lang), 'Validating')

    def build(self):
        out, db = self.v['out'].get(), self.v['db_path'].get()
        if not os.path.exists(os.path.join(out, 'dictionary.txt')):
            return messagebox.showwarning('Build', 'The output folder has no dictionary.txt, so it was not converted '
                                          '(or the conversion could not find the phonetic dictionary).\n\n'
                                          'Set "Phonetic dictionary" at the top and press Convert again.')
        if not db:
            return messagebox.showwarning('Build', 'Enter where to create the DB (folder\\Name).')
        if os.path.exists(db + '.tree'):
            return messagebox.showwarning('Build', '%s already exists. Pick a new name or folder.' % db)
        if not os.path.exists(self.v['dbtool'].get()):
            return messagebox.showwarning('Build', 'VocaloidDBTool3.exe not found. Set its path.')
        self._save()
        self.runner.start(self._py(CLI, 'build', out, db, '--name', self.v['db_name'].get() or 'Singer',
                                   '--language', self.v['db_lang'].get(), '--dbtool', self.v['dbtool'].get()),
                          'Building the singer DB')

    def test(self):
        pc = self._slot_pc()
        if pc is None:
            return messagebox.showwarning('Test', 'Choose a voice slot (the dev editor must be installed).')
        out = self.v['out'].get()
        if not os.path.exists(os.path.join(out, 'dictionary.txt')):
            return messagebox.showwarning('Test', 'Convert the bank first.')
        if not messagebox.askokcancel('Test in VOCALOID', 'Close the VOCALOID4 Editor for Developer if it is open.\n\n'
                                      'Voice slot %d will sing the test DB until you press "Restore my voice slot".'
                                      % pc):
            return
        self._save()
        en = self.v['song'].get().startswith('English')
        args = [self.v['bank'].get(), self._tag(), '--pc', pc, '--seg', out,
                '--language', 'English' if en else self.v['db_lang'].get(), '--song', 'en' if en else 'test']
        if self.v['suffixes'].get().strip():
            args += ['--suffixes', self.v['suffixes'].get().strip()]
        self.runner.start(self._py(os.path.join(TOOLS, 'cycle.py'), *args), 'Building and rendering the test song')

    def play(self):
        work = os.path.join(os.path.dirname(DEVKIT), 'render_tests', self._tag())
        wavs = sorted((os.path.join(work, f) for f in os.listdir(work) if f.endswith('.wav')),
                      key=os.path.getmtime) if os.path.isdir(work) else []
        if not wavs:
            return messagebox.showinfo('Play', 'No render yet for "%s".' % self._tag())
        os.startfile(wavs[-1])

    def restore(self):
        pc = self._slot_pc()
        if pc is None:
            return messagebox.showwarning('Restore', 'Choose the voice slot to restore.')
        self.runner.start(self._py(os.path.join(TOOLS, 'slot.py'), 'restore', pc), 'Restoring voice slot %d' % pc)


def main():
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)         # sharp text on scaled displays
        except Exception:                                          # noqa: BLE001
            pass
    App().mainloop()


if __name__ == '__main__':
    main()
