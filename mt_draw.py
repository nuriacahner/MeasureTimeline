"""MeasureTimeline - draw a stored measurement in the viewport, the way 3-matic shows one.
RE-IMPORTED with mt_core (hot reload); no state between events.

What 3-matic shows (Dlab-IT007 figures), and what is drawn here:
- for a distance, the measured entities: planes as translucent green panels with a
  blue outline, axes and lines in blue, points as blue dots. Not for an angle: the
  user wants only its orange lines and the value there (2026-10-30);
- the measurement in orange: for a distance the line between the two closest points,
  for an angle its two legs and the arc between them;
- the value in a cream label with a dark border, a fixed size on screen, facing the
  viewer.

Everything is drawn where it is, so a measurement inside a bone is hidden by it: the
user doesn't need it in front (2026-11-02). Nothing depends on the camera, so camera
moves cost nothing. History: from 2026-10-30 to 2026-11-02, unplaced measurements were
moved onto a camera-facing "sheet" in front of the model (Fusion's custom graphics have
no "draw on top"; depthPriority only orders coplanar graphics). That meant a redraw at
every camera event, which flickered until redraws moved graphics in place. Measuring
"in front" from rootComponent.boundingBox (it includes custom graphics) fed back and
dragged Fusion's orbit point ~1.3 m off. Measuring the bodies instead made orbiting
sluggish (body.boundingBox ~10 ms per NURBS bone).

Angles are drawn where the entities are, not at a far-away intersection:
- two planes: on their intersection line, at the point nearest the middle of the
  planes, legs across each plane;
- two lines: where they cross, if they do near the measurement (e.g. two axes made in
  the same reference plane); otherwise at the point of the first line nearest the
  middle, the second leg parallel to the second line (dashed) - nearly parallel bone
  axes may only meet far off the bone;
- a line and a plane: where the line meets the plane, legs along the line and along
  its projection on the plane.
Anything else (faces, bodies) gets just the label, at the measured point.

Verified API facts (2026-10-30):
- `CustomGraphicsViewScale.create(pixelScale, anchor)`: the anchor is in the entity's
  LOCAL coordinates (before its transform). A world anchor makes it vanish.
- Text: billboarding ScreenBillBoardStyle + viewScale 1 = constant size in pixels.
"""

import adsk.core
import adsk.fusion

import math

GROUP_ID = 'MeasureTimeline_show'
ALL_ID = 'MeasureTimeline_all'   # + number: "Show Measurements" groups, one per measurement
ORANGE = (235, 110, 20)
BLUE = (30, 60, 230)
PANEL = (90, 170, 90)
PANEL_OPACITY = 0.35
LABEL_BG = (255, 244, 222)
LABEL_EDGE = (70, 70, 70)
LABEL_TEXT = (0, 0, 0)
LABEL_PX = 16               # text size on screen, pixels
LABEL_PAD_PX = 4
DOT_PX = 4                  # point marks, radius in pixels
LEG_CM = 2.0                # angle legs, 20 mm
ARC_CM = 1.4
ANGLE_WEIGHT = 1.5         # angle legs and arc, pixels (was 3 / 2; the user: thinner)
MIN_ARC_CM = 0.2            # a placed arc never shrinks below 2 mm
EXTEND_CM = 0.3             # legs run 3 mm past a placed arc
AXIS_HALF_CM = 6.0          # axes are drawn 60 mm each way from the measurement
PLANE_HALF_CM = 2.0         # plane panels, 40 x 40 mm


# --------------------------------------------------------------------------- #
# small vector maths on tuples (cm, world space)
# --------------------------------------------------------------------------- #

def _t(p):
    return (p.x, p.y, p.z)


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def unit(a):
    n = math.sqrt(dot(a, a))
    return mul(a, 1.0 / n) if n > 1e-12 else None


def mid(*pts):
    return mul(tuple(sum(c) for c in zip(*pts)), 1.0 / len(pts))


def on_line(p, d, q):
    """Point of the line (p, unit d) nearest q."""
    return add(p, mul(d, dot(sub(q, p), d)))


def on_plane(o, n, q):
    """Projection of q on the plane (o, unit n)."""
    return sub(q, mul(n, dot(sub(q, o), n)))


