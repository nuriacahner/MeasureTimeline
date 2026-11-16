"""MeasureTimeline - the working code. RE-IMPORTED on every command (hot reload).

Nothing may live in a module global between events. A stored measurement is a custom
feature in the root component: dependencies 'a' and 'b' are the measured entities,
the attribute (ATTR_GROUP, 'measurement') holds kind / label / last value as JSON, and
the feature's name shows "label = value". Measuring uses app.measureManager, the
engine behind Inspect > Measure.

Selecting a stored measurement in the timeline draws it in the viewport, 3-matic style
(mt_draw). The shell calls on_selection_changed and on_camera_changed; the drawing is
redone from the current selection each time, so nothing is kept between events.
"""

import adsk.core
import adsk.fusion

import datetime
import importlib
import json
import math
import os
import sys
import time
import types

ADDIN_DIR = os.path.dirname(os.path.realpath(__file__))
if ADDIN_DIR not in sys.path:
    sys.path.insert(0, ADDIN_DIR)
import mt_draw  # noqa: E402
importlib.reload(mt_draw)

PANELS = ['InspectPanel']
STORE_CMD_ID = 'MeasureTimeline_Store'      # = the shell's CMD_ID
EDIT_CMD_ID = 'MeasureTimeline_Place'       # the custom feature's edit command (double-click)
PALETTE_ID = 'MeasureTimeline_Palette'      # the Measurements panel (= the shell's)
PLACE_GROUP = 'MeasureTimeline_place'       # the placing command's moving drawing
SHOW_ALL_ATTR = 'showAll'                   # design attribute: '0' = Show Measurements off
ATTR_GROUP = 'MeasureTimeline'
ATTR_NAME = 'measurement'
KIND_DISTANCE = 'Distance'
KIND_ANGLE = 'Angle'
KIND_FORMULA = 'Formula'
DEFAULT_LABEL = {KIND_DISTANCE: 'Distancia', KIND_ANGLE: 'Ángulo', KIND_FORMULA: 'Fórmula'}
ANGLE_ACUTE = 'Acute (0–90°)'
ANGLE_OBTUSE = 'Obtuse (90–180°), as 3-matic shows volar tilt'
# what Inspect > Measure accepts
SELECTION_FILTERS = ('Faces', 'Edges', 'Vertices', 'Bodies', 'SketchCurves', 'SketchPoints',
                     'ConstructionPlanes', 'ConstructionLines', 'ConstructionPoints')
# what gives a direction for "Along": axes / lines, planes (their normal)
ALONG_FILTERS = ('ConstructionLines', 'ConstructionPlanes', 'SketchLines', 'LinearEdges',
                 'PlanarFaces')


# --------------------------------------------------------------------------- #
# toolbar
# --------------------------------------------------------------------------- #

def place_button(ui, cmd_def):
    """Add the button to PANELS. Returns the controls, for the shell to delete."""
    controls = []
    for pid in PANELS:
        panel = ui.allToolbarPanels.itemById(pid)
        if not panel:
            continue
        old = panel.controls.itemById(cmd_def.id)
        if old:
            old.deleteMe()
        controls.append(panel.controls.addCommand(cmd_def))
    return controls


# --------------------------------------------------------------------------- #
# measuring
# --------------------------------------------------------------------------- #

def measure(app, kind, a, b, along=None, obtuse=False):
    """Value in display units: mm for a distance, degrees for an angle.

    Angles are the angle between lines / planes, 0-90 degrees, as 3-matic's Line to
    Line gives them: for two planes Fusion can return the supplement (30 -> 150).
    `obtuse` (the record's 'obtuse') gives the other one, 180 - that: two lines make
    both, and 3-matic shows the volar tilt's as 90 + tilt (e.g. 98°, tilt = measured -
    90). Fusion's own choice between the two follows how the entities happen
    to be oriented, so it is never used.

    A distance `along` an axis / line / plane is only the component along that
    direction (a plane: along its normal) - 3-matic's "Measure X / Y / Z only" in a
    user coordinate system (Dlab-IT007: Stahl L4..L7, varianza ulnar, altura radial).
    Without it, the straight-line distance, which the IT warns is larger."""
    mm = app.measureManager
    if kind == KIND_DISTANCE:
        if along is not None:
            p1, p2, foot, d = mt_draw.along_points(app, a, b, along)
            return abs(mt_draw.dot(mt_draw.sub(p2, p1), d)) * 10.0
        return mm.measureMinimumDistance(a, b).value * 10.0      # API lengths are cm
    deg = math.degrees(mm.measureAngle(a, b).value)
    acute = min(deg, 180.0 - deg)
    return 180.0 - acute if obtuse else acute


def format_value(kind, value, unit=None):
    if kind == KIND_FORMULA:
        if unit == '°':
            return '%.2f°' % value
        if unit == 'mm':
            return '%.2f mm' % value
        return '%.3f' % value                   # a ratio (L5 / L4) or mixed units
    return '%.2f mm' % value if kind == KIND_DISTANCE else '%.2f°' % value


def feature_name(label, kind, value, unit=None):
    if value is None:
        return '%s = ? (%s)' % (label, 'check the formula' if kind == KIND_FORMULA else 'reference lost')
    return '%s = %s' % (label, format_value(kind, value, unit))


# --------------------------------------------------------------------------- #
# formulas: a value derived from other stored measurements, by their labels
# --------------------------------------------------------------------------- #
# e.g. "IR - Pronosupinación" (Dlab-IT007: IR corrected for pronosupination),
# "90 - Inclinación volar", "L5 / L4" (Stahl), "L7 / L6 * 100" (LCR). Stored as its own
# custom feature with no dependencies (allowed, verified): record kind 'Formula', 'expr',
# 'unit', 'value'. Re-evaluated in compute (measurements earlier in the timeline are
# already up to date) and after every command (update_formulas), so the order in the
# timeline doesn't matter. Only + - * / ( ), numbers and labels: no eval().

