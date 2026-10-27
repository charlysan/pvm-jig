#!/usr/bin/env python3
"""pvm-jig panel: Tk GUI for the PVM I2C bridge, one tab per model group.

Run:  python3 pvm-jig-panel.py [-p /dev/cu.usbserial-210] [-m pvm-14n2u]
Needs Python 3.11+, pyserial and tkinter.
"""

import argparse
import glob
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from pvm import __version__
from pvm.device import EEPROM_SIZE, Device, format_dump, parse_dump
from pvm.link import Link, find_port
from pvm.model import Model, Session

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
DUMPS = os.path.join(os.path.dirname(HERE), "dumps")
DEFAULT_MODEL = "pvm-14n2u"
CHANGED, SAVED = "#d35400", "#27ae60"
DANGER_COLOR = {"high": "#c0392b", "medium": "#d35400"}


def find_models():
    """[(display name, path)] for every models/*.toml."""
    out = []
    for path in sorted(glob.glob(os.path.join(MODELS, "*.toml"))):
        try:
            out.append(("%s  (%s)" % (Model(path).name, os.path.basename(path)), path))
        except Exception:
            pass
    return out


class Worker:
    """Runs library calls on one background thread. Results and log lines go into a queue that the
    Tk thread drains (Tk itself is never touched from this thread)."""

    def __init__(self):
        self.jobs = queue.Queue()
        self.out = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def run(self, fn, on_done=None):
        self.jobs.put((fn, on_done))

    def log(self, text):
        self.out.put(("log", str(text)))

    def _loop(self):
        while True:
            fn, on_done = self.jobs.get()
            if fn is None:
                return
            try:
                ok, result = True, fn()
            except Exception as e:
                ok, result = False, e
                self.log("! %s" % (e.args[0] if e.args else e))
            if on_done:
                self.out.put(("done", (on_done, ok, result)))

    def stop(self):
        self.jobs.put((None, None))


