# -*- coding: utf-8 -*-
"""
Copyright (c) 2015-2020, the Inkcut team.

Distributed under the terms of the GPL v3 License.

The full license is in the file LICENSE, distributed with this software.

Created on Jan 16, 2015

@author: jrm
"""
from __future__ import division
import os
import sys
import uuid
import re
from copy import deepcopy
from lxml import etree
from datetime import datetime, timedelta
from atom.api import (
    Enum, Float, Int, Bool, Instance, ContainerList, Range, Str,
    Dict, List, Callable, observe
)
from contextlib import contextmanager
from enaml.qt.QtGui import QPainterPath, QTransform, QPolygonF
from enaml.qt.QtCore import QPointF, QRectF
from enaml.colors import ColorMember
from inkcut.core.api import Model, AreaBase
from inkcut.core.svg import QtSvgDoc
from inkcut.core.utils import split_painter_path, log


from . import filters
from . import ordering
from .layout import pack_shelf


class Material(AreaBase):
    """ Model representing the plot media
    """
    name = Str().tag(config=True)
    color = Str('#000000').tag(config=True)

    is_roll = Bool(False).tag(config=True)

    used = ContainerList(Float(), default=[0, 0]).tag(
        config=True, help="amount used already (to determine available size)")
    cost = Float(1).tag(config=True, help="cost per square unit")

    use_force = Bool(False).tag(config=True)
    use_speed = Bool(False).tag(config=True)
    force = Int(10).tag(config=True)
    speed = Int(10).tag(config=True)

    def reset(self):
        self.used = (0.0, 0.0)

    def unit_cost(self):
        return


class Padding:
    LEFT = 0
    TOP = 1
    RIGHT = 2
    BOTTOM = 3


class JobError(Exception):
    pass


class JobInfo(Model):
    """ Job metadata """
    #: Controls
    done = Bool()
    cancelled = Bool()
    paused = Bool()

    #: Flags
    status = Enum('staged', 'waiting', 'running', 'error',
                  'approved', 'cancelled', 'complete').tag(config=True)

    #: Stats
    created = Instance(datetime).tag(config=True)
    started = Instance(datetime).tag(config=True)
    ended = Instance(datetime).tag(config=True)
    progress = Range(0, 100, 0).tag(config=True)
    data = Str().tag(config=True)
    count = Int().tag(config=True)

    #: Device speed in px/s
    speed = Float(strict=False).tag(config=True)
    #: Length in px
    length = Float(strict=False).tag(config=True)

    #: Estimates based on length and speed
    duration = Instance(timedelta, ()).tag(config=True)

    #: Callback to open the approval dialog
    auto_approve = Bool().tag(config=True)
    request_approval = Callable()

    def __init__(self, *args, **kwargs):
        super(JobInfo, self).__init__(*args, **kwargs)
        self.created = self._default_created()

    def _default_created(self):
        return datetime.now()

    def _default_request_approval(self):
        """ Request approval using the current job """
        #: Resolve the plugin lazily — this default is also evaluated by
        #: __getstate__ (clone/state save), which must work without a
        #: workbench (deserialization, standalone tests)
        def request_approval():
            from inkcut.core.workbench import InkcutWorkbench
            workbench = InkcutWorkbench.instance()
            plugin = workbench.get_plugin("inkcut.job")
            plugin.request_approval(plugin.job)
        return request_approval

    def reset(self):
        """ Reset to initial states"""
        #: TODO: This is a stupid design
        self.progress = 0
        self.paused = False
        self.cancelled = False
        self.done = False
        self.status = 'staged'

    def _observe_done(self, change):
        if change['type'] == 'update':
            #: Increment count every time it's completed
            if self.done:
                self.count += 1

    @observe('length', 'speed')
    def _update_duration(self, change):
        if not self.length or not self.speed:
            self.duration = timedelta()
            return
        dt = self.length/self.speed
        self.duration = timedelta(seconds=dt)