def _measurement_values(design, exclude=None):
    """{label: (value, unit)} of the stored measurements in effect."""
    vals = {}
    for f, rec in stored(design):
        if exclude is not None and f == exclude:
            continue
        unit = (rec.get('unit') if rec['kind'] == KIND_FORMULA
                else '°' if rec['kind'] == KIND_ANGLE else 'mm')
        vals[label_now(f, rec)] = (rec.get('value'), unit)
    return vals


def evaluate(design, expr, exclude=None):
    """(value, unit) of a formula over the stored measurements' labels. Raises
    ValueError with a readable message."""
    import ast
    vals = _measurement_values(design, exclude)
    text = expr
    names = {}
    for k, label in enumerate(sorted(vals, key=len, reverse=True)):    # longest first
        if label and label in text:
            names['m%d' % k] = label
            text = text.replace(label, ' m%d ' % k)
    try:
        tree = ast.parse(text.replace('×', '*').replace('÷', '/').replace(',', '.').strip(), mode='eval')
    except SyntaxError:
        raise ValueError('Cannot read the formula. Use measurement names, numbers, + - * / ( ).')
    used = []

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name) and n.id in names:
            v, unit = vals[names[n.id]]
            if v is None:
                raise ValueError('"%s" has no value.' % names[n.id])
            used.append(unit)
            return float(v)
        if isinstance(n, ast.Name):
            raise ValueError('No measurement is called "%s".' % n.id)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            if b == 0:
                raise ValueError('Division by zero.')
            return a / b
        raise ValueError('Only measurement names, numbers and + - * / ( ) are allowed.')

    value = ev(tree)
    if not used:
        raise ValueError('The formula uses no measurement.')
    divides = any(isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Div, ast.Mult))
                  for n in ast.walk(tree))
    unit = used[0] if (len(set(used)) == 1 and not divides) else ''
    return value, unit


def store_formula(app, definition, label, expr):
    des = adsk.fusion.Design.cast(app.activeProduct)
    value, unit = evaluate(des, expr)
    record = {'kind': KIND_FORMULA, 'label': label, 'expr': expr, 'unit': unit, 'value': value,
              'created': datetime.datetime.now().isoformat(timespec='seconds')}
    inp = des.rootComponent.features.customFeatures.createInput(definition)
    feature = des.rootComponent.features.customFeatures.add(inp)
    feature.attributes.add(ATTR_GROUP, ATTR_NAME, json.dumps(record))
    feature.name = feature_name(label, KIND_FORMULA, value, unit)
    return feature


def _reevaluate(design, feature, rec):
    """Recompute a formula's value; write record and name if they changed."""
    try:
        value, unit = evaluate(design, rec['expr'], exclude=feature)
    except ValueError:
        value, unit = None, rec.get('unit', '')
    label = label_now(feature, rec)
    want = feature_name(label, KIND_FORMULA, value, unit)
    if value != rec.get('value') or unit != rec.get('unit') or label != rec['label']:
        rec.update({'value': value, 'unit': unit, 'label': label})
        _write_record(feature, rec)
    if feature.name != want:
        feature.name = want


def update_formulas(design):
    for f, rec in stored(design):
        if rec['kind'] == KIND_FORMULA:
            try:
                _reevaluate(design, f, rec)
            except Exception:
                pass


def _describe(entity):
    """Short text for the record: what was measured."""
    for attr in ('name',):
        n = getattr(entity, attr, None)
        if n:
            return n
    body = getattr(entity, 'body', None)
    kind = entity.objectType.split('::')[-1]
    return '%s of %s' % (kind, body.name) if body else kind


# --------------------------------------------------------------------------- #
# what a measurement is made of, as the browser names it (Measurements panel)
# --------------------------------------------------------------------------- #
# The user wants to see, for the selected measurement, the items picked to make it: the
# lines / axes of an angle, the points of a distance (2026-11-04). Read live from the
# dependencies, so renames in the browser show at once; the record's 'a' / 'b' (names at
# creation) only stand in when a reference is lost.

def _ref_name(entity):
    """Browser name of what a sketch item was projected from."""
    try:
        return entity.name
    except Exception:
        pass
    F = adsk.fusion
    for cls, word in ((F.SketchLine, 'the line'), (F.SketchPoint, 'a point'), (F.SketchCurve, 'a curve')):
        x = cls.cast(entity)
        if x:
            return '%s of sketch «%s»' % (word, x.parentSketch.name)
    body = getattr(entity, 'body', None)
    kind = entity.objectType.split('::')[-1]
    return '%s of %s' % (kind, body.name) if body else kind


def _point_source(sp):
    """What a sketch point stands on: its own projection source, or the projected point it
    sits on (a line's ends are their own points, coincident with the projected ones)."""
    try:
        if sp.isReference and sp.referencedEntity:
            return _ref_name(sp.referencedEntity)
        for p in sp.parentSketch.sketchPoints:
            if p.isReference and p.worldGeometry.distanceTo(sp.worldGeometry) < 1e-6 and p.referencedEntity:
                return _ref_name(p.referencedEntity)
    except Exception:
        pass
    return None


def browser_name(entity):
    """How the browser would name a measured entity, with what it is."""
    F = adsk.fusion
    for cls, word in ((F.ConstructionAxis, 'axis'), (F.ConstructionPlane, 'plane'),
                      (F.ConstructionPoint, 'point')):
        x = cls.cast(entity)
        if x:
            return '%s (%s)' % (x.name, word)
    line = F.SketchLine.cast(entity)
    if line:
        sk = line.parentSketch.name
        try:
            if line.isReference and line.referencedEntity:
                return 'projection of %s, in sketch «%s»' % (_ref_name(line.referencedEntity), sk)
        except Exception:
            pass
        ends = [_point_source(p) for p in (line.startSketchPoint, line.endSketchPoint)]
        if all(ends):
            return 'line %s → %s, in sketch «%s»' % (ends[0], ends[1], sk)
        return 'line in sketch «%s»' % sk
    point = F.SketchPoint.cast(entity)
    if point:
        src = _point_source(point)
        sk = point.parentSketch.name
        return ('point on %s, in sketch «%s»' % (src, sk)) if src else 'point in sketch «%s»' % sk
    curve = F.SketchCurve.cast(entity)
    if curve:
        return 'curve in sketch «%s»' % curve.parentSketch.name
    for cls, word in ((F.BRepFace, 'face'), (F.BRepEdge, 'edge'), (F.BRepVertex, 'vertex')):
        x = cls.cast(entity)
        if x:
            return '%s of %s' % (word, x.body.name)
    body = F.BRepBody.cast(entity)
    if body:
        return '%s (body)' % body.name
    return _describe(entity)


