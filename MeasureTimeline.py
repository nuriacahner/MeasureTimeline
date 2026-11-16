"""MeasureTimeline - Fusion 360 add-in. THIS FILE IS THE SHELL, NOT THE WORK.

Lifecycle, the toolbar button, event wiring and the custom feature definition. All the
real code lives in mt_core.py and is RE-IMPORTED FRESH on every command, so editing it
takes effect on the next click. Only changes to this file need a Stop/Run.

Each stored measurement is a custom feature in the timeline: it depends on the two
measured entities and Fusion calls compute whenever the model recomputes, including on
opening a document. So the add-in must be running for stored measurements to update,
and FEATURE_ID is written into documents: never change it.

The shell also listens to selection changes, finished commands and document switches,
to draw stored measurements in the viewport (mt_core.show_selected / draw_all, mt_draw).
One button, Measurements, opens the Measurements panel (a palette:
resources/palette/measurements.html) listing every stored measurement. Commands behind
it: Store Measurement ("+ New" in the panel) and Place Measurement (the custom feature's
edit command: double-click, or Place in the panel).
"""

import adsk.core
import adsk.fusion

import importlib
import os
import pathlib
import sys
import time
import traceback

CMD_ID = 'MeasureTimeline_Store'
CMD_NAME = 'Store Measurement'
CMD_TOOLTIP = (
    'Measure a distance or an angle between two entities and store it in the timeline.\n\n'
    'Uses the same measuring engine as Inspect > Measure. The stored measurement shows its '
    'value in its name and updates when the model changes.'
)
FEATURE_ID = 'MeasureTimeline_Measurement'      # stored in documents: never change
FEATURE_NAME = 'Measurement'
PLACE_CMD_ID = 'MeasureTimeline_Place'          # the feature's edit command (= mt_core.EDIT_CMD_ID)
PLACE_CMD_NAME = 'Place Measurement'
SHOW_ALL_ID = 'MeasureTimeline_ShowAll'
SHOW_ALL_NAME = 'Show Measurements'
CHOOSE_ID = 'MeasureTimeline_Choose'           # (Show / Choose: replaced by the panel in v0.8;
                                                #  ids kept so a re-run removes old buttons)
PANEL_CMD_ID = 'MeasureTimeline_Panel'
PANEL_CMD_NAME = 'Measurements'
PALETTE_ID = 'MeasureTimeline_Palette'          # = mt_core.PALETTE_ID

ADDIN_DIR = os.path.dirname(os.path.realpath(__file__))
RES_DIR = os.path.join(ADDIN_DIR, 'resources', 'MeasureTimeline')
PALETTE_HTML = os.path.join(ADDIN_DIR, 'resources', 'palette', 'measurements.html')
PALETTE_NAME = 'Measurements'
LOG_PATH = os.path.join(ADDIN_DIR, 'MeasureTimeline.log')
OWN_MODULES = ('mt_core', 'mt_draw')

_app = None
_ui = None
_handlers = []
_controls = []
_definition = None
_compute = None
_listeners = []          # (event, handler) for selection / commands / documents, removed on stop
_palette_handler = None  # the panel's incomingFromHTML handler (attached once per palette)