class JobItem(Model):
    """One cached source; position is its rotated visual bottom-left, x/right y/up."""
    uid = Str().tag(config=True)
    document = Str().tag(config=True)
    source_document = Str().tag(config=True)
    position = ContainerList(Float(), default=[0, 0]).tag(config=True)
    weedline = Bool(False).tag(config=True)
    rotation = Int(0).tag(config=True)
    missing = Bool(False)
    _invalid = Bool(False)
    _loaded = Bool(False)
    _doc = Instance(QtSvgDoc)

    def _default_uid(self):
        return uuid.uuid4().hex[:8]

    def __str__(self):
        return os.path.basename(self.source_document or self.document)

    def _observe_document(self, change):
        self._loaded = False
        self._doc = None

    def _observe_rotation(self, change):
        rotation = self.rotation % 360
        if rotation not in (0, 90, 180, 270):
            log.warning("Unsupported item rotation %s; using 0", self.rotation)
            rotation = 0
        if self.rotation != rotation:
            self.rotation = rotation

    def load(self, **kwargs):
        """Parse once, or after document changes / explicit reload."""
        if not self._loaded:
            self.missing = not os.path.isfile(self.document)
            self._invalid = False
            self._doc = None
            if not self.missing:
                try:
                    doc = QtSvgDoc(self.document, **kwargs)
                    if doc.isEmpty():
                        raise ValueError('SVG has no geometry')
                    self._doc = doc
                except Exception as exc:
                    self._invalid = True
                    log.warning("Invalid SVG %s: %s", self.document, exc)
            self._loaded = True
        return self._doc

    def rotated_geometry(self):
        doc = self._doc
        t = QTransform()
        c = doc.boundingRect().center()
        t.translate(c.x(), c.y())
        t.rotate(self.rotation)
        t.translate(-c.x(), -c.y())
        return t, t.map(doc).boundingRect()