def measurement_parts(feature, rec):
    """[{'key': 'a' | 'b' | 'c', 'role': 'First' | 'Second' | 'Along', 'text': ...}]"""
    if rec['kind'] == KIND_FORMULA:
        return []
    a, b = _entities(feature)
    parts = [('a', 'First', a), ('b', 'Second', b)]
    if 'along' in rec:
        parts.append(('c', 'Along', _along(feature)))
    out = []
    for key, role, ent in parts:
        if ent is None:
            text = 'lost (was %s)' % rec.get({'c': 'along'}.get(key, key), '?')
        else:
            try:
                text = browser_name(ent)
            except Exception:
                text = rec.get({'c': 'along'}.get(key, key), '?')
        out.append({'key': key, 'role': role, 'text': text})
    return out


# --------------------------------------------------------------------------- #
# stored measurements
# --------------------------------------------------------------------------- #

def store(app, definition, kind, label, a, b, along=None, obtuse=False):
    """Create the custom feature. Returns it. `along` (a distance's direction) is the
    third dependency, 'c'. `obtuse`: an angle shown as 90-180 (see measure)."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    value = measure(app, kind, a, b, along, obtuse)
    record = {'kind': kind, 'label': label, 'value': value,
              'a': _describe(a), 'b': _describe(b),
              'created': datetime.datetime.now().isoformat(timespec='seconds')}
    if along is not None:
        record['along'] = _describe(along)
    if obtuse:
        record['obtuse'] = True
    inp = des.rootComponent.features.customFeatures.createInput(definition)
    inp.addDependency('a', a)
    inp.addDependency('b', b)
    if along is not None:
        inp.addDependency('c', along)
    feature = des.rootComponent.features.customFeatures.add(inp)
    feature.attributes.add(ATTR_GROUP, ATTR_NAME, json.dumps(record))
    feature.name = feature_name(label, kind, value)
    return feature


def record_of(feature):
    attr = feature.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
    return json.loads(attr.value) if attr else None


def label_now(feature, rec):
    """The measurement's label as the user sees it. Renaming the feature in the timeline
    ("L5", or "L5 = 7.42 mm") renames the measurement: the part before ' = '. Before
    2026-11-02 compute put the stored label back, so a timeline rename was lost at the
    next recompute and the panel showed the old label."""
    name = (feature.name or '').strip()
    if ' = ' in name:
        name = name.rsplit(' = ', 1)[0].strip()
    return name or rec['label']


def sync_name(feature, rec):
    """Adopt a timeline rename into the record, and keep the name "label = value".
    Returns True if anything changed."""
    label = label_now(feature, rec)
    want = feature_name(label, rec['kind'], rec.get('value'), rec.get('unit'))
    changed = label != rec['label']
    if changed:
        rec['label'] = label
        _write_record(feature, rec)
    if feature.name != want:
        feature.name = want
        changed = True
    return changed


def on_compute(app, feature):
    """Fusion recomputes a stored measurement: measure again, keep name and record in
    step. The record is missing during add() (compute fires before store() writes it):
    nothing to do then, store() names the feature itself."""
    record = record_of(feature)
    if record is None:
        return
    if record['kind'] == KIND_FORMULA:
        _reevaluate(feature.parentComponent.parentDesign, feature, record)
        return
    ents = _entities(feature)
    along = _along(feature)
    value = None
    if all(ents) and (along is not None or 'along' not in record):
        try:
            value = measure(app, record['kind'], ents[0], ents[1], along, record.get('obtuse', False))
        except Exception:
            value = None
    record['value'] = value
    record['label'] = label_now(feature, record)          # keep a timeline rename
    _write_record(feature, record)
    feature.name = feature_name(record['label'], record['kind'], value)


def selected_measurement(ui):
    """The stored measurement among the selected entities (clicked in the timeline), or
    None."""
    sels = ui.activeSelections
    for i in range(sels.count):
        ent = sels.item(i).entity
        f = adsk.fusion.CustomFeature.cast(ent)
        if f is None:
            tl = adsk.fusion.TimelineObject.cast(ent)
            f = adsk.fusion.CustomFeature.cast(tl.entity) if tl else None
        if f and f.isValid and record_of(f):
            return f
    return None


def _entities(feature):
    deps = [feature.dependencies.itemById(k) for k in ('a', 'b')]
    return [d.entity if d else None for d in deps]


def _along(feature):
    """The 'along' direction entity of a distance (dependency 'c'), or None."""
    d = feature.dependencies.itemById('c')
    return d.entity if d else None


def show_all_on(design):
    """'Show Measurements' for this design: on unless switched off (stored in the
    design, so it opens as it was left)."""
    a = design.attributes.itemByName(ATTR_GROUP, SHOW_ALL_ATTR)
    return a is None or a.value != '0'


def set_show_all(design, on):
    a = design.attributes.itemByName(ATTR_GROUP, SHOW_ALL_ATTR)
    if a:
        a.value = '1' if on else '0'
    else:
        design.attributes.add(ATTR_GROUP, SHOW_ALL_ATTR, '1' if on else '0')


def is_shown_rec(rec):
    """The measurement's own show setting (Choose Measurements / Place Measurement);
    shown unless switched off."""
    return rec.get('shown', True)


def stored(design):
    """(feature, record) of every stored measurement that is in effect (not rolled back
    or suppressed), in timeline order."""
    rows = []
    for item in design.timeline:
        f = adsk.fusion.CustomFeature.cast(item.entity) if item.entity else None
        rec = record_of(f) if f else None
        if rec and not item.isRolledBack and not item.isSuppressed:
            rows.append((f, rec))
    return rows


def _drawn(design):
    """(feature, record, a, b) of the measurements Show Measurements draws: in effect,
    switched on, entities still there. draw_all numbers its groups in this order."""
    rows = []
    for f, rec in stored(design):
        if is_shown_rec(rec):
            a, b = _entities(f)
            c = _along(f)
            if a and b and (c is not None or 'along' not in rec):
                rows.append((f, rec, a, b, c))
    return rows


def draw_all(app):
    """'Show Measurements' on: draw every stored measurement that is switched on (its
    own 'shown'); off: remove those drawings. Left alone while one is being placed - the
    placing command draws that one itself.

    Nothing is drawn while a document is still opening (no viewport yet): drawings made
    then stay on screen, but Fusion never lists them, so nothing can remove them until
    the design is closed - and every reopen drew them again ("ghost" drawings, found
    2026-11-02). They are drawn at the first command or panel click instead."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    if not des or app.activeViewport is None or app.userInterface.activeCommand == EDIT_CMD_ID:
        return
    # only ticked measurements with "Show in model" on are drawn: remove any other
    # drawing left over (the old selection drawing, a placing copy)
    for gid in (mt_draw.GROUP_ID, PLACE_GROUP):
        if mt_draw.is_shown(des, gid):
            mt_draw.clear(des, gid)
            app.activeViewport.refresh()
    if not show_all_on(des):
        if mt_draw._all_groups(des):
            mt_draw.clear_all(des)
            app.activeViewport.refresh()
        return
    n = 0
    for f, rec, a, b, c in _drawn(des):
        try:
            mt_draw.draw(app, rec['kind'], a, b, f.name, rec.get('offset'), mt_draw.all_id(n), c,
                         rec.get('obtuse', False))
            n += 1
        except Exception:
            continue
    removed = bool(mt_draw._all_groups(des)) and n < len(mt_draw._all_groups(des))
    mt_draw.clear_all(des, keep=n)
    if removed:
        app.activeViewport.refresh()


