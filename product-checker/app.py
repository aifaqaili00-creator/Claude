"""Product Checker: Amazon AU / UAE / US delivery check + Helium 10 export ranking.

Run with start.bat (Windows) or:  python app.py
"""
import queue
import re
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

import pandas as pd

import amazon_check as ac
import file_rank as fr

APP_TITLE = 'Product Checker - AU / UAE / US'


class Worker(threading.Thread):
    """Owns the browser. Every browser call runs here, one job at a time."""

    def __init__(self, post):
        super().__init__(daemon=True)
        self.jobs = queue.Queue()
        self.post = post
        self.browser = ac.Browser(log=lambda m: post('log', m))
        self.stop_flag = threading.Event()

    def run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                self.browser.close()
                return
            name, fn = job
            try:
                fn(self.browser)
            except Exception as e:
                self.post('log', '%s failed: %s' % (name, e))
                self.post('log', traceback.format_exc(limit=2))
            finally:
                self.post('idle', name)

    def submit(self, name, fn):
        self.stop_flag.clear()
        self.jobs.put((name, fn))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry('1280x800')
        self.minsize(900, 600)
        self.inbox = queue.Queue()
        self.worker = Worker(lambda kind, data: self.inbox.put((kind, data)))
        self.worker.start()
        self.check_rows = []
        self.rank_all = None
        self.rank_top = None
        self.busy = False
        self._style()
        self._build()
        self.after(100, self._poll)
        self.protocol('WM_DELETE_WINDOW', self._quit)

    # ---------- layout ----------
    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use('vista' if 'vista' in s.theme_names() else 'clam')
        except tk.TclError:
            pass
        s.configure('Treeview', rowheight=24)
        s.configure('Big.TButton', padding=(12, 6))

    def _build(self):
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill='x')
        ttk.Button(top, text='Open browser: log in to Helium 10 / set delivery locations', style='Big.TButton',
                   command=self.open_setup).pack(side='left')
        ttk.Label(top, text='  First time only: log in to Helium 10 and set Amazon delivery to '
                            'AU 2000, UAE Dubai, US 10001. The browser remembers it.',
                  foreground='#555').pack(side='left')

        nb = ttk.Notebook(self)
        nb.pack(fill='both', expand=True, padx=10)
        self.nb = nb
        nb.add(self._check_tab(nb), text='  1. Check a product (local sellers)  ')
        nb.add(self._rank_tab(nb), text='  2. Top 10 from a Helium 10 file  ')

        self.log = ScrolledText(self, height=6, font=('Consolas', 9), state='disabled')
        self.log.pack(fill='x', padx=10, pady=(6, 10))
        self._log('Ready. Tab 1: type a product and press Check. Tab 2: open a Black Box or Xray CSV/Excel.')

    def _tree(self, parent, cols):
        frame = ttk.Frame(parent)
        tree = ttk.Treeview(frame, columns=[c for c, _, _ in cols], show='headings', selectmode='browse')
        for key, head, width in cols:
            tree.heading(key, text=head, command=lambda k=key, t=tree: self._sort(t, k))
            tree.column(key, width=width, stretch=key in ('title', 'delivery'),
                        anchor='w' if key in ('title', 'delivery', 'flags', 'verdict', 'speed', 'market') else 'e')
        ys = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        xs = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        tree.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        xs.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return frame, tree

    def _check_tab(self, nb):
        tab = ttk.Frame(nb, padding=8)
        bar = ttk.Frame(tab)
        bar.pack(fill='x')
        ttk.Label(bar, text='Product:').pack(side='left')
        self.kw = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.kw, width=30, font=('Segoe UI', 11))
        e.pack(side='left', padx=6)
        e.bind('<Return>', lambda _: self.run_check())
        self.mk_vars = {}
        for code in ac.MARKETS:
            v = tk.BooleanVar(value=True)
            self.mk_vars[code] = v
            ttk.Checkbutton(bar, text=ac.MARKETS[code]['name'], variable=v).pack(side='left', padx=4)
        ttk.Label(bar, text='   Fast = within').pack(side='left')
        self.fast_days = tk.IntVar(value=3)
        ttk.Spinbox(bar, from_=1, to=7, width=3, textvariable=self.fast_days).pack(side='left', padx=3)
        ttk.Label(bar, text='days   Pages:').pack(side='left')
        self.pages = tk.IntVar(value=1)
        ttk.Spinbox(bar, from_=1, to=3, width=3, textvariable=self.pages).pack(side='left', padx=3)
        self.check_btn = ttk.Button(bar, text='Check', style='Big.TButton', command=self.run_check)
        self.check_btn.pack(side='left', padx=(12, 4))
        ttk.Button(bar, text='Stop', command=lambda: self.worker.stop_flag.set()).pack(side='left')
        ttk.Button(bar, text='Save to Excel', command=self.export_check).pack(side='right')

        self.summary = ScrolledText(tab, height=9, font=('Consolas', 10), state='disabled', wrap='word')
        self.summary.pack(fill='x', pady=8)

        cols = [('market', 'Market', 60), ('speed', 'Delivery', 70), ('days', 'Days', 50), ('price', 'Price', 70),
                ('reviews', 'Reviews', 75), ('rating', 'Rating', 55), ('bought', 'Bought/mo', 85),
                ('prime', 'Prime', 50), ('ad', 'Ad', 40), ('title', 'Title', 420), ('delivery', 'Delivery text', 300)]
        frame, self.check_tree = self._tree(tab, cols)
        frame.pack(fill='both', expand=True)
        self.check_tree.tag_configure('fast', background='#e3f4e3')
        self.check_tree.tag_configure('unknown', foreground='#888')
        self.check_tree.bind('<Double-1>', lambda _: self._open_url(self.check_tree, self.check_rows))
        ttk.Label(tab, text='Green rows deliver fast (stock is already in that country). '
                            'Double-click a row to open the product.', foreground='#555').pack(anchor='w', pady=(4, 0))
        return tab

    def _rank_tab(self, nb):
        tab = ttk.Frame(nb, padding=8)
        bar = ttk.Frame(tab)
        bar.pack(fill='x')
        ttk.Button(bar, text='Open CSV / Excel file...', style='Big.TButton', command=self.open_file).pack(side='left')
        ttk.Label(bar, text='   Market:').pack(side='left')
        self.rank_market = tk.StringVar(value='auto')
        cb = ttk.Combobox(bar, textvariable=self.rank_market, values=['auto', 'AU', 'AE', 'US'], width=6, state='readonly')
        cb.pack(side='left', padx=4)
        cb.bind('<<ComboboxSelected>>', lambda _: self.rank_path and self._rank(self.rank_path))
        ttk.Label(bar, text='  Show top').pack(side='left')
        self.top_n = tk.IntVar(value=10)
        sp = ttk.Spinbox(bar, from_=5, to=100, width=4, textvariable=self.top_n,
                         command=lambda: self.rank_path and self._rank(self.rank_path))
        sp.pack(side='left', padx=4)
        ttk.Button(bar, text='Check selected on Amazon', command=self.check_selected).pack(side='left', padx=12)
        ttk.Button(bar, text='Save to Excel', command=self.export_rank).pack(side='right')
        self.rank_path = None
        self.rank_info = ttk.Label(tab, text='Open a Helium 10 export (Black Box, Xray...). '
                                             'Australia / UAE / USA is detected from the file.', wraplength=1200)
        self.rank_info.pack(fill='x', pady=8)
        cols = [('rank', '#', 35), ('verdict', 'Verdict', 85), ('price', 'Price', 85), ('sales', 'Sales/mo', 75),
                ('revenue', 'Revenue/mo', 100), ('reviews', 'Reviews', 65), ('rating', 'Rating', 55),
                ('age', 'Age (mo)', 65), ('trend', '90d trend', 75), ('flags', 'Flags', 170), ('title', 'Title', 440)]
        frame, self.rank_tree = self._tree(tab, cols)
        frame.pack(fill='both', expand=True)
        for v, c in (('Good pick', '#e3f4e3'), ('Check first', '#fdf1d6'), ('Close', '#eceeed'), ('Skip', '#f8e1e1')):
            self.rank_tree.tag_configure(v, background=c)
        self.rank_tree.bind('<Double-1>', lambda _: self._open_url(self.rank_tree, self.rank_top))
        ttk.Label(tab, text='Good pick = enough sales, few reviews, no warning flags. '
                            'Check first = numbers are good but read the flags. Double-click to open on Amazon.',
                  foreground='#555').pack(anchor='w', pady=(4, 0))
        return tab

    # ---------- actions ----------
    def open_setup(self):
        self._log('Opening the browser (Helium 10 + Amazon AU, UAE, US)...')
        self.worker.submit('Open browser', lambda b: b.open_setup())

    def run_check(self):
        kw = self.kw.get().strip()
        markets = [c for c, v in self.mk_vars.items() if v.get()]
        if not kw or not markets:
            messagebox.showinfo(APP_TITLE, 'Type a product and tick at least one country.')
            return
        if self.busy:
            messagebox.showinfo(APP_TITLE, 'A check is already running. Press Stop or wait.')
            return
        fast, pages = _num_var(self.fast_days, 3, 1, 7), _num_var(self.pages, 1, 1, 3)
        self.busy = True
        self.check_btn.configure(state='disabled')
        self.check_rows = []
        self.check_tree.delete(*self.check_tree.get_children())
        self._set_text(self.summary, 'Checking "%s" ...\n' % kw)
        stop = self.worker.stop_flag.is_set

        def job(b):
            for code in markets:
                if stop():
                    break
                self.inbox.put(('log', 'Searching %s for "%s"...' % (ac.MARKETS[code]['name'], kw)))
                location, rows = b.check(kw, code, fast_days=fast, pages=pages, stop=stop)
                self.inbox.put(('check', (code, location, rows, fast)))
        self.worker.submit('Check', job)

    def open_file(self):
        path = filedialog.askopenfilename(title='Open a Helium 10 export',
                                          filetypes=[('CSV or Excel', '*.csv *.xlsx *.xls'), ('All files', '*.*')])
        if path:
            self._rank(path)

    def _rank(self, path):
        try:
            mk, top, all_, summary = fr.rank(path, self.rank_market.get(), _num_var(self.top_n, 10, 1, 500))
        except Exception as e:
            messagebox.showerror(APP_TITLE, 'Could not read this file:\n%s' % e)
            return
        self.rank_path, self.rank_all, self.rank_top = path, all_, top.to_dict('records')
        self.rank_info.configure(text='%s\n%s' % (path, summary))
        t = self.rank_tree
        t.delete(*t.get_children())
        cur = fr.TARGETS[mk]['currency']
        for i, r in enumerate(self.rank_top):
            t.insert('', 'end', iid=str(i), tags=(r['verdict'],), values=(
                r['rank'], r['verdict'], _money(r['price'], cur), _int(r['sales']), _money(r['revenue'], cur, 0),
                _int(r['reviews']), _f(r['rating']), _int(r['age']), _pct(r['trend']), r['flags'], r['title']))
        self._log('Ranked %d products from %s.' % (len(all_), path))

    def check_selected(self):
        sel = self.rank_tree.selection()
        if not sel:
            messagebox.showinfo(APP_TITLE, 'Select a product in the list first.')
            return
        row = self.rank_top[int(sel[0])]
        self.kw.set(search_words(row['title'], row.get('brand', '')))
        self.nb.select(0)
        self.run_check()

    def export_check(self):
        if not self.check_rows:
            messagebox.showinfo(APP_TITLE, 'Run a check first.')
            return
        path = filedialog.asksaveasfilename(defaultextension='.xlsx', filetypes=[('Excel', '*.xlsx')],
                                            initialfile='Delivery_check_%s.xlsx' % self.kw.get().strip().replace(' ', '_'))
        if path:
            df = pd.DataFrame(self.check_rows)
            with pd.ExcelWriter(path, engine='openpyxl') as xw:
                pd.DataFrame({'Summary': self.summary.get('1.0', 'end').splitlines()}).to_excel(xw, sheet_name='Summary', index=False)
                df.to_excel(xw, sheet_name='Listings', index=False)
            self._log('Saved %s' % path)

    def export_rank(self):
        if self.rank_all is None:
            messagebox.showinfo(APP_TITLE, 'Open a file first.')
            return
        path = filedialog.asksaveasfilename(defaultextension='.xlsx', filetypes=[('Excel', '*.xlsx')],
                                            initialfile='Top_products.xlsx')
        if path:
            fr.export_excel(self.rank_all, path)
            self._log('Saved %s (all %d products, best first)' % (path, len(self.rank_all)))

    # ---------- results coming back from the worker ----------
    def _poll(self):
        try:
            while True:
                kind, data = self.inbox.get_nowait()
                if kind == 'log':
                    self._log(data)
                elif kind == 'idle':
                    if data == 'Check':
                        self.busy = False
                        self.check_btn.configure(state='normal')
                        self._log('Check finished.')
                elif kind == 'check':
                    self._show_check(*data)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _show_check(self, code, location, rows, fast):
        base = len(self.check_rows)
        self.check_rows.extend(rows)
        self._append_text(self.summary, '\n' + ac.summarize(code, location, rows, fast) + '\n')
        t = self.check_tree
        order = {'fast': 0, 'slow': 1, 'unknown': 2}
        ranked = sorted(enumerate(rows), key=lambda x: (x[1]['sponsored'], order[x[1]['speed']], x[1]['days'] or 99))
        for i, r in ranked:
            t.insert('', 'end', iid=str(base + i), tags=(r['speed'],), values=(
                code, r['speed'], '' if r['days'] is None else r['days'], _f(r['price']), _int(r['reviews']),
                _f(r['rating']), _int(r['bought']), 'yes' if r['prime'] else '', 'ad' if r['sponsored'] else '',
                r['title'], r['delivery']))

    # ---------- helpers ----------
    def _open_url(self, tree, rows):
        sel = tree.selection()
        if sel and rows:
            url = rows[int(sel[0])].get('url')
            if url:
                webbrowser.open(url)

    def _sort(self, tree, col):
        items = [(tree.set(i, col), i) for i in tree.get_children('')]
        def key(v):
            try:
                return (0, float(str(v[0]).replace(',', '').replace('%', '').split()[-1]))
            except (ValueError, IndexError):
                return (1, str(v[0]))
        rev = getattr(tree, '_rev', {}).get(col, False)
        items.sort(key=key, reverse=rev)
        for n, (_, i) in enumerate(items):
            tree.move(i, '', n)
        tree._rev = {col: not rev}

    def _log(self, msg):
        self._append_text(self.log, msg.rstrip() + '\n')

    @staticmethod
    def _set_text(w, text):
        w.configure(state='normal')
        w.delete('1.0', 'end')
        w.insert('end', text)
        w.configure(state='disabled')

    @staticmethod
    def _append_text(w, text):
        w.configure(state='normal')
        w.insert('end', text)
        w.see('end')
        w.configure(state='disabled')

    def _quit(self):
        self.worker.jobs.put(None)
        self.after(300, self.destroy)