class Job(Model):
    """ Create a plot depending on the properties set. Any property that is a
    traitlet will cause an update when the value is changed.

    """
    #: Material this job will be run on
    material = Instance(Material, ()).tag(config=True)

    #: Path to svg document this job parses
    document = Str().tag(config=True)

    #: Path of the original source document when `document` points to a
    #: converted copy (e.g. a PDF/AI imported via a cached SVG). The UI
    #: shows this instead of the internal cache filename.
    source_document = Str().tag(config=True)

    #: Empty with assignment or del items[:]; Atom 0.12 clear() emits no event.
    items = ContainerList(JobItem).tag(config=True)
    item_weedline_padding = ContainerList(
        Float(), default=[7.087, 7.087, 7.087, 7.087]).tag(config=True)
    item_spacing = Float(7.087).tag(config=True)
    layout_transform = Instance(QTransform)
    layout_problems = List()
    _watched_items = List()
    _items_batch = Int()
    _items_dirty = Bool()
    _composing = Bool()

    #: Nodes to restrict
    document_kwargs = Dict().tag(config=True)

    #: Meta info a the job
    info = Instance(JobInfo, ()).tag(config=True)

    # Job properties used for generating the plot
    size = ContainerList(Float(), default=[1, 1])

    #: Size of the final layout (all copies incl. spacing), before the
    #: trailing feed move. Shown in the UI when copies > 1.
    final_size = ContainerList(Float(), default=[0, 0])
    scale = ContainerList(Float(), default=[1, 1]).tag(config=True)
    auto_scale = Bool(False).tag(
        config=True, help="automatically scale if it's too big for the area")
    lock_scale = Bool(True).tag(
        config=True, help="automatically scale if it's too big for the area")

    mirror = ContainerList(Bool(), default=[False, False]).tag(config=True)
    align_center = ContainerList(Bool(),
                                 default=[False, False]).tag(config=True)
    clip_to_plot_area = Bool().tag(config = True)

    # Shifting of original file
    auto_shift = Bool(True).tag(config=True, help="shift to start at origin")
    copy_bbox = Instance(QRectF)

    rotation = Float(0).tag(config=True)
    auto_rotate = Bool(False).tag(
        config=True, help="automatically rotate if it saves space")

    copies = Int(1).tag(config=True)
    auto_copies = Bool(False).tag(
        config=True, help="always use a full stack")
    copy_spacing = ContainerList(Float(),
                                 default=[10, 10]).tag(config=True)
    copy_weedline = Bool(False).tag(config=True)
    copy_weedline_padding = ContainerList(
        Float(), default=[10, 10, 10, 10]).tag(config=True)

    plot_weedline = Bool(False).tag(config=True)
    plot_weedline_padding = ContainerList(
        Float(), default=[10, 10, 10, 10]).tag(config=True)

    order = Enum(*sorted(ordering.REGISTRY.keys())).tag(config=True)

    def _default_order(self):
        return 'Normal'

    #: Feed past the finished cut by default so consecutive jobs never
    #: cut over each other (the machine keeps its position; Inkcut moves
    #: the origin). 53.15px = 15mm at 90dpi.
    feed_to_end = Bool(True).tag(config=True)
    feed_after = Float(53.15).tag(config=True)

    stack_size = ContainerList(Int(), default=[0, 0])

    #: Filters to cut only certain items
    svg_filters = ContainerList(filters.SvgFilter)
    job_filters = ContainerList(filters.JobFilter)

    #: Original path parsed from the source document
    doc = Instance(QtSvgDoc)
    #: Modified path that may be shifted
    path = Instance(QtSvgDoc)

    #: Path filtered by layers/colors and ordered according to the order
    optimized_path = Instance(QPainterPath)

    #: Finaly copy using all the applied job properties
    #: This is what is actually cut out
    model = Instance(QPainterPath)

    _blocked = Bool(False)  # block change events
    _desired_copies = Int(1)  # required for auto copies

    def __str__(self):
        if self.items:
            return "%d graphics" % len(self.items)
        source = self.source_document or self.document
        if not source:
            return "Empty document"
        if source.startswith("<?xml"):
            return "Pasted document"
        try:
            return os.path.split(source)[-1]
        except Exception:
            return source

    def __getstate__(self):
        """ Exclude any members from the state where document does point to stdin - """
        state = super(Job, self).__getstate__()
        if state["document"] == "-": # Stdin, would crash the Plugin every second time
            state["document"] = ''
            state["source_document"] = ''
        return state

    def __setstate__(self, *args, **kwargs):
        """ Ensure that when restoring from disk the material and info
        are not set to None. Ideally these would be defined as Typed but
        the material may be made extendable at some point.
        """
        try:
            with self.batch_items():
                super(Job, self).__setstate__(*args, **kwargs)
                if not self.info:
                    self.info = JobInfo()
                if not self.material:
                    self.material = Material()
        except Exception as e:
            #: Never let a broken item layout discard the whole saved state
            log.exception(e)

    def _observe_document(self, change):
        """ Read the document from stdin """
        source = self.document
        if self._composing:
            return
        if source and self.items:
            self.items = []
        from inkcut.core.workbench import InkcutWorkbench
        #: State restore may set the document before the workbench and
        #: job plugin exist — fall back to the parser defaults then.
        workbench = InkcutWorkbench.instance()
        plugin = None
        if workbench is not None:
            try:
                plugin = workbench.get_plugin("inkcut.job")
            except Exception:
                plugin = None
        if plugin is not None:
            self.document_kwargs["dpi_default"] = plugin.dpi_default
            self.document_kwargs["dpi_auto_detect_inkscape"] = \
                plugin.dpi_auto_detect_inkscape
        if change['type'] == 'update' and source == '-':
            #: Only load from stdin when explicitly changed to it (when doing
            #: open from the cli) otherwise when restoring state this hangs
            #: startup
            self.doc = self.path = QtSvgDoc(sys.stdin, **self.document_kwargs)
        elif source and (source.startswith("<?xml") or os.path.exists(source)):
            self.doc = self.path = QtSvgDoc(source, **self.document_kwargs)

        # Recreate available filters when the document changes
        self.svg_filters = self._default_svg_filters()
        self.job_filters = self._default_job_filters()

    @property
    def has_content(self):
        return bool(self.items) if self.items else bool(self.document)

    @staticmethod
    def item_dpi_kwargs():
        from inkcut.core.workbench import InkcutWorkbench
        workbench = InkcutWorkbench.instance()
        if workbench is not None:
            try:
                plugin = workbench.get_plugin("inkcut.job")
                return dict(dpi_default=plugin.dpi_default,
                            dpi_auto_detect_inkscape=plugin.dpi_auto_detect_inkscape)
            except Exception:
                pass
        return {}

    @contextmanager
    def batch_items(self):
        """Coalesce item changes, including nested commands, into one composition."""
        self._items_batch += 1
        try:
            yield
        finally:
            self._items_batch -= 1
            if not self._items_batch and self._items_dirty:
                self._items_dirty = False
                self._compose_items()

    def _request_composition(self, change=None):
        if self._composing:
            return
        if self._items_batch:
            self._items_dirty = True
        else:
            self._compose_items()

    def _observe_items(self, change):
        if change['type'] == 'create' and not self.items:
            return
        names = ('position', 'weedline', 'rotation', 'document', 'source_document')
        for item in self._watched_items:
            for name in names:
                item.unobserve(name, self._request_composition)
        existing = [item for item in self.items if item in self._watched_items]
        seen = {item.uid for item in existing}
        for item in self.items:
            if item not in existing:
                while item.uid in seen:
                    item.uid = uuid.uuid4().hex[:8]
                seen.add(item.uid)
            for name in names:
                item.observe(name, self._request_composition)
        self._watched_items = list(self.items)
        if self.items:
            self._composing = True
            try:
                self.document = ''
                self.source_document = ''
            finally:
                self._composing = False
        self._request_composition()

    def _observe_item_weedline_padding(self, change):
        if self.items:
            self._request_composition()

    def reload_items(self):
        for item in self.items:
            item._loaded = False
        self._request_composition()

    def set_item_weedlines(self, enabled):
        with self.batch_items():
            for item in self.items:
                item.weedline = enabled

    def item_bounds(self, item, outer=True):
        """Bounds in x/right, y/up coordinates (QRectF top means the lower y)."""
        if item.load(**self.item_dpi_kwargs()) is None:
            return None
        _, bbox = item.rotated_geometry()
        x, y = item.position
        w, h = bbox.width(), bbox.height()
        if outer and item.weedline:
            left, top, right, bottom = self.item_weedline_padding
            x, y = x - left, y - bottom
            w, h = w + left + right, h + top + bottom
        return QRectF(x, y, w, h)

    def _update_layout_problems(self):
        problems, bounds = [], []
        area = self.material.available_area
        for item in self.items:
            if item.missing or item._invalid:
                problems.append((item.uid, 'missing' if item.missing else 'invalid'))
                continue
            rect = self.item_bounds(item)
            if rect is None:
                continue
            if (rect.left() < 0 or rect.top() < 0 or
                    rect.right() > area.width() or rect.bottom() > area.height()):
                problems.append((item.uid, 'outside'))
            bounds.append((item.uid, rect))
        overlaps = set()
        for i, (uid, a) in enumerate(bounds):
            for other, b in bounds[i + 1:]:
                if (max(a.left(), b.left()) < min(a.right(), b.right()) and
                        max(a.top(), b.top()) < min(a.bottom(), b.bottom())):
                    overlaps.update((uid, other))
        problems.extend((item.uid, 'overlap') for item in self.items if item.uid in overlaps)
        self.layout_problems = problems

    @observe('material', 'material.size', 'material.padding')
    def _layout_area_changed(self, change):
        if self.items and not self._composing:
            # Source caches remain valid; update the composite viewport as well.
            self._request_composition()

    @staticmethod
    def _svg_matrix(t):
        return 'matrix({})'.format(','.join(format(v, '.17g') for v in
            (t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy())))

    @staticmethod
    def _filter_key(f):
        return type(f), f.name, getattr(f, 'data', None)

    def _compose_items(self):
        if not self.items and self.document:
            return
        self._composing = True
        try:
            enabled = {self._filter_key(f): f.enabled
                       for f in list(self.svg_filters) + list(self.job_filters)}
            ns = 'http://www.w3.org/2000/svg'
            ink = '{http://www.inkscape.org/namespaces/inkscape}'
            area = self.material.available_area
            root = etree.Element('{%s}svg' % ns, nsmap={None: ns},
                                 width='%spx' % area.width(), height='%spx' % area.height())
            valid = 0
            for item in self.items:
                doc = item.load(**self.item_dpi_kwargs())
                if doc is None:
                    continue
                valid += 1
                rotation, bbox = item.rotated_geometry()
                source = deepcopy(doc._e)
                source.tag = '{%s}g' % ns
                for name in ('width', 'height', 'viewBox', 'x', 'y',
                             'preserveAspectRatio', 'version'):
                    source.attrib.pop(name, None)
                source.set('transform', self._svg_matrix(doc.parseTransform(doc._e)))
                ids = {e.get('id'): item.uid + '-' + e.get('id')
                       for e in source.iter() if e.get('id') is not None}
                for e in source.iter():
                    if e.get('id') in ids:
                        e.set('id', ids[e.get('id')])
                    for name, value in list(e.attrib.items()):
                        if name in ('href', '{http://www.w3.org/1999/xlink}href'):
                            if value.startswith('#') and value[1:] in ids:
                                value = '#' + ids[value[1:]]
                        value = re.sub(r'url\(\s*([\'"]?)#([^\s)\'"]+)\1\s*\)',
                                       lambda m: 'url(#%s)' % ids.get(m[2], m[2]), value)
                        e.set(name, value)
                    if (e.tag == '{%s}g' % ns and e.get(ink + 'groupmode') == 'layer'
                            and e.get(ink + 'label')):
                        e.set(ink + 'label', '%s [%s] / %s' %
                              (item, item.uid, e.get(ink + 'label', '')))
                placement = QTransform.fromTranslate(item.position[0] - bbox.left(),
                                                      -item.position[1] - bbox.bottom())
                outer = etree.SubElement(root, '{%s}g' % ns,
                                         transform=self._svg_matrix(placement))
                content = etree.SubElement(outer, '{%s}g' % ns,
                                           transform=self._svg_matrix(rotation))
                content.append(source)
                if item.weedline:
                    left, top, right, bottom = self.item_weedline_padding
                    etree.SubElement(outer, '{%s}rect' % ns,
                                     x=str(bbox.left() - left), y=str(bbox.top() - top),
                                     width=str(bbox.width() + left + right),
                                     height=str(bbox.height() + top + bottom))
            if valid:
                self.doc = self.path = QtSvgDoc(root, parent=True, **self.item_dpi_kwargs())
                self.svg_filters = self._default_svg_filters()
                self.job_filters = self._default_job_filters()
                for f in list(self.svg_filters) + list(self.job_filters):
                    f.enabled = enabled.get(self._filter_key(f), f.enabled)
                self.optimized_path = self._default_optimized_path()
            else:
                self.doc = self.path = self.optimized_path = self.model = None
                self.svg_filters = []
                self.job_filters = []
            self._update_layout_problems()
        finally:
            self._composing = False
        self.update_document()

    def arrange_items(self, items=None):
        """Pack outer bounds from the anchor; appended items go above existing ones."""
        selected = list(self.items if items is None else items)
        bounds = [(item, self.item_bounds(item)) for item in selected]
        bounds = [(item, rect) for item, rect in bounds if rect is not None]
        if not bounds:
            return
        area = self.material.available_area
        others = [self.item_bounds(item) for item in self.items if item not in selected]
        others = [rect for rect in others if rect is not None]
        start_y = max(rect.bottom() for rect in others) + self.item_spacing if others else 0
        sizes = [(rect.width(), rect.height()) for _, rect in bounds]
        if area.width() <= 0 or area.height() <= start_y:
            positions, overflow = [None] * len(bounds), list(range(len(bounds)))
        else:
            positions, overflow = pack_shelf(sizes, area.width(),
                                             area.height() - start_y, self.item_spacing)
        overflow_x = 0.0
        with self.batch_items():
            for i, (item, rect) in enumerate(bounds):
                if positions[i] is None:
                    x, y = overflow_x, area.height() + self.item_spacing
                    overflow_x += rect.width() + self.item_spacing
                else:
                    x, y = positions[i]
                    y += start_y
                if item.weedline:
                    x += self.item_weedline_padding[Padding.LEFT]
                    y += self.item_weedline_padding[Padding.BOTTOM]
                item.position = [float(x), float(y)]

    def get_filters_from_registry(self, registry):
        results = []
        if not self.path:
            return results
        for Filter in registry.values():
            try:
                results.extend(Filter.get_filter_options(self, self.path))
            except Exception as e:
                log.error("Failed loading filters for: %s" % Filter)
                log.exception(e)
        return results

    def _default_svg_filters(self):
        return self.get_filters_from_registry(filters.SVG_FILTERS)

    def _default_job_filters(self):
        return self.get_filters_from_registry(filters.JOB_FILTERS)

    def apply_filters(self, filter_list, doc):
        for f in filter_list:
            # If the color/layer is NOT enabled, then remove that color/layer
            if not f.enabled:
                log.debug("Applying filter {}".format(f))
                doc = f.apply_filter(self, doc)
        return doc

    def _default_optimized_path(self):
        """ Filter parts of the documen based on the selected layers and colors

        """
        doc = self.path
        # SVG filters need to happen first,
        # these rely on the SVG structure of
        # the document.
        doc = self.apply_filters(self.svg_filters, doc)

        # Apply ordering to path
        # this delegates to objects in the ordering module
        OrderingHandler = ordering.REGISTRY.get(self.order)
        if OrderingHandler:
            doc = OrderingHandler().order(self, doc)

        return doc

    @observe('path', 'order', 'svg_filters', 'job_filters', 'clip_to_plot_area')
    def _update_optimized_path(self, change):
        """ Whenever the loaded file (and parsed SVG path) changes update
        it based on the filters from the job.

        """
        if not self._composing:
            self.optimized_path = self._default_optimized_path()

    def _create_copy(self):
        """ Creates a copy of the original graphic applying the given
        transforms

        """
        optimized_path = self.optimized_path
        if optimized_path is None:
            optimized_path = self.optimized_path = self._default_optimized_path()
        if optimized_path is None:
            log.debug("Path is %s" % self.path)
            raise ValueError("Path is empty")
        bbox = optimized_path.boundingRect()

        # Create the base copy
        t = QTransform()

        t.scale(
            self.scale[0] * (self.mirror[0] and -1 or 1),
            self.scale[1] * (self.mirror[1] and -1 or 1),
            )

        self.copy_bbox = t.mapRect(bbox)

        # Rotate about center
        if self.rotation != 0:
            c = bbox.center()
            t.translate(-c.x(), -c.y())
            t.rotate(self.rotation)
            t.translate(c.x(), c.y())

        # Apply transform
        path = t.map(optimized_path)

        # Add weedline to copy
        if self.copy_weedline:
            self._add_weedline(path, self.copy_weedline_padding)

        # If it's too big we have to scale it
        w, h = path.boundingRect().width(), path.boundingRect().height()
        available_area = self.material.available_area

        #: This screws stuff up!
        if w > available_area.width() or h > available_area.height():

            # If it's too big an auto scale is enabled, resize it to fit
            if self.auto_scale:
                sx, sy = 1, 1
                if w > available_area.width():
                    sx = available_area.width() / w
                if h > available_area.height():
                    sy = available_area.height() / h
                s = min(sx, sy)  # Fit to the smaller of the two
                path = QTransform.fromScale(s, s).map(optimized_path)

        # Save original bbox
        bbox = path.boundingRect()

        # Move to bottom left
        br = bbox.bottomRight()
        path = QTransform.fromTranslate(-br.x(), -br.y()).map(path)
        return path

    @contextmanager
    def events_suppressed(self):
        """ Block change events to prevent feedback loops

        """
        self._blocked = True
        try:
            yield
        finally:
            self._blocked = False

    @observe('path', 'scale', 'auto_scale', 'lock_scale', 'mirror',
             'align_center', 'rotation', 'auto_rotate', 'copies', 'order',
             'copy_spacing', 'copy_weedline', 'copy_weedline_padding',
             'plot_weedline', 'plot_weedline_padding', 'feed_to_end',
             'feed_after', 'material', 'material.size', 'material.padding',
             'auto_copies', 'auto_shift', 'clip_to_plot_area')
    def update_document(self, change=None):
        """ Recreate an instance of of the plot using the current settings

        """
        if self._blocked or self._composing:
            return

        if change:
            name = change['name']
            if name == 'copies':
                self._desired_copies = self.copies
            elif name in ('layer', 'color'):
                self._update_optimized_path(change)

        model = self.create()
        if model or self.items:
            self.model = model

    def create(self, swap_xy=False, scale=None):
        """ Create a path model that is rotated and scaled

        """
        if self.items:
            return self._create_items(swap_xy, scale)

        model = QPainterPath()

        if not self.path:
            return

        path = self._create_copy()

        # Update size
        bbox = path.boundingRect()
        self.size = [bbox.width(), bbox.height()]

        # Create copies
        c = 0
        points = self._copy_positions_iter(path)

        if self.auto_copies:
            self.stack_size = self._compute_stack_sizes(path)
            if self.stack_size[0]:
                copies_left = self.copies % self.stack_size[0]
                if copies_left:  # not a full stack
                    with self.events_suppressed():
                        self.copies = self._desired_copies
                        self.add_stack()

        while c < self.copies:
            x, y = next(points)
            model.addPath(QTransform.fromTranslate(x, -y).map(path))
            c += 1

        # Create weedline
        if self.plot_weedline:
            self._add_weedline(model, self.plot_weedline_padding)

        # Determine padding
        bbox = model.boundingRect()
        if self.align_center[0]:
            px = (self.material.width() - bbox.width())/2.0
        else:
            px = self.material.padding_left

        if self.align_center[1]:
            py = -(self.material.height() - bbox.height())/2.0
        else:
            py = -self.material.padding_bottom

        # Scale and rotate
        if scale:
            model = QTransform.fromScale(*scale).map(model)
            px, py = px*abs(scale[0]), py*abs(scale[1])

        if swap_xy:
            t = QTransform()
            t.rotate(90)
            model = t.map(model)

        # Move to 0,0
        bbox = model.boundingRect()
        p = bbox.bottomLeft()
        tx, ty = -p.x(), -p.y()

        if not self.auto_shift:
            # Re-add original shift
            bbox = self.copy_bbox
            tx += -bbox.right() if self.mirror[0] else bbox.left()
            ty += bbox.bottom() if self.mirror[1] else -bbox.top()

        # If swapped, make sure padding is still correct
        if swap_xy:
            px, py = -py, -px
        tx += px
        ty += py

        model = QTransform.fromTranslate(tx, ty).map(model)

        # Apply the job filters to the final result
        # after copies and transformations have been applied.
        model = self.apply_filters(self.job_filters, model)

        if not swap_xy and scale is None:
            #: Only on the UI update path (device init passes swap/scale
            #: and would leave device-space numbers here): the whole
            #: layout's size, excluding the trailing feed move below
            fbox = model.boundingRect()
            self.final_size = [fbox.width(), fbox.height()]

        end_point = (QPointF(
            0, -self.feed_after + model.boundingRect().top())
                     if self.feed_to_end else QPointF(0, 0))
        model.moveTo(end_point)

        return model

    def _create_items(self, swap_xy=False, scale=None):
        """Keep absolute placement; copies use the union of outer bounds.

        Ignore job scale/mirror/rotation and auto_shift/align_center/auto_scale.
        Positions use y/up; the composite stores (x, -y). layout_transform maps
        those SVG coordinates to model coordinates, a translation on the UI path.
        Device scale/swap arguments still apply to the complete layout.
        """
        if self.optimized_path is None or self.optimized_path.isEmpty():
            self.model = None
            return None
        path = QPainterPath(self.optimized_path)
        layout = QPainterPath(self.doc)
        if self.copy_weedline:
            self._add_weedline(path, self.copy_weedline_padding)
            self._add_weedline(layout, self.copy_weedline_padding)
        bbox = layout.boundingRect()
        self.copy_bbox = bbox
        self.size = [bbox.width(), bbox.height()]
        points = self._copy_positions_iter(layout)
        if self.auto_copies:
            self.stack_size = self._compute_stack_sizes(layout)
            if self.stack_size[0] and self.copies % self.stack_size[0]:
                with self.events_suppressed():
                    self.copies = self._desired_copies
                    self.add_stack()
        model = QPainterPath()
        for _ in range(self.copies):
            x, y = next(points)
            model.addPath(QTransform.fromTranslate(x, -y).map(path))
        if self.plot_weedline and not model.isEmpty():
            self._add_weedline(model, self.plot_weedline_padding)
        # Re-anchor like the single-file path (bbox -> padding corner after
        # device scale/swap), keeping the envelope's layout offset as extra
        # padding so absolute positions survive mirror/swap.
        c = model.boundingRect()
        px = self.material.padding_left + c.left()
        py = -self.material.padding_bottom + c.bottom()
        t = QTransform()
        if scale:
            t = QTransform.fromScale(*scale)
            px, py = px * abs(scale[0]), py * abs(scale[1])
        if swap_xy:
            rotation = QTransform()
            rotation.rotate(90)
            t = t * rotation
            px, py = -py, -px
        b = t.mapRect(c)
        t = t * QTransform.fromTranslate(px - b.left(), py - b.bottom())
        if not swap_xy and scale is None:
            self.layout_transform = t
        model = self.apply_filters(self.job_filters, t.map(model))
        if not swap_xy and scale is None:
            bbox = model.boundingRect()
            self.final_size = [bbox.width(), bbox.height()]
        end_point = (QPointF(0, -self.feed_after + model.boundingRect().top())
                     if self.feed_to_end else QPointF(0, 0))
        model.moveTo(end_point)
        return model

    def _check_bounds(self, plot, area):
        """ Checks that the width and height of plot are less than the width
        and height of area

        """
        return plot.width() > area.width() or plot.height() > area.height()

    def _copy_positions_iter(self, path, axis=0):
        """ Generator that creates positions of points

        """
        other_axis = axis +1 % 2
        p = [0, 0]

        bbox = path.boundingRect()
        d = (bbox.width(), bbox.height())
        pad = self.copy_spacing
        stack_size = self._compute_stack_sizes(path)

        while True:
            p[axis] = 0
            yield p  # Beginning of each row

            for i in range(stack_size[axis]-1):
                p[axis] += d[axis]+pad[axis]
                yield p

            p[other_axis] += d[other_axis]+pad[other_axis]

    def _compute_stack_sizes(self, path):
        # Usable area
        material = self.material
        a = [material.width(), material.height()]
        a[0] -= material.padding[Padding.LEFT] + material.padding[Padding.RIGHT]
        a[1] -= material.padding[Padding.TOP] + material.padding[Padding.BOTTOM]

        # Clone includes weedline but not spacing
        bbox = path.boundingRect()
        size = [bbox.width(), bbox.height()]

        stack_size = [0, 0]
        p = [0, 0]
        for i in range(2):
            # Compute stack
            while (p[i]+size[i]) < a[i]:  # while another one fits
                stack_size[i] += 1
                p[i] += size[i] + self.copy_spacing[i]  # Add only to end

        self.stack_size = stack_size
        return stack_size

    def _add_weedline(self, path, padding):
        """ Adds a weedline to the path
        by creating a box around the path with the given padding

        """
        bbox = path.boundingRect()
        w, h = bbox.width(), bbox.height()

        tl = bbox.topLeft()
        x = tl.x() - padding[Padding.LEFT]
        y = tl.y() - padding[Padding.TOP]

        w += padding[Padding.LEFT] + padding[Padding.RIGHT]
        h += padding[Padding.TOP] + padding[Padding.BOTTOM]

        path.addRect(x, y, w, h)
        return path

    @property
    def state(self):
        pass

    @property
    def move_path(self):
        """ Returns the path the head moves when not cutting

        """
        # Compute the negative
        path = QPainterPath()
        for i in range(self.model.elementCount()):
            e = self.model.elementAt(i)
            if e.isMoveTo():
                path.lineTo(e.x, e.y)
            else:
                path.moveTo(e.x, e.y)
        return path

    @property
    def cut_path(self):
        """ Returns path where it is cutting

        """
        return self.model

    #     def get_offset_path(self,device):
    #         """ Returns path where it is cutting """
    #         path = QPainterPath()
    #         _p = QPointF(0,0) # previous point
    #         step = 0.1
    #         for subpath in QtSvgDoc.toSubpathList(self.model):#.toSubpathPolygons():
    #             e = subpath.elementAt(0)
    #             path.moveTo(QPointF(e.x,e.y))
    #             length = subpath.length()
    #             distance = 0
    #             while distance<=length:
    #                 t = subpath.percentAtLength(distance)
    #                 p = subpath.pointAtPercent(t)
    #                 a = subpath.angleAtPercent(t)+90
    #                 #path.moveTo(p)#QPointF(x,y))
    #                 # TOOD: Do i need numpy here???
    #                 x = p.x()+np.multiply(self.device.blade_offset,np.sin(np.deg2rad(a)))
    #                 y = p.y()+np.multiply(self.device.blade_offset,np.cos(np.deg2rad(a)))
    #                 path.lineTo(QPointF(x,y))
    #                 distance+=step
    #             #_p = p # update last
    #
    #         return path

    def add_stack(self):
        """ Add a complete stack or fill the row

        """
        stack_size = self.stack_size[0]
        if stack_size == 0:
            self.copies += 1
            return # Don't divide by 0
        copies_left = stack_size - (self.copies % stack_size)
        if copies_left == 0: # Add full stack
            self.copies += stack_size
        else: # Fill stack
            self.copies += copies_left

    def remove_stack(self):
        """ Remove a complete stack or the rest of the row

        """
        stack_size = self.stack_size[0]
        if stack_size == 0 or self.copies <= stack_size:
            self.copies = 1
            return
        copies_left = self.copies % stack_size
        if copies_left == 0: # Add full stack
            self.copies -= stack_size
        else:  # Fill stack
            self.copies -= copies_left

    def clone(self):
        """ Return a cloned instance of this object

        """
        state = self.__getstate__()
        state.update({
            'material': Material(**self.material.__getstate__()),
            'info': JobInfo(**self.info.__getstate__()),
            'items': [JobItem(**item.__getstate__()) for item in self.items],
        })
        return Job(**state)
