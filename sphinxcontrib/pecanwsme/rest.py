# -*- encoding: utf-8 -*-
#
# Copyright © 2013 New Dream Network, LLC (DreamHost)
#
# Author: Doug Hellmann <doug.hellmann@dreamhost.com>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
"""Sphinx extension for automatically generating API documentation
from Pecan controllers exposed through WSME.

"""
import inspect

from docutils import nodes
from docutils.parsers import rst
from docutils.statemachine import ViewList

from sphinx.util.nodes import nested_parse_with_titles
from sphinx.util.docstrings import prepare_docstring

import wsme.types


def import_object(import_name):
    """Import the named object and return it.

    The name should be formatted as package.module:obj.
    """
    module_name, expr = import_name.split(':', 1)
    mod = __import__(module_name)
    mod = reduce(getattr, module_name.split('.')[1:], mod)
    globals = __builtins__
    if not isinstance(globals, dict):
        globals = globals.__dict__
    return eval(expr, globals, mod.__dict__)


def http_directive(method, path, content):
    """Build an HTTP directive for documenting a single URL.

    :param method: HTTP method ('get', 'post', etc.)
    :param path: URL
    :param content: Text describing the endpoint.
    """
    method = method.lower().strip()
    if isinstance(content, basestring):
        content = content.splitlines()
    yield ''
    yield '.. http:{method}:: {path}'.format(**locals())
    yield ''
    for line in content:
        yield '   ' + line
    yield ''


def datatypename(datatype):
    """Return the formatted name of the data type.

    Derived from wsmeext.sphinxext.datatypename.
    """
    if isinstance(datatype, wsme.types.DictType):
        return 'dict(%s: %s)' % (datatypename(datatype.key_type),
                                 datatypename(datatype.value_type))
    if isinstance(datatype, wsme.types.ArrayType):
        return 'list(%s)' % datatypename(datatype.item_type)
    if isinstance(datatype, wsme.types.UserType):
        return ':class:`%s`' % datatype.name
    if isinstance(datatype, wsme.types.Base) or hasattr(datatype, '__name__'):
        return ':class:`%s`' % datatype.__name__
    return datatype.__name__


class RESTControllerDirective(rst.Directive):

    required_arguments = 1
    option_spec = {
        'webprefix': rst.directives.unchanged,
    }
    has_content = True

    def make_rst_for_method(self, path, method, http_method):
        docstring = prepare_docstring((method.__doc__ or '').rstrip('\n'))
        blank_line = docstring[-1]
        docstring = docstring[:-1]  # remove blank line appended automatically


        if hasattr(method, '_wsme_definition'):
            funcdef = method._wsme_definition

            # Add the parameter type information. Assumes that the
            # developer has provided descriptions of the parameters.
            for arg in funcdef.arguments:
                docstring.append(':type %s: %s' %
                                 (arg.name, datatypename(arg.datatype)))

            # Add a blank line before return type to avoid the formatting issues
            # that are caused because of missing blank lines between blocks
            docstring.append(blank_line)

            # Add the return type
            if funcdef.return_type:
                return_type = datatypename(funcdef.return_type)
                docstring.append(':return type: %s' % return_type)

            # restore the blank line added as a spacer
            docstring.append(blank_line)

        directive = http_directive(http_method, path, docstring)
        return directive

    def make_rst_for_controller(self, path_prefix, controller):
        env = self.state.document.settings.env
        app = env.app

        controller_path = path_prefix.rstrip('/')

        # Some of the controllers are instantiated dynamically, so
        # we need to look at their constructor arguments to see
        # what parameters are needed and include them in the
        # URL. For now, we only ever want one at a time.
        try:
            argspec = inspect.getargspec(controller.__init__)
        except TypeError:
            # The default __init__ for object is a "slot wrapper" not
            # a method, so we can't inspect it. It doesn't take any
            # arguments, though, so just knowing that we didn't
            # override __init__ helps us build the controller path
            # correctly.
            pass
        else:
            if len(argspec[0]) > 1:
                first_arg_name = argspec[0][1]
                controller_path += '/(' + first_arg_name + ')'

        lines = []

        for method_name, http_method_name in [('get_all', 'get'),
                                              ('get', 'get'),
                                          ]:
            app.info('Checking %s for %s method' % (controller, method_name))
            method = getattr(controller, method_name, None)
            if method and method.exposed:
                app.info('Found method: %s' % method_name)
                lines.extend(
                    self.make_rst_for_method(
                        controller_path,
                        method,
                        http_method_name,
                    )
                )

        # Handle the special case for get_one(). The path should
        # include the name of the argument used to find the object.
        if hasattr(controller, 'get_one') and controller.get_one.exposed:
            app.info('Found method: get_one')
            funcdef = controller.get_one._wsme_definition
            first_arg_name = funcdef.arguments[0].name
            path = controller_path + '/(' + first_arg_name + ')'
            lines.extend(
                self.make_rst_for_method(
                    path,
                    controller.get_one,
                    'get',
                )
            )

        for method_name, http_method_name in [('post', 'post'),
                                              ('put', 'put'),
                                              ('delete', 'delete'),
                                              ('patch', 'patch'),
                                          ]:
            app.info('Checking %s for %s method' % (controller, method_name))
            method = getattr(controller, method_name, None)
            if method and method.exposed:
                app.info('Found method: %s' % method_name)
                lines.extend(
                    self.make_rst_for_method(
                        controller_path,
                        method,
                        http_method_name,
                    )
                )

        # Look for exposed custom methods
        for name in sorted(controller._custom_actions.keys()):
            app.info('Adding custom method: %s' % name)
            path = controller_path + '/' + name
            actions = controller._custom_actions[name]
            for action in actions:
                # Check for a method prefixed with the HTTP verb
                # otherwise use the method with only the action name
                method_name = "%s_%s" % (action.lower(), name)
                if hasattr(controller, method_name):
                    method = getattr(controller, method_name)
                else:
                    method = getattr(controller, name)
                app.info('Custom method %s uses action %s' % (method, action))
                lines.extend(
                    self.make_rst_for_method(
                        path,
                        method,
                        action.lower(),
                    )
                )

        return lines

    def run(self):
        env = self.state.document.settings.env
        app = env.app
        controller_id = self.arguments[0]
        app.info('found root-controller %s' % controller_id)

        result = ViewList()
        controller = import_object(self.arguments[0])

        for line in self.make_rst_for_controller(
                self.options.get('webprefix', '/'),
                controller):
            app.info('ADDING: %r' % line)
            result.append(line, '<' + __name__ + '>')

        node = nodes.section()
        # necessary so that the child nodes get the right source/line set
        node.document = self.state.document
        nested_parse_with_titles(self.state, result, node)

        return node.children


def setup(app):
    app.info('Initializing %s' % __name__)
    app.add_directive('rest-controller', RESTControllerDirective)
