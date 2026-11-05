"""Protected release tooling with ephemeral keys and an isolated HTTP double."""
import hashlib
import json
from pathlib import Path
import urllib.error
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,PublicFormat,NoEncryption
from spot_os.release_tools import release
from spot_os.publish_release import publish
from spot_os.update_contract import verify,REPOSITORY


@pytest.fixture
def assets(tmp_path):
    key=Ed25519PrivateKey.generate()
    private=tmp_path/'test-private.pem';public=tmp_path/'test-public.pem'
    private.write_bytes(key.private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))
    folder=tmp_path/'release'
    result=release(folder,'a'*40,106,'0.1.4','0.1.4','prerelease','Ephemeral test only',private,public)
    return folder,private,public,result


def test_signed_release_builder_keeps_private_identity_outside_artifacts(assets):
    import zipfile
    folder,private,public,result=assets
    data=verify((folder/'os-update.json').read_bytes(),(folder/'os-update.json.sig').read_bytes(),public.read_bytes())
    assert data['source_commit']=='a'*40 and result['status']=='signed-candidate'
    with zipfile.ZipFile(folder/data['asset']) as archive:
        assert 'spot_os/LICENSE' in archive.namelist()
        assert all('test-private' not in n and '/tests/' not in n and 'publish_release' not in n for n in archive.namelist())
        assert all(private.read_bytes() not in archive.read(n) for n in archive.namelist())


def test_unsigned_preparation_never_fabricates_signature(tmp_path):
    result=release(tmp_path,'a'*40,106,'0.1.4','0.1.4')
    assert result['signed'] is False and result['status']=='unsigned-preparation'
    assert not (tmp_path/'os-update.json.sig').exists()


def test_signer_must_match_explicit_public_key(assets,tmp_path):
    folder,private,public,_=assets
    public.write_bytes(Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))
    with pytest.raises(ValueError,match='differs'):release(tmp_path/'other','a'*40,106,'0.1.4','0.1.4',signing_key=private,public_key=public)


@pytest.mark.parametrize('exists',[None,'release','tag'])
def test_publisher_fixed_channel_four_assets_and_immutable_tags(assets,monkeypatch,exists):
    folder,_,public,_=assets;calls=[]
    class Response:
        def __init__(self,value):self.value=value
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):return json.dumps(self.value).encode()
    class Opener:
        def open(self,req,**kwargs):
            calls.append(req)
            url=req.full_url
            assert url.startswith(('https://api.github.com/repos/'+REPOSITORY+'/',
                                   'https://uploads.github.com/repos/'+REPOSITORY+'/'))
            if '/releases/tags/' in url or '/git/ref/tags/' in url:
                if (exists=='release' and '/releases/tags/' in url or exists=='tag' and '/git/ref/tags/' in url):return Response({'id':1})
                raise urllib.error.HTTPError(url,404,'absent',{},None)
            if '?per_page=' in url:return Response([])
            return Response(dict(id=17,tag_name='os-v'+__import__('spot_os').VERSION,prerelease=True,html_url='https://github.com/'+REPOSITORY+'/releases/tag/os-v0.1.6'))
    monkeypatch.setattr('urllib.request.build_opener',lambda *args:Opener())
    if exists:
        with pytest.raises(ValueError,match='overwritten'):publish(folder,public,'fake-publisher')
        assert all(r.get_method()=='GET' for r in calls)
    else:
        assert publish(folder,public,'fake-publisher')['prerelease']
        uploads=[r for r in calls if r.full_url.startswith('https://uploads.github.com/')]
        assert len(uploads)==4
        assert {r.full_url.split('?name=')[1] for r in uploads}=={'spot-os-'+__import__('spot_os').VERSION+'.zip','os-update.json','os-update.json.sig','SHA256SUMS.txt'}


def test_publisher_rechecks_checksums_before_any_network_action(assets,monkeypatch):
    folder,_,public,_=assets
    (folder/'SHA256SUMS.txt').write_text('bad')
    monkeypatch.setattr('urllib.request.build_opener',lambda *args:pytest.fail('must refuse before network'))
    with pytest.raises(ValueError,match='checksums'):publish(folder,public,'fake-publisher')