def arc(centre, a, b, radius, n=24):
    """Points on the arc from unit a to unit b (the shorter way) around centre."""
    ang = math.acos(max(-1.0, min(1.0, dot(a, b))))
    w = unit(sub(b, mul(a, dot(a, b))))
    if w is None:
        return [add(centre, mul(a, radius))]
    return [add(centre, add(mul(a, radius * math.cos(ang * k / n)),
                            mul(w, radius * math.sin(ang * k / n)))) for k in range(n + 1)]


# --------------------------------------------------------------------------- #
# what the measured entities are, geometrically
# --------------------------------------------------------------------------- #

def shape(entity):
    """('point', p) | ('line', p, d) | ('plane', o, n, u, v) | None, world space, cm."""
    F = adsk.fusion
    for cls, get in ((F.ConstructionPoint, lambda e: e.geometry),
                     (F.SketchPoint, lambda e: e.worldGeometry),
                     (F.BRepVertex, lambda e: e.geometry)):
        e = cls.cast(entity)
        if e:
            return ('point', _t(get(e)))
    e = F.ConstructionAxis.cast(entity)
    if e:
        g = e.geometry
        return ('line', _t(g.origin), unit(_t(g.direction)))
    line = None
    e = F.SketchLine.cast(entity)
    if e:
        line = e.worldGeometry
    e = F.BRepEdge.cast(entity)
    if e:
        line = adsk.core.Line3D.cast(e.geometry)
    if line:
        a, b = _t(line.startPoint), _t(line.endPoint)
        return ('line', mid(a, b), unit(sub(b, a)))
    e = F.ConstructionPlane.cast(entity)
    plane = e.geometry if e else None
    centre = _t(plane.origin) if plane else None
    e = F.BRepFace.cast(entity)
    if e and e.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType:
        plane = adsk.core.Plane.cast(e.geometry)
        centre = _t(e.pointOnFace)
    if plane:
        return ('plane', centre, unit(_t(plane.normal)), unit(_t(plane.uDirection)),
                unit(_t(plane.vDirection)))
    return None


# --------------------------------------------------------------------------- #
# the measurement as a list of items, in 3D
# --------------------------------------------------------------------------- #
# ('seg', p, q, rgb, weight, dashed) | ('poly', [p...], rgb, weight)
# ('panel', [4 corners]) | ('dot', p, rgb) | ('label', p, text)

def _entity_items(s, near):
    """The measured entity, 3-matic style, around the point `near`."""
    if s[0] == 'point':
        return [('dot', s[1], BLUE)]
    if s[0] == 'line':
        c = on_line(s[1], s[2], near)
        return [('seg', add(c, mul(s[2], -AXIS_HALF_CM)), add(c, mul(s[2], AXIS_HALF_CM)),
                 BLUE, 2, False)]
    c, u, v = on_plane(s[1], s[2], near), s[3], s[4]
    return [('panel', [add(add(c, mul(u, su * PLANE_HALF_CM)), mul(v, sv * PLANE_HALF_CM))
                       for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))])]


def _angle_frame(sa, sb, centre):
    """(vertex, leg a, leg b, dashed b?, slide) for the angle between two shapes, or
    None. `slide` is the direction the vertex may move in without changing what is
    shown (two planes: their intersection line; two lines: along the first), or None."""
    kinds = (sa[0], sb[0])
    if kinds == ('plane', 'plane'):
        n1, n2 = sa[2], sb[2]
        d = unit(cross(n1, n2))
        if d is None:
            return None
        h1, h2, c12 = dot(n1, sa[1]), dot(n2, sb[1]), dot(n1, n2)
        p0 = mul(add(mul(n1, h1 - h2 * c12), mul(n2, h2 - h1 * c12)), 1.0 / (1 - c12 * c12))
        v = on_line(p0, d, centre)
        u1, u2 = unit(cross(d, n1)), unit(cross(d, n2))
        if dot(sub(centre, v), u1) < 0:
            u1 = mul(u1, -1)
        if dot(u1, u2) < 0:
            u2 = mul(u2, -1)
        return v, u1, u2, False, d
    if kinds == ('line', 'line'):
        d1, d2 = sa[2], sb[2]
        if dot(d1, d2) < 0:
            d2 = mul(d2, -1)
        n = cross(d1, d2)
        nn = math.sqrt(dot(n, n))
        if nn > 1e-6:
            gap = abs(dot(sub(sb[1], sa[1]), n)) / nn
            t = dot(cross(sub(sb[1], sa[1]), d2), n) / (nn * nn)
            x = add(sa[1], mul(d1, t))             # line 1's point nearest line 2
            if gap < MEET_CM and math.sqrt(dot(sub(x, centre), sub(x, centre))) < MEET_FAR_CM:
                return x, d1, d2, False, d1          # they cross here: a plain angle
        # nearly parallel bone axes meet far off the bone, or never: leg 2 transported
        return on_line(sa[1], d1, centre), d1, d2, True, d1
    if 'line' in kinds and 'plane' in kinds:
        ln, pl = (sa, sb) if sa[0] == 'line' else (sb, sa)
        d, n = ln[2], pl[2]
        dn = dot(d, n)
        v = (add(ln[1], mul(d, dot(sub(pl[1], ln[1]), n) / dn)) if abs(dn) > 1e-9
             else on_line(ln[1], d, centre))
        proj = unit(sub(d, mul(n, dn)))
        if proj is None:                   # perpendicular: any direction in the plane
            proj = pl[3]
        if dot(d, proj) < 0:
            d = mul(d, -1)
        legs = (d, proj) if ln is sa else (proj, d)
        return v, legs[0], legs[1], False, None
    return None


