import os, re, glob

base = 'D:/code/long-running-agent-harness/projects/factory-copilot/frontend/src'
files = glob.glob(f'{base}/**/*.jsx', recursive=True) + glob.glob(f'{base}/**/*.js', recursive=True)
updated = 0

for fp in files:
    if 'node_modules' in fp or 'request.js' == os.path.basename(fp) or 'main.jsx' == os.path.basename(fp):
        continue

    with open(fp, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content

    # fetch('/api/... -> fetch(window.__API_BASE__ + '/...
    content = content.replace("fetch('/api/", "fetch(window.__API_BASE__ + '/")
    content = content.replace('fetch("/api/', 'fetch(window.__API_BASE__ + "/')
    # EventSource('/api/...
    content = content.replace("EventSource('/api/", "EventSource(window.__API_BASE__ + '/")
    content = content.replace('EventSource("/api/', 'EventSource(window.__API_BASE__ + "/')

    if content != original:
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'OK: {os.path.relpath(fp, base)}')
        updated += 1

print(f'Total updated: {updated}')
