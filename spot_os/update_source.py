"""Tokenless, bounded HTTPS transport for the fixed public OS release channel."""
import json
import time
import urllib.request
from urllib.parse import urlparse
from .update_contract import REPOSITORY,MAX_ARCHIVE,verify,compatible,version,IncompatibleOS


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,url):
        check_url(url)
        return super().redirect_request(request,fp,code,msg,headers,url)


def check_url(url):
    parsed=urlparse(url)
    if (parsed.scheme!='https' or parsed.port not in (None,443) or parsed.username or parsed.password or
        not (parsed.hostname=='api.github.com' or (parsed.hostname or '').endswith('.githubusercontent.com'))):
        raise ValueError('Untrusted OS download redirect')


class Source:
    def __init__(self,key,progress=lambda *_:None):
        self.key,self.progress=key,progress

    def get(self,path,limit,binary=False):
        if not path.startswith('repos/'+REPOSITORY+'/releases'):raise ValueError('OS API path')
        request=urllib.request.Request('https://api.github.com/'+path,headers={
            'Accept':'application/octet-stream' if binary else 'application/vnd.github+json','User-Agent':'SPOT-OS-Updater/1'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),Redirect())
        deadline=time.monotonic()+120
        with opener.open(request,timeout=30) as stream:
            check_url(stream.url)
            data=bytearray()
            while True:
                if time.monotonic()>deadline:raise TimeoutError('OS download deadline')
                block=stream.read(min(65536,limit+1-len(data)))
                if not block:break
                data.extend(block)
                if len(data)>limit:raise ValueError('OS download size limit')
                if limit==MAX_ARCHIVE:self.progress('downloading',len(data))
            return bytes(data)

    def asset(self,release,name,limit):
        matches=[a for a in release.get('assets',[]) if a.get('name')==name]
        if len(matches)!=1 or type(matches[0].get('size')) is not int or not 0<matches[0]['size']<=limit:
            raise ValueError('Missing/oversized OS release asset')
        asset=matches[0]
        if type(asset.get('id')) is not int or asset['id']<=0:raise ValueError('OS asset ID')
        return self.get(f"repos/{REPOSITORY}/releases/assets/{asset['id']}",limit,True)

    def check(self,installed,app,floor,prerelease=False):
        releases=json.loads(self.get(f'repos/{REPOSITORY}/releases?per_page=100',2_000_000))
        if not isinstance(releases,list) or len(releases)>100:raise ValueError('OS release listing')
        candidates=[];incompatible=[]
        for release in releases:
            if release.get('draft') or release.get('prerelease') and not prerelease:continue
            try:
                tag=release.get('tag_name','')
                if not tag.startswith('os-v'):continue
                if version(tag[4:])>version(installed):candidates.append(release)
            except ValueError:continue
        for release in sorted(candidates,key=lambda r:version(r['tag_name'][4:]),reverse=True):
            raw=self.asset(release,'os-update.json',65536)
            sig=self.asset(release,'os-update.json.sig',64)
            data=verify(raw,sig,self.key)
            if release['tag_name']!=data['tag'] or bool(release.get('prerelease'))!=(data['channel']=='prerelease'):
                raise ValueError('OS GitHub tag/channel differs from signature')
            try:compatible(data,installed,app,floor,prerelease)
            except IncompatibleOS as exc:incompatible.append(str(exc));continue
            assets=[a for a in release.get('assets',[]) if a.get('name')==data['asset']]
            if len(assets)!=1 or assets[0].get('size')!=data['size']:raise ValueError('OS ZIP asset size')
            return dict(manifest=data,raw=raw,signature=sig,release=release)
        if incompatible:raise IncompatibleOS('Keine kompatible OS-Freigabe: '+', '.join(sorted(set(incompatible))))
        return None

    def download(self,candidate):
        return self.asset(candidate['release'],candidate['manifest']['asset'],MAX_ARCHIVE)