def _frame(sa, sb, centre, obtuse=False):
    """_angle_frame, with leg b reversed for a measurement shown as the obtuse angle
    (3-matic's volar tilt, 90-180: the record's 'obtuse'), so the arc spans what the
    number says."""
    f = _angle_frame(sa, sb, centre)
    if f and obtuse:
        v, l1, l2, dashed, slide = f
        return v, l1, mul(l2, -1), dashed, slide
    return f


def _distance_offset(p1, p2, offset):
    """The part of the user's offset across the distance line: the dimension line is
    moved sideways, never along itself (3-matic's dimension lines, IT007 figures)."""
    u = unit(sub(p2, p1))
    return sub(offset, mul(u, dot(offset, u))) if u else offset


def along_dir(entity):
    """Unit direction a distance is measured along: an axis / line's direction, or a
    plane's normal (a distance 'along' a plane is across it, like 3-matic's single axis
    of a plane's coordinate system). None if the entity gives no direction."""
    s = shape(entity) if entity is not None else None
    if s and s[0] in ('line', 'plane'):
        return s[2]
    return None


def along_points(app, a, b, along):
    """(p1, p2, foot, d): the measured points, the direction, and foot = p1 + the part of
    p2 - p1 along d - so p1 -> foot is the measured length, and foot -> p2 is what is
    left out (the other two directions)."""
    r = app.measureManager.measureMinimumDistance(a, b)
    p1, p2 = _t(r.positionOne), _t(r.positionTwo)
    d = along_dir(along)
    if d is None:
        raise ValueError('"Along" must be an axis, a line or a plane.')
    return p1, p2, add(p1, mul(d, dot(sub(p2, p1), d))), d


def handle(app, kind, a, b, along=None, obtuse=False):
    """The point of the drawing that follows the mouse when it is being placed, before
    any offset: the arc's middle for an angle, the middle of the line for a distance."""
    if kind == 'Distance':
        if along is not None:
            p1, p2, foot, d = along_points(app, a, b, along)
            return mid(p1, foot)
        r = app.measureManager.measureMinimumDistance(a, b)
        return mid(_t(r.positionOne), _t(r.positionTwo))
    sa, sb = shape(a), shape(b)
    known = [s for s in (sa, sb) if s]
    frame = _frame(sa, sb, mid(*[s[1] for s in known]), obtuse) if sa and sb else None
    if frame is None:
        return _t(app.measureManager.measureAngle(a, b).positionOne)
    v, l1, l2 = frame[:3]
    return add(v, mul(unit(add(l1, l2)) or l1, ARC_CM))


