"""Camera/UI double, with the REAL signed Handoff in a separate process.

Only this isolated fixture passes enforce_platform=False and uses a throwaway
Ed25519 test key. Production launch/update code and its nonce/locks are unchanged.
"""
CONTROLLER = '''
import json,os,subprocess,sys,time
from pathlib import Path
from PySide6.QtCore import QObject,Signal,QTimer
class AppUpdateController(QObject):
 changed=Signal()
 def __init__(self,window):
  super().__init__(window);self.window=window;self.stage=None;self.activating=False
  from types import SimpleNamespace
  self.jobs=SimpleNamespace(active=set())
  self.activity=lambda:False;self.status='';self.helper=None;self.close_ticks=0
  self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(100)
 def activate(self):
  if not self.stage or self.activating or self.activity():return
  self.activating=True
  code="from spot_handoff import run_handoff;import sys;run_handoff(sys.argv[1],False,timeout=5)"
  self.helper=subprocess.Popen([sys.executable,'-c',code,str(self.stage)],start_new_session=True)
 def tick(self):
  trigger=Path(os.environ['SPOT_TEST_TRIGGER'])
  if not self.activating and trigger.exists():
   request=json.loads(trigger.read_text());trigger.unlink();self.stage=Path(request['directory']);self.activate()
  if self.activating:
   status=self.stage/'status.json'
   if status.exists() and json.loads(status.read_text()).get('phase')=='waiting_for_app_exit':
    self.close_ticks+=1
    if self.close_ticks>=5:
     with Path(os.environ['SPOT_TEST_EVENTS']).open('a') as stream:
      stream.write(json.dumps(dict(event='old_gui_closing',at=time.monotonic(),pid=os.getpid()))+'\\n')
     self.window.close()
   elif self.helper.poll() is not None:self.activating=False
'''

GUI = '''
import os,sys,time,json
from pathlib import Path
from PySide6.QtWidgets import QApplication,QMainWindow,QLabel
from PySide6.QtCore import QTimer
from types import SimpleNamespace as S
from spot_update_ui import AppUpdateController
from spot_runtime import BootReadiness
class SpotThermalWindow(QMainWindow):
 def __init__(self):
  super().__init__();self.setCentralWidget(QLabel('SPOT OS GUI double - camera unavailable'))
  self.resize(800,480);self.thermal_view=S(device_status='')
  self.device_controls=S(power=S(state=S(value='active')))
  self.app_updates=AppUpdateController(self);self.picam3=S();self.frame_timer=QTimer(self)
  self.lepton_monitor=S(health=lambda:S(permits=lambda _:False))
  self.stream=S(capture_monotonic=0);self.camera_timer=QTimer(self)
  def camera_tick():
   path=Path(os.environ['SPOT_TEST_TRIGGER']).with_name('camera.json')
   value=json.loads(path.read_text()) if path.exists() else {}
   if value.get('fresh'):self.stream.capture_monotonic=time.monotonic()
   self.lepton_monitor=S(health=lambda:S(permits=lambda stamp:value.get('available',False) and 0<=time.monotonic()-stamp<1))
  self.camera_timer.timeout.connect(camera_tick);self.camera_timer.start(30)
  self.boot_readiness=BootReadiness(self)
  self.boot_timer=QTimer(self);self.boot_timer.timeout.connect(lambda:self.boot_readiness.tick(False));self.boot_timer.start(30)
 def _update_activity(self): return False
app=QApplication([]);w=SpotThermalWindow();w.showFullScreen();sys.exit(app.exec())
'''