FILLER = {'pack', 'packs', 'pcs', 'pc', 'piece', 'pieces', 'set', 'sets', 'with', 'and', 'for', 'the', 'of', 'in',
          'premium', 'heavy', 'duty', 'new', 'large', 'small', 'xl', 'xxl', 'extra', 'quality', 'best', 'black',
          'white', 'grey', 'gray', 'oz', 'ml', 'cm', 'inch', 'inches'}


def search_words(title, brand=''):
    """A short Amazon search phrase from a long listing title: no brand, sizes, counts or filler words."""
    head = re.split(r'[,|\-–(]', title)[0]
    brand_words = set(brand.lower().split())
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'&]+", head)
             if w.lower() not in FILLER and w.lower() not in brand_words and not w.isupper()]
    return ' '.join(words[:5]) or title[:40]


def _num_var(var, default, lo, hi):
    try:
        return max(lo, min(hi, int(var.get())))
    except (tk.TclError, ValueError):
        return default


def _ok(v):
    return v is not None and v == v


def _int(v):
    return f'{int(v):,}' if _ok(v) else ''


def _f(v):
    return f'{v:,.2f}'.rstrip('0').rstrip('.') if _ok(v) else ''


def _money(v, cur, dec=2):
    return f'{cur} {v:,.{dec}f}' if _ok(v) else ''


def _pct(v):
    return f'{v:+.0f}%' if _ok(v) else ''


if __name__ == '__main__':
    App().mainloop()