def _write_record(feature, rec):
    feature.attributes.itemByName(ATTR_GROUP, ATTR_NAME).value = json.dumps(rec)


def on_command_terminated(app):
    """Any command may have changed, added or deleted measurements: draw them again
    (cheap: unchanged drawings are only moved, see mt_draw.render), and refresh the
    Measurements panel. A command may also have been a rename in the timeline: adopt it
    (sync_name)."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    if des and app.userInterface.activeCommand != EDIT_CMD_ID:
        for f, rec in stored(des):
            try:
                sync_name(f, rec)
            except Exception:
                pass
        update_formulas(des)                    # values or names they use may have changed
    draw_all(app)
    push_palette(app)


# --------------------------------------------------------------------------- #
# the Measurements panel (a palette: resources/palette/measurements.html)
# --------------------------------------------------------------------------- #
# It lists every stored measurement - tick box (its 'shown'), name, live value, Place,
# Delete - with the design's master switch (Show Measurements) and "+ New" (Store
# Measurement). Clicking a name selects the measurement in the timeline. The panel holds
# no state of its own: every message returns the whole state, and push_palette sends it
# after anything that may have changed it.

def _feature(design, token):
    found = design.findEntityByToken(token) if token else []
    f = adsk.fusion.CustomFeature.cast(found[0]) if found else None
    return f if f and record_of(f) else None


def palette_state(app):
    des = adsk.fusion.Design.cast(app.activeProduct)
    if not des:
        return {'doc': None, 'master': False, 'rows': []}
    rows = []
    for item in des.timeline:
        f = adsk.fusion.CustomFeature.cast(item.entity) if item.entity else None
        rec = record_of(f) if f else None
        if not rec:
            continue
        value = rec.get('value')
        if rec['kind'] == KIND_FORMULA:
            broken = value is None                      # a name it uses is gone / renamed
        else:
            a, b = _entities(f)
            broken = not (a and b) or ('along' in rec and _along(f) is None)
        rows.append({'token': f.entityToken, 'label': label_now(f, rec), 'kind': rec['kind'],
                     'value': '?' if value is None else format_value(rec['kind'], value, rec.get('unit')),
                     'along': rec.get('along'), 'expr': rec.get('expr'), 'shown': is_shown_rec(rec),
                     'off': bool(item.isRolledBack or item.isSuppressed), 'broken': broken,
                     'obtuse': bool(rec.get('obtuse')), 'parts': measurement_parts(f, rec)})
    sel = selected_measurement(app.userInterface)
    return {'doc': app.activeDocument.name, 'master': show_all_on(des), 'rows': rows,
            'selected': sel.entityToken if sel else None}


PANEL_MIN_W = 150       # narrower than this counts as collapsed
PANEL_W = 320


def toggle_palette(app, pal):
    """The Measurements button. Opens the panel, restoring a usable width if it was
    collapsed; closes it only if it is open at a usable width. A docked palette can be
    collapsed to a ~22 px strip (its >> button): a plain isVisible toggle then hid it or
    "opened" the strip, and the user saw nothing open (2026-11-02). A docked palette
    ignores a new width: undock it, size it, dock it back (verified)."""
    if pal.isVisible and pal.width >= PANEL_MIN_W:
        pal.isVisible = False
        return
    pal.isVisible = True
    if pal.width < PANEL_MIN_W:
        floating = adsk.core.PaletteDockingStates.PaletteDockStateFloating
        dock = pal.dockingState
        if dock != floating:
            pal.dockingState = floating
        pal.width = PANEL_W
        if dock != floating:
            pal.dockingState = dock
    push_palette(app)


def push_palette(app):
    """Send the current state to the panel, if it is open."""
    pal = app.userInterface.palettes.itemById(PALETTE_ID)
    if pal and pal.isVisible:
        pal.sendInfoToHTML('update', json.dumps(palette_state(app)))


def on_palette_message(app, action, data):
    """A click in the panel. Returns the new state (JSON) for the panel to show, or ''."""
    if action == 'rendered':
        return ''
    ui = app.userInterface
    des = adsk.fusion.Design.cast(app.activeProduct)
    d = json.loads(data) if data else {}
    f = _feature(des, d.get('token')) if des else None
    if des and action == 'master':
        set_show_all(des, bool(d.get('on')))
        if d.get('on'):
            mt_draw.clear(des)                  # the single selection drawing
        draw_all(app)
        show_selected(app)
    elif f and action == 'toggle':
        rec = record_of(f)
        rec['shown'] = bool(d.get('shown'))
        _write_record(f, rec)
        draw_all(app)
    elif f and action in ('select', 'place'):
        ui.activeSelections.clear()
        ui.activeSelections.add(f)
        if action == 'place':
            ui.commandDefinitions.itemById(EDIT_CMD_ID).execute()
    elif f and action == 'select_part':
        # a part clicked in the expanded row: select it too, so the browser and the model
        # show it; the measurement stays selected, so the row stays open
        ent = {'a': _entities(f)[0], 'b': _entities(f)[1], 'c': _along(f)}.get(d.get('key'))
        ui.activeSelections.clear()
        ui.activeSelections.add(f)
        if ent is not None:
            try:
                ui.activeSelections.add(ent)
            except Exception:
                pass                    # e.g. inside a hidden sketch: nothing to highlight
    elif f and action == 'delete':
        f.deleteMe()
        draw_all(app)
    elif f and action == 'edit':
        # the Store dialog opens on this measurement in edit mode (_start_edit)
        _set_pending_edit(f.entityToken)
        ui.commandDefinitions.itemById(STORE_CMD_ID).execute()
    elif action == 'new':
        _set_pending_edit(None)
        ui.commandDefinitions.itemById(STORE_CMD_ID).execute()
    return json.dumps(palette_state(app))


def on_document_activated(app, show_all_control=None):
    """Another document: draw its measurements and show them in the panel."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    if des:
        if show_all_control:
            show_all_control.isChecked = show_all_on(des)
        draw_all(app)
    push_palette(app)


