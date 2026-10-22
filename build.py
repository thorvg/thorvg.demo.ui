#!/usr/bin/env python3
"""Build the existing C++ demo and its dependencies for WebGL2."""
import argparse
import gzip
import os
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
p = argparse.ArgumentParser()
p.add_argument('--emsdk', type=Path, default=Path.home() / 'Downloads/emsdk')
p.add_argument('--thorvg', type=Path, default=Path.home() / 'thorvg')
p.add_argument('--toolkit', type=Path, default=Path.home() / 'thorvg.toolkit')
p.add_argument('--sdl', type=Path, default=Path('/tmp/SDL3-3.2.0'))
p.add_argument('--full-fonts', action='store_true', help='Embed original fonts instead of subsetting (default requires fontTools)')
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
    '-Db_lto=true', '-Db_ndebug=true', '-Dpartial=false', '-Dengines=gl', '-Dloaders=ttf',
    '-Dsavers=', '-Dthreads=false', '-Dsimd=false', '-Dextra=opengl_es', '-Dtests=false',
]
if (build / 'thorvg/build.ninja').exists():
    run('meson', 'configure', build / 'thorvg', *meson_options)
else:
    run('meson', 'setup', build / 'thorvg', a.thorvg, '--cross-file', cross, *meson_options)
run('meson', 'compile', '-C', build / 'thorvg')
run(em / 'emcmake', 'cmake', '-S', a.sdl.resolve(), '-B', build / 'sdl',
    '-DCMAKE_BUILD_TYPE=MinSizeRel', '-DCMAKE_C_FLAGS_MINSIZEREL=-Oz -DNDEBUG -flto',
    '-DCMAKE_CXX_FLAGS_MINSIZEREL=-Oz -DNDEBUG -flto', '-DSDL_SHARED=OFF', '-DSDL_STATIC=ON', '-DSDL_TESTS=OFF',
    '-DSDL_AUDIO=OFF', '-DSDL_JOYSTICK=OFF', '-DSDL_HAPTIC=OFF', '-DSDL_SENSOR=OFF', '-DSDL_CAMERA=OFF')
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
s = replace(s, '#include <cassert>', '#include <cassert>\n#include <emscripten.h>\n#include <emscripten/html5.h>')
header = toolkit / 'tvgWindow.h'
header.write_text(replace(header.read_text(), '    void show();',
    '    void show();\n    void frame();\n    uint64_t previousTime = 0;\n    double pixelRatio = 0;'))
s = replace(s, '    // Mainloop\n    SDL_Event event;\n\n    auto ptime = SDL_GetTicks();\n\n    while (running) {', '''    EM_ASM({ if (Module.onDemoReady) Module.onDemoReady(); });
    previousTime = SDL_GetTicks();
    // The browser owns scheduling. simulate_infinite_loop keeps run() from
    // returning and releasing the app/fonts while callbacks still use them.
    emscripten_set_main_loop_arg([](void* data) {
        auto self = static_cast<Window*>(data);
        self->frame();
        if (!self->running) {
            emscripten_cancel_main_loop();
            delete self;  // destroys app and canvas before fonts/ThorVG
            tvg::Text::unload(DEMO_BOLD_FONT_PATH);
            tvg::Text::unload(DEMO_FONT_PATH);
            auto result = tvg::Initializer::term();
            emscripten_force_exit(result == tvg::Result::Success ? 0 : 1);
        }
    }, this, 0, true);
}

void Window::frame()
{
    SDL_Event event;
    int w = EM_ASM_INT({ return Math.max(1, Math.round(innerWidth)); });
    int h = EM_ASM_INT({ return Math.max(1, Math.round(innerHeight)); });
    const double dpr = emscripten_get_device_pixel_ratio();
    if (dpr != pixelRatio) {
        pixelRatio = dpr;
        // SDL refreshes its backing-buffer ratio in its browser resize handler.
        // Polling also catches DPR changes without a CSS viewport resize.
        EM_ASM({ window.dispatchEvent(new Event('resize')); });
        needResize = needDraw = true;
    }
    if (w != int(size.w) || h != int(size.h)) SDL_SetWindowSize(window, w, h);
    {''')
s = replace(s, '(ctime - ptime)', '(ctime - previousTime)')
s = replace(s, 'ptime = ctime;', 'previousTime = ctime;')
s = replace(s, 'SDL_GL_SetSwapInterval(0);', '(void)0;')
s = replace(s, 'if (fullscreen) flags |= SDL_WINDOW_FULLSCREEN;', '(void)fullscreen;')
# App layout and SDL input remain in CSS pixels. Only the GL target is physical.
s = replace(s, 'flags | SDL_WINDOW_HIDDEN | SDL_WINDOW_RESIZABLE',
    'flags | SDL_WINDOW_HIDDEN | SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY')
s = replace(s, 'SDL_GetWindowSizeInPixels(window, &w, &h)', 'SDL_GetWindowSize(window, &w, &h)')
s = replace(s, '    // set the canvas target and draw on it.',
    '    int pixelWidth, pixelHeight;\n    SDL_GetWindowSizeInPixels(window, &pixelWidth, &pixelHeight);')