def items(app, kind, a, b, text, offset=None, along=None, obtuse=False):
    """What to draw for the measurement of entities a, b (kind 'Distance' / 'Angle').

    An angle is only its orange legs, arc and label: the user didn't want the measured
    planes / axes drawn (2026-10-30). A distance also shows the measured entities.

    `offset` (cm, world) is where the user slid the drawing to, 3-matic style, so that
    it doesn't hide the bones: handle() + offset is the point the user put under the
    mouse.
    - An angle (as in IT007's IR figure): the vertex stays where the planes meet; the
      arc goes through the point of the angle's own plane under the mouse
      (offset_for_mouse), on the mouse's side; the legs are drawn from the vertex out
      past the arc as extension lines, so the figure stays tied to the measured
      planes / lines. History: v0.4 moved the angle as a rigid figure (no extension
      lines, the user missed them); then the vertex slid along the planes'
      intersection line, which in an oblique view ate most of a sideways drag, so the
      arc hardly grew (the user, 2026-10-30).
    - A distance: its line is moved sideways, with thin extension lines back to the
      measured points.
    - A distance `along` an axis / plane normal (3-matic's "Measure X / Y / Z only"):
      the dimension line runs along that direction from the first point to the second
      point's level, with a thin extension line from the second point to it (as in
      IT007's Stahl figure); placing slides it sideways."""
    o = tuple(offset) if offset else (0.0, 0.0, 0.0)
    sa, sb = shape(a), shape(b)
    known = [s for s in (sa, sb) if s]
    out = []
    if kind == 'Distance' and along is not None:
        p1, p2, foot, d = along_points(app, a, b, along)
        for s in known:
            out += _entity_items(s, mid(p1, p2))
        o = sub(o, mul(d, dot(o, d)))           # sideways only
        q1, q2 = add(p1, o), add(foot, o)
        if dot(o, o) > 1e-8:
            out.append(('seg', p1, q1, ORANGE, 1, False))
        if dot(sub(p2, q2), sub(p2, q2)) > 1e-8:
            out.append(('seg', p2, q2, ORANGE, 1, False))
        out += [('seg', q1, q2, ORANGE, 3, False), ('dot', q1, ORANGE), ('dot', q2, ORANGE),
                ('label', mid(q1, q2), text)]
        return out
    if kind == 'Distance':
        r = app.measureManager.measureMinimumDistance(a, b)
        p1, p2 = _t(r.positionOne), _t(r.positionTwo)
        for s in known:
            out += _entity_items(s, mid(p1, p2))
        o = _distance_offset(p1, p2, o)
        q1, q2 = add(p1, o), add(p2, o)
        if dot(o, o) > 1e-8:
            out += [('seg', p1, q1, ORANGE, 1, False), ('seg', p2, q2, ORANGE, 1, False)]
        out += [('seg', q1, q2, ORANGE, 3, False), ('dot', q1, ORANGE), ('dot', q2, ORANGE),
                ('label', mid(q1, q2), text)]
        return out
    centre = mid(*[s[1] for s in known]) if known else None
    frame = _frame(sa, sb, centre, obtuse) if sa and sb else None
    if frame is None:
        return [('label', add(_t(app.measureManager.measureAngle(a, b).positionOne), o), text)]
    v, l1, l2, dashed = frame[:4]
    bis = unit(add(l1, l2)) or l1
    if dot(o, o) <= 1e-8:                      # not placed: the default figure
        return [('seg', v, add(v, mul(l1, LEG_CM)), ORANGE, ANGLE_WEIGHT, False),
                ('seg', v, add(v, mul(l2, LEG_CM)), ORANGE, ANGLE_WEIGHT, dashed),
                ('poly', arc(v, l1, l2, ARC_CM), ORANGE, ANGLE_WEIGHT),
                ('label', add(v, mul(bis, ARC_CM * 1.4)), text)]
    m = add(add(v, mul(bis, ARC_CM)), o)      # where the user put the arc
    if dashed and dot(l1, l2) > math.cos(math.radians(PARALLEL_DEG)):
        # nearly parallel lines that don't cross (bone axes, e.g. pronosupination 0.47°):
        # there is no true vertex, so it slides along line 1 to where the user put the
        # arc. With it fixed at the bones' middle, the legs ran from there back through the
        # bone to a label placed at the top (found 2026-11-05). Skew lines at a
        # real angle (IR, 11.7°) keep their vertex, so their legs still reach the lines.
        v = sub(on_line(v, l1, m), mul(l1, ARC_CM))
    w = sub(m, v)
    ns = unit(cross(l1, l2))
    if ns:                                     # into the plane of the angle
        w = sub(w, mul(ns, dot(w, ns)))
    r = math.sqrt(dot(w, w))
    if r < MIN_ARC_CM:
        r, w = ARC_CM, mul(bis, ARC_CM)
    if dot(w, bis) < 0:                        # the mouse is on the other side
        l1, l2, bis = mul(l1, -1), mul(l2, -1), mul(bis, -1)
    reach = max(r, LEG_CM) + EXTEND_CM
    # the label sits on the arc, at its point nearest the mouse (at the mouse itself it
    # floated away from the arc when the mouse was beside it)
    e2 = unit(sub(l2, mul(l1, dot(l1, l2)))) or bis
    span = math.acos(max(-1.0, min(1.0, dot(l1, l2))))
    phi = min(max(math.atan2(dot(w, e2), dot(w, l1)), 0.0), span)
    on_arc = add(v, mul(add(mul(l1, math.cos(phi)), mul(e2, math.sin(phi))), r))
    return [('seg', v, add(v, mul(l1, reach)), ORANGE, ANGLE_WEIGHT, False),
            ('seg', v, add(v, mul(l2, reach)), ORANGE, ANGLE_WEIGHT, dashed),
            ('poly', arc(v, l1, l2, r), ORANGE, ANGLE_WEIGHT),
            ('label', on_arc, text)]


