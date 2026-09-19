"""Settings tab for installing stable AnimKey GitHub Releases."""

from __future__ import absolute_import

import os

import maya.cmds as cmds

from AnimKey.core import updater
from AnimKey.mods import configMod as config
from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
from AnimKey.version import __version__


def _reload_after_update(install_root=None):
    """Load the freshly installed package in the current Maya session."""
    import importlib
    import sys

    try:
        if install_root:
            # The new release may be older and lack this updater fix. Select
            # its import path here before importing any of the new modules.
            install_parent = os.path.dirname(os.path.realpath(install_root))
            sys.path[:] = [
                path for path in sys.path
                if os.path.normcase(os.path.realpath(path)) != os.path.normcase(install_parent)
            ]
            sys.path.insert(0, install_parent)
        importlib.invalidate_caches()
        for module_name in list(sys.modules.keys()):
            if module_name == "AnimKey" or module_name.startswith("AnimKey."):
                sys.modules.pop(module_name, None)
        import AnimKey
        AnimKey.show()
        cmds.inViewMessage(
            amg="<span style='color:#a3be8c'>AnimKey updated and reloaded successfully.</span>",
            pos="topCenter",
            fade=True,
            fadeStayTime=2500,
        )
    except Exception as exc:
        cmds.warning(
            "AnimKey: The update was installed but the toolbar could not reload: {}".format(
                exc
            )
        )


def _restore_after_failed_update():
    """Restore the existing toolbar if an update attempt fails before activation."""
    try:
        import AnimKey
        AnimKey.show()
    except Exception as exc:
        cmds.warning("AnimKey: Could not restore the toolbar: {}".format(exc))


class _TaskSignals(QtCore.QObject):
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    finished = QtCore.Signal()


class _Task(QtCore.QRunnable):
    def __init__(self, callback, *args, **kwargs):
        super(_Task, self).__init__()
        self.callback = callback
        self.args = args
        self.kwargs = kwargs
        self.signals = _TaskSignals()

    def run(self):
        try:
            result = self.callback(*self.args, **self.kwargs)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()