def on_document_saving(app, document):
    """About to save: remove every drawing first. Drawings present at Save are stored in
    that version - they came back on every open of the saved versions, with nothing
    ticked and even after the measurement was deleted, and the API lists none of them, so
    they can't be removed (2026-11-02; verified by opening old versions with drawing
    switched off)."""
    des = adsk.fusion.Design.cast(document.products.itemByProductType('DesignProductType'))
    if des:
        mt_draw.clear_everything(des)


def on_document_saved(app, document):
    """Saved: draw again. This marks the document modified (any custom graphics change
    does), so it shows unsaved right after a save; the design itself is unchanged."""
    if document == app.activeDocument:
        draw_all(app)


def on_toggle_show_all(app, checked):
    """The Show Measurements button was clicked. The drawing follows at the end of the
    command (on_command_terminated)."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    if des:
        set_show_all(des, checked)
        if checked:
            mt_draw.clear(des)              # the single selection drawing is not needed


def show_selected(app, closing_place=False):
    """Selecting a measurement no longer draws it (2026-11-02): the user wants drawings
    only for measurements ticked in the panel with "Show in model" on (draw_all). Before,
    with the master switch off, selecting one - in the timeline or by its name in the
    panel - drew it although unticked, and the drawing stayed until the selection
    changed. Now this only removes such a drawing if one is left."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    if des and mt_draw.is_shown(des):
        mt_draw.clear(des)
        app.activeViewport.refresh()


def on_selection_changed(app):
    """Also refreshes the panel: the selected measurement's row opens to show its parts
    (selected in the panel or in the timeline)."""
    show_selected(app)
    push_palette(app)


def on_camera_changed(app):
    """Nothing to do: drawings sit where the measurements are and don't depend on the
    camera (since 2026-11-02). Kept because the running shell calls it; can go with the
    shell's camera listener at the next shell change."""
    return


def all_measurements(design):
    """(name, record) of every stored measurement, in timeline order."""
    rows = []
    for item in design.timeline:
        f = adsk.fusion.CustomFeature.cast(item.entity) if item.entity else None
        if f:
            rec = record_of(f)
            if rec:
                rows.append((f.name, rec))
    return rows


# --------------------------------------------------------------------------- #
# command handlers (called by the shell)
# --------------------------------------------------------------------------- #

def _kind(inputs):
    return inputs.itemById('kind').selectedItem.name


def _picked(inputs, iid):
    sel = inputs.itemById(iid)
    return sel.selection(0).entity if sel.selectionCount else None


def _add_angle_style(inputs, obtuse=False):
    """'Angle shown': which of the two angles two lines make is stored (see measure)."""
    dd = inputs.addDropDownCommandInput('angle_style', 'Angle shown',
                                        adsk.core.DropDownStyles.TextListDropDownStyle)
    dd.listItems.add(ANGLE_ACUTE, not obtuse)
    dd.listItems.add(ANGLE_OBTUSE, obtuse)
    dd.tooltip = ('Two lines make two angles that add up to 180°. Acute: the smaller one '
                  '(IR, pronosupination). Obtuse: the larger one, as 3-matic shows the volar '
                  'tilt (e.g. 98° → tilt = 98 − 90 = 8°).')
    return dd


def _obtuse(inputs):
    dd = inputs.itemById('angle_style')
    return bool(dd and dd.isVisible and dd.selectedItem and dd.selectedItem.name == ANGLE_OBTUSE)


