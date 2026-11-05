"""OS-only adapter for signed SPOT, including a legacy-reader safety fuse; loaded before its QApplication exists.

Observation never acknowledges the signed updater on its behalf. Extra AND-gates
require presented cover before activation and mapped/rendered GUI before its
existing BootReadiness can report gui_ready. Camera, nonce and commit stay native.
"""
import json
import os
from pathlib import Path
import time
import signal


def install():
    from PySide6 import QtWidgets
    from PySide6.QtCore import QObject, QEvent, QTimer, Qt
    from PySide6.QtNetwork import QLocalSocket
    original_application = QtWidgets.QApplication

    class Bridge(QObject):
        def __init__(self, app):
            super().__init__(app)
            self.app = app
            self.window = None
            self.paints = 0
            self.probes = 0
            self.rendered = False
            self.os_gui_prepared = False
            self.shutdown_requested = None
            # The stable launcher retains app.lock while Qt's existing closeEvent
            # drains recording/file workers. No forced kill or lock deletion.
            signal.signal(signal.SIGTERM,lambda *_:setattr(self,'shutdown_requested',time.monotonic()))
            self.buffer = b''
            self.activation = None
            self.covering_controller = None
            self.deadline = 0
            self.socket = QLocalSocket(self)
            self.socket.readyRead.connect(self.receive)
            self.socket.connectToServer(os.environ['SPOT_OS_SOCKET'])
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(200)
            app.installEventFilter(self)
            app.setDesktopFileName('spot-app')
            app.setOverrideCursor(Qt.CursorShape.BlankCursor)
            import spot_update_ui
            from spot_runtime import BootReadiness
            original_tick = BootReadiness.tick
            bridge = self

            class ReadinessWindow:
                def __init__(self,window): self.actual=window
                def __getattr__(self,name): return getattr(self.actual,name)
                def isVisible(self):
                    # Extra AND-gate: never manufacture nonce/camera/commit success.
                    return self.actual.isVisible() and bridge.os_gui_prepared

            def gated_tick(boot,camera_ready):
                if not boot.directory:
                    return original_tick(boot,camera_ready)
                actual=boot.window
                boot.window=ReadinessWindow(actual)
                try:
                    return original_tick(boot,camera_ready)
                finally:
                    boot.window=actual

            BootReadiness.tick=gated_tick
            activate = spot_update_ui.AppUpdateController.activate

            def covered_activate(controller):
                if not controller.stage or controller.activating or controller.activity():
                    return activate(controller)
                if self.activation:
                    return
                self.activation = (controller, activate)
                self.deadline = time.monotonic() + 8
                self.send(dict(command='cover', purpose='update'))
                controller.status = 'SPOT-Startbild wird vorbereitet …'
                controller.changed.emit()

            spot_update_ui.AppUpdateController.activate = covered_activate

        def eventFilter(self, obj, event):
            if obj is self and event.type() == QEvent.Type.User:
                self.probes += 1
            if isinstance(obj, QtWidgets.QMainWindow) and hasattr(obj, 'app_updates'):
                self.window = obj
            if (event.type() == QEvent.Type.Paint and isinstance(obj, QtWidgets.QWidget)
                    and self.window is not None and obj.window() is self.window):
                self.paints += 1
            return False

        def send(self, value):
            from .common import encode
            value.update(token=os.environ['SPOT_OS_TOKEN'], pid=os.getpid())
            if self.socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
                self.socket.write(encode(value))

        def receive(self):
            self.buffer += bytes(self.socket.readAll())
            if len(self.buffer) > 16384:
                self.socket.abort()
                return
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                message = json.loads(line)
                if message.get('os_update_shutdown') is True and self.window is not None:
                    window=self.window
                    controller=getattr(window,'os_updates',None)
                    if (controller is not None and not window._update_activity() and
                            not window.app_updates.jobs.active and not window.app_updates.activating and not window.app_updates.stage):
                        controller.reserved=True
                        controller.changed.emit()
                        window.close()
                if message.get('gui_prepared') is True:
                    self.os_gui_prepared = True
                if message.get('covered') and self.activation:
                    controller, activate = self.activation
                    self.activation = None
                    # Rechecks activity and context at the actual activation moment.
                    activate(controller)
                    self.covering_controller = controller

        def tick(self):
            self.app.postEvent(self, QEvent(QEvent.Type.User))
            if self.activation and time.monotonic() > self.deadline:
                controller, _ = self.activation
                self.activation = None
                controller.status = 'Startbild nicht bestätigt; Anwendung bleibt geöffnet'
                controller.changed.emit()
                self.send(dict(command='cancel_cover'))
            if self.covering_controller and not self.covering_controller.activating:
                self.send(dict(command='cancel_cover'))
                self.covering_controller = None
            window = self.window
            if window is None:
                window = next((widget for widget in self.app.topLevelWidgets()
                    if isinstance(widget,QtWidgets.QMainWindow) and hasattr(widget,'app_updates')),None)
                self.window = window
            if not window:
                return
            handle = window.windowHandle()
            if not self.rendered and self.probes >= 2 and window.isVisible():
                # QWidget.grab forces the fully laid-out widget tree to paint into
                # its backing store even while the independent overlay occludes it.
                image=window.grab()
                self.rendered=not image.isNull() and image.width()>=800 and image.height()>=480 and self.paints>=2
            camera = False
            try:
                camera = window.lepton_monitor.health().permits(window.stream.capture_monotonic)
            except (AttributeError, TypeError):
                pass
            initialized = all(hasattr(window, name) for name in
                              ('device_controls', 'app_updates', 'lepton_monitor', 'picam3', 'frame_timer'))
            if self.shutdown_requested is not None:
                if initialized:
                    window.close()
                    return
                if time.monotonic()-self.shutdown_requested>3:
                    self.app.quit()
                    return
            boot = getattr(window, 'boot_readiness', None)
            self.send(dict(command='evidence', version=Path(os.sys.argv[0]).resolve().with_name('VERSION').read_text().strip(),
                wayland=self.app.platformName() == 'wayland',
                mapped=bool(handle and handle.isVisible()), painted=self.rendered,
                responsive=self.probes >= 2,
                enabled=bool(window.centralWidget() and window.centralWidget().isEnabled()
                             and not getattr(boot, 'directory', None)),
                initialized=initialized, size=[window.width(), window.height()], camera=bool(camera),
                position=[window.x(),window.y()],
                screen=[window.screen().geometry().width(),window.screen().geometry().height()],
                fullscreen=bool(window.windowState() & Qt.WindowState.WindowFullScreen),
                busy=bool(window._update_activity() or window.app_updates.jobs.active or window.app_updates.activating or getattr(window.app_updates,'stage',None)) if initialized else True,
                power_state=getattr(window.device_controls.power.state,'value','unknown') if initialized else 'unknown',
                sleeping=getattr(window.device_controls.power.state, 'value', '') == 'sleeping'
                    if initialized else False))

    class Application(original_application):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            from .reader_contract import guard
            import sys
            # SPOT defines LeptonStream before constructing QApplication.
            # Legacy app rollback remains operable with camera disabled.
            for name in ('__main__','SPOT_v27'):
                module=sys.modules.get(name)
                if module is not None:guard(module)
            self.spot_os_bridge = Bridge(self)

    QtWidgets.QApplication = Application