class Tooltip:
    """Shows text in a small borderless window while the mouse rests on a widget."""

    def __init__(self, widget, text_fn, delay=400):
        self.widget, self.text_fn, self.delay = widget, text_fn, delay
        self.job = self.win = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, _e=None):
        self._hide()
        self.job = self.widget.after(self.delay, self._show)

    def _show(self):
        x = self.widget.winfo_pointerx() + 14
        y = self.widget.winfo_pointery() + 10
        self.win = tk.Toplevel(self.widget)
        self.win.wm_overrideredirect(True)
        self.win.wm_geometry("+%d+%d" % (x, y))
        tk.Label(self.win, text=self.text_fn(), justify="left", wraplength=420, background="#ffffe0",
                 foreground="#000", relief="solid", borderwidth=1, padx=6, pady=4).pack()

    def _hide(self, _e=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.win:
            self.win.destroy()
            self.win = None


class Row:
    """One item: spinner in the menu's units, raw hex, Set button, state."""

    def __init__(self, app, parent, r, item):
        self.app, self.item = app, item
        self.raw = None
        self.changed = False
        o = item.osd
        if o and (o.get("offset", 0) or o.get("shift", 0)):
            self.unit = "osd-hex" if o.get("hex") else "osd"
        elif o is not None or item.signed:
            self.unit = "dec"
        else:
            self.unit = "hex"
        level = (item.danger or {}).get("level")
        name = item.title + (" !" if level in ("high", "medium") else "")
        label = ttk.Label(parent, text=name, width=24, foreground=DANGER_COLOR.get(level, ""),
                          cursor="question_arrow")
        label.grid(row=r, column=0, sticky="w", pady=1)
        Tooltip(label, self.help_text)
        menu = tk.Menu(label, tearoff=0)
        menu.add_command(label="Copy description", command=self.copy_help)
        for seq in ("<Button-2>", "<Button-3>", "<Control-Button-1>"):
            label.bind(seq, lambda e: menu.tk_popup(e.x_root, e.y_root))
        self.spin = ttk.Spinbox(parent, width=8, from_=-1e9, to=1e9)
        self.spin.grid(row=r, column=1, padx=2)
        self.spin.bind("<<Increment>>", lambda _e: self.step(1))
        self.spin.bind("<<Decrement>>", lambda _e: self.step(-1))
        self.spin.bind("<Return>", lambda _e: self.send())
        self.raw_label = ttk.Label(parent, width=8, foreground="#666")
        self.raw_label.grid(row=r, column=2, sticky="w")
        self.button = ttk.Button(parent, text="Set", width=4, command=self.send)
        self.button.grid(row=r, column=3, padx=4)
        self.live_var = None
        if app.model.can_live(item):
            self.live_var = tk.BooleanVar(value=item.key not in app.no_live)
            check = ttk.Checkbutton(parent, text="live calc", variable=self.live_var, command=self.toggle_live)
            check.grid(row=r, column=4, sticky="w")
            Tooltip(check, self.calc_text)
        how = "live" if item.live or app.model.can_live(item) else "on release" if not item.when else "live if %s" % item.when
        self.state = ttk.Label(parent, width=16, text=how, foreground="#666")
        self.state.grid(row=r, column=5, sticky="w")
        ttk.Label(parent, text=self.where(), foreground="#888").grid(row=r, column=6, sticky="w", padx=6)
        self.enable(False)

    def toggle_live(self):
        on = self.live_var.get()
        (self.app.no_live.discard if on else self.app.no_live.add)(self.item.key)
        if not self.changed:
            self.state.config(text=self.app.live_text(self.item), foreground="#666")
        self.app.log("%s: %s" % (self.item.label, "live (calculated by this tool)" if on
                                 else "not live (the MCU calculates on release)"))

    def where(self):
        s = "EEPROM %s" % self.item.location()
        if self.item.vdp:
            s += " -> %s" % self.item.target() + (" (%s)" % self.item.when if self.item.when else "")
        if self.item.calc:
            s += " -> calc %s" % ", ".join(self.item.calc)
        return s

    def copy_help(self):
        root = self.spin.winfo_toplevel()
        root.clipboard_clear()
        root.clipboard_append("%s\n\n%s" % (self.item.label, self.help_text()))
        self.app.log("Copied the description of %s" % self.item.label)

    def help_text(self):
        i = self.item
        parts = [i.help or "(no description yet)"]
        if i.danger:
            parts.append("%s RISK: %s" % (i.danger["level"].upper(), i.danger["text"]))
        if i.safe_range():
            parts.append("Safe range: %s" % i.safe_range())
        parts.append("%s.  %s" % (self.where(), "Changes the picture live." if self.app.is_live(i)
                                  else "Applied when BINT is released (MCU reload)."))
        return "\n\n".join(parts)

    def calc_text(self):
        parts = []
        for name in self.item.calc:
            c = self.app.model.calc[name]
            targets = ", ".join(c.target_name(n) for n in range(len(c.targets)))
            parts.append("%s -> %s\n%s" % (name, targets, c.formula()) + ("\n%s" % c.note if c.note else ""))
        parts.append("Ticked: the tool calculates and writes the VDP at once.\n"
                     "Unticked: only the EEPROM changes; the MCU calculates it when BINT is released.")
        return "\n\n".join(parts)

    def text(self, raw):
        if self.unit in ("osd", "osd-hex"):
            return self.item.osd_value(raw)
        if self.unit == "dec":
            return str(self.item.decimal(raw))
        return "%0*X" % (self.item.width(), raw)

    def prefix(self):
        return {"osd": "osd:", "osd-hex": "osd:", "dec": "d:", "hex": ""}[self.unit]

    def parse(self, text):
        return self.item.parse(self.prefix() + text.strip(), lambda: self.raw)

    def show(self, raw, state=None, color="#666"):
        self.raw = raw
        self.spin.set(self.text(raw))
        self.raw_label.config(text="0x%0*X" % (self.item.width(), raw))
        if state is not None:
            self.state.config(text=state, foreground=color)

    def step(self, d):
        if self.raw is None or not self.app.held:
            return "break"
        try:
            v = int(self.spin.get(), 16 if self.unit in ("hex", "osd-hex") else 10) + d
            raw = self.parse(("%X" % v if v >= 0 else "-%X" % -v) if self.unit in ("hex", "osd-hex") else str(v))
        except ValueError:
            return "break"
        if 0 <= raw <= self.item.limit():
            self.spin.set(self.text(raw))
        if self.app.auto_set.get():
            self.send()
        return "break"

    def send(self):
        if not self.app.held or self.raw is None:
            return
        try:
            raw = self.parse(self.spin.get())
        except ValueError as e:
            self.app.log("! %s" % e)
            self.show(self.raw)
            return
        self.app.set_item(self, raw)

    def enable(self, on):
        for w in (self.spin, self.button):
            w.state(["!disabled"] if on else ["disabled"])


class App:
    def __init__(self, root, port, model_path):
        self.root = root
        self.models = find_models()
        self.model = Model(model_path)
        self.model_path = model_path
        self.session = None
        self.held = False
        self.rows = {}
        self.no_live = set()
        self.history, self.hist_pos = [], 0
        self.worker = Worker()

        root.minsize(760, 560)
        outer = ttk.Frame(root, padding=8)
        outer.pack(fill="both", expand=True)
        self._build_status_bar(outer, port)
        self._build_session_bar(outer)

        paned = ttk.PanedWindow(outer, orient="vertical")
        paned.pack(fill="both", expand=True)
        self.tabs = ttk.Notebook(paned)
        bottom = ttk.Frame(paned)
        paned.add(self.tabs, weight=3)
        paned.add(bottom, weight=1)
        self._build_tabs()
        self._build_raw_row(bottom)
        self._build_console(bottom)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(200, self.connect)
        root.after(50, self._drain)

    def _drain(self):
        while True:
            try:
                kind, payload = self.worker.out.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log_ui(payload)
            else:
                fn, ok, result = payload
                fn(ok, result)
        self.root.after(50, self._drain)

    # ---- model / tabs ----
    def _build_tabs(self):
        for tab in self.tabs.tabs():
            self.tabs.nametowidget(tab).destroy()
        self.rows = {}
        for group in self.model.groups():
            frame = ttk.Frame(self.tabs, padding=6)
            self.tabs.add(frame, text=group.capitalize())
            items = [i for i in self.model.items.values() if i.group == group]
            for r, item in enumerate(items):
                self.rows[item.key] = Row(self, frame, r, item)
        self.root.title("pvm-jig  v%s  -  %s" % (__version__, self.model.name))

    def select_model(self, _event=None):
        path = dict(self.models).get(self.model_box.get())
        if not path or path == self.model_path:
            return
        if self.held:
            messagebox.showinfo("Model", "Release BINT before switching models.")
            self._show_model()
            return
        self.model, self.model_path = Model(path), path
        if self.session is not None:
            self.session.model = self.model
            self.session.dev.vdp = self.model.vdp_addr
            self.session.pending.clear()
        self.no_live.clear()
        self._build_tabs()
        self.log("Model: %s" % self.model.name)

    def _show_model(self):
        for name, path in self.models:
            if path == self.model_path:
                self.model_box.set(name)

    # ---- bars ----
    def _build_status_bar(self, parent, port):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 4))
        self.dot = ttk.Label(bar, text="●", foreground="#c0392b")
        self.dot.pack(side="left")
        self.status = ttk.Label(bar, text="Disconnected")
        self.status.pack(side="left", padx=6)
        ttk.Label(bar, text="Model:").pack(side="left", padx=(12, 3))
        self.model_box = ttk.Combobox(bar, state="readonly", width=32, values=[n for n, _p in self.models])
        self.model_box.pack(side="left")
        self.model_box.bind("<<ComboboxSelected>>", self.select_model)
        self._show_model()
        self.auto_set = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Auto-set spinners", variable=self.auto_set).pack(side="left", padx=12)
        ttk.Button(bar, text="Reconnect", command=self.connect).pack(side="right")
        self.port = ttk.Entry(bar, width=24)
        self.port.insert(0, port or find_port() or "")
        self.port.pack(side="right")
        ttk.Label(bar, text="Port:").pack(side="right", padx=(0, 3))

    def _build_session_bar(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 6))
        self.bint_var = tk.BooleanVar(value=False)
        self.bint_check = ttk.Checkbutton(bar, text="BINT (jig mode: live edit)", variable=self.bint_var,
                                          command=self.toggle_bint)
        self.bint_check.pack(side="left")
        self.save_button = ttk.Button(bar, text="Save to EEPROM", command=self.save)
        self.save_button.pack(side="left", padx=8)
        self.revert_button = ttk.Button(bar, text="Revert", command=self.revert)
        self.revert_button.pack(side="left")
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(side="left", padx=8)
        self.pending_label = ttk.Label(bar, text="")
        self.pending_label.pack(side="left", padx=8)
        self.restore_button = ttk.Button(bar, text="Restore EEPROM…", command=self.restore)
        self.restore_button.pack(side="right")
        self.dump_button = ttk.Button(bar, text="Dump EEPROM…", command=self.dump)
        self.dump_button.pack(side="right", padx=4)
        self._session_buttons(False)

    def _session_buttons(self, on):
        for b in (self.save_button, self.revert_button, self.dump_button, self.restore_button):
            b.state(["!disabled"] if on else ["disabled"])

    # ---- EEPROM dump / restore ----
    def dump(self):
        path = filedialog.asksaveasfilename(
            title="Dump EEPROM", initialdir=DUMPS if os.path.isdir(DUMPS) else HERE,
            initialfile=time.strftime("eeprom_%Y%m%d_%H%M.hex"), defaultextension=".hex")
        if not path:
            return

        def job():
            text = format_dump(self.session.dev.ee_read(0, EEPROM_SIZE))
            with open(path, "w") as f:
                f.write(text + "\n")
            return text.splitlines()[-1]

        self.worker.run(job, lambda ok, last: ok and self.log("Dumped to %s (%s)" % (path, last)))

    def restore(self):
        path = filedialog.askopenfilename(title="Restore EEPROM from dump",
                                          initialdir=DUMPS if os.path.isdir(DUMPS) else HERE)
        if not path:
            return
        try:
            ref = parse_dump(path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Restore", "Can't read %s:\n%s" % (path, e))
            return
        if len(ref) != EEPROM_SIZE:
            messagebox.showerror("Restore", "%s has %d bytes, expected %d." % (path, len(ref), EEPROM_SIZE))
            return
        self.worker.run(lambda: self.session.dev.ee_diff(ref), lambda ok, diff: ok and self._confirm_restore(path, ref, diff))

    def _confirm_restore(self, path, ref, diff):
        if not diff:
            messagebox.showinfo("Restore", "The EEPROM already matches %s." % os.path.basename(path))
            return
        lines = ["%03X: %02X -> %02X" % d for d in diff[:20]] + (["…"] if len(diff) > 20 else [])
        if not messagebox.askokcancel("Restore EEPROM",
                                      "%d byte(s) differ from %s:\n\n%s\n\nWrite them? Release BINT afterwards to apply."
                                      % (len(diff), os.path.basename(path), "\n".join(lines))):
            return

        def done(ok, left):
            if not ok:
                return
            self.log("Restored %d byte(s) from %s%s" % (len(diff), path, "" if not left else
                     "; verify FAILED at " + " ".join("%03X" % i for i in left)))
            self.refresh()

        self.worker.run(lambda: self.session.dev.ee_restore(ref, diff), done)

    # ---- connection ----
    def connect(self):
        port = self.port.get().strip() or None
        self.status.config(text="Connecting ...")

        def job():
            if self.session is not None:
                self.session.dev.link.close()
            link = Link(port)
            self.log("Connected %s: %s" % (link.port, link.banner))
            return Session(Device(link, self.model.vdp_addr), self.model)

        self.worker.run(job, self._on_connect)

    def _on_connect(self, ok, session):
        if not ok:
            self.dot.config(foreground="#c0392b")
            self.status.config(text="Disconnected")
            return
        self.session = session
        session.no_live = self.no_live
        self.dot.config(foreground=SAVED)
        self.status.config(text="Connected  %s" % session.dev.link.port)
        self._set_held(False)

    # ---- BINT session ----
    def toggle_bint(self):
        if self.session is None:
            self.bint_var.set(False)
            return
        if self.bint_var.get():
            self.log("Asserting BINT, waiting for jig mode ...")
            self.worker.run(lambda: (self.session.dev.bint(True), self._read_all())[1], self._on_held)
            return
        n = len(self.session.pending)
        if n:
            answer = messagebox.askyesnocancel("Unsaved changes",
                                               "%d changed item(s) are not saved.\nSave them before releasing BINT?" % n)
            if answer is None:
                self.bint_var.set(True)
                return
            if answer:
                self.worker.run(self.session.save, lambda ok, k: self.log("Saved %s item(s)" % k))
        self.release()

    def _read_all(self):
        data = self.session.dev.ee_read(0, EEPROM_SIZE)
        self.session.ctx = None
        self.session.context()
        return data

    def is_live(self, item):
        if item.key in self.no_live:
            return False
        if self.session is not None and self.session.ctx is not None:
            return self.session.live(item)
        return item.live or self.model.can_live(item)

    def live_text(self, item):
        if self.is_live(item):
            return "live"
        return "on release (CPU)" if item.calc else "on release"

    def _on_held(self, ok, data):
        if not ok:
            self.bint_var.set(False)
            self.log("BINT released (jig mode not reached)")
            return
        self._fill(data)
        self._set_held(True)
        ctx = ", ".join("%s %s" % kv for kv in sorted(self.session.ctx.items()))
        self.log("Jig mode: VDP reachable, %d items read from the EEPROM (%s)" % (len(self.rows), ctx))

    def _fill(self, data):
        for key, row in self.rows.items():
            row.changed = False
            row.show(self.model.decode(row.item, data), self.live_text(row.item))
        self._update_pending()

    def release(self):
        self.log("Releasing BINT: the MCU reloads from the EEPROM (short blank)")
        self.worker.run(self.session.discard, lambda ok, _r: self._set_held(False))

    def _set_held(self, on):
        self.held = on
        self.bint_var.set(on)
        for row in self.rows.values():
            row.enable(on)
        self._session_buttons(on)
        self._update_pending()

    def save(self):
        def done(ok, n):
            if ok:
                self.log("Saved %d item(s) to the EEPROM; release BINT to apply" % n)
                for row in self.rows.values():
                    if row.changed:
                        row.changed = False
                        row.state.config(text="saved", foreground=SAVED)
                self._update_pending()

        self.worker.run(self.session.save, done)

    def revert(self):
        if self.session.pending and not messagebox.askokcancel(
                "Revert", "Discard %d change(s) and reload from the EEPROM?" % len(self.session.pending)):
            return
        self.release()

    def refresh(self):
        if self.held:
            self.worker.run(self._read_all, lambda ok, data: ok and self._fill(data))

    def set_item(self, row, raw):
        item = row.item
        force = False
        if not item.safe(raw):
            text = item.danger["text"] if item.danger else ""
            if not messagebox.askokcancel(
                    "Outside the safe range",
                    "%s = %d is outside the safe range %s.\n\n%s\n\nWrite it anyway?"
                    % (item.label, item.decimal(raw), item.safe_range(), text), icon="warning"):
                row.show(row.raw)
                return
            force = True

        def done(ok, live):
            if not ok:
                row.show(row.raw)
                return
            row.changed = True
            row.show(raw, "changed" + ("" if live else " (on release)"), CHANGED)
            self.log("%s = %s %s" % (item.label, item.display(raw), "(live)" if live else "(EEPROM only)"))
            self._update_pending()

        self.worker.run(lambda: self.session.set(item, raw, force), done)

    def _update_pending(self):
        n = len(self.session.pending) if self.session else 0
        self.pending_label.config(text="%d unsaved" % n if n else "", foreground=CHANGED)

    # ---- raw console ----
    def _build_raw_row(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(4, 2))
        ttk.Label(row, text="Raw:").pack(side="left")
        self.raw_entry = ttk.Entry(row)
        self.raw_entry.pack(side="left", fill="x", expand=True, padx=4)
        self.raw_entry.bind("<Return>", lambda _e: self.send_raw())
        self.raw_entry.bind("<Up>", lambda _e: self._history(-1))
        self.raw_entry.bind("<Down>", lambda _e: self._history(1))
        ttk.Button(row, text="Send", command=self.send_raw).pack(side="left")
        ttk.Button(row, text="Clear", command=self.clear_console).pack(side="left", padx=4)

    def send_raw(self):
        line = self.raw_entry.get().strip()
        if not line or self.session is None:
            return
        text = self.model.raw_danger(line)
        if text and not messagebox.askokcancel("Danger register", "%s\n\n%s\n\nSend it anyway?" % (line, text),
                                               icon="warning"):
            return
        self.history.append(line)
        self.hist_pos = len(self.history)
        self.raw_entry.delete(0, "end")
        self.log(">> " + line)
        dev = self.session.dev

        def job():
            out = dev.raw(line)
            return out, dev.bint()

        def done(ok, result):
            if ok:
                self.log(result[0])
                if result[1] != self.held:
                    self.log("BINT changed by a raw command; press Refresh after 'b 1'")
                    self.held = result[1]
                    self._set_held(result[1])

        self.worker.run(job, done)

    def _history(self, step):
        if not self.history:
            return "break"
        self.hist_pos = max(0, min(len(self.history), self.hist_pos + step))
        self.raw_entry.delete(0, "end")
        if self.hist_pos < len(self.history):
            self.raw_entry.insert(0, self.history[self.hist_pos])
        return "break"

    def _build_console(self, parent):
        self.console = ScrolledText(parent, height=8, state="disabled", wrap="word")
        self.console.pack(fill="both", expand=True, pady=(2, 0))

    def clear_console(self):
        self.console.config(state="normal")
        self.console.delete("1.0", "end")
        self.console.config(state="disabled")

    def log(self, text):
        self.worker.log(text)

    def _log_ui(self, text):
        self.console.config(state="normal")
        self.console.insert("end", text.rstrip("\n") + "\n")
        self.console.see("end")
        self.console.config(state="disabled")

    def on_close(self):
        s = self.session
        if s is not None and self.held and s.pending and not messagebox.askokcancel(
                "Quit", "%d unsaved change(s) will be discarded. Quit?" % len(s.pending)):
            return

        def job():
            if s is not None:
                if self.held:
                    s.discard()
                s.dev.link.close()

        def done(ok, _r):
            self.worker.stop()
            self.root.destroy()

        self.worker.run(job, done)


def main():
    ap = argparse.ArgumentParser(description="pvm-jig control panel")
    ap.add_argument("-p", "--port", help="serial port (default: first USB serial port)")
    ap.add_argument("-m", "--model", default=DEFAULT_MODEL, help="model name or .toml path")
    a = ap.parse_args()
    path = a.model if os.path.exists(a.model) else os.path.join(MODELS, a.model + ".toml")
    root = tk.Tk()
    App(root, a.port, path)
    root.mainloop()


if __name__ == "__main__":
    main()