def on_created(app, cmd):
    inputs = cmd.commandInputs
    kind = inputs.addDropDownCommandInput('kind', 'Measure',
                                          adsk.core.DropDownStyles.TextListDropDownStyle)
    kind.listItems.add(KIND_ANGLE, True)
    kind.listItems.add(KIND_DISTANCE, False)
    kind.listItems.add(KIND_FORMULA, False)
    _add_angle_style(inputs)
    for iid, label in (('a', 'First'), ('b', 'Second')):
        sel = inputs.addSelectionInput(iid, label, 'Select what you would select in '
                                       'Inspect > Measure: an axis, plane, face, edge, point...')
        for f in SELECTION_FILTERS:
            sel.addSelectionFilter(f)
        sel.setSelectionLimits(0, 1)     # required for angles / distances: on_validate
    expr = inputs.addStringValueInput('expr', 'Formula', '')
    expr.tooltip = ('Other measurements by name, numbers and + - * / ( ). '
                    'E.g. IR - Pronosupinación, 90 - Inclinación volar, L5 / L4, L7 / L6 * 100.')
    expr.isVisible = False
    names = inputs.addTextBoxCommandInput('names', 'Names', '', 3, True)
    names.isVisible = False
    sel = inputs.addSelectionInput(
        'along', 'Along (optional)',
        'Measure only along this direction: an axis or line (its direction) or a plane (its '
        'normal) - like 3-matic\'s "Measure X / Y / Z" alone. Leave empty for the '
        'straight-line distance.')
    for f in ALONG_FILTERS:
        sel.addSelectionFilter(f)
    sel.setSelectionLimits(0, 1)
    sel.isVisible = False                # distances only
    inputs.addStringValueInput('label', 'Name', DEFAULT_LABEL[KIND_ANGLE])
    inputs.addTextBoxCommandInput('value', 'Value', 'Select two entities.', 1, True)
    inputs.addStringValueInput('editing', 'editing', '').isVisible = False
    cmd.okButtonText = 'Store in timeline'
    # the panel's ✎ left a note naming the measurement to edit
    token = _take_pending_edit()
    f = _feature(adsk.fusion.Design.cast(app.activeProduct), token) if token else None
    if f is not None:
        _start_edit(app, cmd, f)


# --------------------------------------------------------------------------- #
# editing a stored measurement: what it measures (the user, 2026-11-05)
# --------------------------------------------------------------------------- #
# The same dialog, filled with the measurement's own items; Update changes them on the
# SAME feature, so its timeline place, drawing place and the formulas using it stay.
# Verified first in throwaway documents:
# - a dependency's `entity` can be set in place, even to something newer in the
#   timeline (still right after a rebuild from the start; the API can't reorder it);
# - adding a dependency needs the timeline rolled to just before the feature
#   (rollTo(True)), else "Didn't roll editing feature back"; removing one works rolled
#   to just after it (rollTo(False)).

PENDING_EDIT_S = 10        # the ✎ note is only for the dialog it opens


def _state():
    """A small module object NOT reloaded with this core, for the one thing that must
    cross from the panel click to the dialog it opens: which measurement to edit. The
    selection was used first, but it can't always be cleared (Fusion refuses with no
    command running), so "+ New" opened the last selected measurement in edit mode."""
    return sys.modules.setdefault('mt_state', types.ModuleType('mt_state'))


def _set_pending_edit(token):
    _state().pending_edit = (token, time.time()) if token else None


def _take_pending_edit():
    """The token ✎ left, if it is fresh; the note is used once."""
    pe = getattr(_state(), 'pending_edit', None)
    _state().pending_edit = None
    if pe and time.time() - pe[1] < PENDING_EDIT_S:
        return pe[0]
    return None


def _editing(app, inputs):
    """The measurement being edited, or None when storing a new one."""
    token = inputs.itemById('editing').value
    if not token:
        return None
    return _feature(adsk.fusion.Design.cast(app.activeProduct), token)


def _start_edit(app, cmd, f):
    inputs = cmd.commandInputs
    rec = record_of(f)
    inputs.itemById('editing').value = f.entityToken
    kind = inputs.itemById('kind')
    for i in range(kind.listItems.count):
        kind.listItems.item(i).isSelected = kind.listItems.item(i).name == rec['kind']
    kind.isEnabled = False                  # a new kind is a new measurement
    on_input_changed(app, inputs, 'kind')   # which inputs show
    inputs.itemById('label').value = label_now(f, rec)
    if rec['kind'] == KIND_FORMULA:
        inputs.itemById('expr').value = rec.get('expr', '')
        _formula_preview(app, inputs)
    else:
        a, b = _entities(f)
        for iid, ent in (('a', a), ('b', b), ('along', _along(f))):
            if ent is not None:
                try:
                    inputs.itemById(iid).addSelection(ent)
                except Exception:
                    pass
        dd = inputs.itemById('angle_style')
        dd.listItems.item(1 if rec.get('obtuse') else 0).isSelected = True
        on_input_changed(app, inputs, 'angle_style')    # the value box
    cmd.okButtonText = 'Update'


def _add_dependency(design, f, key, entity):
    """Add dependency `key` -> entity. Fusion only adds one with the timeline rolled to
    just before the feature; there the entity may not exist yet, so a placeholder that
    always does (the root X axis) goes in first and the real entity is set after."""
    tl = design.timeline
    marker = tl.markerPosition
    f.timelineObject.rollTo(True)
    try:
        f.dependencies.add(key, design.rootComponent.xConstructionAxis)
    finally:
        tl.markerPosition = marker
    f.dependencies.itemById(key).entity = entity


def _remove_dependency(design, f, key):
    dep = f.dependencies.itemById(key)
    if dep is None:
        return
    tl = design.timeline
    marker = tl.markerPosition
    f.timelineObject.rollTo(False)
    try:
        dep.deleteMe()
    finally:
        tl.markerPosition = marker


