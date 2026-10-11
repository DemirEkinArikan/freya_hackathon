"""Read-only check of semantic-review evidence against the original bytes.

Each evidence object naming a file and its raw SHA256 is rehashed; a quote,
excerpt or value is then located at its cited JSON pointer or line range.
Review records, sources and generated artifacts are never modified.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _first(item, keys):
    return next((item[k] for k in keys if item.get(k) is not None), None)


def _norm(text):
    return ' '.join(text.split())


def _resolve(doc, pointer):
    for part in pointer.split('/')[1:]:
        part = part.replace('~1', '/').replace('~0', '~')
        doc = doc[int(part)] if isinstance(doc, list) else doc[part]
    return doc


def _contains(target, expected):
    if not isinstance(expected, str):
        return target == expected
    if not isinstance(target, str):
        target = json.dumps(target, ensure_ascii=False)
    return _norm(expected) in _norm(target)


def check_item(item):
    path = ROOT / _first(item, ('path', 'absolute_original_path'))
    if not path.is_file():
        return 'missing file'
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != _first(item, ('raw_sha256', 'sha256')):
        return 'sha256 mismatch'
    expected = _first(item, ('quote', 'excerpt', 'value'))
    if expected is None:
        return None
    text = data.decode('utf-8')
    pointer = _first(item, ('pointer', 'json_pointer'))
    if pointer is not None:
        try:
            target = _resolve(json.loads(text), pointer)
        except (KeyError, IndexError, ValueError, TypeError):
            return 'pointer not found'
        if 'value' in item and 'quote' not in item and 'excerpt' not in item:
            return None if target == expected else 'value mismatch'
    else:
        lines = text.splitlines()
        start = _first(item, ('line', 'line_start')) or (item.get('lines') or [None])[0]
        end = item.get('line_end') or (item.get('lines') or [None, None])[1]
        target = text if start is None else '\n'.join(lines[start - 1:end or start + 1])
    return None if _contains(target, expected) else 'quote not found'


def check_record(record):
    """Return (evidence count, quote count, failures) for one review record."""
    counts, failures = [0, 0], []

    def walk(node, where):
        if isinstance(node, dict):
            if _first(node, ('path', 'absolute_original_path')) and _first(node, ('raw_sha256', 'sha256')):
                counts[0] += 1
                counts[1] += _first(node, ('quote', 'excerpt', 'value')) is not None
                reason = check_item(node)
                if reason:
                    failures.append((where, reason))
            for key, value in node.items():
                walk(value, f'{where}/{key}')
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f'{where}/{index}')

    walk(record, '')
    return counts[0], counts[1], failures


def main(paths):
    ok = True
    for name in paths:
        items, quotes, failures = check_record(json.loads(Path(name).read_text(encoding='utf-8')))
        ok = ok and not failures and items > 0
        print(json.dumps(dict(record=name, evidence_items=items, quoted_items=quotes,
                              failures=[dict(location=w, reason=r) for w, r in failures])))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
