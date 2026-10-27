#!/usr/bin/env python3
"""pvm-jig-web: web panel for the PVM over the Nano, made for the MiSTer (runs on any host with the library).

One long-lived Session, like the Tk panel: the serial port stays open, because opening it resets the Nano
(and releases BINT). Serves web/ and a small JSON API. Standard library only, Python 3.9+.

Run:  python3 pvm-jig-web.py [-p /dev/ttyUSB0] [-m pvm-14n2u] [--listen 0.0.0.0:8183] [--idle 120]
Port (auto = first USB serial port, found again on every connect), model and the profile switches are set
from the page too and kept in pvm-jig-web.json next to this file; -p / -m override them at start.

Profiles (profiles-<model>.json): a baseline and per-core snapshots of the profile items. The watcher thread
follows /tmp/CORENAME; with auto-apply on, it writes baseline + core profile to the EEPROM when they differ
(compared with BINT released, so no blank when nothing changes) and releases BINT: the MCU reloads.
"""

import argparse
import glob
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pvm import __version__
from pvm.device import EEPROM_SIZE, Device
from pvm.link import Link, LinkError, find_port, list_ports
from pvm.model import Model, Session, UnsafeValue

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
MODELS = os.path.join(HERE, "models")
CONFIG = os.path.join(HERE, "pvm-jig-web.json")
DEFAULT_MODEL = "pvm-14n2u"
CORENAME = "/tmp/CORENAME"
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
PREFIX = {"osd": "osd:", "osd-hex": "osd:", "dec": "d:", "hex": ""}
# items a profile holds: these groups, plus the centring items, never the model byte or the input select
PROFILE_GROUPS = ("user", "picture", "geometry", "per input")
PROFILE_ALSO = ("h_cent", "v_cent")
PROFILE_NEVER = ("model", "input", "prev_input")
NO_CORE = ("", "MENU")  # menu (or no MiSTer): baseline only


def log(text):
    print(time.strftime("%H:%M:%S"), text, file=sys.stderr, flush=True)


def unit(item):
    """Units of the value box, as in the Tk panel: OSD value, decimal or raw hex."""
    o = item.osd
    if o and (o.get("offset", 0) or o.get("shift", 0)):
        return "osd-hex" if o.get("hex") else "osd"
    if o is not None or item.signed:
        return "dec"
    return "hex"


def text(item, raw):
    u = unit(item)
    if u in ("osd", "osd-hex"):
        return item.osd_value(raw)
    if u == "dec":
        return str(item.decimal(raw))
    return "%0*X" % (item.width(), raw)


def where(item):
    s = "EEPROM %s" % item.location()
    if item.vdp:
        s += " -> %s" % item.target() + (" (%s)" % item.when if item.when else "")
    if item.calc:
        s += " -> calc %s" % ", ".join(item.calc)
    return s


def core():
    try:
        with open(CORENAME) as f:
            return f.read().strip()
    except OSError:
        return ""


def model_path(name):
    return name if os.path.exists(name) else os.path.join(MODELS, name + ".toml")


def find_models():
    out = []
    for path in sorted(glob.glob(os.path.join(MODELS, "*.toml"))):
        try:
            out.append({"id": os.path.basename(path)[:-5], "name": Model(path).name})
        except Exception:
            pass
    return out


def profile_items(model):
    return [i for i in model.items.values()
            if (i.group in PROFILE_GROUPS or i.key in PROFILE_ALSO) and i.key not in PROFILE_NEVER]


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_json(path, d):
    try:
        with open(path, "w") as f:
            json.dump(d, f, indent=1, sort_keys=True)
    except OSError as e:
        log("can't save %s: %s" % (path, e))


class ApiError(Exception):
    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code = code
        self.body = {"error": message, **extra}


