"""Build ProductChecker.exe: one file to share. Run build_exe.bat (or: .venv\\Scripts\\python.exe build_exe.py).

The .exe holds Python, the app and its pages; it uses the Chrome or Edge already on the PC for the Amazon checks (Edge
comes with Windows). Your data never goes into it: each PC keeps its own in %LOCALAPPDATA%\\ProductChecker.
"""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / 'build'
DIST = HERE / 'dist'
NAME = 'ProductChecker'


def pip(*pkgs):
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', *pkgs], check=True)


def icon(path):
    """The sidebar's logo (a blue tile with a rising line) as a Windows icon."""
    from PIL import Image, ImageDraw
    big = 256
    img = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=64, fill=(31, 99, 208, 255))
    pts = [(64, 168), (104, 120), (136, 144), (192, 72)]                     # the logo's M8 21l5-6 4 3 7-9, scaled by 8
    d.line(pts, fill=(255, 255, 255, 255), width=24, joint='curve')
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - 12, y - 12, x + 12, y + 12), fill=(255, 255, 255, 255))
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def main():
    print('Installing the build tools (PyInstaller, Pillow) into this Python...', flush=True)
    pip('pyinstaller', 'pillow')
    BUILD.mkdir(exist_ok=True)
    ico = BUILD / (NAME + '.ico')
    icon(ico)
    sep = ';' if sys.platform == 'win32' else ':'
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed', '--name', NAME,
           '--icon', str(ico), '--distpath', str(DIST), '--workpath', str(BUILD / 'work'), '--specpath', str(BUILD),
           '--add-data', '%s%sui' % (HERE / 'ui', sep), '--add-data', '%s%s.' % (HERE / 'toast.ps1', sep),
           '--collect-data', 'tzdata', '--collect-all', 'playwright',
           '--exclude-module', 'tkinter', '--exclude-module', 'matplotlib', '--exclude-module', 'IPython',
           '--exclude-module', 'pytest', '--exclude-module', 'PyInstaller',
           str(HERE / 'app.py')]
    print('Building %s.exe (a few minutes)...' % NAME, flush=True)
    subprocess.run(cmd, check=True, cwd=HERE)
    exe = DIST / (NAME + '.exe')
    print('Checking it...', flush=True)
    r = subprocess.run([str(exe), '--selftest'], capture_output=True, text=True, timeout=300)
    print(r.stdout or '(the self-test result is in %LOCALAPPDATA%\\ProductChecker\\selftest.txt)')
    if r.returncode != 0:
        print('The self-test found a problem (see above).')
        return 1
    size = exe.stat().st_size / 1e6
    print('\nDone: %s (%.0f MB). Share this one file: double-click it to start Product Checker.' % (exe, size))
    import config
    print('\nTo tell everyone who has it (their app shows "Version %s available"):\n'
          '  1. github.com/%s -> Releases -> Draft a new release\n'
          '  2. Tag: v%s   Title: Product Checker %s   Describe what changed\n'
          '  3. Attach %s, then Publish release.' % (config.VERSION, config.defaults().get('update_repo'), config.VERSION,
                                                    config.VERSION, exe))
    shutil.rmtree(BUILD / 'work', ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