def _mouse_ray(app, screen_pt):
    """(origin, unit direction, view direction) of the line of sight under the mouse."""
    vp = app.activeViewport
    cam = vp.camera
    eye = _t(cam.eye)
    vd = unit(sub(_t(cam.target), eye))
    p = _t(vp.viewToModelSpace(screen_pt))
    if cam.cameraType == adsk.core.CameraTypes.PerspectiveCameraType:
        return eye, unit(sub(p, eye)), vd
    return p, vd, vd


def _ray_plane(origin, d, point, normal):
    den = dot(d, normal)
    if abs(den) < 1e-9:
        return None
    return add(origin, mul(d, dot(sub(point, origin), normal) / den))


def offset_at_screen(app, ref, screen_pt):
    """World offset that brings the point `ref` under the mouse (`screen_pt`, a
    viewport Point2D), moving it in the plane facing the camera through `ref`."""
    origin, d, vd = _mouse_ray(app, screen_pt)
    hit = _ray_plane(origin, d, ref, vd)
    return sub(hit, ref) if hit else (0.0, 0.0, 0.0)


PARALLEL_DEG = 5.0          # non-crossing lines closer than this: the vertex slides (items)
EDGE_ON = 0.15              # |cos| below this: the angle's plane is too edge-on to aim at
MEET_CM = 0.05              # two lines closer than 0.5 mm cross: vertex at the crossing...
MEET_FAR_CM = 15.0          # ...if the crossing is within 150 mm of the measurement


def offset_for_mouse(app, kind, a, b, screen_pt, along=None, obtuse=False):
    """The offset to store while placing: for an angle, the point of the angle's own
    plane under the mouse (so the arc passes under the cursor); for a distance, or an
    angle seen edge-on, the point under the mouse in the plane facing the camera."""
    ref = handle(app, kind, a, b, along, obtuse)
    if kind != 'Distance':
        sa, sb = shape(a), shape(b)
        known = [s for s in (sa, sb) if s]
        frame = _frame(sa, sb, mid(*[s[1] for s in known]), obtuse) if sa and sb else None
        ns = unit(cross(frame[1], frame[2])) if frame else None
        if ns:
            origin, d, vd = _mouse_ray(app, screen_pt)
            if abs(dot(d, ns)) > EDGE_ON:
                hit = _ray_plane(origin, d, frame[0], ns)
                if hit:
                    return sub(hit, ref)
    return offset_at_screen(app, ref, screen_pt)


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def _colour(rgb, alpha=255):
    return adsk.fusion.CustomGraphicsSolidColorEffect.create(
        adsk.core.Color.create(rgb[0], rgb[1], rgb[2], alpha))


def _coords(pts):
    flat = []
    for p in pts:
        flat.extend(p)
    return adsk.fusion.CustomGraphicsCoordinates.create(flat)


def _lines(group, segments, rgb, weight, dashed=False):
    pts = [p for s in segments for p in s]
    cg = group.addLines(_coords(pts), list(range(len(pts))), False)
    cg.color = _colour(rgb)
    cg.weight = weight
    if dashed:
        cg.lineStylePattern = adsk.fusion.LineStylePatterns.dashedLineStylePattern
        cg.isScreenSpaceLineStyle = True
    cg.isSelectable = False
    return cg