def _log(msg):
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as fh:
            fh.write('%s  %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except Exception:
        pass


def _core():
    """Fresh import of the worker module. This is the hot reload.

    Fusion runs every add-in and script in ONE Python: drop a same-named module loaded
    from elsewhere and keep this folder first on sys.path, so the add-in always runs
    its own installed files."""
    while ADDIN_DIR in sys.path:
        sys.path.remove(ADDIN_DIR)
    sys.path.insert(0, ADDIN_DIR)
    here = os.path.normcase(ADDIN_DIR)
    for name in OWN_MODULES:
        mod = sys.modules.get(name)
        if mod and os.path.normcase(os.path.dirname(os.path.realpath(mod.__file__))) != here:
            del sys.modules[name]
    if 'mt_core' in sys.modules:
        return importlib.reload(sys.modules['mt_core'])
    return importlib.import_module('mt_core')


def _loaded_core():
    """The core without reloading it - compute runs on every recompute."""
    mod = sys.modules.get('mt_core')
    here = os.path.normcase(ADDIN_DIR)
    if mod and os.path.normcase(os.path.dirname(os.path.realpath(mod.__file__))) == here:
        return mod
    return _core()


def _fail(where):
    tb = traceback.format_exc()
    _log('%s\n%s' % (where, tb))
    if _ui:
        _ui.messageBox('%s\n\n%s\n\nFull traceback in\n%s'
                       % (where, tb.strip().splitlines()[-1], LOG_PATH), CMD_NAME)


# --------------------------------------------------------------------------- #
# command handlers - thin, they only delegate
# --------------------------------------------------------------------------- #

class _Created(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            cmd = args.command
            _core().on_created(_app, cmd)
            for h, ev in ((_Execute(), cmd.execute),
                          (_Changed(), cmd.inputChanged),
                          (_Validate(), cmd.validateInputs)):
                ev.add(h)
                _handlers.append(h)
        except Exception:
            _fail('Store Measurement could not open its dialog.')


class _Execute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            sys.modules['mt_core'].on_execute(_app, args.command, _definition)
        except Exception:
            _fail('Store Measurement failed.')


class _Changed(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            sys.modules['mt_core'].on_input_changed(
                _app, args.input.parentCommand.commandInputs, args.input.id)
        except Exception:
            _log(traceback.format_exc())


class _Validate(adsk.core.ValidateInputsEventHandler):
    def notify(self, args):
        try:
            args.areInputsValid = sys.modules['mt_core'].on_validate(_app, args.inputs)
        except Exception:
            args.areInputsValid = False


class _Compute(adsk.fusion.CustomFeatureEventHandler):
    """Fusion recomputes a stored measurement: measure again, update its name."""

    def notify(self, args):
        try:
            _loaded_core().on_compute(_app, args.customFeature)
        except Exception:
            _log(traceback.format_exc())


class _PlaceCreated(adsk.core.CommandCreatedEventHandler):
    """Double-click on a stored measurement: slide its drawing to where it doesn't hide
    the bones."""

    def notify(self, args):
        try:
            cmd = args.command
            _core().on_place_created(_app, cmd)
            for h, ev in ((_PlaceExecute(), cmd.execute),
                          (_PlacePreview(), cmd.executePreview),
                          (_PlaceChanged(), cmd.inputChanged),
                          (_PlaceMouse('move', cmd), cmd.mouseMove),
                          (_PlaceMouse('click', cmd), cmd.mouseClick),
                          (_PlaceDestroy(), cmd.destroy)):
                ev.add(h)
                _handlers.append(h)
        except Exception:
            _fail('Place Measurement could not start.')


class _PlaceExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            sys.modules['mt_core'].on_place_execute(_app, args.command)
        except Exception:
            _fail('Place Measurement failed.')


class _PlacePreview(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            sys.modules['mt_core'].on_place_preview(_app, args.command)
        except Exception:
            _log(traceback.format_exc())


class _PlaceChanged(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            sys.modules['mt_core'].on_place_input_changed(
                _app, args.input.parentCommand.commandInputs, args.input.id)
        except Exception:
            _log(traceback.format_exc())


class _PlaceMouse(adsk.core.MouseEventHandler):
    def __init__(self, what, cmd):
        super().__init__()
        self.what = what
        self.cmd = cmd

    def notify(self, args):
        try:
            core = sys.modules['mt_core']
            fn = core.on_place_mouse_move if self.what == 'move' else core.on_place_click
            fn(_app, self.cmd, args.viewportPosition)
        except Exception:
            _log(traceback.format_exc())


class _PlaceDestroy(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            sys.modules['mt_core'].on_place_destroy(_app, args.command)
        except Exception:
            _log(traceback.format_exc())


class _SelectionChanged(adsk.core.ActiveSelectionEventHandler):
    """A measurement selected in the timeline is drawn; anything else clears it."""

    def notify(self, args):
        try:
            _loaded_core().on_selection_changed(_app)
        except Exception:
            _log(traceback.format_exc())


class _PanelCreated(adsk.core.CommandCreatedEventHandler):
    """The Measurements button: open / close the panel."""

    def notify(self, args):
        try:
            h = _PanelExecute()
            args.command.execute.add(h)
            _handlers.append(h)
        except Exception:
            _log(traceback.format_exc())


class _PanelExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            _core().toggle_palette(_app, _palette())
        except Exception:
            _fail('The Measurements panel could not open.')


class _PaletteIncoming(adsk.core.HTMLEventHandler):
    """A click in the panel (tick box, name, Place, Delete, + New, master switch)."""

    def notify(self, args):
        try:
            args.returnData = _loaded_core().on_palette_message(_app, args.action, args.data)
        except Exception:
            _log(traceback.format_exc())
            args.returnData = ''


def _palette():
    """The Measurements panel, created (docked right) the first time, with its message
    handler attached once."""
    global _palette_handler
    pal = _ui.palettes.itemById(PALETTE_ID)
    if pal is None:
        # a real file URL: the plain Windows path (spaces in "Autodesk Fusion 360",
        # backslashes) never loaded - verified 2026-11-02
        url = pathlib.Path(PALETTE_HTML).as_uri()
        pal = _ui.palettes.add(PALETTE_ID, PALETTE_NAME, url, False, True, True, 320, 420, True)
        pal.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
        _palette_handler = None
    if _palette_handler is None:
        _palette_handler = _PaletteIncoming()
        pal.incomingFromHTML.add(_palette_handler)
    return pal


class _CommandTerminated(adsk.core.ApplicationCommandEventHandler):
    """Show Measurements: a finished command may have changed any measurement."""

    def notify(self, args):
        try:
            _loaded_core().on_command_terminated(_app)
        except Exception:
            _log(traceback.format_exc())


class _DocumentActivated(adsk.core.DocumentEventHandler):
    def notify(self, args):
        try:
            _loaded_core().on_document_activated(_app)
        except Exception:
            _log(traceback.format_exc())


class _DocumentSaving(adsk.core.DocumentEventHandler):
    """Drawings present at Save are stored in the file: remove them first."""

    def notify(self, args):
        try:
            _loaded_core().on_document_saving(_app, args.document)
        except Exception:
            _log(traceback.format_exc())


class _DocumentSaved(adsk.core.DocumentEventHandler):
    def notify(self, args):
        try:
            _loaded_core().on_document_saved(_app, args.document)
        except Exception:
            _log(traceback.format_exc())


# --------------------------------------------------------------------------- #
# add-in entry points
# --------------------------------------------------------------------------- #

def run(context):
    global _app, _ui, _definition, _compute
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface
        # same id again (Stop/Run) returns the existing definition
        _definition = adsk.fusion.CustomFeatureDefinition.create(
            FEATURE_ID, FEATURE_NAME, RES_DIR)
        _compute = _Compute()
        _definition.customFeatureCompute.add(_compute)
        for ev, h in ((_ui.activeSelectionChanged, _SelectionChanged()),
                      (_ui.commandTerminated, _CommandTerminated()),
                      (_app.documentActivated, _DocumentActivated()),
                      (_app.documentSaving, _DocumentSaving()),
                      (_app.documentSaved, _DocumentSaved())):
            ev.add(h)
            _listeners.append((ev, h))

        old = _ui.commandDefinitions.itemById(CMD_ID)
        if old:
            old.deleteMe()
        cmd_def = _ui.commandDefinitions.addButtonDefinition(
            CMD_ID, CMD_NAME, CMD_TOOLTIP, RES_DIR)
        h = _Created()
        cmd_def.commandCreated.add(h)
        _handlers.append(h)
        # Store Measurement has no button of its own: the panel's "+ New" runs it

        # Measurements: the one button, opening the panel (list, show / hide, place,
        # delete, + New, the design's master switch)
        for cid in (SHOW_ALL_ID, CHOOSE_ID):           # replaced by the panel (v0.8)
            old = _ui.commandDefinitions.itemById(cid)
            if old:
                old.deleteMe()
        old = _ui.commandDefinitions.itemById(PANEL_CMD_ID)
        if old:
            old.deleteMe()
        panel_def = _ui.commandDefinitions.addButtonDefinition(
            PANEL_CMD_ID, PANEL_CMD_NAME,
            'Open / close the Measurements panel: every stored measurement with its value, '
            'show / hide in the model, place, delete, and + New to store one.', RES_DIR)
        h = _PanelCreated()
        panel_def.commandCreated.add(h)
        _handlers.append(h)
        _controls[:] = _core().place_button(_ui, panel_def)

        # double-click / Edit Feature on a stored measurement runs Place Measurement
        old = _ui.commandDefinitions.itemById(PLACE_CMD_ID)
        if old:
            old.deleteMe()
        place_def = _ui.commandDefinitions.addButtonDefinition(
            PLACE_CMD_ID, PLACE_CMD_NAME, 'Slide the measurement to where it does not hide the model.',
            RES_DIR)
        h = _PlaceCreated()
        place_def.commandCreated.add(h)
        _handlers.append(h)
        _definition.editCommandId = PLACE_CMD_ID
        _core().on_document_activated(_app)                        # the open document
        _log('started, button in %d panel(s)' % len(_controls))
    except Exception:
        _fail('MeasureTimeline failed to start.')


def stop(context):
    global _compute, _palette_handler
    try:
        pal = _ui.palettes.itemById(PALETTE_ID) if _ui else None
        if pal:
            pal.deleteMe()
        _palette_handler = None
        if _definition and _compute:
            _definition.customFeatureCompute.remove(_compute)
            _compute = None
        for ev, h in _listeners:
            try:
                ev.remove(h)
            except Exception:
                pass
        _listeners[:] = []
        try:                     # don't leave a drawing behind
            des = adsk.fusion.Design.cast(_app.activeProduct)
            sys.modules['mt_draw'].clear(des)
            sys.modules['mt_draw'].clear_all(des)
        except Exception:
            pass
        for ctrl in _controls:
            try:
                ctrl.deleteMe()
            except Exception:
                pass
        _controls[:] = []
        for cid in (CMD_ID, PLACE_CMD_ID, PANEL_CMD_ID, SHOW_ALL_ID, CHOOSE_ID):
            cmd_def = _ui.commandDefinitions.itemById(cid)
            if cmd_def:
                cmd_def.deleteMe()
        _handlers[:] = []
        _log('stopped')
    except Exception:
        _fail('MeasureTimeline failed to stop.')