class Panel:
    """The PVM session behind the API. Every call holds the lock: one serial conversation at a time."""

    def __init__(self, port, model_id, idle, cfg):
        self.port = port  # None = auto
        self.model_id = model_id
        self.model = Model(model_path(model_id))
        self.idle = idle
        self.lock = threading.Lock()
        self.session = None
        self.held = False
        self.last = time.time()
        self.note = ""
        self.error = ""
        self.auto_apply = bool(cfg.get("auto_apply"))
        self.delay = float(cfg.get("delay", 1.5))
        self.applied = None  # core whose profile is known to be in the EEPROM (None = check again)
        self.last_apply = ""
        self.watcher = None  # stop Event of the running watcher thread
        self.load_profiles()
        if cfg.get("watcher"):
            self.start_watcher()

    def connect(self):
        if self.session is None:
            if self.port is None and find_port() is None:
                raise LinkError("no USB serial port found")
            link = Link(self.port)
            log("connected %s: %s" % (link.port, link.banner))
            self.session = Session(Device(link, self.model.vdp_addr), self.model)
            self.held = False
            self.error = ""
        return self.session

    def drop(self, e=None):
        if e is not None:
            log("link lost: %s" % e)
        try:
            self.session.dev.link.close()
        except Exception:
            pass
        self.session = None
        self.held = False
        self.error = "" if e is None else str(e)

    def call(self, fn, *args, activity=True):
        with self.lock:
            if activity:
                self.last = time.time()
            try:
                return fn(*args)
            except OSError as e:  # serial port gone (Nano unplugged): reconnect on the next call
                if self.session is not None:
                    self.drop(e)
                raise ApiError(502, "serial port: %s" % e)

    def need_held(self):
        if not self.held:
            raise ApiError(409, "not editing: press Edit first")
        return self.session

    # ---- API ----
    def status(self):
        d = {"version": __version__, "model": self.model.name, "core": core(), "held": self.held,
             "idle": self.idle, "note": self.note, "port_setting": self.port or "auto",
             "watcher": self.watcher is not None, "auto_apply": self.auto_apply, "last_apply": self.last_apply}
        try:
            s = self.connect()
        except (OSError, LinkError) as e:
            d.update(connected=False, error=self.error or str(e))
            return d
        d.update(connected=True, port=s.dev.link.port, banner=s.dev.link.banner, pending=len(s.pending))
        if self.held:
            d.update(pvm=True, context=s.ctx or {})
        else:
            d["pvm"] = 0x50 in s.dev.scan(0x50, 0x50)  # address-only probe, safe with BINT released
        return d

    def items(self):
        out = []
        for group in self.model.groups():
            rows = []
            for i in self.model.items.values():
                if i.group != group:
                    continue
                rows.append({"key": i.key, "title": i.title, "unit": unit(i), "help": i.help,
                             "danger": i.danger, "range": i.safe_range(), "where": where(i),
                             "live": i.live or self.model.can_live(i), "when": i.when})
            out.append({"name": group, "items": rows})
        return {"model": self.model.name, "groups": out}

    def values(self):
        s = self.need_held()
        data = bytearray(s.dev.ee_read(0, EEPROM_SIZE))
        out = {}
        for key, item in self.model.items.items():
            raw = s.pending.get(key, self.model.decode(item, data))
            out[key] = {"raw": raw, "text": text(item, raw), "live": s.live(item), "pending": key in s.pending}
        return {"values": out, "context": s.ctx}

    def hold(self):
        s = self.connect()
        if not self.held:
            s.dev.bint(True)  # waits for jig mode, raises "check the BINT wire" if the VDP doesn't answer
            s.ctx = None
            s.context()
            self.held = True
            self.note = ""
            log("editing: BINT asserted (%s)" % ", ".join("%s %s" % kv for kv in sorted(s.ctx.items())))
        return self.values()

    def set(self, body):
        s = self.need_held()
        item = self.model.items.get(body.get("key", ""))
        if item is None:
            raise ApiError(400, "unknown item: %s" % body.get("key"))
        raw = item.parse(PREFIX[unit(item)] + str(body.get("text", "")).strip(), lambda: s.get(item))
        try:
            live = s.set(item, raw, bool(body.get("force")))
        except UnsafeValue as e:
            raise ApiError(409, str(e), unsafe=True, danger=(item.danger or {}).get("text", ""))
        log("%s = %s %s" % (item.label, item.display(raw), "(live)" if live else "(EEPROM only)"))
        return {"key": item.key, "raw": raw, "text": text(item, raw), "live": live, "pending": len(s.pending)}

    def save(self):
        s = self.need_held()
        n = len(s.pending)
        s.commit()
        self.held = False
        log("saved %d item(s), BINT released, MCU reloads" % n)
        return {"saved": n}

    def revert(self):
        s = self.need_held()
        s.discard()
        self.held = False
        log("reverted, BINT released, MCU reloads")
        return {"saved": 0}

    def config(self):
        ports = [{"device": p.device, "description": p.description}
                 for p in sorted(list_ports.comports(), key=lambda p: p.device)]
        return {"port": self.port or "", "detected": find_port() or "", "ports": ports,
                "model": self.model_id, "models": find_models(),
                "watcher": self.watcher is not None, "auto_apply": self.auto_apply, "delay": self.delay}

    def configure(self, body):
        if self.held:
            raise ApiError(409, "Save or Revert first")
        if "model" in body and body["model"] != self.model_id:
            path = model_path(str(body["model"]))
            if not os.path.isfile(path):
                raise ApiError(400, "unknown model: %s" % body["model"])
            self.model, self.model_id = Model(path), str(body["model"])
            if self.session is not None:
                self.session.model = self.model
                self.session.dev.vdp = self.model.vdp_addr
                self.session.pending.clear()
                self.session.reset()
            self.load_profiles()
            log("model: %s" % self.model.name)
        if "port" in body:
            port = str(body["port"]).strip() or None
            if port != self.port:
                self.port = port
                if self.session is not None:
                    self.drop()  # reconnects on the next status, on the new port
                log("port: %s" % (port or "auto"))
        if "delay" in body:
            self.delay = min(30.0, max(0.5, float(body["delay"])))
        if body.get("auto_apply") and not self.auto_apply:
            if not self.profiles["baseline"]:  # first time: the current EEPROM becomes the baseline
                self.capture({"target": "baseline"})
            self.applied = None
        if "auto_apply" in body:
            self.auto_apply = bool(body["auto_apply"])
            log("auto-apply %s" % ("on" if self.auto_apply else "off"))
        if "watcher" in body and bool(body["watcher"]) != (self.watcher is not None):
            self.start_watcher() if body["watcher"] else self.stop_watcher()
        save_json(CONFIG, {"port": self.port, "model": self.model_id, "watcher": self.watcher is not None,
                           "auto_apply": self.auto_apply, "delay": self.delay})
        return self.config()

    # ---- profiles ----
    def profiles_path(self):
        return os.path.join(HERE, "profiles-%s.json" % os.path.splitext(os.path.basename(self.model_id))[0])

    def load_profiles(self):
        d = load_json(self.profiles_path())
        self.profiles = {"baseline": d.get("baseline", {}), "cores": d.get("cores", {}),
                         "presets": d.get("presets", {})}
        self.applied = None

    def save_profiles(self):
        save_json(self.profiles_path(), self.profiles)
        self.applied = None  # the watcher compares again (and writes only if something differs)

    def profile(self, body):
        """The profile a request names: {"preset": name}, {"core": name}, or the baseline ({"core": ""})."""
        if body.get("preset"):
            if body["preset"] not in self.profiles["presets"]:
                raise ApiError(404, "no preset %s" % body["preset"])
            return self.profiles["presets"][body["preset"]]
        name = body.get("core")
        if name in NO_CORE or name is None:
            return self.profiles["baseline"]
        if name not in self.profiles["cores"]:
            raise ApiError(404, "no profile for %s" % name)
        return self.profiles["cores"][name]

    def profile_item(self, key):
        for item in profile_items(self.model):
            if item.key == key:
                return item
        raise ApiError(400, "not a profile item: %s" % key)

    def target(self, name, preset=None):
        """Baseline + the core's profile (or + a preset): what apply writes."""
        vals = dict(self.profiles["baseline"])
        if preset:
            vals.update(self.profiles["presets"][preset])
        elif name not in NO_CORE:
            vals.update(self.profiles["cores"].get(name, {}))
        return vals

    def snapshot(self):
        """The whole EEPROM, read with BINT released (manual mode: no blank). Read twice, because the MCU may
        use the bus at the same time (the Nano backs off on a collision)."""
        if self.held:
            raise ApiError(409, "editing: Save or Revert first")
        s = self.connect()
        if 0x50 not in s.dev.scan(0x50, 0x50):
            raise ApiError(409, "PVM off or not wired (the EEPROM doesn't answer)")
        s.dev.set_manual(True)
        try:
            a = s.dev.ee_read(0, EEPROM_SIZE)
            b = s.dev.ee_read(0, EEPROM_SIZE)
        finally:
            s.dev.set_manual(False)
        if a != b:
            raise ApiError(503, "EEPROM reads differ (the MCU was using the bus), try again")
        return a

    def profiles_view(self):
        def show(vals):
            items = self.model.items
            return {k: {"raw": v, "text": text(items[k], v)} for k, v in vals.items() if k in items}

        name = core()
        return {"items": [{"key": i.key, "title": i.title, "group": i.group, "unit": unit(i), "help": i.help,
                           "danger": i.danger, "range": i.safe_range()} for i in profile_items(self.model)],
                "baseline": show(self.profiles["baseline"]),
                "presets": {n: show(v) for n, v in sorted(self.profiles["presets"].items())},
                "cores": {n: show(v) for n, v in sorted(self.profiles["cores"].items())},
                "core": name, "active": name if name in self.profiles["cores"] else "",
                "watcher": self.watcher is not None, "auto_apply": self.auto_apply,
                "last_apply": self.last_apply}

    def capture(self, body):
        """Store the profile items as they are in the EEPROM now: as the baseline, for the running core, or
        as a preset (new or existing, {"target": "preset", "name": ...})."""
        target = body.get("target")
        if target == "preset":
            name = str(body.get("name", "")).strip()
            if not name or len(name) > 40:
                raise ApiError(400, "preset name: 1 to 40 characters")
            store, label = self.profiles["presets"], "preset %s" % name
        elif target == "core":
            name = core()
            if name in NO_CORE:
                raise ApiError(409, "no core running (menu): save it as the baseline")
            store, label = self.profiles["cores"], name
        else:
            name, store, label = "", None, "baseline"
        data = self.snapshot()
        vals = {i.key: self.model.decode(i, data) for i in profile_items(self.model)}
        old = store.get(name, {}) if store is not None else self.profiles["baseline"]
        changes = ["%s %s -> %s" % (i.label, text(i, old[i.key]), text(i, vals[i.key]))
                   for i in profile_items(self.model) if i.key in old and old[i.key] != vals[i.key]]
        if store is not None:
            store[name] = vals
        else:
            self.profiles["baseline"] = vals
        self.save_profiles()
        if target != "preset":
            self.applied = name or None  # the EEPROM already holds it
        log("%s: %d items from the EEPROM%s" % (label, len(vals), "; " + ", ".join(changes) if changes else ""))
        unsafe = [i.label for i in profile_items(self.model) if not i.safe(vals[i.key])]
        return {"profile": label, "kind": target or "baseline", "name": name, "items": len(vals),
                "new": not old, "changes": changes, "unsafe": unsafe}

    def profile_set(self, body):
        vals = self.profile(body)
        item = self.profile_item(body.get("key", ""))
        current = vals.get(item.key, self.profiles["baseline"].get(item.key))
        if current is None:
            raise ApiError(409, "%s isn't in the baseline yet: save the baseline first" % item.label)
        raw = item.parse(PREFIX[unit(item)] + str(body.get("text", "")).strip(), lambda: current)
        try:
            Session.check(item, raw, bool(body.get("force")))
        except UnsafeValue as e:
            raise ApiError(409, str(e), unsafe=True, danger=(item.danger or {}).get("text", ""))
        vals[item.key] = raw
        self.save_profiles()
        return {"key": item.key, "raw": raw, "text": text(item, raw)}

    def profile_remove(self, body):
        vals = self.profile(body)
        vals.pop(self.profile_item(body.get("key", "")).key, None)
        self.save_profiles()
        return {"removed": body.get("key")}

    def profile_delete(self, body):
        if body.get("preset"):
            store, name, label = self.profiles["presets"], body["preset"], "preset %s" % body["preset"]
        else:
            store, name, label = self.profiles["cores"], body.get("core"), body.get("core")
        if name in NO_CORE or name not in store:
            raise ApiError(404, "no %s" % label)
        del store[name]
        self.save_profiles()
        log("%s deleted" % label)
        return {"deleted": label}

    def apply(self, vals, which):
        """Write vals to the EEPROM if they differ from it, then release BINT: the MCU reloads. Goes through
        Session.set/commit like the CLI's set, so calculated values the MCU keeps in the EEPROM (the vertical
        A0) are stored too."""
        if not vals:
            return "no profile"
        data = self.snapshot()
        todo, skipped = [], []
        for key, raw in vals.items():
            item = self.model.items.get(key)
            if item is None or key in PROFILE_NEVER:
                continue
            if not 0 <= raw <= item.limit() or not item.safe(raw):
                skipped.append(item.label)
            elif self.model.decode(item, data) != raw:
                todo.append((item, raw, self.model.decode(item, data)))
        if skipped:
            log("%s: outside the safe range, not written: %s" % (which, ", ".join(skipped)))
        if not todo:
            return "already set"
        s = self.session
        s.dev.bint(True)  # waits for jig mode
        try:
            for item, raw, _old in todo:
                s.set(item, raw)
            s.commit()  # EEPROM, release BINT: the MCU reloads
        finally:
            if s.pending or s.dev.bint():
                s.discard()
        return "%d item(s) written: %s" % (len(todo), ", ".join(
            "%s %s -> %s" % (i.label, text(i, old), text(i, raw)) for i, raw, old in todo))

    def apply_now(self, body=None):
        """Apply a profile now: {"preset": name} = baseline + that preset, {"core": name} = baseline + that
        core's profile, {"core": ""} = the baseline alone, nothing = the running core (what the watcher does).
        It holds until the next core change."""
        body = body or {}
        running = core()
        preset = body.get("preset")
        chosen = "core" in body
        name = (body["core"] or "") if chosen else running
        if preset and preset not in self.profiles["presets"]:
            raise ApiError(404, "no preset %s" % preset)
        if chosen and name and name not in self.profiles["cores"]:
            raise ApiError(404, "no profile for %s" % name)
        if preset:
            which = "preset %s" % preset
        elif name in self.profiles["cores"]:
            which = "%s (profile)" % name
        elif chosen:
            which = "baseline"
        else:
            which = "%s (baseline)" % (name or "MENU")
        msg = self.apply(self.target(name, preset), which)
        self.applied = running  # the watcher leaves it until the core changes
        self.last_apply = "%s  %s: %s" % (time.strftime("%H:%M:%S"), which, msg)
        log("apply %s: %s" % (which, msg))
        return {"result": self.last_apply}

    # ---- watcher ----
    def start_watcher(self):
        self.watcher = threading.Event()
        threading.Thread(target=watch_core, args=(self, self.watcher), daemon=True).start()
        self.applied = None
        log("watcher on, core: %s, auto-apply %s" % (core() or "-", "on" if self.auto_apply else "off"))

    def stop_watcher(self):
        self.watcher.set()
        self.watcher = None
        log("watcher off")

    def expire(self):
        """Idle timeout: a closed or hidden page must not leave BINT asserted (MCU off the bus) during play."""
        if self.held and time.time() - self.last > self.idle:
            n = len(self.session.pending)
            self.session.discard()
            self.held = False
            self.note = "No activity for %d s: %d change(s) reverted, BINT released." % (self.idle, n)
            log(self.note)

    def close(self):
        with self.lock:
            if self.session is not None:
                if self.held:
                    self.session.discard()
                    log("BINT released, MCU reloads")
                self.session.dev.link.close()


