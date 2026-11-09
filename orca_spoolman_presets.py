# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# id = "pipspool-slot-sync"
# name = "PipSpool Slot Sync"
# description = "Selects the PipSpool profile of the Spoolman spool loaded in each lane of a Moonraker printer for the matching filament slot. Never creates presets."
# author = "HappyPants9999 (fork of DIEHARDave/orca_spoolman_presets)"
# version = "0.2.0"
# ///
"""PipSpool Slot Sync.

A fork of DIEHARDave/orca_spoolman_presets for setups where PipSpool creates one Orca filament
profile per Spoolman spool. Run it from the Plugins dialog and press Sync slots.

Sync slots
    Reads lane_data from the printer's Moonraker, asks Spoolman (through Moonraker's
    /server/spoolman/proxy) about each lane's spool, and selects a filament preset for the
    matching slot (lane 0 is slot 1). Run Orca's own filament sync first so the slot count
    and colors are right; this replaces the generic presets it picks.

    The preset is, in order:
      1. the "orca_preset" extra field of the spool, then of its filament (explicit override);
      2. the PipSpool profile for that spool: the one whose name holds "(#<spool id>)" exactly
         once and ends in " - PipSpool" (or the older " - Spoolman"), as PipSpool itself decides;
      3. the compatible preset whose name, without any " @Printer" suffix, is the longest
         whole-word prefix of "<vendor> <filament name>" or "<vendor> <material> <filament name>".

This fork never creates or edits presets: PipSpool owns them.
"""

import json
import queue
import re
import urllib.error
import urllib.request

try:
    import orca
except ImportError:  # unit tests and plain Python
    orca = None

PLUGIN_NAME = "PipSpool Slot Sync"
HTTP_TIMEOUT = 5

RANK_USER, RANK_BUNDLE, RANK_SYSTEM = 0, 1, 2

MATERIALS = {"PLA", "PETG", "PET", "PCTG", "ABS", "ASA", "TPU", "TPE", "PA", "PA6", "PA12", "PC",
             "PVA", "HIPS", "PP", "PPS", "PEEK", "NYLON", "CF", "GF"}
PIPSPOOL_NAME_RE = re.compile(r"^(?P<core>.*? - (?:PipSpool|Spoolman))(?: @.*)?$")


# Matching. Plain functions: no orca, no network.

def decode_extra(value):
    """Spoolman stores extra field values JSON-encoded, so a text field arrives as '"name"'."""
    if not isinstance(value, str):
        return ""
    try:
        decoded = json.loads(value)
    except ValueError:
        return value
    return decoded if isinstance(decoded, str) else ""


def base_name(name, bundle_id=""):
    """A preset's name as the user sees it, without the bundle prefix or " @Printer" suffix."""
    if bundle_id:
        prefix = "_local/%s/" % bundle_id
        if name.startswith(prefix):
            name = name[len(prefix):]
        else:
            name = name.rsplit("/", 1)[-1]
    return name.split(" @", 1)[0].strip()


def pipspool_spool_id(name):
    """Spool id of a PipSpool-managed profile name, else None.

    Mirrors PipSpool's own rule: the name ends in " - PipSpool" (or " - Spoolman") and holds
    "(#<id>)" exactly once. A trailing " @Printer" suffix is ignored.
    """
    match = PIPSPOOL_NAME_RE.match((name or "").rsplit("/", 1)[-1].strip())
    if not match:
        return None
    ids = re.findall(r"\(#(\d+)\)", match.group("core"))
    return int(ids[0]) if len(ids) == 1 and int(ids[0]) > 0 else None


def find_pipspool_preset(spool_id, pipspool):
    """(preset, problem) for a spool: its usable PipSpool profile, or why there is none.

    pipspool: [{"spool_id", "name", "base", "rank", "usable"}] over every loaded filament preset.
    """
    found = [p for p in pipspool if p["spool_id"] == spool_id]
    usable = [p for p in found if p["usable"]]
    if usable:
        return min(usable, key=lambda p: (p["rank"], p["name"])), ""
    if found:
        return None, ("PipSpool profile for #%d is hidden or not compatible with this printer; "
                      "check its compatible printers" % spool_id)
    return None, ""


def _is_word_prefix(text, prefix):
    if not prefix or len(prefix) > len(text) or text[:len(prefix)].casefold() != prefix.casefold():
        return False
    return len(text) == len(prefix) or text[len(prefix)] == " "


def _material_word(word):
    parts = word.upper().rstrip("+").replace("+", "").split("-")
    return any(p in MATERIALS for p in parts)