def update_measurement(app, f, inputs):
    """Write the dialog's items, name and angle style into the measurement `f`."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    rec = record_of(f)
    label = inputs.itemById('label').value.strip()
    if rec['kind'] == KIND_FORMULA:
        expr = inputs.itemById('expr').value.strip()
        value, unit = evaluate(des, expr, exclude=f)
        rec.update({'expr': expr, 'value': value, 'unit': unit, 'label': label})
        _write_record(f, rec)
        f.name = feature_name(label, KIND_FORMULA, value, unit)
        return
    a, b = _picked(inputs, 'a'), _picked(inputs, 'b')
    c = _along_input(inputs)
    obtuse = _obtuse(inputs) if rec['kind'] == KIND_ANGLE else False
    rec.update({'label': label, 'a': _describe(a), 'b': _describe(b)})
    if obtuse:
        rec['obtuse'] = True
    else:
        rec.pop('obtuse', None)
    if c is not None:
        rec['along'] = _describe(c)
    else:
        rec.pop('along', None)
    _write_record(f, rec)                   # compute (fired by the changes) reads it
    cur_a, cur_b = _entities(f)
    if a != cur_a:
        f.dependencies.itemById('a').entity = a
    if b != cur_b:
        f.dependencies.itemById('b').entity = b
    dep_c = f.dependencies.itemById('c')
    if c is not None and dep_c is None:
        _add_dependency(des, f, 'c', c)
    elif c is not None and dep_c.entity != c:
        dep_c.entity = c
    elif c is None and dep_c is not None:
        _remove_dependency(des, f, 'c')
    rec = record_of(f)
    rec['label'] = label
    rec['value'] = measure(app, rec['kind'], a, b, c, obtuse)
    _write_record(f, rec)
    f.name = feature_name(label, rec['kind'], rec['value'])


def _along_input(inputs):
    """The picked 'along' entity, only for a distance."""
    return _picked(inputs, 'along') if _kind(inputs) == KIND_DISTANCE else None


def _formula_preview(app, inputs):
    box = inputs.itemById('value')
    expr = inputs.itemById('expr').value.strip()
    if not expr:
        box.text = 'Type a formula using the names below.'
        return
    try:
        value, unit = evaluate(adsk.fusion.Design.cast(app.activeProduct), expr,
                               exclude=_editing(app, inputs))
        box.text = format_value(KIND_FORMULA, value, unit)
    except ValueError as e:
        box.text = str(e)


def on_input_changed(app, inputs, input_id):
    if input_id == 'kind':
        label = inputs.itemById('label')
        if label.value in DEFAULT_LABEL.values():
            label.value = DEFAULT_LABEL[_kind(inputs)]
        formula = _kind(inputs) == KIND_FORMULA
        for iid in ('a', 'b'):
            inputs.itemById(iid).isVisible = not formula
        inputs.itemById('along').isVisible = _kind(inputs) == KIND_DISTANCE
        inputs.itemById('angle_style').isVisible = _kind(inputs) == KIND_ANGLE
        inputs.itemById('expr').isVisible = formula
        names = inputs.itemById('names')
        names.isVisible = formula
        if formula:
            labels = sorted(_measurement_values(adsk.fusion.Design.cast(app.activeProduct)))
            names.text = ', '.join(labels) if labels else 'No stored measurements yet.'
            _formula_preview(app, inputs)
            return
    if input_id in ('expr',) and _kind(inputs) == KIND_FORMULA:
        _formula_preview(app, inputs)
        return
    if input_id not in ('kind', 'a', 'b', 'along', 'angle_style'):
        return
    a, b = _picked(inputs, 'a'), _picked(inputs, 'b')
    # move on to the next selection box, as Fusion's own tools do (an add-in dialog
    # doesn't by itself: the user had to click "Second" before picking, 2026-11-02)
    if input_id == 'a' and a and not b:
        inputs.itemById('b').hasFocus = True
    elif input_id == 'b' and b and not a:
        inputs.itemById('a').hasFocus = True
    elif input_id == 'b' and b and _kind(inputs) == KIND_DISTANCE and _picked(inputs, 'along') is None:
        inputs.itemById('along').hasFocus = True
    box = inputs.itemById('value')
    if not (a and b):
        box.text = 'Select two entities.'
        return
    along = _along_input(inputs)
    try:
        text = format_value(_kind(inputs), measure(app, _kind(inputs), a, b, along, _obtuse(inputs)))
        box.text = text + (' along %s' % _describe(along) if along is not None else '')
    except Exception as e:
        box.text = 'Cannot measure this pair: %s' % str(e).split(':')[-1].strip()


def on_validate(app, inputs):
    des = adsk.fusion.Design.cast(app.activeProduct)
    if not inputs.itemById('label').value.strip():
        return False
    if des.designType != adsk.fusion.DesignTypes.ParametricDesignType:
        return False                     # custom features need the timeline
    if _kind(inputs) == KIND_FORMULA:
        try:
            evaluate(des, inputs.itemById('expr').value.strip(), exclude=_editing(app, inputs))
            return True
        except ValueError:
            return False
    a, b = _picked(inputs, 'a'), _picked(inputs, 'b')
    if not (a and b):
        return False
    try:
        measure(app, _kind(inputs), a, b, _along_input(inputs))
        return True
    except Exception:
        return False


def on_execute(app, cmd, definition):
    inputs = cmd.commandInputs
    editing = _editing(app, inputs)
    if editing is not None:
        update_measurement(app, editing, inputs)
        return
    label = inputs.itemById('label').value.strip()
    if _kind(inputs) == KIND_FORMULA:
        store_formula(app, definition, label, inputs.itemById('expr').value.strip())
        return
    store(app, definition, _kind(inputs), label,
          _picked(inputs, 'a'), _picked(inputs, 'b'), _along_input(inputs), _obtuse(inputs))


# --------------------------------------------------------------------------- #
# placing a stored measurement: the custom feature's edit command
# --------------------------------------------------------------------------- #
# Double-click the measurement in the timeline (or Edit Feature): its drawing follows
# the mouse; a click fixes it, another click lets it follow again; OK keeps the place.
# 3-matic lets you slide a measurement around so it doesn't hide the bones.
# The place is the record's 'offset' (cm, world). The state lives in hidden inputs:
# 'feature' (token), 'offset' (JSON), 'follow' ('1' / '0'). The moving drawing is made
# in executePreview - graphics made anywhere else in a command are dropped by Fusion -
# and changing the hidden 'offset' input is what makes Fusion run the preview again.

def _placed(app, inputs):
    """(feature, record, a, b, along) of the measurement being placed."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    found = des.findEntityByToken(inputs.itemById('feature').value)
    f = adsk.fusion.CustomFeature.cast(found[0]) if found else None
    if f is None:
        raise ValueError('The measurement being placed is gone.')
    a, b = _entities(f)
    return f, record_of(f), a, b, _along(f)


