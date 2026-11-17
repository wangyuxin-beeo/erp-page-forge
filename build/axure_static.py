"""Self-contained Axure data reader. Never evaluates JavaScript.

Accepts JSON/JS data literals (comments, quoted/unquoted keys, trailing commas).
Functions, calls, computed properties, spreads and variable references are rejected.
"""
import json
import os
import re
import urllib.parse
import urllib.request

MAX_BYTES = 32 * 1024 * 1024


class StaticDataError(ValueError):
    pass


class LiteralReader:
    def __init__(self, text, pos=0):
        self.text, self.pos = text, pos

    def skip(self):
        while True:
            match = re.match(r"(?:\s+|//[^\n]*(?:\n|$)|/\*[\s\S]*?\*/)", self.text[self.pos:])
            if not match:
                return
            self.pos += match.end()

    def string(self):
        quote = self.text[self.pos]
        self.pos += 1
        result = []
        while self.pos < len(self.text):
            char = self.text[self.pos]
            self.pos += 1
            if char == quote:
                return ''.join(result)
            if char in '\r\n':
                raise StaticDataError('Unescaped newline in string')
            if char != '\\':
                result.append(char)
                continue
            if self.pos >= len(self.text):
                break
            esc = self.text[self.pos]
            self.pos += 1
            if esc in ('u', 'x'):
                count = 4 if esc == 'u' else 2
                raw = self.text[self.pos:self.pos + count]
                if len(raw) != count or not re.fullmatch('[0-9a-fA-F]+', raw):
                    raise StaticDataError('Invalid character escape')
                result.append(chr(int(raw, 16)))
                self.pos += count
            elif esc in '\r\n':
                if esc == '\r' and self.text[self.pos:self.pos+1] == '\n':
                    self.pos += 1
            else:
                result.append({'n':'\n', 'r':'\r', 't':'\t', 'b':'\b', 'f':'\f', 'v':'\v', '0':'\0'}.get(esc, esc))
        raise StaticDataError('Unterminated string')

    def value(self, depth=0):
        if depth > 150:
            raise StaticDataError('Data nesting exceeds 150 levels')
        self.skip()
        char = self.text[self.pos:self.pos+1]
        if char in ('"', "'"):
            return self.string()
        if char in ('{', '['):
            obj = char == '{'
            self.pos += 1
            result = {} if obj else []
            end = '}' if obj else ']'
            self.skip()
            while self.text[self.pos:self.pos+1] != end:
                if obj:
                    self.skip()
                    if self.text[self.pos:self.pos+1] in ('"', "'"):
                        key = self.string()
                    else:
                        m = re.match(r'[A-Za-z_$][\w$]*', self.text[self.pos:])
                        if not m:
                            raise StaticDataError('Only literal object keys are supported')
                        key = m.group()
                        self.pos += len(key)
                    self.skip()
                    if self.text[self.pos:self.pos+1] != ':':
                        raise StaticDataError('Expected colon after object key')
                    self.pos += 1
                    result[key] = self.value(depth+1)
                else:
                    result.append(self.value(depth+1))
                self.skip()
                if self.text[self.pos:self.pos+1] == end:
                    break
                if self.text[self.pos:self.pos+1] != ',':
                    raise StaticDataError('Expressions and executable JavaScript are unsupported')
                self.pos += 1
                self.skip()
            self.pos += 1
            return result
        m = re.match(r'-?(?:0[xX][0-9a-fA-F]+|(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)', self.text[self.pos:])
        if m:
            raw = m.group()
            self.pos += len(raw)
            return int(raw, 16) if 'x' in raw.lower() else json.loads(raw)
        for word, val in [('true', True), ('false', False), ('null', None)]:
            if re.match(word + r'\b', self.text[self.pos:]):
                self.pos += len(word)
                return val
        raise StaticDataError('Expected a data literal; executable JavaScript is not evaluated')


