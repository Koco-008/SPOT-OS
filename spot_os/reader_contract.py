"""OS safety fuse for an application rollback to a legacy negotiating reader.

The actual transport/recovery belongs to signed app >=0.28.11, which also works
on OS 0.1.2. No new privileged recovery service or camera reset is needed.
"""
import ast
import hashlib


def inspect(folder, manifest):
    try:
        for name in ('SPOT_v27.py','spot_v4l2.py'):
            raw=(folder/name).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=manifest.get('files',{}).get(name):return False
        tree=ast.parse((folder/'SPOT_v27.py').read_text(encoding='utf-8'))
        stream=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LeptonStream')
        return any(isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='producer_protocol' for t in n.targets)
                   and isinstance(n.value,ast.Constant) and n.value.value==1 for n in stream.body)
    except (OSError,ValueError,SyntaxError,StopIteration):
        return False


def guard(module):
    """Called before window construction, never after a reader has opened."""
    stream=getattr(module,'LeptonStream',None)
    if stream is None or getattr(stream,'producer_protocol',None)==1:return True
    def disabled(self):
        self._clear_frames()
        self._set_error('Kameraschutz: SPOT-App 0.28.11 oder neuer erforderlich')
        self.reopen_done.set()
    stream.start=disabled
    # A legacy manual recovery/standby wake must not start an unsafe reader.
    def unavailable(self,*args,**kwargs):
        raise RuntimeError('Kameraschutz: zuerst signierte SPOT-App 0.28.11 installieren')
    stream.reopen=unavailable
    stream.request_reopen=lambda self:disabled(self)
    return False