def spool_keys(vendor, material, name):
    """The names a spool is matched by: '<vendor> <name>', then with the material inserted."""
    vendor, material, name = (s.strip() for s in (vendor or "", material or "", name or ""))
    keys = [" ".join(p for p in (vendor, name) if p)]
    if material and not any(_material_word(w) for w in name.split()):
        keys.append(" ".join(p for p in (vendor, material, name) if p))
    return [k for k in keys if k]


def match_preset(keys, candidates, name=""):
    """Best candidate for any key: the longest base that is a whole-word prefix, lowest rank on a tie.

    candidates: dicts with "name", "base" and "rank". When no key matches, the bare filament name
    is tried against your own presets only, for presets named without the vendor.
    """
    def best_of(keys, pool):
        best = None
        for key in keys:
            for cand in pool:
                if not _is_word_prefix(key, cand["base"]):
                    continue
                if best is None or len(cand["base"]) > len(best["base"]) or (
                        len(cand["base"]) == len(best["base"]) and cand["rank"] < best["rank"]):
                    best = cand
        return best

    found = best_of(keys, candidates)
    if found is None and name.strip():
        found = best_of([name.strip()], [c for c in candidates if c["rank"] != RANK_SYSTEM])
    return found


def find_named_preset(name, candidates):
    """The candidate an orca_preset value names: its full name, or its base name ignoring case."""
    for cand in candidates:
        if cand["name"] == name:
            return cand
    named = [c for c in candidates if c["base"].casefold() == base_name(name).casefold()]
    return min(named, key=lambda c: c["rank"]) if named else None


def normalize_color(value):
    """'#RRGGBB' from '#rrggbb', 'RRGGBB', '0xRRGGBB' or with an alpha byte; '' when not a color."""
    text = str(value or "").strip()
    if text[:2].lower() == "0x":
        text = text[2:]
    text = text.lstrip("#")
    if len(text) == 8:
        text = text[:6]
    return "#" + text.upper() if re.fullmatch(r"[0-9A-Fa-f]{6}", text) else ""


def lanes_from_lane_data(value):
    """(slot number, spool id, lane material, lane color) for each lane, slot = lane + 1."""
    lanes = []
    for lane in (value or {}).values():
        if not isinstance(lane, dict):
            continue
        try:
            index = int(str(lane.get("lane", "")))
        except ValueError:
            continue
        spool_id = lane.get("spool_id")
        lanes.append((index + 1, spool_id if isinstance(spool_id, int) and spool_id > 0 else None,
                      lane.get("material") or "", normalize_color(lane.get("color"))))
    return sorted(lanes)


def choose_preset(spool_id, spool, snap):
    """(candidate, how, problem) for one lane's spool. Pure: no orca, no network."""
    fil = spool.get("filament") or {}
    named = decode_extra((spool.get("extra") or {}).get("orca_preset")) or \
        decode_extra((fil.get("extra") or {}).get("orca_preset"))
    if named:
        cand = find_named_preset(named, snap["candidates"])
        if cand is None:
            return None, "", 'orca_preset "%s" is not a compatible preset' % named
        return cand, "orca_preset", ""
    pip, problem = find_pipspool_preset(spool_id, snap["pipspool"])
    if pip is not None:
        return pip, "PipSpool", ""
    if problem:
        return None, "", problem
    if fil:
        vendor = ((fil.get("vendor") or {}).get("name") or "")
        cand = match_preset(spool_keys(vendor, fil.get("material"), fil.get("name")),
                            snap["candidates"], fil.get("name") or "")
        if cand is not None:
            return cand, "name match; no PipSpool profile", ""
    return None, "", ("No PipSpool profile for #%d: run PipSpool's Synchronize now, "
                      "then restart OrcaSlicer" % spool_id)


def set_slot_color(slot, color):
    """Set a slot's color where Orca lets plugins (preset.filament.set_slot_color); False otherwise."""
    setter = getattr(orca.host.preset.filament, "set_slot_color", None)
    if setter is None or not color:
        return False
    setter(slot, [color])
    return True


# Moonraker and Spoolman

