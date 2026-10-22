#!/usr/bin/env python3
"""Build the existing C++ demo and its dependencies for WebGL2."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parent.parent
p = argparse.ArgumentParser()
p.add_argument('--emsdk', type=Path, default=Path.home() / 'Downloads/emsdk')
p.add_argument('--thorvg', type=Path, default=Path.home() / 'thorvg')
p.add_argument('--toolkit', type=Path, default=Path.home() / 'thorvg.toolkit')
p.add_argument('--sdl', type=Path, default=Path('/tmp/SDL3-3.2.0'))
a = p.parse_args()
a.thorvg = a.thorvg.resolve()
a.toolkit = a.toolkit.resolve()
a.sdl = a.sdl.resolve()
build = root / '.web-build'
build.mkdir(exist_ok=True)
sdk = a.emsdk.resolve()
em = sdk / 'upstream/emscripten'
env = os.environ.copy()
env['PATH'] = str(em) + os.pathsep + env['PATH']
env['EM_CACHE'] = str(build / 'cache')
config = build / 'emscripten-config'
config.write_text(f"LLVM_ROOT = {str(sdk / 'upstream/bin')!r}\nBINARYEN_ROOT = {str(sdk / 'upstream')!r}\nNODE_JS = {str(next((sdk / 'node').glob('*/bin/node')))!r}\n")
env['EM_CONFIG'] = str(config)
def run(*args):
    subprocess.run([str(x) for x in args], cwd=root, env=env, check=True)

cross = build / 'cross.ini'
cross.write_text(f"""[binaries]
c = '{em}/emcc'
cpp = '{em}/em++'
ar = '{em}/emar'
strip = '{em}/emstrip'
[host_machine]
system = 'emscripten'
cpu_family = 'wasm32'
cpu = 'wasm32'
endian = 'little'
[properties]
needs_exe_wrapper = true
""")
meson_options = [
    '--default-library=static', '-Dbuildtype=release', '-Doptimization=s',
    '-Db_ndebug=true', '-Dpartial=false', '-Dengines=gl', '-Dloaders=ttf',
    '-Dsavers=', '-Dthreads=false', '-Dsimd=false', '-Dextra=opengl_es', '-Dtests=false',
]
if (build / 'thorvg/build.ninja').exists():
    run('meson', 'configure', build / 'thorvg', *meson_options)
else:
    run('meson', 'setup', build / 'thorvg', a.thorvg, '--cross-file', cross, *meson_options)
run('meson', 'compile', '-C', build / 'thorvg')
run(em / 'emcmake', 'cmake', '-S', a.sdl.resolve(), '-B', build / 'sdl',
    '-DCMAKE_BUILD_TYPE=MinSizeRel', '-DCMAKE_C_FLAGS_MINSIZEREL=-Oz -DNDEBUG',
    '-DCMAKE_CXX_FLAGS_MINSIZEREL=-Oz -DNDEBUG', '-DSDL_SHARED=OFF', '-DSDL_STATIC=ON', '-DSDL_TESTS=OFF')
run('cmake', '--build', build / 'sdl', '-j', '4')

# Keep the desktop Toolkit unchanged; adapt its platform layer in the build tree.
toolkit = build / 'toolkit'
toolkit.mkdir(exist_ok=True)
for name in ('tvgWindow.h', 'tvgToolkit.cpp', 'tvgWindow.cpp'):
    shutil.copy2(a.toolkit / 'src' / name, toolkit / name)
window = toolkit / 'tvgWindow.cpp'
def replace(source, old, new):
    if old not in source:
        raise RuntimeError('Toolkit source changed; review web adapter: ' + old)
    return source.replace(old, new)

s = window.read_text()
s = replace(s, '#include <cassert>', '''#include <cassert>
#include <emscripten.h>
#include <emscripten/html5.h>
EM_ASYNC_JS(void, waitBrowserFrame, (), {
    await new Promise(resolve => requestAnimationFrame(resolve));
});''')
s = replace(s, '    SDL_ShowWindow(window);', '    SDL_ShowWindow(window);\n    EM_ASM({ if (Module.onDemoReady) Module.onDemoReady(); });')
s = replace(s, 'SDL_GL_SetSwapInterval(0);', '(void)0;')
s = replace(s, 'if (fullscreen) flags |= SDL_WINDOW_FULLSCREEN;', '(void)fullscreen;')
s = replace(s, 'while (running) {', 'while (running) {\n        waitBrowserFrame();')
s = replace(s, 'event.button.x, event.button.y);', 'event.motion.x, event.motion.y);')
s = replace(s, 'auto display = SDL_EGL_GetCurrentDisplay();', 'void* display = nullptr;')
s = replace(s, 'auto surface = display ? SDL_EGL_GetWindowSurface(window) : nullptr;', 'void* surface = nullptr;')
s = replace(s, 'target(display, surface, context,', 'target(display, surface, reinterpret_cast<void*>(static_cast<intptr_t>(emscripten_webgl_get_current_context())),')
# Resize the SDL backing buffer to match the page; the app already handles resize.
s = replace(s, 'waitBrowserFrame();', '''waitBrowserFrame();
        int w = EM_ASM_INT({ return Math.max(1, Math.round(innerWidth)); });
        int h = EM_ASM_INT({ return Math.max(1, Math.round(innerHeight)); });
        if (w != int(size.w) || h != int(size.h)) SDL_SetWindowSize(window, w, h);''')
window.write_text(s)
run(em / 'em++', '-std=c++17', '-Oz', '-DNDEBUG', '-DTVG_TOOLKIT_STATIC', '-DTVG_GLES_SUPPORTED',
    '-DDEMO_FONT_PATH="/assets/DejaVuSans.ttf"', '-DDEMO_BOLD_FONT_PATH="/assets/DejaVuSans-Bold.ttf"',
    '-I' + str(a.thorvg / 'inc'), '-I' + str(a.toolkit / 'inc'),
    '-I' + str(a.sdl / 'include'), '-I' + str(build / 'sdl/include'),
    *sorted((root / 'src').glob('*.cpp')), toolkit / 'tvgToolkit.cpp', window,
    build / 'thorvg/src/libthorvg-1.a', build / 'sdl/libSDL3.a',
    '-sASYNCIFY', '-sALLOW_MEMORY_GROWTH=1', '-sINITIAL_MEMORY=67108864',
    '-sMIN_WEBGL_VERSION=2', '-sMAX_WEBGL_VERSION=2', '-sENVIRONMENT=web',
    '-sASSERTIONS=1', '-sEXIT_RUNTIME=1', '--embed-file', str(root / 'assets') + '@/assets',
    '-o', root / 'web/demo.js')
licenses = []
for label, path in [('ThorVG', a.thorvg / 'LICENSE'), ('ThorVG Toolkit', a.toolkit / 'LICENSE'), ('SDL3', a.sdl / 'LICENSE.txt'), ('DejaVu fonts', root / 'assets/DejaVuSans.LICENSE')]:
    licenses.append(label + '\n' + '=' * len(label) + '\n' + path.read_text())
(root / 'web/THIRD_PARTY_LICENSES.txt').write_text('\n\n'.join(licenses))
print('Built web/demo.js and web/demo.wasm; serve web/ over HTTP.')
