"""
Copyright (c) 2026, Peter Sharpe

Distributed under the terms of the GPL v3 License.

The full license is in the file LICENSE, distributed with this software.

Tests for Inkcut UI plugin discovery.
"""

from enaml.workbench.api import PluginManifest, Workbench

from inkcut.ui.plugin import InkcutPlugin


class FakeEntryPoint:
    def __init__(self, name, group='inkcut.plugin', value=None, error=None):
        self.name = name
        self.group = group
        self.value = value
        self.error = error

    def load(self):
        if self.error is not None:
            raise self.error
        return self.value


class SelectableEntryPoints(list):
    def __init__(self, *entry_points):
        super().__init__(entry_points)
        self.selected_group = None

    def select(self, **params):
        self.selected_group = params.get('group')
        return [
            entry_point for entry_point in self
            if entry_point.group == self.selected_group
        ]


def test_load_ext_plugins_uses_selectable_entry_point_api(monkeypatch):
    expected = object()
    entry_points = SelectableEntryPoints(
        FakeEntryPoint('external', value=expected),
        FakeEntryPoint('unrelated', group='other.plugin', value=object()),
    )
    monkeypatch.setattr(
        'inkcut.ui.plugin.importlib.metadata.entry_points',
        lambda: entry_points,
    )

    plugins = InkcutPlugin().load_ext_plugins()

    assert entry_points.selected_group == 'inkcut.plugin'
    assert plugins == [expected]


def test_load_ext_plugins_supports_legacy_entry_point_api(monkeypatch):
    expected = object()
    entry_points = [
        FakeEntryPoint('external', value=expected),
        FakeEntryPoint('unrelated', group='other.plugin', value=object()),
    ]
    monkeypatch.setattr(
        'inkcut.ui.plugin.importlib.metadata.entry_points',
        lambda: entry_points,
    )

    assert InkcutPlugin().load_ext_plugins() == [expected]


def test_load_ext_plugins_supports_mapping_entry_point_api(monkeypatch):
    expected = object()
    entry_points = {
        'inkcut.plugin': [FakeEntryPoint('external', value=expected)],
        'other.plugin': [FakeEntryPoint('unrelated', value=object())],
    }
    monkeypatch.setattr(
        'inkcut.ui.plugin.importlib.metadata.entry_points',
        lambda: entry_points,
    )

    assert InkcutPlugin().load_ext_plugins() == [expected]


def test_load_ext_plugins_continues_after_broken_plugin(monkeypatch):
    expected = object()
    entry_points = SelectableEntryPoints(
        FakeEntryPoint('broken', error=RuntimeError('broken plugin')),
        FakeEntryPoint('external', value=expected),
    )
    monkeypatch.setattr(
        'inkcut.ui.plugin.importlib.metadata.entry_points',
        lambda: entry_points,
    )

    assert InkcutPlugin().load_ext_plugins() == [expected]


def test_load_plugins_registers_external_manifest(monkeypatch):
    entry_points = [
        FakeEntryPoint(
            'external',
            value=lambda: PluginManifest(id='test.external'),
        ),
    ]
    monkeypatch.setattr(
        'inkcut.ui.plugin.importlib.metadata.entry_points',
        lambda: entry_points,
    )
    workbench = Workbench()
    host_manifest = PluginManifest(id='test.host')
    workbench.register(host_manifest)
    plugin = InkcutPlugin(manifest=host_manifest)

    plugin.load_plugins()

    assert workbench.get_manifest('test.external') is not None
