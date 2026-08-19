# -*- coding: utf-8 -*-
"""
Blade calibration support for the "Calibrate Cut" wizard.

Pure logic separated from the dialog so it can be unit tested headless:
the diagnostic test pattern, the offset adjustment rules, the result
icons (drawn in code, no bundled artwork) and the plumbing that stores
the chosen offset in the device's blade-offset filter so every future
job uses it.

Copyright (c) 2026, distributed under the terms of the GPL v3 License.
"""
from inkcut.core.utils import from_unit, to_unit, log


#: Preset label -> blade offset in mm. Typical values for common drag
#: knife geometries; None means the user enters the offset manually.
BLADE_PRESETS = [
    ("30° blade (0.25 mm)", 0.25),
    ("45° blade (0.25 mm)", 0.25),
    ("45° blade (0.50 mm)", 0.50),
    ("60° blade (0.50 mm)", 0.50),
    ("Custom", None),
]

#: How much one calibration round moves the offset (mm)
ADJUST_STEP_MM = 0.05

#: Test pattern geometry (mm)
TEST_SQUARE_MM = 50.0
TEST_CIRCLE_MM = 60.0

#: Test-cut result classifications
RESULT_LOW = 'low'      # corners rounded/blunt -> offset too small
RESULT_OK = 'ok'        # corners crisp -> offset correct
RESULT_HIGH = 'high'    # corners overshoot/flag -> offset too large

#: The filter registered by inkcut.device.filters
BLADE_FILTER_ID = 'blade-offset'


def make_test_svg():
    """ SVG source for the diagnostic cut: a 50x50 mm square centered
    inside a 60 mm circle. Corners of the square show a too-small
    offset (rounded) or a too-large offset (overshoot) clearly; the
    circle shows curve quality. Returned as an ``<?xml`` string, which
    Job accepts directly as a document.
    """
    ci = TEST_CIRCLE_MM
    sq = TEST_SQUARE_MM
    inset = (ci - sq) / 2.0
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        'width="{ci}mm" height="{ci}mm" viewBox="0 0 {ci} {ci}">\n'
        '  <rect x="{inset}" y="{inset}" width="{sq}" height="{sq}" '
        'fill="none" stroke="#000000" stroke-width="0.1"/>\n'
        '  <circle cx="{c}" cy="{c}" r="{r}" '
        'fill="none" stroke="#000000" stroke-width="0.1"/>\n'
        '</svg>\n'
    ).format(ci=ci, sq=sq, inset=inset, c=ci / 2.0, r=ci / 2.0)


def make_test_job():
    """ Build a Job for the test pattern, pre-approved (the user just
    clicked the button, no approval dialog needed).
    """
    from inkcut.job.models import Job, Material
    job = Job(document=make_test_svg(), material=Material())
    job.info.auto_approve = True
    return job


def adjust_offset(offset_mm, result):
    """ Apply one calibration round to the offset (mm) based on which
    test-cut result the user picked. Rounded corners mean the machine
    under-rotates the blade tip: increase the offset. Overshot corners
    ("flags") mean it over-compensates: decrease. Never below zero.
    """
    if result == RESULT_LOW:
        offset_mm = offset_mm + ADJUST_STEP_MM
    elif result == RESULT_HIGH:
        offset_mm = offset_mm - ADJUST_STEP_MM
    return round(max(0.0, offset_mm), 2)


def find_blade_declaration(declarations):
    """ Locate the blade-offset filter declaration from the plugin's
    registered filter extensions.
    """
    for d in declarations:
        if d.id == BLADE_FILTER_ID:
            return d
    return None


def ensure_blade_filter(device, declarations):
    """ Return the device's blade-offset filter instance, creating and
    attaching one (in declaration order, like the setup dialog does)
    when the device doesn't have it enabled yet.
    """
    for f in device.filters:
        if f.declaration and f.declaration.id == BLADE_FILTER_ID:
            return f
    decl = find_blade_declaration(declarations)
    if decl is None:
        raise ValueError("The blade-offset filter is not registered")
    f = decl.factory()
    f.declaration = decl
    order = {d.id: i for i, d in enumerate(declarations)}
    filters = device.filters[:] + [f]
    filters.sort(key=lambda x: order.get(
        x.declaration.id if x.declaration else '', -1))
    device.filters = filters
    return f