s = replace(s, 'context, 0, size.w, size.h,', 'context, 0, pixelWidth, pixelHeight,')
s = replace(s, 'event.button.x, event.button.y);', 'event.motion.x, event.motion.y);')
s = replace(s, 'auto display = SDL_EGL_GetCurrentDisplay();', 'void* display = nullptr;')
s = replace(s, 'auto surface = display ? SDL_EGL_GetWindowSurface(window) : nullptr;', 'void* surface = nullptr;')
s = replace(s, 'target(display, surface, context,', 'target(display, surface, reinterpret_cast<void*>(static_cast<intptr_t>(emscripten_webgl_get_current_context())),')
window.write_text(s)

# Scale completed root paints once per scene rebuild. This includes overlays,
# clips and FPS labels without changing any widget's CSS layout/input contract.
main = toolkit / 'main.cpp'
source = (root / 'src/main.cpp').read_text()
source = replace(source, '#include <thorvg_toolkit.h>',
    '#include <thorvg_toolkit.h>\n#include <emscripten/html5.h>')
source = replace(source, '    bool render(tvg::Canvas* canvas)\n    {', '''    bool render(tvg::Canvas* canvas)
    {
        if (!renderLogical(canvas)) return false;
        int pixelWidth, pixelHeight;
        if (emscripten_get_canvas_element_size("#canvas", &pixelWidth, &pixelHeight) != EMSCRIPTEN_RESULT_SUCCESS) return false;
        const float sx = float(pixelWidth) / viewport.w;
        const float sy = float(pixelHeight) / viewport.h;
        for (auto paint : canvas->paints()) {
            auto matrix = paint->transform();
            matrix.e11 *= sx; matrix.e12 *= sx; matrix.e13 *= sx;
            matrix.e21 *= sy; matrix.e22 *= sy; matrix.e23 *= sy;
            if (paint->transform(matrix) != tvg::Result::Success) return false;
        }
        return true;
    }

    bool renderLogical(tvg::Canvas* canvas)
    {''')
main.write_text(source)

# Preserve every printable input character and every character present in the
# demo sources. Original assets remain untouched; subset output is build-only.
fonts = root / 'assets'
if not a.full_fonts:
    sys.path.insert(0, str(build / 'python'))
    try:
        from fontTools import subset
    except ImportError:
        raise SystemExit('Font subsetting requires fontTools (pip install fonttools), or use --full-fonts')
    fonts = build / 'fonts'
    fonts.mkdir(exist_ok=True)
    characters = set(range(32, 127))
    characters.add(0x2022)  # password bullet encoded as UTF-8 byte escapes
    for source in (root / 'src').iterdir():
        if source.suffix in ('.cpp', '.h'):
            characters.update(map(ord, source.read_text()))
    for name in ('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf'):
        options = subset.Options()
        options.name_IDs = ['*']
        font = subset.load_font(str(root / 'assets' / name), options)
        subsetter = subset.Subsetter(options=options)
        subsetter.populate(unicodes=characters)
        subsetter.subset(font)
        subset.save_font(font, str(fonts / name), options)

run(em / 'em++', '-std=c++17', '-Oz', '-flto', '-DNDEBUG', '-DTVG_TOOLKIT_STATIC', '-DTVG_GLES_SUPPORTED',
    '-DDEMO_FONT_PATH="/assets/DejaVuSans.ttf"', '-DDEMO_BOLD_FONT_PATH="/assets/DejaVuSans-Bold.ttf"',
    '-I' + str(root / 'src'), '-I' + str(a.thorvg / 'inc'), '-I' + str(a.toolkit / 'inc'),
    '-I' + str(a.sdl / 'include'), '-I' + str(build / 'sdl/include'),
    *[path for path in sorted((root / 'src').glob('*.cpp')) if path.name != 'main.cpp'], main, toolkit / 'tvgToolkit.cpp', window,
    build / 'thorvg/src/libthorvg-1.a', build / 'sdl/libSDL3.a',
    '--js-library', root / 'web/pixel-ratio.js',
    '-sASYNCIFY=0', '-sALLOW_MEMORY_GROWTH=1', '-sINITIAL_MEMORY=67108864',
    '-sMIN_WEBGL_VERSION=2', '-sMAX_WEBGL_VERSION=2', '-sENVIRONMENT=web',
    '-sASSERTIONS=0', '-sEXIT_RUNTIME=1', '--embed-file', str(fonts) + '@/assets',
    '-o', root / 'web/demo.js')
licenses = []
for label, path in [('ThorVG', a.thorvg / 'LICENSE'), ('ThorVG Toolkit', a.toolkit / 'LICENSE'), ('SDL3', a.sdl / 'LICENSE.txt'), ('DejaVu fonts', root / 'assets/DejaVuSans.LICENSE')]:
    licenses.append(label + '\n' + '=' * len(label) + '\n' + path.read_text())
(root / 'web/THIRD_PARTY_LICENSES.txt').write_text('\n\n'.join(licenses))
for name in ('demo.js', 'demo.wasm'):
    output = root / 'web' / name
    output.with_suffix(output.suffix + '.gz').write_bytes(gzip.compress(output.read_bytes(), compresslevel=9, mtime=0))
print('Built web/demo.js, web/demo.wasm, and optional gzip sidecars; serve web/ over HTTP.')
