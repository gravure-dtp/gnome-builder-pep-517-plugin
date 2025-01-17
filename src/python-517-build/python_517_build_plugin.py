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
# pylint: disable=R0201,C0116,W0613,W0511,R0913
#
import os
import sys
import venv
from pathlib import Path

import gi  # noqa
from gi.repository import Gio, GLib, GObject, Ide

import tomli
from backends import BuildType, PypaBuildBackend
from packaging.utils import parse_sdist_filename, parse_wheel_filename
# from packaging.version import Version
from stage import Python517BuildStage

_ = Ide.gettext


class Python517BuildSystemDiscovery(Ide.SimpleBuildSystemDiscovery):
    """SimpleBuildSystemDiscovery subclass.

    The "glob" property is a glob to match for files within the project
    directory. This can be used to quickly match the project file, such as
    "configure.*".
    The "hint" property is used from ide_build_system_discovery_discover()
    if the build file was discovered.
    The "priority" property is the priority of any match.

    All of those Ide.SimpleBuildSystemDiscovery property
    are available since ABI 3.32
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.props.glob = "pyproject.toml"
        self.props.hint = "python_517_build_plugin"
        self.props.priority = 500


class Python517BuildSystem(Ide.Object, Ide.BuildSystem, Gio.AsyncInitable):
    """A Python Pep 517 BuildSystem.

    In certain circumstances, projects may wish to include the source code
    for the build backend directly in the source tree, rather than referencing
    the backend via the requires key.
    Projects can specify that their backend code is hosted in-tree by including
    the backend-path key in pyproject.toml.
        * Directories in backend-path are interpreted as relative to the project
          root, and MUST refer to a location within the source tree
        * The backend code MUST be loaded from one of the directories specified
          in backend-path

    All of those Ide.BuildSystem methods and property
    are available since ABI 3.32
    """

    project_file = GObject.Property(type=Gio.File)  # 'pyproject.toml'
    backends = GObject.Property(
        type=GLib.HashTable,
        default={"setuptools.build_meta": PypaBuildBackend},
        flags=GObject.ParamFlags.READABLE,
    )
    # requires = GObject.Property(type=GLib.List, default=[])
    # backend_path = GObject.Property(type=GLib.List, default=[])
    frontend = GObject.Property(type=str, default="pip")
    build_backend = GObject.Property(type=object, default=None)
    builds = GObject.Property(type=GLib.HashTable, default={})

    # TODO: set wheel as installable

    def do_init_async(self, priority, cancel, callback, data=None):
        task = Gio.Task.new(self, cancel, callback)
        task.set_priority(priority)
        # parse project_file
        project_file = self.get_pyproject_toml()
        project_file.load_contents_async(
            cancel,
            self._on_load_pyproject_toml,
            task,
        )

    def do_init_finish(self, result):
        return result.propagate_boolean()

    def _on_load_pyproject_toml(self, project_file, result, task):
        """Load and parse the pyproject.toml file.

        If the pyproject.toml file is absent, or the build-backend
        key  is missing, the source tree is not using Pep 517
        specification. Tools should revert to the legacy behaviour
        of running setup.py  (either directly, or by implicitly invoking
        the setuptools.build_meta:__legacy__ backend).
        Where the build-backend key exists, this takes precedence
        and the source tree follows the format and conventions
        of the specified backend (as such no setup.py is needed unless
        the backend requires it).
        """

        try:
            _ok, contents, _etag = project_file.load_contents_finish(result)
        except GLib.Error as err:  # IOError
            task.return_error(err)
            return

        try:
            py_project = tomli.loads(contents.decode('utf-8'))
        except tomli.TOMLDecodeError as err:  # Invalid toml file
            task.return_error(err)
            return

        if (
            "build-system" not in py_project
            or not isinstance(py_project["build-system"], dict)
            or "build-backend" not in py_project["build-system"]
            or not isinstance(
                py_project["build-system"]["build-backend"], str
            )
        ):
            # Not a PEP 517 python project
            task.return_error(
                GLib.Error(
                    "Not a valid python PEP-517 build system",
                    domain=GLib.quark_to_string(Gio.io_error_quark()),
                    code=Gio.IOErrorEnum.NOT_SUPPORTED,
                )
            )
            return

        _backend = self.props.backends.get(
                    py_project["build-system"]["build-backend"]
                )
        if _backend:
            self.props.build_backend = _backend()

        task.return_boolean(True)

    def get_pyproject_toml(self):
        """Return the 'pyproject.toml' file.

        Returns(Gio.File): the 'pyproject.toml' file
        """
        if self.props.project_file.get_basename() != 'pyproject.toml':
            return self.props.project_file.get_child('pyproject.toml')
        return self.props.project_file

    def do_get_project_version(self):
        """If the build system supports it, gets the project
        version as configured in the build system's configuration files.

        Returns(str): a string containing the project version
        """
        # TODO: do_get_project_version()
        return

    def do_build_system_supports_language(self, language):
        """Say if this BuilSystem support 'language'.

        Returns True if self in it's current configuration
        is known to support 'language'.
        """
        return language == "python3"

    def do_get_builddir(self, pipeline):
        return self.get_builddir()

    def get_builddir(self):
        """Return a path to the build directory.

        This path may not be the same for different
        build backend, so ask to the backend what is
        its build directory.

        Returns(str): A path representing the build directory.
        """
        if self.props.build_backend is None:
            return self.get_context().ref_workdir().get_path()
        _wd = self.get_context().ref_workdir()
        return self.props.build_backend.get_builddir(_wd).get_path()

    def do_get_id(self):
        return "python_517_build_system"

    def do_get_display_name(self):
        return "Python (pyproject.toml)"

    def do_get_priority(self):
        return 500

    def add_build(self, file):
        """Register a build artifact.

        The artifact will be add only if its kind is supported
        by the Build Backend.

        Args:
            file(pathlib.Path): the artifact to register.
        """
        if(
            file.is_dir() and BuildType.TREE
            in self.props.build_backend.get_build_types()
        ):
            self.props.builds[file.name] = BuildType.TREE
        elif(
              file.suffix == ".egg" and BuildType.EGG
              in self.props.build_backend.get_build_types()
        ):
            self.props.builds[file.name] = BuildType.EGG
        elif(
             file.suffix == ".whl" and BuildType.WHEEL
             in self.props.build_backend.get_build_types()
        ):
            self.props.builds[file.name] = BuildType.WHEEL
        # FIXME: valid suffixes for sdist?
        elif(
             file.suffix in [".gz", ".tar", ".zip"]
             and BuildType.SDIST
             in self.props.build_backend.get_build_types()
        ):
            self.props.builds[file.name] = BuildType.SDIST
        elif(
             BuildType.FILE in
             self.props.build_backend.get_build_types()
        ):
            self.props.builds[file.name] = BuildType.FILE

    def clean_builds(self):
        """Unregister all build artifact.

        This actually does not remove any files from the build directory,
        this only clear the artifact register dictionary.
        """
        self.props.builds.clear()

    def get_builds_installable(self):
        """Return a list of installable artifacts.

        Return a list of artifacts resulting from previous build stage that
        could be installable. If pip version>=21.1 or if a setup.py file
        exist, a pseudo artifact representing the project source tree
        is added to authorize install in editable mode.

        Return(list): a list of tuple(file, BuildType, artifact name).
        """
        # FIXME: if pip<21.1 and no setup.py don't add editable install
        b_inst = [(self.get_context().ref_workdir().get_path(),
                   BuildType.TREE,
                   "sources")]
        installable = None
        name = "Unknown"

        # TODO: study priority of installable, what about egg and file
        if BuildType.WHEEL in self.props.builds.values():
            installable = BuildType.WHEEL
        elif BuildType.SDIST in self.props.builds.values():
            installable = BuildType.SDIST
        elif BuildType.TREE in self.props.builds.values():
            installable = BuildType.TREE
        for file, kind in self.props.builds.items():
            if kind is installable:
                if kind is BuildType.WHEEL:
                    name, _ver, _build, _tags = parse_wheel_filename(file)
                elif kind is BuildType.SDIST:
                    name, _ver = parse_sdist_filename(file)
                b_inst.append((file, kind, name))
        return b_inst

    def get_virtual_env(self):
        """Return a virtualenv for this project.

        This method looks for a variable 'VIRTUAL_ENV' in global environment
        or one defined in the configuration ui. If the path is a valid
        directory the virtualenv will be updated with pip, otherwise env will
        be created. If no 'VIRTUAL_ENV' variable was found None
        will be returned.

        Returns(pathlib.Path): A Path to a virtualenv or None.
        """
        context = self.get_context()
        config_manager = Ide.ConfigManager.from_context(context)
        config = config_manager.get_current()
        virtual_env = config.getenv("VIRTUAL_ENV")
        os_virtual_env = os.environ.get("VIRTUAL_ENV")

        if virtual_env or os_virtual_env:
            virtual_env = (
                Path(virtual_env) if virtual_env else Path(os_virtual_env)
            )
            if not virtual_env.is_absolute():
                cwd = Path(context.ref_workdir().get_path())
                virtual_env = cwd / virtual_env
            if virtual_env.is_dir():
                print(f"Updating venv in {virtual_env.absolute()}")
                builder = venv.EnvBuilder(upgrade=True, with_pip=True)
                builder.create(virtual_env)
            else:
                print(f"Creating venv in {virtual_env.absolute()}")
                builder = venv.EnvBuilder(with_pip=True)
                builder.create(virtual_env)
        return virtual_env


class Python517PipelineAddin(Ide.Object, Ide.PipelineAddin):
    """
    Builder uses the concept of a “Build Pipeline” to build a project.
    The build pipeline consists of multiple “phases” and build “stages”
    run in a given phase.
    The Ide.Pipeline is used to specify how and when build operations
    should occur. Plugins attach build stages to the pipeline to perform
    build actions.
    The Python517PipelineAddin registers those stages to be executed
    when various phases of the build pipeline are requested.
    """

    def do_load(self, pipeline):
        context = pipeline.get_context()
        build_system = Ide.BuildSystem.from_context(context)

        # Only register stages if we are a pyproject.toml
        if not isinstance(build_system, Python517BuildSystem):
            return

        # Build Phase
        build_backend = build_system.get_property("build_backend")
        build_stage = Python517BuildStage(build_backend)
        phase = Ide.PipelinePhase.BUILD
        stage_id = pipeline.attach(phase, 100, build_stage)
        self.track(stage_id)

        # TODO: publishing phase


class Python517BuildTarget(Ide.Object, Ide.BuildTarget):
    """Python517BuildTarget.

    Ide.BuildTarget API is available since ABI 3.32
    """
    name = GObject.Property(type=str, default="Unknown")
    action = GObject.Property(type=str, default="null")
    priority = GObject.Property(type=int, default=0)
    virtual_env = GObject.Property(type=str, default=None)

    def __init__(self, name, action, priority, virtual_env, argv, **kwargs):
        super().__init__(**kwargs)
        self.props.name = name
        self.props.action = action
        self.props.priority = priority
        self.props.virtual_env = str(virtual_env) if virtual_env else None
        self.argv = argv

    def do_get_install_directory(self):
        """Returns(Gio.File): a GFile or None."""
        # sys.executable return python path following venv
        return Gio.File.new_for_path(str(Path(sys.executable).parent))

    def do_get_display_name(self):
        """A display name for the build target
        to be displayed in UI. May contain pango markup.

        Returns(str): A display name.
        """
        return f"{self.props.name} : {self.props.action}"

    def do_get_name(self):
        """Return a command name.

        Returns(str): A command name (a filename) or None.
        """
        return self.props.action

    def do_get_priority(self):
        """Gets the priority of the build target.

        This is used to sort build targets by their importance.
        The lowest value (negative values are allowed) will be run
        as the default run target by Builder.

        Returns(int): the priority of the build target
        """
        return self.props.priority

    def do_get_argv(self):
        """Gets the arguments used to run the target.

        Returns(str): containing the arguments to run the target.
        """
        if self.props.virtual_env:
            _argv = self.argv.copy()
            _argv[0] = f"{self.props.virtual_env}/bin/{_argv[0]}"
            return _argv
        return self.argv

    def do_get_cwd(self):
        """Gets the correct working directory.

        For build systems and build target providers that insist
        to be run in a specific place, this method gets the correct
        working directory.
        If this method returns None, the runtime will pick a default
        working directory for the spawned process (usually, the user
        home directory in the host system, or the flatpak sandbox
        home under flatpak).

        Returns(str): the working directory to use for this target
        """
        context = self.get_context()
        project_file = Ide.BuildSystem.from_context(context).project_file
        if project_file.query_file_type(0, None) == Gio.FileType.DIRECTORY:
            return project_file.get_path()
        return project_file.get_parent().get_path()

    def do_get_language(self):
        """Return the programming language of this build target.

        Return the main programming language that was used to
        write this build target.
        This method is primarily used to choose an appropriate
        debugger. Therefore, if a build target is composed of
        components in multiple language (eg. a GJS app with
        GObject Introspection libraries, or a Java app with JNI
        libraries), this should return the language that is
        most likely to be appropriate for debugging.
        The default implementation returns "asm", which indicates
        an unspecified language that compiles to native code.

        Returns(str): the programming language of this target
        """
        return "python3"

    def do_get_kind(self):
        """Gets the kind of artifact.

        Returns(Ide.ArtifactKind): an IdeArtifactKind
        """
        return Ide.ArtifactKind.EXECUTABLE

    def do_get_install(self):
        """Checks if the Ide.BuildTarget gets installed.

        Returns(bool): TRUE if the build target is installed
        """
        # TODO: develop case for different target
        return True


class Python517BuildTargetProvider(Ide.Object, Ide.BuildTargetProvider):
    """PythonBuildTargetProvider.

    Ide.BuildTargetProvider API is available since ABI 3.32
    """

    def do_get_targets_async(self, cancellable, callback, data):
        """Asynchronously requests that the provider fetch all
        of the known build targets that are part of the project.
        Generally this should be limited to executables that Builder
        might be interested in potentially running.
        'callback' should call ide_build_target_provider_get_targets_finish()
        to complete the asynchronous operation.

        Args:
            cancellable(GCancellable): a GCancellable or None
            callback(callable): a callback to execute upon completion
            user_data: closure data for callback
        """

        task = Ide.Task.new(self, cancellable, callback)
        task.set_priority(GLib.PRIORITY_LOW)

        context = self.get_context()
        build_system = Ide.BuildSystem.from_context(context)

        if not isinstance(build_system, Python517BuildSystem):
            task.return_error(
                GLib.Error(
                    "Not a python 517 build system",
                    domain=GLib.quark_to_string(Gio.io_error_quark()),
                    code=Gio.IOErrorEnum.NOT_SUPPORTED,
                )
            )
            return

        build_dir = build_system.props.build_backend.get_builddir_name()
        installables = build_system.get_builds_installable()
        virtual_env = build_system.get_virtual_env()
        task.targets = []

        for file, kind, name in installables:
            if kind is BuildType.SDIST:
                cmd = build_system.props.build_backend.get_wheel_cmd()
                if build_system.props.build_backend.has_isolation():
                    _venv = None
                else:
                    _venv = virtual_env
                task.targets.append(Python517BuildTarget(
                    name=name,
                    action="wheel",
                    priority=100,
                    virtual_env=_venv,
                    argv=cmd
                ))
            # TODO: what about different frontend than pip
            if kind in [BuildType.SDIST, BuildType.WHEEL]:
                task.targets.append(Python517BuildTarget(
                    name=name,
                    action="install",
                    priority=200,
                    virtual_env=virtual_env,
                    argv=["python", "-m", "pip",
                          "install", f"{build_dir}/{file}"]
                ))
                task.targets.append(Python517BuildTarget(
                    name=name,
                    action="uninstall",
                    priority=400,
                    virtual_env=virtual_env,
                    argv=["python", "-m", "pip",
                          "uninstall", name]
                ))
            if kind in [BuildType.TREE]:
                task.targets.append(Python517BuildTarget(
                    name=name,
                    action="install editable",
                    priority=200,
                    virtual_env=virtual_env,
                    argv=["python", "-m", "pip",
                          "install", '-e', file]
                ))

        # TODO: adding run target for console script entry point
        # task.targets = [build_system.ensure_child_typed(Python517BuildTarget)]
        task.return_boolean(True)

    def do_get_targets_finish(self, result):
        """Completes a request to get the targets for the project.

        Args:
            result(GAsyncResult): a GAsyncResult provided to the callback

        Returns(list): A list of Ide.BuildTarget or None upon failure
                       and error is set.
        """
        if result.propagate_boolean():
            return result.targets
        return None

