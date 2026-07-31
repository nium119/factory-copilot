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

    # Template literals: `/api/ -> `${window.__API_BASE__}/
    content = re.sub(r'`/api/', r'`${window.__API_BASE__}/', content)

    if content != original:
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'OK: {os.path.relpath(fp, base)}')
        updated += 1

print(f'Total updated: {updated}')
