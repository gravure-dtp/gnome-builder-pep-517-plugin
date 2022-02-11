#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#       Copyright (c) Gilles Coissac 2022 <info@gillescoissac.fr>
#
#       This program is free software; you can redistribute it and/or modify
#       it under the terms of the GNU General Public License as published by
#       the Free Software Foundation; either version 3 of the License, or
#       (at your option) any later version.
#
#       This program is distributed in the hope that it will be useful,
#       but WITHOUT ANY WARRANTY; without even the implied warranty of
#       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#       GNU General Public License for more details.
#
#       You should have received a copy of the GNU General Public License
#       along with this program; if not, write to the Free Software
#       Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#       MA 02110-1301, USA.
#
# pylint: disable=unused-argument
# pylint: disable=too-many-arguments, attribute-defined-outside-init
# pylint: disable=too-many-locals, no-self-use
#
import os
from pathlib import Path
import json
import threading

# for raising an ImportError so our plugin wont load
# if pylint is not installed.
import pylint
import gi

from gi.repository import GLib, GObject, Gio
from gi.repository import Ide


_ = Ide.gettext


SEVERITY_MAP = {
    0: Ide.DiagnosticSeverity.IGNORED,
    'convention': Ide.DiagnosticSeverity.NOTE,
    'refactor': Ide.DiagnosticSeverity.NOTE,
    2: Ide.DiagnosticSeverity.UNUSED,
    3: Ide.DiagnosticSeverity.DEPRECATED,
    'warning': Ide.DiagnosticSeverity.WARNING,
    'error': Ide.DiagnosticSeverity.ERROR,
    'fatal': Ide.DiagnosticSeverity.FATAL,
}


class PythonLinterDiagnosticProvider(Ide.Object, Ide.DiagnosticProvider):
  
    def create_launcher(self):
        """create_launcher."""
        context = self.get_context()
        srcdir = context.ref_workdir().get_path()
        launcher = None

        if context.has_project():
            build_manager = Ide.BuildManager.from_context(context)
            pipeline = build_manager.get_pipeline()
            if pipeline is not None:
                srcdir = pipeline.get_srcdir()
            runtime = pipeline.get_config().get_runtime()
            launcher = runtime.create_launcher()

        if launcher is None:
            launcher = Ide.SubprocessLauncher.new(0)

        launcher.set_flags(
            Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE
        )
        launcher.set_cwd(srcdir)

        return launcher


    def do_diagnose_async(
        self, file, file_content, lang_id, cancellable, callback, user_data
    ):
        self.diagnostics_list = []
        task = Gio.Task.new(self, cancellable, callback)
        task.diagnostics_list = []

        launcher = self.create_launcher()

        threading.Thread(
            target=self._execute,
            args=(task, launcher, file, file_content),
            name="pylint-thread",
        ).start()

    def _execute(self, task, launcher, file, file_content):
        try:
            launcher.push_args(('pylint', '--output-format', 'json',
                                '--persistent', 'n',
                                '-j', '1',
                                '--exit-zero',
                                ))

            if file_content:
                launcher.push_argv('--from-stdin')
                launcher.push_argv(file.get_path())
            else:
                launcher.push_argv(file.get_path())

            sub_process = launcher.spawn()
            stdin = file_content.get_data().decode('UTF-8')
            success, stdout, _stderr = sub_process.communicate_utf8(stdin, None)

            if not success:
                task.return_boolean(False)
                return

            results = json.loads(stdout)
            for item in results:
                line = item.get('line', None)
                column = item.get('column', None)
                if not line or not column:
                    continue
                start_line = max(item['line'] - 1, 0)
                start_col = max(item['column'], 0)
                start = Ide.Location.new(file, start_line, start_col)
                
                severity = SEVERITY_MAP[item['type']]
                end = None
                
                end_line = item.get('endLine', None)
                end_col = item.get('endColumn', None)
                if end_line and end_col:
                    end_line = max(end_line - 1, 0)
                    end_col = max(end_col , 0)
                    if not severity in (Ide.DiagnosticSeverity.ERROR,
                                        Ide.DiagnosticSeverity.FATAL):
                        # make underlined run on multiple lines
                        # only for hight severity code
                        end_col = start_col if start_line != end_line else end_col
                        end_line = start_line
                    end = Ide.Location.new(file, end_line, end_col)

                _symbol = item.get('symbol')
                _message = item.get('message')
                _code = item.get('message-id')
                diagnostic = Ide.Diagnostic.new(
                                severity,
                                f"{_symbol} ({_code})\n{_message}",
                                start,
                             )
                if end is not None:
                    range_ = Ide.Range.new(start, end)
                    diagnostic.add_range(range_)
                    # if 'fix' in message:
                    # Fixes often come without end* information so we
                    # will rarely get here, instead it has a file offset
                    # which is not actually implemented in IdeSourceLocation
                    # fixit = Ide.Fixit.new(range_, message['fix']['text'])
                    # diagnostic.take_fixit(fixit)

                task.diagnostics_list.append(diagnostic)
        except GLib.Error as err:
            task.return_error(err)
        except (json.JSONDecodeError, UnicodeDecodeError, IndexError) as err:
            task.return_error(
                GLib.Error(f"Failed to decode pylint json: {err}")
            )
        else:
            task.return_boolean(True)

    def do_diagnose_finish(self, result):
        if result.propagate_boolean():
            diagnostics = Ide.Diagnostics()
            for diag in result.diagnostics_list:
                diagnostics.add(diag)
            return diagnostics
        return None


# class PythonLinterPreferencesAddin(GObject.Object, Ide.PreferencesAddin):
#     """PythonLinterPreferencesAddin."""

#     def do_load(self, preferences):
#         """do_load."""
#         self.python_linter_id = preferences.add_switch(
# to the code-insight page
#             "code-insight",
# in the diagnostics group
#             "diagnostics",
# mapping to the gsettings schema
#             "org.gnome.builder.plugins.python-linter",
# with the gsettings schema key
#             "enable-python-linter",
# And the gsettings path
#             None,
# The target GVariant value if necessary (usually not)
#             "false",
# title
#             "Python Linter",
# subtitle
#             "Enable the use of PyLint, which may execute code in your project",
# translators: these are keywords used to search for preferences
#             "pylint python lint code execute execution",
# with sort priority
#             500)

#     def do_unload(self, preferences):
#         preferences.remove_id(self.python_linter_id)

