"""Root-owned disposable import tree; hostile CWD/PYTHONPATH never executed."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/spot-os-0.1.7'


def run(*args,check=True,cwd=None):
    result=subprocess.run(args,capture_output=True,text=True,timeout=30,cwd=cwd)
    if check and result.returncode:raise RuntimeError(str(args)+': '+result.stderr[-4000:]+result.stdout[-1000:])
    return result


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    base=Path('/run/spot-test-cli-'+uuid.uuid4().hex)
    runtime='spot-test-updater-'+uuid.uuid4().hex
    unit=runtime+'.service'
    evidence=dict(real_root=True,hostile_code_executed=False,cases=[])
    with tempfile.TemporaryDirectory(prefix='spot-test-hostile-cwd-') as directory:
        directory=Path(directory);os.chmod(directory,0o755)
        marker=directory/'ATTACK_EXECUTED'
        hostile=directory/'spot_os';hostile.mkdir()
        (hostile/'__init__.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("UNSAFE ROOT IMPORT")\n')
        (directory/'sitecustomize.py').write_text((hostile/'__init__.py').read_text())
        cli=directory/'trusted_cli.py'
        cli.write_text('import json,sys,os\ndef main():\n print(json.dumps(dict(installed=True,uid=os.geteuid(),path=__file__,cwd=os.getcwd())))\n return 0\n')
        empty=directory/'empty';empty.write_text('')
        try:
            run('sudo','install','-d','-m','755',str(base/'0.1.4'))
            run('sudo','install','-m','644',str(ROOT/'spot_os/admin_entry.py'),str(base/'admin_entry.py'))
            run('sudo','install','-m','644',str(empty),str(base/'0.1.4/__init__.py'))
            run('sudo','install','-m','644',str(cli),str(base/'0.1.4/cli.py'))
            run('sudo','ln','-s','0.1.4',str(base/'spot_os'))
            for cwd in (directory,hostile,ROOT):
                result=run('sudo','env','PYTHONPATH='+str(directory),'PYTHONSTARTUP='+str(directory/'sitecustomize.py'),
                    '/usr/bin/python3','-I',str(base/'admin_entry.py'),'static-check',cwd=cwd)
                info=json.loads(result.stdout)
                assert info['uid']==0 and info['installed'] and str(base) in info['path']
                assert not marker.exists()
                evidence['cases'].append(dict(cwd=str(cwd),installed_code_only=True))
            run('sudo','chmod','777',str(base/'0.1.4/cli.py'))
            refused=run('sudo','/usr/bin/python3','-I',str(base/'admin_entry.py'),'status',check=False,cwd=directory)
            assert refused.returncode and 'Untrusted' in refused.stderr and not marker.exists()
            evidence['cases'].append(dict(writable_installed_code_rejected=True))
            unsafe=run('sudo','/usr/bin/python3',str(base/'admin_entry.py'),'status',check=False,cwd=directory)
            assert unsafe.returncode and 'requires python3 -I' in unsafe.stderr
            evidence['cases'].append(dict(nonisolated_entry_rejected=True))
            # Parse the real new service/socket settings without loading either
            # production unit or requiring production installation paths.
            service=(ROOT/'spot_os/config/spot-os-update.service').read_text()
            service=service.replace('ExecStart=/usr/bin/python3 -I /usr/local/lib/spot-os/admin_entry.py --update-service','ExecStart=/usr/bin/true')
            service=service.replace('spot-os-update.socket','spot-test-update.socket')
            socket_unit=(ROOT/'spot_os/config/spot-os-update.socket').read_text().replace('@GID@','0')
            socket_unit=socket_unit.replace('Service=spot-os-update.service','Service=spot-test-update.service')
            (directory/'spot-test-update.service').write_text(service)
            (directory/'spot-test-update.socket').write_text(socket_unit)
            run('systemd-analyze','verify',str(directory/'spot-test-update.service'),str(directory/'spot-test-update.socket'))
            evidence['cases'].append(dict(actual_update_units_parse=True))
            # Exercise the actual root sandbox settings in a disposable unit:
            # the existing installer must update its /run boot-status file, and
            # unrelated /run directories must stay read-only. Never load a
            # production unit or touch its paths.
            body=service.split('[Service]\n',1)[1].split('[Install]',1)[0]
            program='from pathlib import Path; p=Path('+repr('/run/'+runtime+'/probe')+'); p.write_text("OK"); denied=False\ntry: Path('+repr(str(base/'outside'))+').write_text("BAD")\nexcept OSError: denied=True\nassert denied'
            lines=[]
            for line in body.splitlines():
                # RestartForceExitStatus belongs to the real notify daemon;
                # systemd rejects it on this one-shot namespace probe.
                if line.startswith(('RestartForceExitStatus=','SuccessExitStatus=')):continue
                if line.startswith('Type='):line='Type=oneshot'
                elif line.startswith('NotifyAccess='):continue
                elif line.startswith('ExecStart='):line='ExecStart=/usr/bin/python3 -I -c '+json.dumps(program)
                elif line.startswith('Restart='):line='Restart=no'
                elif line.startswith('StateDirectory='):line='StateDirectory='+runtime
                elif line.startswith('RuntimeDirectory='):line='RuntimeDirectory='+runtime
                elif line.startswith('ReadWritePaths='):line='ReadWritePaths=/run/'+runtime+' /var/lib/'+runtime
                lines.append(line)
            # systemd ExecStart is not a shell: escape embedded newlines as the
            # documented C-style sequence inside a double-quoted argument.
            sandbox=directory/unit;sandbox.write_text('[Unit]\nDescription=Disposable SPOT updater sandbox\n[Service]\n'+'\n'.join(lines)+'\n')
            run('sudo','install','-m','644',str(sandbox),'/etc/systemd/system/'+unit)
            run('sudo','systemctl','daemon-reload')
            started=run('sudo','systemctl','start',unit,check=False)
            if started.returncode:
                raise RuntimeError(run('sudo','journalctl','-u',unit,'--no-pager').stdout)
            assert run('sudo','cat','/run/'+runtime+'/probe').stdout=='OK'
            assert not (base/'outside').exists()
            run('sudo','systemctl','stop',unit)
            assert run('sudo','cat','/run/'+runtime+'/probe').stdout=='OK'
            evidence['cases'].append(dict(real_root_sandbox_runtime_writable=True,outside_runtime_read_only=True,runtime_preserved_on_stop=True))
            evidence['passed']=True
        finally:
            # Exact predeclared /run test root, no computed external tree deletion.
            assert str(base).startswith('/run/spot-test-cli-') and base.name.split('-')[-1].isalnum()
            (OUT/'admin-sandbox-journal.log').write_text(run('sudo','journalctl','-u',unit,'--no-pager',check=False).stdout)
            if (directory/unit).exists():(OUT/'admin-sandbox-unit.txt').write_bytes((directory/unit).read_bytes())
            run('sudo','systemctl','stop',unit,check=False)
            run('sudo','rm','-f','/etc/systemd/system/'+unit,check=False)
            assert runtime.startswith('spot-test-updater-') and runtime.removeprefix('spot-test-updater-').isalnum()
            run('sudo','rm','-rf','--','/run/'+runtime,'/var/lib/'+runtime,check=False)
            run('sudo','systemctl','daemon-reload',check=False)
            run('sudo','rm','-rf','--',str(base),check=False)
            (OUT/'admin-cli-proof.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(evidence,indent=2))


if __name__=='__main__':main()
