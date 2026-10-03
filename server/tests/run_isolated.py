"""Run backend test modules in separate processes with disposable runtime state.

HTTP fixtures set process-level configuration before importing the app. A fresh
interpreter per module keeps those settings and imported stores independent.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

def main():
    server = Path(os.environ.get('BMC_TEST_SERVER_ROOT', Path(__file__).resolve().parents[1])).resolve()
    tests = server / 'tests'
    if not tests.is_dir() or not any(tests.glob('test_*.py')):
        raise RuntimeError(f'No backend test modules found: {tests}')
    failed = []
    count = 0
    bootstrap = ('import os,sys,runpy;from pathlib import Path;'
                 'handles=[os.add_dll_directory(str(p)) for p in (Path(sys.executable).parent,Path(sys.executable).parent/"DLLs") '
                 'if os.name=="nt" and hasattr(os,"add_dll_directory") and p.is_dir()];'
                 'sys.argv=["unittest",*sys.argv[1:]];runpy.run_module("unittest",run_name="__main__")')
    for module in sorted(tests.glob('test_*.py')):
        with tempfile.TemporaryDirectory(prefix='bmc-backend-test-') as folder:
            env = os.environ.copy()
            env.update(BMC_SKIP_DOTENV='1', BMC_DATABASE_PATH=str(Path(folder) / 'web.db'),
                       PYTHONPATH=str(server) + os.pathsep + env.get('PYTHONPATH', ''))
            result = subprocess.run([sys.executable, '-c', bootstrap, 'discover', '-s', str(tests), '-p', module.name, '-v'],
                                    cwd=server.parent, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            count += 1
            if result.returncode:
                failed.append(module.name)
                print(result.stdout, flush=True)
            else:
                summary = next((s for s in result.stdout.splitlines() if s.startswith('Ran ')), 'passed')
                print(f'{module.name}: {summary}', flush=True)
    print(f'Backend isolated modules: {count}, failed: {len(failed)}', flush=True)
    return 1 if failed else 0

if __name__ == '__main__':
    raise SystemExit(main())