def on_place_created(app, cmd):
    """Fusion selects the feature before running its edit command."""
    f = selected_measurement(app.userInterface)
    if f is None:
        raise ValueError('Select a stored measurement first.')
    inputs = cmd.commandInputs
    inputs.addStringValueInput('feature', 'feature', f.entityToken).isVisible = False
    inputs.addStringValueInput('offset', 'offset',
                               json.dumps(record_of(f).get('offset') or [0.0, 0.0, 0.0])).isVisible = False
    inputs.addStringValueInput('follow', 'follow', '1').isVisible = False
    inputs.addTextBoxCommandInput(
        'help', '', '<b>%s</b><br>Move the mouse: the measurement follows it. Click to fix it '
        'there (click again to move it again), then OK.' % f.name, 3, True)
    inputs.addBoolValueInput('reset', 'Back to its own place', True, '', False)
    cmd.okButtonText = 'Place'
    inputs.addBoolValueInput('shown', 'Shown', True, '', is_shown_rec(record_of(f)))
    if record_of(f)['kind'] == KIND_ANGLE:                  # switch acute / obtuse later
        _add_angle_style(inputs, record_of(f).get('obtuse', False))
    des = adsk.fusion.Design.cast(app.activeProduct)
    mt_draw.clear(des)                                      # the still copies: selection's
    for n, row in enumerate(_drawn(des)):                   # and Show Measurements' one
        if row[0] == f:
            mt_draw.clear(des, mt_draw.all_id(n))
            break


def _offset_to(app, inputs, screen_pt):
    f, rec, a, b, c = _placed(app, inputs)
    inputs.itemById('offset').value = json.dumps(
        list(mt_draw.offset_for_mouse(app, rec['kind'], a, b, screen_pt, c, _obtuse(inputs))))


def on_place_mouse_move(app, cmd, screen_pt):
    inputs = cmd.commandInputs
    if inputs.itemById('follow').value == '1':
        _offset_to(app, inputs, screen_pt)


def on_place_click(app, cmd, screen_pt):
    inputs = cmd.commandInputs
    follow = inputs.itemById('follow')
    if follow.value == '1':
        _offset_to(app, inputs, screen_pt)
        follow.value = '0'
    else:
        follow.value = '1'


def on_place_input_changed(app, inputs, input_id):
    if input_id != 'reset':
        return
    box = inputs.itemById('reset')
    if not box.value:                       # our own untick echoing back
        return
    box.value = False
    inputs.itemById('follow').value = '0'
    inputs.itemById('offset').value = json.dumps([0.0, 0.0, 0.0])


def on_place_preview(app, cmd):
    inputs = cmd.commandInputs
    f, rec, a, b, c = _placed(app, inputs)
    text = f.name
    if rec['kind'] == KIND_ANGLE and _obtuse(inputs) != rec.get('obtuse', False):
        text = feature_name(label_now(f, rec), rec['kind'], measure(app, rec['kind'], a, b, c, _obtuse(inputs)))
    mt_draw.draw(app, rec['kind'], a, b, text, json.loads(inputs.itemById('offset').value),
                 PLACE_GROUP, c, _obtuse(inputs))


def on_place_execute(app, cmd):
    inputs = cmd.commandInputs
    f, rec, a, b, c = _placed(app, inputs)
    rec['offset'] = json.loads(inputs.itemById('offset').value)
    rec['shown'] = inputs.itemById('shown').value
    if rec['kind'] == KIND_ANGLE and _obtuse(inputs) != rec.get('obtuse', False):
        rec['obtuse'] = _obtuse(inputs)
        rec['label'] = label_now(f, rec)
        rec['value'] = measure(app, rec['kind'], a, b, c, rec['obtuse'])
        f.name = feature_name(rec['label'], rec['kind'], rec['value'])
    _write_record(f, rec)


# --------------------------------------------------------------------------- #
# Choose Measurements: which stored measurements Show Measurements draws
# (v0.6 command; the v0.8 shell replaced it and Show Measurements with the panel. Kept
#  only so an older running shell doesn't break until the user's Stop/Run - can go.)
# --------------------------------------------------------------------------- #
# One tick box per measurement ('m0', 'm1', ... in timeline order; their features'
# tokens in the hidden input 'tokens'), plus Show all / Hide all. OK writes each
# record's 'shown'; the drawing follows when the command ends (on_command_terminated).

def on_choose_created(app, cmd):
    des = adsk.fusion.Design.cast(app.activeProduct)
    inputs = cmd.commandInputs
    rows = stored(des) if des else []
    if not rows:
        inputs.addTextBoxCommandInput('empty', '', 'There are no stored measurements in this '
                                      'design.', 2, True)
        cmd.isOKButtonVisible = False
        return
    if not show_all_on(des):
        inputs.addTextBoxCommandInput(
            'off', '', '<i>Show Measurements is off: nothing is drawn until you switch it on '
            '(Inspect &rarr; Show Measurements).</i>', 2, True)
    inputs.addStringValueInput('tokens', 'tokens', json.dumps([f.entityToken for f, _ in rows])).isVisible = False
    for i, (f, rec) in enumerate(rows):
        inputs.addBoolValueInput('m%d' % i, f.name, True, '', is_shown_rec(rec))
    inputs.addBoolValueInput('all', 'Show all', False, '', False)
    inputs.addBoolValueInput('none', 'Hide all', False, '', False)
    cmd.okButtonText = 'Apply'


def on_choose_input_changed(app, inputs, input_id):
    if input_id not in ('all', 'none'):
        return
    n = len(json.loads(inputs.itemById('tokens').value))
    for i in range(n):
        inputs.itemById('m%d' % i).value = input_id == 'all'


def on_choose_execute(app, cmd):
    des = adsk.fusion.Design.cast(app.activeProduct)
    inputs = cmd.commandInputs
    tokens = inputs.itemById('tokens')
    if not des or tokens is None:
        return
    for i, tok in enumerate(json.loads(tokens.value)):
        found = des.findEntityByToken(tok)
        f = adsk.fusion.CustomFeature.cast(found[0]) if found else None
        rec = record_of(f) if f else None
        if rec is None:
            continue
        on = inputs.itemById('m%d' % i).value
        if is_shown_rec(rec) != on:
            rec['shown'] = on
            _write_record(f, rec)


def on_place_destroy(app, cmd):
    """Drop the moving copy; the measurement stays selected and is drawn at its (new)
    place."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    if not des:
        return
    mt_draw.clear(des, PLACE_GROUP)
    try:
        f = _placed(app, cmd.commandInputs)[0]
        sels = app.userInterface.activeSelections
        sels.clear()
        sels.add(f)
    except Exception:
        pass
    show_selected(app, closing_place=True)
