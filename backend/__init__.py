import os
import glob
import shutil
import tempfile
import subprocess
import typing


class BaseRenderer:
    def __init__(self, data: dict, labels: dict, basename: str, language: str, template: str, dockerfile: str, environment: dict, out_dir: str):
        self.data = data
        self.labels = labels
        self.basename = basename
        self.language = language
        self.template = template
        self.dockerfile = dockerfile
        self.environment = environment
        self.out_dir = out_dir

    def render(self) -> None:
        raise NotImplementedError('Subclasses should implement this method.')

    def run_in_docker(
        self,
        cmd: str | list[str],
        files: dict[str, typing.Any],
        outputs: list[str],
    ) -> dict[str, typing.Any]:
        '''
            Build image from self.dockerfile, run cmd with provided files in a temp workspace,
            and collect specified outputs.

            - cmd: shell string or argv list executed in the container.
            - files: mapping of relative paths -> content (str|bytes). Directories are created automatically.
            - outputs: list of file or glob patterns relative to the workspace (or starting with /workspace).
            Returns dict with stdout, stderr, outputs (bytes), image, returncode, build logs.
        '''

        if not shutil.which('docker'):
            raise RuntimeError('Docker CLI not found. Install Docker Desktop for Mac and ensure `docker` is on PATH.')

        df_path = os.path.abspath(self.dockerfile)
        if not df_path or not os.path.exists(df_path):
            raise FileNotFoundError(f'Dockerfile not found: `{df_path}`.')

        # Build context = directory of `Dockerfile`.
        context_dir = os.path.dirname(df_path) or '.'

        # Internal image tag (derived from basename).
        base = (self.basename or 'cv').lower()
        safe = ''.join(ch if ch.isalnum() or ch in '-._' else '-' for ch in base).strip('-.')
        image_tag = f'{safe or 'cv'}:latest'

        # Build image.
        build_cmd = ['docker', 'build', '-t', image_tag, '-f', df_path, context_dir]
        build_proc = subprocess.run(build_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if build_proc.returncode != 0:
            raise RuntimeError(
                f'Docker build failed (code {build_proc.returncode}).\nSTDOUT:\n{build_proc.stdout}\nSTDERR:\n{build_proc.stderr}'
            )

        mount_dir = '/workspace'

        with tempfile.TemporaryDirectory(prefix='cv-work-') as tmpdir:
            # Materialize input files into temp workspace.
            for rel, content in (files or {}).items():
                rel = str(rel).lstrip('/').replace('\\', '/')
                host_path = os.path.join(tmpdir, rel)
                os.makedirs(os.path.dirname(host_path), exist_ok=True)
                if isinstance(content, bytes):
                    with open(host_path, 'wb') as fh:
                        fh.write(content)
                else:
                    with open(host_path, 'w', encoding='utf-8') as fh:
                        fh.write('' if content is None else str(content))

            # Run container; mount tmpdir at /workspace and set it as working dir.
            run_cmd: list[str] = ['docker', 'run', '--rm', '-v', f'{tmpdir}:{mount_dir}', '-w', mount_dir, image_tag]
            if isinstance(cmd, str):
                run_cmd += ['/bin/sh', '-lc', cmd]
            else:
                run_cmd += list(cmd)

            run_proc = subprocess.run(run_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if run_proc.returncode != 0:
                raise RuntimeError(
                    f'Docker run failed (code {run_proc.returncode}).\nSTDOUT:\n{run_proc.stdout}\nSTDERR:\n{run_proc.stderr}'
                )

            # Collect outputs from the mounted workspace.
            collected: dict[str, bytes] = {}
            for pattern in outputs or []:
                if pattern.startswith(mount_dir + '/'):
                    rel_pattern = pattern[len(mount_dir) + 1 :]
                elif pattern.startswith('/'):
                    # Outside mounted volume; cannot collect.
                    continue
                else:
                    rel_pattern = pattern

                host_pattern = os.path.join(tmpdir, rel_pattern)
                matches = glob.glob(host_pattern, recursive=True)
                for m in matches:
                    if os.path.isdir(m):
                        for root, _dirs, files_in_dir in os.walk(m):
                            for f in files_in_dir:
                                p = os.path.join(root, f)
                                relp = os.path.relpath(p, tmpdir)
                                with open(p, 'rb') as fh:
                                    collected[relp] = fh.read()
                    else:
                        relp = os.path.relpath(m, tmpdir)
                        with open(m, 'rb') as fh:
                            collected[relp] = fh.read()

            return {
                'stdout': run_proc.stdout,
                'stderr': run_proc.stderr,
                'outputs': collected,
                'image': image_tag,
                'returncode': run_proc.returncode,
                'build_stdout': build_proc.stdout,
                'build_stderr': build_proc.stderr,
            }