class Handler(BaseHTTPRequestHandler):
    panel = None
    server_version = "pvm-jig-web/" + __version__

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/status":
            self.api(self.panel.status)
        elif path == "/api/items":
            self.api(self.panel.items, activity=False)
        elif path == "/api/values":
            self.api(self.panel.values)
        elif path == "/api/config":
            self.api(self.panel.config, activity=False)
        elif path == "/api/profiles":
            self.api(self.panel.profiles_view, activity=False)
        else:
            self.static(path)

    def do_POST(self):
        p = self.panel
        # path -> (function, takes the JSON body)
        routes = {"/api/hold": (p.hold, False), "/api/set": (p.set, True), "/api/save": (p.save, False),
                  "/api/revert": (p.revert, False), "/api/config": (p.configure, True),
                  "/api/profiles/capture": (p.capture, True), "/api/profiles/set": (p.profile_set, True),
                  "/api/profiles/remove": (p.profile_remove, True), "/api/profiles/delete": (p.profile_delete, True),
                  "/api/profiles/apply": (p.apply_now, True)}
        if self.path not in routes:
            return self.reply(404, {"error": "not found"})
        fn, takes_body = routes[self.path]
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.reply(400, {"error": "bad JSON"})
        if takes_body:
            return self.api(fn, body, activity=not self.path.startswith("/api/profiles/"))
        self.api(fn)

    def api(self, fn, *args, activity=True):
        try:
            self.reply(200, self.panel.call(fn, *args, activity=activity))
        except ApiError as e:
            self.reply(e.code, e.body)
        except LinkError as e:
            self.reply(502, {"error": str(e)})
        except (ValueError, KeyError) as e:
            self.reply(400, {"error": e.args[0] if e.args else str(e)})
        except Exception as e:
            log("error: %r" % e)
            self.reply(500, {"error": "server error: %s" % e})

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def static(self, path):
        name = path.lstrip("/") or "index.html"
        full = os.path.join(WEB, name)
        if "/" in name or name.startswith(".") or not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(os.path.splitext(name)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


def watch(panel):
    while True:
        time.sleep(5)
        try:
            panel.call(panel.expire, activity=False)
        except Exception as e:
            log("idle check: %s" % e)


def watch_core(panel, stop):
    """Follows /tmp/CORENAME. Once it has been stable for `delay` seconds and auto-apply is on, applies that
    core's profile. A failed apply (editing, PVM off, Nano unplugged) is retried every few seconds."""
    seen, since, retry, failed = None, time.time(), 0.0, ""
    while not stop.wait(0.5):
        name, now = core(), time.time()
        if name != seen:
            if seen is not None:
                log("core: %s -> %s" % (seen or "-", name or "-"))
            seen, since = name, now
            continue
        if not panel.auto_apply or name == panel.applied or now - since < panel.delay or now < retry:
            continue
        try:
            panel.call(panel.apply_now, {}, activity=False)
            failed = ""
        except (ApiError, LinkError, ValueError) as e:
            msg = "%s: not applied: %s" % (name or "MENU", e)
            if msg != failed:  # log once, not on every retry
                log(msg)
                failed = msg
            panel.last_apply = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
            retry = now + 3


def main():
    ap = argparse.ArgumentParser(description="pvm-jig web panel")
    ap.add_argument("-p", "--port", help="serial port, 'auto' = first USB serial port (default: saved setting, else auto)")
    ap.add_argument("-m", "--model", help="model name or .toml path (default: saved setting, else %s)" % DEFAULT_MODEL)
    ap.add_argument("--listen", default="0.0.0.0:8183", help="address:port to serve on (default 0.0.0.0:8183)")
    ap.add_argument("--idle", type=int, default=120,
                    help="seconds without requests before an edit is reverted and BINT released (default 120)")
    a = ap.parse_args()
    cfg = load_json(CONFIG) or load_json(os.path.join(HERE, "pvm-web.json"))  # settings of the older pvm-web
    serial_port = cfg.get("port") if a.port is None else None if a.port == "auto" else a.port
    host, _, port = a.listen.rpartition(":")
    model_id = a.model or cfg.get("model") or DEFAULT_MODEL
    if not os.path.isfile(model_path(model_id)):
        log("model %s not found, using %s" % (model_id, DEFAULT_MODEL))
        model_id = DEFAULT_MODEL
    Handler.panel = panel = Panel(serial_port, model_id, a.idle, cfg)
    model = panel.model
    try:
        panel.call(panel.connect, activity=False)
    except Exception as e:
        log("not connected yet: %s" % e)
    threading.Thread(target=watch, args=(panel,), daemon=True).start()
    server = ThreadingHTTPServer((host or "0.0.0.0", int(port)), Handler)
    signal.signal(signal.SIGTERM, lambda *_a: sys.exit(0))
    log("pvm-jig-web %s: %s on http://%s:%s" % (__version__, model.name, host or "0.0.0.0", port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        panel.close()


if __name__ == "__main__":
    main()