def _on_screen(cg, at, prio):
    """Face the viewer, fixed pixel size, local geometry around (0, 0, 0) put at `at`."""
    m = adsk.core.Matrix3D.create()
    m.translation = adsk.core.Vector3D.create(*at)
    cg.transform = m
    bb = adsk.fusion.CustomGraphicsBillBoard.create(None)
    bb.billBoardStyle = adsk.fusion.CustomGraphicsBillBoardStyles.ScreenBillBoardStyle
    cg.billBoarding = bb
    cg.viewScale = adsk.fusion.CustomGraphicsViewScale.create(1, adsk.core.Point3D.create(0, 0, 0))
    cg.depthPriority = prio
    cg.isSelectable = False


def _quad(group, x0, y0, x1, y1):
    box = [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)]
    return group.addMesh(_coords(box), [0, 1, 2, 0, 2, 3, 0, 2, 1, 0, 3, 2], [], []), box


def _dot(group, at, rgb):
    n = 16
    ring = [(DOT_PX * math.cos(2 * math.pi * k / n), DOT_PX * math.sin(2 * math.pi * k / n), 0)
            for k in range(n)]
    idx = []
    for k in range(n):
        idx += [n, k, (k + 1) % n, n, (k + 1) % n, k]
    disk = group.addMesh(_coords(ring + [(0, 0, 0)]), idx, [], [])
    disk.color = _colour(rgb)
    _on_screen(disk, at, 4)


def _label(group, at, text):
    """Cream box with the text, its left middle at `at`. Under billboarding the text
    starts at its origin and is centred on it vertically (seen 2026-10-30). Offsets must
    stay in local pixel units, so the box is built around the text instead of moving
    the text."""
    txt = group.addText(text, 'Arial', LABEL_PX, adsk.core.Matrix3D.create())
    w, h, pad = txt.width, txt.height, LABEL_PAD_PX
    bg, box = _quad(group, -pad, -h / 2.0 - pad, w + pad, h / 2.0 + pad)
    edge = group.addLines(_coords(box + [box[0]]), [0, 1, 1, 2, 2, 3, 3, 4], False)
    for cg, rgb, prio in ((bg, LABEL_BG, 5), (edge, LABEL_EDGE, 6), (txt, LABEL_TEXT, 7)):
        cg.color = _colour(rgb)
        _on_screen(cg, at, prio)
    edge.weight = 1


def _groups(design, group_id=GROUP_ID):
    groups = design.rootComponent.customGraphicsGroups
    return [groups.item(i) for i in range(groups.count)
            if groups.item(i).id.split('|')[0] == group_id]


def clear(design, group_id=GROUP_ID):
    for g in _groups(design, group_id):
        g.deleteMe()


def clear_everything(design):
    """Every MeasureTimeline drawing in the design: the selection's, the placing copy and
    all of Show Measurements'. Run before Save: drawings present at Save are stored in
    that version and shown again on every open, out of the API's reach (2026-11-02)."""
    groups = design.rootComponent.customGraphicsGroups
    for g in [groups.item(i) for i in range(groups.count)]:
        if g.id.startswith('MeasureTimeline'):
            g.deleteMe()


def is_shown(design, group_id=GROUP_ID):
    return bool(_groups(design, group_id))


def _specs(todo):
    """The graphics to make, in drawing order (panels, then lines, then dots and labels):
    ('panel' | 'lines', [points], ...) are placed by their coordinates,
    ('dot' | 'label', at, ...) by their transform."""
    specs = []
    for it in todo:
        if it[0] == 'panel':
            c = it[1]
            specs.append(('panel', c))
            specs.append(('lines', [c[0], c[1], c[1], c[2], c[2], c[3], c[3], c[0]], BLUE, 2, False, 1))
    for it in todo:
        if it[0] == 'seg':
            specs.append(('lines', [it[1], it[2]], it[3], it[4], it[5], 2))
        elif it[0] == 'poly':
            pts = it[1]
            specs.append(('lines', [q for i in range(len(pts) - 1) for q in (pts[i], pts[i + 1])],
                          it[2], it[3], False, 2))
    for it in todo:
        if it[0] in ('dot', 'label'):
            specs.append((it[0], it[1], it[2]))
    return specs


