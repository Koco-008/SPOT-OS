"""Isolated CI producer/consumer doubles; never installed on the instrument."""
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from spot_os.camera_ready import wait_for_format
from spot_os.common import atomic
from spot_os.supervisor import notify
from spot_os.system import System


def main():
    mode=sys.argv[1]
    base=Path(os.environ['SPOT_ORDERING_FIXTURE'])
    case=json.loads((base/'case.json').read_text())
    if mode=='producer':
        if case['name']=='streamer-failed':return 7
        fd=os.open('/dev/null',os.O_WRONLY)
        try:
            time.sleep(case['delay'])
            atomic(base/'format.json',dict(width=160,height=120,fourcc='Y16 ',
                bytesperline=320,sizeimage=38400,at=time.monotonic()),0o644)
            while True:time.sleep(.2)
        finally:os.close(fd)
    if mode=='probe':
        def reader(_):
            if case['name']=='hung-probe':time.sleep(300)
            try:return json.loads((base/'format.json').read_text())
            except FileNotFoundError:return dict(width=640,height=480,fourcc='BGR3')
        result=wait_for_format(System(),unit=os.environ['SPOT_ORDERING_PRODUCER'],device='/dev/null',
            timeout=case['budget'],interval=.05,format_reader=reader)
        result['at']=time.monotonic()
        atomic(base/'probe.json',result,0o644)
        return 0
    if mode=='consumer':
        try:probe=json.loads((base/'probe.json').read_text())
        except FileNotFoundError:probe={}
        atomic(base/'consumer.json',dict(at=time.monotonic(),gui=True,format_ready=probe.get('format_ready',False),
            camera_error=not probe.get('format_ready',False),pid=os.getpid()),0o644)
        notify('READY=1')
        while True:time.sleep(.2)
    raise ValueError(mode)


if __name__=='__main__':sys.exit(main())