def read_call(text, method):
    # Token scan prevents accidentally selecting a method name inside comments/strings.
    reader = LiteralReader(text)
    while reader.pos < len(text):
        reader.skip()
        if reader.pos >= len(text):
            break
        if text[reader.pos] in ('"', "'"):
            reader.string()
            continue
        m = re.match(r'\$axure\s*\.\s*' + re.escape(method) + r'\s*\(', text[reader.pos:])
        if m:
            reader.pos += m.end()
            value = reader.value()
            reader.skip()
            if text[reader.pos:reader.pos+1] != ')':
                raise StaticDataError('Axure call must contain one data literal')
            if not isinstance(value, dict):
                raise StaticDataError('Axure data must be an object')
            return value
        reader.pos += 1
    raise StaticDataError('Missing $axure.%s data call' % method)


def safe_page(value):
    path = urllib.parse.unquote(str(value)).replace('\\', '/')
    path = path.split('?', 1)[0].split('#', 1)[0]
    if path.endswith('.html'):
        path = path[:-5]
    if not path or path.startswith('/') or any(p in ('', '.', '..') for p in path.split('/')) or ':' in path:
        raise StaticDataError('Unsafe page path: %r' % value)
    return path


def extract(source, out_dir, pages=None, advanced=True):
    remote = bool(re.match(r'^https?://', source))
    base = source.rstrip('/') + '/' if remote else os.path.realpath(source)

    def read(relative):
        if remote:
            with urllib.request.urlopen(base + urllib.parse.quote(relative, safe='/'), timeout=30) as response:
                data = response.read(MAX_BYTES + 1)
        else:
            with open(os.path.join(base, *relative.split('/')), 'rb') as f:
                data = f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise StaticDataError('Axure data file exceeds 32 MB')
        return data.decode('utf-8-sig')

    document = read_call(read('data/document.js'), 'loadDocument')
    sitemap = document.get('sitemap') or {}
    roots = sitemap.get('rootNodes', []) if isinstance(sitemap, dict) else sitemap
    catalog = []
    def visit(nodes):
        for node in nodes:
            if node.get('url'):
                page = safe_page(node['url'])
                catalog.append({'pageName':node.get('pageName') or page, 'url':page+'.html'})
            visit(node.get('children') or [])
    visit(roots)
    selected = [safe_page(p) for p in pages] if pages else [safe_page(p['url']) for p in catalog]
    if not selected:
        raise StaticDataError('Axure sitemap contains no pages')
    os.makedirs(out_dir, exist_ok=True)
    def dump(path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    dump(os.path.join(out_dir, 'sitemap.json'), {'sitemap':roots, 'pages':catalog, 'logo':(document.get('configuration') or {}).get('logoImagePath','')})
    for name in selected:
        data = read_call(read('files/'+name+'/data.js'), 'loadCurrentPage')
        page = data.get('page') or data
        notes = {'page':page.get('notes') or {}}
        interactions = {}
        paths = data.get('objectPaths') or {}
        def collect(node):
            if not isinstance(node, dict):
                return
            annotations = node.get('annotations') or []
            if isinstance(annotations, dict):
                annotations = [dict(value, ownerId=key) for key, value in annotations.items() if isinstance(value, dict)]
            for annotation in annotations:
                if isinstance(annotation, dict):
                    owner = annotation.get('ownerId') or node.get('id') or str(len(notes))
                    script = (paths.get(owner) or {}).get('scriptId') or owner
                    notes['id-'+str(script)] = dict(annotation)
            if isinstance(node.get('notes'), dict) and node is not page:
                script = (paths.get(node.get('id')) or {}).get('scriptId') or node.get('id') or str(len(notes))
                notes['id-'+str(script)] = dict(node['notes'])
            if node.get('interactionMap'):
                interactions[str(node.get('id') or 'page')] = node['interactionMap']
            for key,value in node.items():
                if key in ('annotations','notes','interactionMap','objectPaths'):
                    continue
                if isinstance(value, dict): collect(value)
                elif isinstance(value,list):
                    for child in value: collect(child)
        if advanced: collect(page)
        dest = os.path.join(out_dir,'pages',name)
        dump(os.path.join(dest,'data.json'),data)
        dump(os.path.join(dest,'notes.json'),notes)
        dump(os.path.join(dest,'interactions.json'),interactions)
    return {'pages':selected,'parser':'python-static','limitations':['No JavaScript execution, rendered screenshots or computed theme extraction']}
