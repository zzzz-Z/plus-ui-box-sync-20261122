#!/usr/bin/env python3
"""Incremental file sync between box and local Mac via GitHub Contents API.

Usage:
  python3 gh_sync.py push  <namespace> <root> <relpath> [relpath ...]
  python3 gh_sync.py push-dir <namespace> <root> <subdir>
  python3 gh_sync.py pull  <namespace> <root>
  python3 gh_sync.py status <namespace>
  python3 gh_sync.py clear <namespace>

Namespace separates directions, e.g. box2local (box edits -> local view)
and local2box (local edits -> box). Only changed files are transferred.
Token: env TOKEN / GH_TOKEN, else ~/.gh-sync-token, else /tmp/boxtransfer/gh_token.txt
"""
import os, sys, json, base64, hashlib, urllib.request, urllib.error, time

REPO = 'zzzz-Z/plus-ui-box-sync-20261122'
API = 'https://api.github.com'
BRANCH = 'main'
PREFIX = 'synctree'


def get_token():
    for key in ('TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN'):
        v = os.environ.get(key)
        if v:
            return v.strip()
    for p in (os.path.expanduser('~/.gh-sync-token'), '/tmp/boxtransfer/gh_token.txt'):
        if os.path.exists(p):
            return open(p).read().strip()
    raise RuntimeError('no token: set TOKEN or write ~/.gh-sync-token')


def api(path, method='GET', data=None, token=None, timeout=120):
    url = API + path
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'gh-sync',
               'X-GitHub-Api-Version': '2022-11-28'}
    if token:
        headers['Authorization'] = 'token ' + token
    body = json.dumps(data).encode() if data is not None else None
    if body:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
                return json.loads(b.decode()) if b else {}
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            if e.code == 404 and method == 'GET':
                return None
            last = 'HTTP %d: %s' % (e.code, err[:180])
            if e.code in (403, 429, 500, 502, 503):
                time.sleep(2 * (attempt + 1)); continue
            raise RuntimeError(last)
        except Exception as e:
            last = '%s: %s' % (type(e).__name__, str(e)[:150])
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(last or 'api failed')


def get_file_bytes(path, token=None):
    d = api('/repos/%s/contents/%s?ref=%s' % (REPO, path, BRANCH), token=token)
    if not isinstance(d, dict):
        return None
    if d.get('encoding') == 'base64' and d.get('content'):
        return base64.b64decode(d['content'])
    sha = d.get('sha')
    if sha:
        b = api('/repos/%s/git/blobs/%s' % (REPO, sha), token=token)
        if b and b.get('encoding') == 'base64':
            return base64.b64decode(b['content'])
    return None


def put_file(path, data, message, token):
    cur = api('/repos/%s/contents/%s?ref=%s' % (REPO, path, BRANCH), token=token)
    payload = {'message': message, 'content': base64.b64encode(data).decode(), 'branch': BRANCH}
    if isinstance(cur, dict) and cur.get('sha'):
        payload['sha'] = cur['sha']
    return api('/repos/%s/contents/%s' % (REPO, path), 'PUT', payload, token=token)


def manifest_path(ns):
    return PREFIX + '/' + ns + '/manifest.json'


def read_manifest(ns, token):
    raw = get_file_bytes(manifest_path(ns), token)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode())
    except Exception:
        return {}


def write_manifest(ns, manifest, token):
    data = json.dumps(manifest, ensure_ascii=False, indent=1).encode()
    put_file(manifest_path(ns), data, 'sync manifest [%s]' % ns, token)


def cmd_push(ns, root, rels, token):
    manifest = read_manifest(ns, token)
    for rel in rels:
        fp = os.path.join(root, rel)
        if not os.path.isfile(fp):
            print('SKIP missing', rel); continue
        data = open(fp, 'rb').read()
        md5 = hashlib.md5(data).hexdigest()
        put_file('%s/%s/%s' % (PREFIX, ns, rel), data, 'sync %s: %s' % (ns, rel), token)
        manifest[rel] = {'md5': md5, 'size': len(data)}
        print('PUSHED', rel, len(data), md5)
    write_manifest(ns, manifest, token)
    print('MANIFEST[%s] files=%d' % (ns, len(manifest)))


def cmd_pull(ns, root, token):
    manifest = read_manifest(ns, token)
    if not manifest:
        print('EMPTY MANIFEST[%s]' % ns); return
    ok = fail = 0
    for rel, meta in manifest.items():
        data = get_file_bytes('%s/%s/%s' % (PREFIX, ns, rel), token)
        if data is None:
            print('FAIL download', rel); fail += 1; continue
        md5 = hashlib.md5(data).hexdigest()
        if md5 != meta.get('md5'):
            print('FAIL md5', rel, md5, '!=', meta.get('md5')); fail += 1; continue
        fp = os.path.join(root, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        open(fp, 'wb').write(data)
        ok += 1
        print('PULLED', rel, len(data))
    print('DONE[%s] ok=%d fail=%d' % (ns, ok, fail))


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    token = get_token()
    if cmd == 'push':
        ns, root, rels = sys.argv[2], sys.argv[3], sys.argv[4:]
        cmd_push(ns, root, rels, token)
    elif cmd == 'push-dir':
        ns, root, sub = sys.argv[2], sys.argv[3], sys.argv[4]
        base = os.path.join(root, sub)
        rels = []
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in ('.git', 'node_modules', '.vite', 'dist')]
            for f in fn:
                rels.append(os.path.relpath(os.path.join(dp, f), root))
        print('files to push =', len(rels))
        cmd_push(ns, root, rels, token)
    elif cmd == 'pull':
        cmd_pull(sys.argv[2], sys.argv[3], token)
    elif cmd == 'status':
        print(json.dumps(read_manifest(sys.argv[2], token), ensure_ascii=False, indent=1))
    elif cmd == 'clear':
        write_manifest(sys.argv[2], {}, token)
        print('cleared', sys.argv[2])
    else:
        print('unknown cmd', cmd)


if __name__ == '__main__':
    main()
