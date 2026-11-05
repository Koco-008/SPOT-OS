"""OS-only, exact-schema Ed25519 contract. Never execute manifest commands."""
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import re
import stat
import zipfile

REPOSITORY='Koco-008/SPOT-OS-Releases'
PLATFORM='raspios-debian13-aarch64'
MAX_ARCHIVE=32*1024**2
MAX_UNPACKED=64*1024**2
FIELDS={'schema','kind','repository','platform','tag','version','revision','sequence',
        'asset','size','sha256','source_commit','channel','notes','requires_os_min',
        'requires_os_max','requires_app_min','reader_protocol','engine','reboot'}

class IncompatibleOS(ValueError):pass


def version(text):
    if not isinstance(text,str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)',text):
        raise ValueError('Invalid OS version')
    return tuple(map(int,text.split('.')))


def strict_json(raw):
    def pairs(values):
        result={}
        for key,value in values:
            if key in result:raise ValueError('Duplicate JSON field')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Invalid JSON constant')))


def manifest(raw):
    if not isinstance(raw,bytes) or len(raw)>65536:raise ValueError('OS manifest size')
    data=strict_json(raw)
    if type(data) is not dict or set(data)!=FIELDS:raise ValueError('OS manifest schema')
    if type(data['schema']) is not int or data['schema']!=1 or data['kind']!='spot-os':raise ValueError('Wrong manifest type')
    if data['repository']!=REPOSITORY or data['platform']!=PLATFORM:raise ValueError('Wrong OS release origin/platform')
    release=data['version']
    if version(release)[0]!=0:raise ValueError('OS major generation requires a new reviewed contract')
    if data['tag']!='os-v'+release or data['asset']!='spot-os-'+release+'.zip':raise ValueError('OS tag/asset mismatch')
    if not isinstance(data['revision'],str) or not re.fullmatch('[a-z0-9-]{1,64}',data['revision']):raise ValueError('OS revision')
    if type(data['sequence']) is not int or not 0<data['sequence']<2**53:raise ValueError('OS sequence')
    if type(data['size']) is not int or not 0<data['size']<=MAX_ARCHIVE:raise ValueError('OS size')
    if not isinstance(data['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',data['sha256']):raise ValueError('OS SHA256')
    if not isinstance(data['source_commit'],str) or not re.fullmatch('[a-f0-9]{40}',data['source_commit']):raise ValueError('OS source commit')
    if data['channel'] not in ('stable','prerelease'):raise ValueError('OS channel')
    if not isinstance(data['notes'],str) or len(data['notes'])>1500:raise ValueError('OS release notes')
    if data['reboot'] is not False:raise ValueError('Reboot updates require a future explicitly reviewed contract')
    if type(data['engine']) is not int or data['engine']!=1 or type(data['reader_protocol']) is not int or data['reader_protocol']!=1:
        raise ValueError('Unsupported OS engine/reader contract')
    if version(data['requires_os_min'])>version(data['requires_os_max']):raise ValueError('OS compatibility range')
    version(data['requires_app_min'])
    return data


def verify(raw,signature,key_bytes):
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(signature,bytes) or len(signature)!=64:raise ValueError('OS signature size')
    key=load_pem_public_key(key_bytes)
    if not isinstance(key,Ed25519PublicKey):raise ValueError('OS Ed25519 key required')
    key.verify(signature,raw)
    return manifest(raw)


def compatible(data,installed,app,floor,prerelease=False):
    if data['channel']=='prerelease' and not prerelease:raise ValueError('OS pre-release not enabled')
    if not version(data['requires_os_min'])<=version(installed)<=version(data['requires_os_max']):
        raise IncompatibleOS('OS bootstrap/engine compatibility differs')
    if version(app)<version(data['requires_app_min']):raise IncompatibleOS('App update required before OS update')
    if version(data['version'])<=max(version(installed),version(floor.get('version','0.0.0'))):raise ValueError('OS downgrade/replay refused')
    if data['sequence']<=floor.get('sequence',0):raise ValueError('OS sequence replay refused')
    return data


def trusted_file(path,maximum=65536):
    """Root-controlled fixed paths only. Never read a GUI-supplied path."""
    path=Path(path)
    for parent in path.parents:
        info=parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
            raise PermissionError('Untrusted OS state parent')
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or info.st_size>maximum:
            raise PermissionError('Untrusted OS state file')
        result=stream.read(maximum+1)
        if len(result)>maximum:raise ValueError('Oversized OS state')
        return result


def trust(directory=Path('/etc/spot-os')):
    key=trusted_file(directory/'os-release.pub',4096)
    pinned=strict_json(trusted_file(directory/'os-trust.json',4096))
    if set(pinned)!={'schema','repository','sha256'} or type(pinned['schema']) is not int or pinned['schema']!=1 or pinned['repository']!=REPOSITORY:
        raise ValueError('OS trust configuration')
    if hashlib.sha256(key).hexdigest()!=pinned['sha256']:raise ValueError('OS trust key fingerprint differs')
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(load_pem_public_key(key),Ed25519PublicKey):raise ValueError('OS key type')
    return key


def unpack(raw,data,destination):
    """Validate EVERY member before creating files. No extractall or symlinks."""
    if len(raw)!=data['size'] or hashlib.sha256(raw).hexdigest()!=data['sha256']:raise ValueError('OS archive hash/size')
    payload={};total=0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos=archive.infolist()
        if len(infos)>500:raise ValueError('Too many OS archive members')
        for item in infos:
            name=item.filename;path=PurePosixPath(name)
            if (str(path)!=name or '\\' in name or ':' in name or not name.startswith('spot_os/') or
                path.is_absolute() or '..' in path.parts or item.is_dir() or item.flag_bits&1 or
                any(part.startswith('.') or part in ('tests','__pycache__') for part in path.parts) or
                stat.S_IFMT(item.external_attr>>16) not in (0,stat.S_IFREG) or name in payload):
                raise ValueError('Unsafe OS archive member')
            total+=item.file_size
            if total>MAX_UNPACKED or item.file_size>8*1024**2:raise ValueError('OS unpack limit')
            payload[name]=archive.read(item)
    internal=strict_json(payload.get('spot_os/PACKAGE.json',b'{}'))
    if (set(internal)!={'schema','kind','version','revision','app_version','base_commit','files'} or
        internal['schema']!=1 or internal['kind']!='os-installer' or internal['version']!=data['version'] or
        internal['revision']!=data['revision'] or internal['app_version']!='unchanged'):
        raise ValueError('OS internal manifest differs')
    hashes=internal['files']
    if not isinstance(hashes,dict) or set(hashes)!=set(payload)-{'spot_os/PACKAGE.json'}:raise ValueError('OS member set differs')
    for name,digest in hashes.items():
        if not isinstance(digest,str) or hashlib.sha256(payload[name]).hexdigest()!=digest:raise ValueError('OS member digest differs')
    required={'spot_os/'+name for name in ('__init__.py','VERSION','cli.py','admin_entry.py','installer.py','system.py',
        'manager.py','supervisor.py','qt_bridge.py','update_engine.py','update_contract.py','update_backend.py',
        'update_service.py','update_source.py','config/spot-os-session.service','config/spot-os-update.socket',
        'config/spot-os-update.service','native/Makefile')}
    if not required.issubset(payload):raise ValueError('Incomplete OS runtime package')
    if payload['spot_os/VERSION'].decode().strip()!=data['version']:raise ValueError('OS version file differs')
    import ast
    tree=ast.parse(payload.get('spot_os/__init__.py',b''))
    constants={node.targets[0].id:node.value.value for node in tree.body if isinstance(node,ast.Assign)
        and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and isinstance(node.value,ast.Constant)}
    if constants.get('VERSION')!=data['version'] or constants.get('REVISION')!=data['revision']:raise ValueError('OS source identity differs')
    destination=Path(destination)
    destination.mkdir(mode=0o700)  # caller owns root-private parent; refuse reuse
    for name,content in payload.items():
        target=destination/name
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno())
    return destination/'spot_os'