def _signature(specs, group_id):
    """What the group holds, apart from where: equal signatures can be updated in place."""
    parts = [group_id]
    for s in specs:
        parts.append('label:' + s[2] if s[0] == 'label' else
                     '%s%d' % (s[0], len(s[1])) if s[0] in ('panel', 'lines') else s[0])
    return '|'.join(parts)


def _at(p):
    m = adsk.core.Matrix3D.create()
    m.translation = adsk.core.Vector3D.create(*p)
    return m


def _build(group, specs):
    for s in specs:
        if s[0] == 'panel':
            mesh = group.addMesh(_coords(s[1]), [0, 1, 2, 0, 2, 3, 0, 2, 1, 0, 3, 2], [], [])
            mesh.color = _colour(PANEL)
            mesh.setOpacity(PANEL_OPACITY, True)
            mesh.isSelectable = False
        elif s[0] == 'lines':
            pts = s[1]
            cg = _lines(group, [(pts[i], pts[i + 1]) for i in range(0, len(pts), 2)], s[2], s[3], s[4])
            cg.depthPriority = s[5]
        elif s[0] == 'dot':
            _dot(group, s[1], s[2])
        else:
            _label(group, s[1], s[2])            # 3 entities: text, box, border


def _update(group, specs):
    """Move what is already drawn to the new place: coordinates or transforms only."""
    i = 0
    for s in specs:
        if s[0] in ('panel', 'lines'):
            e = group.item(i)
            cg = (adsk.fusion.CustomGraphicsMesh.cast(e) if s[0] == 'panel'
                  else adsk.fusion.CustomGraphicsLines.cast(e))
            cg.coordinates = _coords(s[1])
            i += 1
        else:
            n = 1 if s[0] == 'dot' else 3
            for k in range(n):
                group.item(i + k).transform = _at(s[1])
            i += n


def render(app, todo, group_id=GROUP_ID):
    """Draw the items where they are. If the same drawing is already there (redrawn
    after a command, or the same measurement selected again), it is moved in place, no
    delete / rebuild, no forced refresh. `group_id` keeps the placing command's drawing
    apart from the selection's and Show Measurements'."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    specs = _specs(todo)
    sig = _signature(specs, group_id)
    full = sig + '|@' + _fingerprint(specs)
    current = _groups(des, group_id)
    if len(current) == 1 and current[0].id == full:
        return current[0]                    # exactly as drawn: touch nothing
    if len(current) == 1 and current[0].id.split('|@')[0] == sig:
        _update(current[0], specs)
        current[0].id = full
        return current[0]
    clear(des, group_id)
    group = des.rootComponent.customGraphicsGroups.add()
    group.id = full
    _build(group, specs)
    app.activeViewport.refresh()
    return group


def _fingerprint(specs):
    """Where everything is, to 0.001 mm. Any change to custom graphics marks the document
    as modified, and draw_all runs after every command - Save included - so redrawing an
    unchanged drawing made the document 'unsaved' again right after saving
    (2026-11-02). Equal fingerprint: nothing is touched."""
    import hashlib
    h = hashlib.md5()
    for s in specs:
        pts = s[1] if s[0] in ('panel', 'lines') else [s[1]]
        for p in pts:
            h.update(('%.4f,%.4f,%.4f;' % tuple(p)).encode())
    return h.hexdigest()[:12]


def draw(app, kind, a, b, text, offset=None, group_id=GROUP_ID, along=None, obtuse=False):
    """Draw a measurement where it is (inside a bone, if that is where it is: the user
    doesn't need it in front, 2026-11-02)."""
    return render(app, items(app, kind, a, b, text, offset, along, obtuse), group_id)


# "Show Measurements" on: every stored measurement drawn, one group each, ids
# ALL_ID + '0', ALL_ID + '1', ... in timeline order.

def all_id(i):
    return '%s%d' % (ALL_ID, i)


def _all_groups(design):
    groups = design.rootComponent.customGraphicsGroups
    return [groups.item(i) for i in range(groups.count)
            if groups.item(i).id.split('|')[0].startswith(ALL_ID)]


def clear_all(design, keep=0):
    """Remove the show-all groups from number `keep` on (all of them by default)."""
    for g in _all_groups(design):
        if int(g.id.split('|')[0][len(ALL_ID):]) >= keep:
            g.deleteMe()
