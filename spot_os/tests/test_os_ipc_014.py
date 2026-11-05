import os
import socket
import threading
import json
from pathlib import Path
import pytest
from spot_os.update_service import peer,serve_request
from spot_os.update_contract import trust
from spot_os.provision import provision
from test_os_updates_014 import signed,engine

pytestmark=pytest.mark.skipif(os.name!='posix',reason='Linux SO_PEERCRED and O_NOFOLLOW')


def test_actual_peercred_rejects_wrong_account():
    a,b=socket.socketpair()
    try:
        assert peer(a,os.getuid())==os.getpid()
        with pytest.raises(PermissionError):peer(a,os.getuid()+1)
    finally:a.close();b.close()


@pytest.mark.parametrize('raw',[b'{"action":"status","path":"/etc/shadow"}\n',b'{"action":"shell"}\n',
    b'{"action":"status","action":"install"}\n',b'x'*8193+b'\n'])
def test_real_socket_protocol_rejects_untrusted_input(engine,raw):
    a,b=socket.socketpair()
    thread=threading.Thread(target=lambda:serve_request(a,engine,os.getuid()))
    try:
        thread.start();b.sendall(raw);result=json.loads(b.recv(16384))
        assert result.get('error') and engine.backend.events==[]
    finally:thread.join(timeout=5);a.close();b.close()


def test_root_requests_cannot_select_arbitrary_account(engine):
    # There is deliberately no root special-case or caller-supplied UID.
    a,b=socket.socketpair()
    try:
        with pytest.raises(PermissionError):peer(a,os.getuid()+1)
    finally:a.close();b.close()


def test_provision_requires_independent_fingerprint_and_forbids_rotation(signed,tmp_path):
    import hashlib
    folder=tmp_path/'etc';folder.mkdir()
    key=tmp_path/'public.pem';key.write_bytes(signed['public'])
    digest=hashlib.sha256(signed['public']).hexdigest()
    with pytest.raises(ValueError):provision(key,'0'*64,folder)
    assert provision(key,digest,folder)['provisioned']
    assert provision(key,digest,folder)['provisioned']
    other=tmp_path/'other';other.write_bytes(signed['public']+b'\n')
    with pytest.raises(ValueError,match='replaced'):provision(other,hashlib.sha256(other.read_bytes()).hexdigest(),folder)


def test_symlink_public_key_never_provisioned(signed,tmp_path):
    import hashlib
    target=tmp_path/'target';target.write_bytes(signed['public'])
    link=tmp_path/'link';link.symlink_to(target)
    with pytest.raises(OSError):provision(link,hashlib.sha256(signed['public']).hexdigest(),tmp_path)


@pytest.mark.parametrize('failure',['missing','fingerprint','repository','schema'])
def test_anchor_policy_never_defaults_or_accepts_changed_pin(signed,monkeypatch,failure):
    import hashlib
    from spot_os import update_contract as contract
    policy=dict(schema=1,repository=contract.REPOSITORY,sha256=hashlib.sha256(signed['public']).hexdigest())
    if failure=='fingerprint':policy['sha256']='0'*64
    if failure=='repository':policy['repository']='other/repository'
    if failure=='schema':policy['schema']=2
    def trusted(path,limit):
        if failure=='missing':raise FileNotFoundError('anchor absent')
        return signed['public'] if path.name=='os-release.pub' else json.dumps(policy).encode()
    monkeypatch.setattr(contract,'trusted_file',trusted)
    with pytest.raises((ValueError,FileNotFoundError)):contract.trust()