class Moonraker:
    def __init__(self, url):
        self.url = (url or "").strip().rstrip("/")
        if not re.match(r"^https?://[^/]+", self.url):
            raise ValueError("Set the printer's Moonraker URL first, e.g. http://10.0.1.107:7125")

    def _request(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method="POST" if data else "GET",
                                         headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RuntimeError("Moonraker %s answered HTTP %d" % (path, error.code))
        except (urllib.error.URLError, OSError) as error:
            raise RuntimeError("Cannot reach Moonraker at %s: %s" % (self.url, getattr(error, "reason", error)))

    def lane_data(self):
        return self._request("/server/database/item?namespace=lane_data").get("result", {}).get("value") or {}

    def spoolman(self, path):
        result = self._request("/server/spoolman/proxy",
                               {"use_v2_response": True, "request_method": "GET", "path": path}).get("result") or {}
        if result.get("error"):
            raise RuntimeError("Spoolman: %s" % (result["error"].get("message") if isinstance(result["error"], dict)
                                                 else result["error"]))
        return result.get("response")


# Orca side

def preset_snapshot(bundle):
    """Filament presets as plain data. Runs on the UI thread, which owns the live presets."""
    filaments = bundle.filaments
    candidates, pipspool = [], []
    for index in range(filaments.size()):
        preset = filaments.preset(index)
        if preset.is_default:
            continue
        rank = RANK_SYSTEM if preset.is_system else RANK_BUNDLE if preset.bundle_id else RANK_USER
        base = base_name(preset.name, preset.bundle_id)
        usable = bool(preset.is_compatible and preset.is_visible)
        if usable:
            candidates.append({"name": preset.name, "base": base, "rank": rank})
        spool_id = pipspool_spool_id(preset.name)
        if spool_id is not None:
            pipspool.append({"spool_id": spool_id, "name": preset.name, "base": base,
                             "rank": rank, "usable": usable})
    return {"candidates": candidates, "pipspool": pipspool,
            "slots": list(bundle.current_filament_preset_names())}


def sync_slots(moonraker, snap, select=None, color=None):
    """Select a preset per lane; returns one report row per lane.

    select(slot, preset_name) and color(slot, '#RRGGBB') -> bool default to the Orca calls.
    """
    select = select or (lambda slot, name: orca.host.preset.filament.begin_update(slot, name).commit())
    color = color or set_slot_color
    rows = []
    slots = snap["slots"]
    for slot, spool_id, lane_material, lane_color in lanes_from_lane_data(moonraker.lane_data()):
        row = {"slot": slot, "spool": spool_id, "spool_name": "", "preset": "", "status": ""}
        rows.append(row)
        if spool_id is None:
            row["status"] = "No Spoolman spool in this lane" if lane_material else "Empty lane"
            continue
        try:
            spool = moonraker.spoolman("/v1/spool/%d" % spool_id) or {}
        except RuntimeError:
            spool = {}  # PipSpool matching needs only the id; carry on without Spoolman
        fil = spool.get("filament") or {}
        vendor = ((fil.get("vendor") or {}).get("name") or "")
        row["spool_name"] = " ".join(p for p in (vendor, fil.get("name") or "") if p)
        cand, how, problem = choose_preset(spool_id, spool, snap)
        if cand is None:
            row["status"] = problem
            continue
        row["preset"] = cand["base"]
        if slot > len(slots):
            row["status"] = "Orca has only %d filament slots; run Orca's sync first" % len(slots)
            continue
        if slots[slot - 1] == cand["name"]:
            row["status"] = "Already set (%s)" % how
        else:
            select(slot, cand["name"])
            row["status"] = "Selected (%s)" % how
        # Selecting a preset gives the slot that preset's default color; put the spool's back.
        if color(slot, lane_color or normalize_color(fil.get("color_hex"))):
            row["status"] += ", color set"
    return rows


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><style>
:root { color-scheme: light dark; --line: rgba(127,127,127,.35); --a: #009688; }
* { box-sizing: border-box; }
body { margin: 0; padding: 14px 16px; font: 13px/1.45 var(--orca-font, system-ui, sans-serif);
       color: var(--orca-fg, CanvasText); background: var(--orca-bg, Canvas); }
h2 { font-size: 14px; margin: 18px 0 6px; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
input[type=text] { flex: 1; min-width: 200px; font: inherit; color: inherit; background: transparent;
                   border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; }
button { font: inherit; border: 1px solid var(--a); background: var(--a); color: #fff; border-radius: 6px;
         padding: 6px 12px; cursor: pointer; }
button.quiet { background: transparent; color: inherit; border-color: var(--line); }
button:disabled { opacity: .5; cursor: default; }
.note { opacity: .7; font-size: 12px; margin: 2px 0 8px; }
table { width: 100%; border-collapse: collapse; margin-top: 6px; }
th, td { text-align: left; padding: 5px 6px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-weight: 600; font-size: 12px; opacity: .8; }
.dim { opacity: .65; font-size: 12px; }
#status { margin-top: 12px; min-height: 18px; }
.error { color: #d13c32; }
</style></head><body>
<div class="row"><label for="url">Moonraker URL</label>
  <input type="text" id="url" placeholder="http://10.0.1.107:7125"><button class="quiet" id="save">Save</button></div>

<h2>Sync slots</h2>
<p class="note">Run Orca's own filament sync first, then this to put each loaded spool's PipSpool profile in its slot.</p>
<div class="row"><button id="sync">Sync slots from printer</button></div>
<table id="syncTable" hidden><thead><tr><th>Slot</th><th>Spool</th><th>Preset</th><th>Result</th></tr></thead><tbody></tbody></table>

<div id="status"></div>
<script>
'use strict';
const $ = id => document.getElementById(id);
let busy = false;
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function setBusy(value, text) {
  busy = value;
  ['save', 'sync'].forEach(id => $(id).disabled = value);
  if (text !== undefined) status(text);
}
function status(text, error) { $('status').textContent = text || ''; $('status').className = error ? 'error' : ''; }
function send(command) { setBusy(true, 'Working…'); orca.postMessage({command, url: $('url').value}); }
$('save').onclick = () => send('save_url');
$('sync').onclick = () => send('sync');
function renderSync(rows) {
  $('syncTable').hidden = false;
  $('syncTable').tBodies[0].innerHTML = rows.map(r => '<tr><td>' + r.slot + '</td><td>' + esc(r.spool_name || (r.spool ? '#' + r.spool : '—')) +
    (r.spool && r.spool_name ? ' <span class="dim">#' + r.spool + '</span>' : '') +
    '</td><td>' + esc(r.preset) + '</td><td>' + esc(r.status) + '</td></tr>').join('') || '<tr><td colspan="4" class="dim">No lanes in lane_data</td></tr>';
}
orca.onMessage(m => {
  if (!m) return;
  if (m.url !== undefined && document.activeElement !== $('url')) $('url').value = m.url;
  if (m.sync) renderSync(m.sync);
  setBusy(false);
  status(m.error || m.message || '', !!m.error);
});
orca.postMessage({command: 'init'});
</script></body></html>"""


if orca is not None:
    class PipSpoolSlotSync(orca.script.ScriptPluginCapabilityBase):
        window = None
        _commands = None
        _cancelled = False

        def get_name(self):
            return PLUGIN_NAME

        def get_default_config(self):
            return {"moonraker_url": ""}

        def _config(self):
            config = {"moonraker_url": ""}
            try:
                config.update(json.loads(self.get_config() or "{}"))
            except ValueError:
                pass
            return config

        # Runs on the Script worker: network and begin_update() block here, never the UI thread.
        def execute(self):
            if self.window is not None and self.window.is_open():
                self.window.show()
                return orca.ExecutionResult.success(PLUGIN_NAME + " is already open.")
            commands = queue.Queue()
            self._commands = commands
            try:
                if self._cancelled:
                    return orca.ExecutionResult.success("Closed.")
                self.window = orca.host.ui.create_window(
                    title=PLUGIN_NAME, html=PAGE, width=760, height=480,
                    on_message=self._on_message, on_close=self._on_close)
                while True:
                    message = commands.get()
                    if self._cancelled or message.get("command") == "close":
                        return orca.ExecutionResult.success("Closed.")
                    try:
                        reply = self._run(message)
                    except Exception as error:
                        reply = {"error": str(error)}
                    if self.window is not None and self.window.is_open():
                        self.window.post(reply)
            except Exception as error:
                return orca.ExecutionResult.failure(orca.PluginResult.RecoverableError,
                                                    PLUGIN_NAME + " failed: %s" % error)
            finally:
                if self._commands is commands:
                    self._commands = None
                self.window = None

        def _run(self, message):
            if message["command"] != "sync":
                raise ValueError("Unknown command")
            rows = sync_slots(Moonraker(message.get("url")), message["snapshot"])
            changed = sum(r["status"].startswith("Selected") for r in rows)
            return {"sync": rows, "message": "%d slot(s) changed. Presets stay unsaved project choices." % changed}

        # UI thread: reads presets and config, and hands network work to the worker.
        def _on_message(self, message):
            message = dict(message) if isinstance(message, dict) else {}
            command = message.get("command")
            try:
                if command in ("init", "save_url"):
                    config = self._config()
                    if command == "save_url":
                        config["moonraker_url"] = (message.get("url") or "").strip().rstrip("/")
                        self.save_config(json.dumps(config))
                    self.window.post({"url": config["moonraker_url"],
                                      "message": "Saved." if command == "save_url" else ""})
                elif command == "sync":
                    message["snapshot"] = preset_snapshot(orca.host.preset_bundle())
                    self._enqueue(message)
            except Exception as error:
                if self.window is not None and self.window.is_open():
                    self.window.post({"error": str(error)})

        def _on_close(self):
            self._enqueue({"command": "close"})

        def on_cancelled(self):
            self._cancelled = True
            self._enqueue({"command": "close"})

        def _enqueue(self, message):
            commands = self._commands
            if commands is not None:
                commands.put(message)

    @orca.plugin
    class PipSpoolSlotSyncPlugin(orca.base):
        def register_capabilities(self):
            orca.register_capability(PipSpoolSlotSync)