def get_offset_mm(device):
    """ Current blade offset in mm from the device's filter, 0.0 when
    the filter isn't enabled.
    """
    for f in device.filters:
        if f.declaration and f.declaration.id == BLADE_FILTER_ID:
            return round(to_unit(f.config.offset, 'mm'), 2)
    return 0.0


def save_offset_mm(device, declarations, offset_mm):
    """ Store the offset (mm) in the device's blade-offset filter. The
    filter list is reassigned so the config-tagged member notifies and
    the device state persists.
    """
    f = ensure_blade_filter(device, declarations)
    f.config.offset = from_unit(offset_mm, 'mm')
    f.config.offset_units = 'mm'
    device.filters = device.filters[:]
    log.info("calibration | blade offset saved: %s mm" % offset_mm)
    return f


def submit_test_cut(plugin, workbench, offset_mm):
    """ Save the offset to the device filter and send the diagnostic
    pattern through the normal submit path. Errors surface through the
    workbench dialogs; returns True when the job was handed off.
    """
    from twisted.internet import defer
    device = plugin.device
    if not device:
        workbench.message_warning(
            "Calibrate Cut",
            "No device is set up. Use Device > Setup first.")
        return False
    try:
        save_offset_mm(device, plugin.filters, offset_mm)
    except Exception as e:
        workbench.message_critical("Calibrate Cut", str(e))
        return False

    def on_error(err):
        log.error(err)
        workbench.message_critical("Calibrate Cut", str(err.value))

    d = defer.maybeDeferred(plugin.submit, make_test_job())
    d.addErrback(on_error)
    return True


def result_icon_png(kind, size=96):
    """ Draw one of the three test-cut result icons and return PNG
    bytes. Original artwork drawn in code: a card with the circle and
    inner square, whose corners show the failure mode.

    kind: RESULT_LOW (rounded corners), RESULT_OK (crisp corners) or
    RESULT_HIGH (corners overshooting into small flags).
    """
    from enaml.qt.QtCore import QBuffer, QByteArray, QRectF, Qt
    from enaml.qt.QtGui import (
        QColor, QImage, QPainter, QPainterPath, QPen
    )
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor('#ffffff'))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor('#222222'))
    pen.setWidthF(size * 0.03)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    # Outer circle
    m = size * 0.10
    p.drawEllipse(QRectF(m, m, size - 2 * m, size - 2 * m))

    # Inner square, corner treatment depends on the result kind
    s0 = size * 0.30
    s1 = size * 0.70
    side = s1 - s0
    path = QPainterPath()
    if kind == RESULT_LOW:
        r = side * 0.28
        path.addRoundedRect(QRectF(s0, s0, side, side), r, r)
    elif kind == RESULT_OK:
        path.addRect(QRectF(s0, s0, side, side))
    elif kind == RESULT_HIGH:
        path.addRect(QRectF(s0, s0, side, side))
        # Overshoot "flags": short tails continuing past each corner,
        # the classic sign of too much blade-offset compensation
        t = side * 0.22
        path.moveTo(s0, s0)
        path.lineTo(s0 - t * 0.5, s0 - t)
        path.moveTo(s1, s0)
        path.lineTo(s1 + t, s0 - t * 0.5)
        path.moveTo(s1, s1)
        path.lineTo(s1 + t * 0.5, s1 + t)
        path.moveTo(s0, s1)
        path.lineTo(s0 - t, s1 + t * 0.5)
    else:
        raise ValueError("Unknown result kind: %r" % (kind,))
    p.drawPath(path)
    p.end()

    #: The QByteArray must outlive the QBuffer (PyQt ownership pitfall:
    #: a temporary is collected while the buffer still points at it)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, 'PNG')
    buf.close()
    return bytes(ba)
