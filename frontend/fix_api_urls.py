import os, re, glob

base = '/d/code/long-running-agent-harness/projects/factory-copilot/frontend/src'
files = glob.glob(f'{base}/**/*.jsx', recursive=True) + glob.glob(f'{base}/**/*.js', recursive=True)

for fp in files:
    if 'node_modules' in fp or 'request.js' in fp:
        continue
    with open(fp, 'r', encoding='utf-8') as f:
        content = f.read()

    if not re.search(r"(?:fetch|EventSource)\('/api/", content):
        continue

    rel = os.path.relpath(f'{base}/services/request', os.path.dirname(fp))
    rel = rel.replace('\\', '/')
    if not rel.startswith('.'):
        rel = './' + rel
    import_stmt = f"import {{ apiUrl }} from '{rel}'"

    if 'apiUrl' not in content:
        lines = content.split('\n')
        last_import = max(i for i, line in enumerate(lines) if line.startswith('import '))
        lines.insert(last_import + 1, import_stmt)
        content = '\n'.join(lines)

    content = re.sub(r"fetch\('/api/", "fetch(apiUrl('/", content)
    content = re.sub(r"EventSource\('/api/", "EventSource(apiUrl('/", content)

    with open(fp, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'OK: {os.path.relpath(fp, base)}')
