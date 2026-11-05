"""
Copyright (c) 2017, Jairus Martin.

Distributed under the terms of the GPL v3 License.

The full license is in the file LICENSE, distributed with this software.

Created on Dec 7, 2017

@author: jrm
"""
from atom.api import observe
from enaml.widgets.api import Container, DockArea, DockItem


# -----------------------------------------------------------------------------
# Custom widgets
# -----------------------------------------------------------------------------
class Section(Container):
    """ Container that asks its parent to lay out again when it is shown
    or hidden; enaml's QtContainer swallows that request (qt_container.py
    request_relayout), so the parent's vbox would keep the old children.

    This only works when the parent is a Container (a Form included).

    """
    @observe('visible')
    def _relayout_parent(self, change):
        if change['type'] == 'update' and isinstance(self.parent, Container):
            self.parent.request_relayout()


class PickableDockItem(DockItem):
    """ A custom pickable dock item class.

    """
    def __getstate__(self):
        """ Get the pickle state for the dock item.

        This method saves the necessary state for the dock items used
        in this example. Different applications will have different
        state saving requirements.

        The default __setstate__ method provided on the Atom base class
        provides sufficient unpickling behavior.

        """
        return {'name': self.name, 'title': self.title}


class PickableDockArea(DockArea):
    """ A custom pickable dock area class.

    """
    def get_save_items(self):
        """ Get the list of dock items to save with this dock area.

        """
        return [c for c in self.children if isinstance(c, PickableDockItem)]

    def __getstate__(self):
        """ Get the pickle state for the dock area.

        This method saves the necessary state for the dock area used
        in this example. Different applications will have different
        state saving requirements.

        """
        state = {
            'name': self.name,
            'layout': self.save_layout(),
            'items': self.get_save_items(),
        }
        return state

    def __setstate__(self, state):
        """ Restore the state of the dock area.

        """
        self.name = state['name']
        self.layout = state['layout']
        self.insert_children(None, state['items'])