class UpdateTab(QtWidgets.QWidget):
    """Browse, upgrade, downgrade, or reinstall stable releases."""

    def __init__(self, parent=None):
        super(UpdateTab, self).__init__(parent)
        self._installed_version = __version__
        self._releases = []
        self._tasks = []
        self._install_buttons = []
        self._busy = False
        self._runtime_suspended = False
        self._install_root = None
        self._build_ui()
        if config.get_setting("check_updates", True):
            QtCore.QTimer.singleShot(
                0, lambda: self.refresh_releases(silent=True)
            )

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        title_block = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("AnimKey Updates")
        title.setStyleSheet("color: #FFF; font-size: 17px; font-weight: 700;")
        title_block.addWidget(title)
        self.current_label = QtWidgets.QLabel()
        self.current_label.setStyleSheet("color: #a3be8c; font-size: 11px;")
        title_block.addWidget(self.current_label)
        header.addLayout(title_block)
        header.addStretch()

        self.refresh_button = QtWidgets.QPushButton("Check for updates")
        self.refresh_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.refresh_button.setFixedHeight(32)
        self.refresh_button.setStyleSheet("""
            QPushButton {
                background-color: #2f536d;
                color: #FFF;
                border: 1px solid #477a9d;
                border-radius: 5px;
                padding: 5px 12px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #376985; }
            QPushButton:disabled { color: #777; background-color: #3f3f3f; border-color: #555; }
        """)
        self.refresh_button.clicked.connect(self.refresh_releases)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        description = QtWidgets.QLabel(
            "Only published GitHub Releases are shown. Drafts and prereleases are ignored. "
            "Your preferences, shortcuts, workspace, and saved data are kept when changing versions."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #AAA; font-size: 11px;")
        layout.addWidget(description)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Version", "Published", "Release notes", "Status", "Action"]
        )
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #333;
                alternate-background-color: #383838;
                color: #CCC;
                border: 1px solid #555;
                border-radius: 5px;
            }
            QHeaderView::section {
                background-color: #414141;
                color: #DDD;
                border: none;
                border-bottom: 1px solid #5a5a5a;
                padding: 6px;
                font-weight: 600;
            }
        """)
        table_header = self.table.horizontalHeader()
        table_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        table_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        table_header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        table_header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        table_header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

        footer = QtWidgets.QHBoxLayout()
        self.auto_check = QtWidgets.QCheckBox("Check automatically when opening this tab")
        self.auto_check.setChecked(bool(config.get_setting("check_updates", True)))
        self.auto_check.setStyleSheet("color: #AAA;")
        self.auto_check.toggled.connect(
            lambda enabled: config.set_setting("check_updates", bool(enabled))
        )
        footer.addWidget(self.auto_check)
        footer.addStretch()
        releases_link = QtWidgets.QPushButton("Open GitHub Releases")
        releases_link.setCursor(QtCore.Qt.PointingHandCursor)
        releases_link.setStyleSheet("""
            QPushButton { color: #69aee6; background: transparent; border: none; padding: 4px; }
            QPushButton:hover { color: #9acdf2; text-decoration: underline; }
        """)
        releases_link.clicked.connect(
            lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(updater.RELEASES_PAGE_URL))
        )
        footer.addWidget(releases_link)
        layout.addLayout(footer)

        self.status_label = QtWidgets.QLabel("Ready to check GitHub.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #999; font-size: 11px;")
        layout.addWidget(self.status_label)
        self._refresh_current_label()

    def _refresh_current_label(self):
        self.current_label.setText("Installed version: {}".format(self._installed_version))

    def _start_task(self, callback, on_success, on_failure, *args, **kwargs):
        task = _Task(callback, *args, **kwargs)
        self._tasks.append(task)
        task.signals.succeeded.connect(on_success)
        task.signals.failed.connect(on_failure)
        task.signals.finished.connect(lambda task=task: self._release_task(task))
        QtCore.QThreadPool.globalInstance().start(task)

    def _release_task(self, task):
        try:
            self._tasks.remove(task)
        except ValueError:
            pass

    def _set_busy(self, busy, message=""):
        self._busy = bool(busy)
        self.refresh_button.setEnabled(not self._busy)
        for button in self._install_buttons:
            button.setEnabled(not self._busy and bool(button.property("installable")))
        if message:
            self.status_label.setText(message)

    def _set_settings_controls_enabled(self, enabled):
        settings_window = self.window()
        if settings_window is None or settings_window is self:
            return
        settings_window._update_install_in_progress = not enabled
        try:
            settings_window.title_bar.close_btn.setEnabled(enabled)
        except Exception:
            pass
        for button in getattr(settings_window, "_settings_action_buttons", ()):
            try:
                button.setEnabled(enabled)
            except Exception:
                pass

    def refresh_releases(self, _checked=False, silent=False):
        if self._busy:
            return
        self._set_busy(True, "Checking stable releases on GitHub...")
        self.table.setRowCount(0)
        self._install_buttons = []
        failure_handler = self._on_silent_check_failed if silent else self._on_task_failed
        self._start_task(
            updater.fetch_releases,
            self._on_releases_loaded,
            failure_handler,
        )

    def _on_releases_loaded(self, releases):
        self._releases = releases
        self._populate_table()
        self._set_busy(False)
        installable = sum(1 for release in releases if release.installable)
        if releases:
            self.status_label.setText(
                "Found {} stable release(s); {} include a verified installer.".format(
                    len(releases), installable
                )
            )
        else:
            self.status_label.setText(
                "No stable releases have been published yet. Publish one from the GitHub Actions tab."
            )

    def _populate_table(self):
        self.table.setRowCount(len(self._releases))
        self._install_buttons = []
        for row, release in enumerate(self._releases):
            relation = updater.compare_versions(release.version, self._installed_version)
            version_text = release.version
            if row == 0:
                version_text += "  (latest)"
            self._set_item(row, 0, version_text, release.name)
            published = release.published_at[:10] if release.published_at else "-"
            self._set_item(row, 1, published)

            notes = " ".join(release.notes.split())
            summary = notes[:90] + ("..." if len(notes) > 90 else "")
            self._set_item(row, 2, summary or "No release notes", release.notes)

            if not release.installable:
                status = "Package missing"
                action = "Unavailable"
            elif relation > 0:
                status = "Newer"
                action = "Upgrade"
            elif relation < 0:
                status = "Older"
                action = "Downgrade"
            else:
                status = "Installed"
                action = "Reinstall"
            self._set_item(row, 3, status)

            button = QtWidgets.QPushButton(action)
            button.setProperty("installable", release.installable)
            button.setEnabled(release.installable and not self._busy)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setStyleSheet("""
                QPushButton {
                    color: #FFF;
                    background-color: #355b46;
                    border: 1px solid #57836a;
                    border-radius: 4px;
                    padding: 5px 10px;
                    font-weight: 600;
                }
                QPushButton:hover { background-color: #427257; }
                QPushButton:disabled { color: #777; background-color: #404040; border-color: #555; }
            """)
            button.clicked.connect(
                lambda _checked=False, selected=release: self._confirm_install(selected)
            )
            self.table.setCellWidget(row, 4, button)
            self.table.setRowHeight(row, 38)
            self._install_buttons.append(button)

    def _set_item(self, row, column, text, tooltip=""):
        item = QtWidgets.QTableWidgetItem(text)
        if tooltip:
            item.setToolTip(tooltip)
        self.table.setItem(row, column, item)

    def _confirm_install(self, release):
        if self._busy or not release.installable:
            return
        maya_app_dir = cmds.internalVar(userAppDir=True)
        maya_scripts_dir = cmds.internalVar(userScriptDir=True)
        allowed_parents = (maya_app_dir, maya_scripts_dir, os.path.join(maya_app_dir, "scripts"))
        try:
            install_root = updater.resolve_install_root(allowed_parents)
        except updater.UpdateError as exc:
            self._on_task_failed(str(exc))
            return
        relation = updater.compare_versions(release.version, self._installed_version)
        action = "upgrade" if relation > 0 else "downgrade" if relation < 0 else "reinstall"
        message = (
            "This will {} AnimKey {} to version {}.\n\n"
            "Your user data will be kept. All AnimKey tool windows will close and the toolbar "
            "will reload automatically. Continue?"
        ).format(action, self._installed_version, release.version)
        result = QtWidgets.QMessageBox.question(
            self,
            "{} AnimKey".format(action.capitalize()),
            message,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if result != QtWidgets.QMessageBox.Yes:
            return

        self._install_root = install_root
        plugin_destination = os.path.join(maya_app_dir, "plug-ins", "AnimKey_plugin.py")
        settings_window = self.window()
        try:
            from AnimKey.mods import uiMod
            uiMod.cleanup_animkey_runtime(
                except_widget=settings_window,
                full=True,
            )
            self._runtime_suspended = True
        except Exception:
            self._runtime_suspended = False
        try:
            if cmds.workspaceControl("AnimKey_Toolbar", query=True, exists=True):
                cmds.deleteUI("AnimKey_Toolbar", control=True)
        except Exception:
            pass
        self._set_settings_controls_enabled(False)
        self._set_busy(True, "Downloading and verifying AnimKey {}...".format(release.version))
        self._start_task(
            updater.install_release,
            self._on_install_complete,
            self._on_task_failed,
            release,
            install_root=install_root,
            allowed_parents=allowed_parents,
            plugin_destination=plugin_destination,
        )

    def _on_install_complete(self, result):
        self._runtime_suspended = False
        self._installed_version = result.version
        self._refresh_current_label()
        self._populate_table()
        self._set_busy(
            False,
            "AnimKey {} is installed. Reloading the toolbar...".format(result.version),
        )
        warning_text = ""
        if result.warnings:
            warning_text = "\n\nWarnings:\n- " + "\n- ".join(result.warnings)
        QtWidgets.QMessageBox.information(
            self,
            "AnimKey {} installed".format(result.version),
            "Installation completed successfully. AnimKey will close its tool windows and reload "
            "the toolbar now." + warning_text,
        )

        settings_window = self.window()
        try:
            from AnimKey.mods import uiMod
            uiMod.cleanup_animkey_runtime(
                except_widget=settings_window,
                full=True,
            )
        except Exception:
            pass
        try:
            if cmds.workspaceControl("AnimKey_Toolbar", query=True, exists=True):
                cmds.deleteUI("AnimKey_Toolbar", control=True)
        except Exception:
            pass
        if settings_window is not None and settings_window is not self:
            settings_window._update_install_in_progress = False
            settings_window.close()
            settings_window.deleteLater()

        install_root = self._install_root
        cmds.evalDeferred(lambda: _reload_after_update(install_root))

    def _on_silent_check_failed(self, message):
        self._set_busy(False, "Updates unavailable: {}".format(message))

    def _on_task_failed(self, message):
        self._set_busy(False, "Update error: {}".format(message))
        if self._runtime_suspended:
            self._runtime_suspended = False
            cmds.evalDeferred(_restore_after_failed_update)
        self._set_settings_controls_enabled(True)
        QtWidgets.QMessageBox.warning(self, "AnimKey Update", message)
