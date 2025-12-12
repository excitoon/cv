import datetime
import os
import re
import typing

from jinja2 import Environment, FileSystemLoader

import backend


class Renderer(backend.BaseRenderer):
    def _render_impl(self, intermediate: dict) -> str:
        template_dir = os.path.join(self.root_dir, 'template')
        template_file = os.path.join(template_dir, self.template)
        if not os.path.exists(template_file):
            raise FileNotFoundError(f'Template not found: {template_file}.')

        output_pdf = os.path.join(self.out_dir, self.stem + '.pdf')
        output_log = os.path.join(self.out_dir, self.stem + '.log')
        output_tex = os.path.join(self.out_dir, self.stem + '.tex')

        env = Environment(loader=FileSystemLoader(template_dir), autoescape=False, trim_blocks=True, lstrip_blocks=True)

        # Add a filter to format dates into localized labels like "Oct 2025" (or "окт 2025").
        # Month names are read from labels.months_short (a 12-item list localized to current language).
        labels_data = (intermediate.get('labels') or {})

        def _fmt_ym_label(value: typing.Any, l: typing.Any) -> str:
            if value is None:
                return ''
            s = str(value).strip()
            if not s:
                return ''

            # Normalize language code to two letters.
            try:
                lang2 = str(l or 'en').strip().lower().replace('_', '-').split('-')[0]
            except Exception:
                lang2 = 'en'

            # Prefer labels.months_short (already localized to the current language via expand_intermediate)
            months_list = labels_data.get('months_short')
            # `YYYY-MM`.
            m = re.fullmatch(r'\s*(\d{4})-(\d{2})\s*', s)
            if m:
                y = int(m.group(1))
                mm = int(m.group(2))
                if not (isinstance(months_list, (list, tuple)) and len(months_list) == 12):
                    return s
                name = months_list[max(min(mm, 12), 1) - 1]
                return f'{name} {y}'
            # `YYYY`
            m2 = re.fullmatch(r'\s*(\d{4})\s*', s)
            if m2:
                return m2.group(1)
            # Try ISO date.
            try:
                d = datetime.date.fromisoformat(s)
                if not (isinstance(months_list, (list, tuple)) and len(months_list) == 12):
                    return s
                name = months_list[d.month - 1]
                return f'{name} {d.year}'
            except Exception:
                return s

        env.filters['fmt_ym'] = _fmt_ym_label

        # Escape LaTeX special characters in plain text (not for content containing LaTeX macros).
        def _tex_escape(value: typing.Any) -> str:
            s = '' if value is None else str(value)
            if not s:
                return ''
            # Replace backslash first to avoid creating control sequences
            s = s.replace('\\', r'\textbackslash{}')
            # Escape common LaTeX special chars: & % $ # _ { }
            s = re.sub(r'([&%$#_{}])', r'\\\1', s)
            # Handle ~ and ^ which are active characters in TeX text mode.
            s = s.replace('~', r'\ensuremath{\sim}').replace('^', r'\textasciicircum{}')
            return s

        env.filters['tex_escape'] = _tex_escape

        template = env.get_template(os.path.basename(template_file))
        rendered_tex = template.render(**intermediate)

        # Save the rendered TeX always (helps when build/ has no artifacts yet).
        with open(output_tex, 'w', encoding='utf-8') as f:
            f.write(rendered_tex)

        # Files for container.
        files: dict[str, typing.Any] = {'main.tex': rendered_tex}
        for dirpath, _dirnames, filenames in os.walk(template_dir):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, template_dir)
                if rel.endswith('.j2') or rel == 'main.tex':
                    continue
                with open(full, 'rb') as f:
                    files[rel] = f.read()

        shell_script = (
            'set -u; status=0; : > build.log; '
            '{ echo "== env =="; pwd; ls -la; which pdflatex || true; pdflatex --version || true; } >> build.log 2>&1; '
            'echo "== first run ==" >> build.log; '
            'pdflatex -interaction=nonstopmode -halt-on-error main.tex >> build.log 2>&1 || status=$?; '
            'echo "== second run ==" >> build.log; '
            'pdflatex -interaction=nonstopmode -halt-on-error main.tex >> build.log 2>&1 || status=$?; '
            'echo ${status} > exit.code'
        )
        cmd = ['sh', '-lc', shell_script]
        outputs = ['main.pdf', 'build.log', 'main.log', 'exit.code']

        result = self.run_in_docker(cmd=cmd, files=files, outputs=outputs)
        outputs_map: dict[str, bytes] = result.get('outputs') or {}

        # Prefer `exit.code` from container; if missing/invalid, fall back to docker returncode.
        code_s = (outputs_map.get('exit.code') or b'0')
        try:
            exit_code = int(code_s.decode('utf-8').strip()) if isinstance(code_s, (bytes, bytearray)) else int(str(code_s).strip())
        except Exception:
            try:
                exit_code = int(result.get('returncode') or 0)
            except Exception:
                exit_code = 0

        # Prefer non-empty `build.log`; fall back to TeX's `main.log`. If both are empty/missing,
        # write a diagnostic fallback so the user always sees something useful.
        log_bytes = outputs_map.get('build.log') or b''
        mainlog_bytes = outputs_map.get('main.log') or b''
        chosen_log = log_bytes if (isinstance(log_bytes, (bytes, bytearray)) and len(log_bytes) > 0) else mainlog_bytes

        def _write_bytes(path: str, data: typing.Union[bytes, bytearray, str]) -> None:
            with open(path, 'wb') as _f:
                if isinstance(data, (bytes, bytearray)):
                    _f.write(data)
                else:
                    _f.write(str(data).encode('utf-8', errors='ignore'))

        if isinstance(chosen_log, (bytes, bytearray)) and len(chosen_log) > 0:
            _write_bytes(output_log, chosen_log)
        elif isinstance(chosen_log, str) and chosen_log.strip():
            _write_bytes(output_log, chosen_log)
        else:
            # No logs captured from container; write a diagnostic fallback.
            diag = []
            diag.append('No logs were captured from Docker (build.log and main.log are empty or missing).')
            diag.append(f'Exit code recorded: {exit_code}.')
            try:
                diag.append(f'Available output keys: {sorted(list(result.keys()))}')
            except Exception:
                pass
            try:
                _cmd_str = ' '.join(cmd) if isinstance(cmd, list) else str(cmd)
                diag.append(f'Command: {_cmd_str}.')
            except Exception:
                pass
            diag.append('Hints: container working directory might be non-writable; pdflatex may be missing; or outputs weren\'t found.')

            # Include docker stdout/stderr if present for more context.
            def _as_text(v: typing.Any) -> str:
                if isinstance(v, (bytes, bytearray)):
                    return v.decode('utf-8', errors='ignore')
                return str(v)
            docker_out = result.get('stdout') or result.get('build_stdout')
            docker_err = result.get('stderr') or result.get('build_stderr')
            if docker_out:
                diag.append('\n--- docker stdout ---\n' + _as_text(docker_out))
            if docker_err:
                diag.append('\n--- docker stderr ---\n' + _as_text(docker_err))
            _write_bytes(output_log, '\n'.join(diag) + '\n')

        pdf_bytes = outputs_map.get('main.pdf')
        if pdf_bytes:
            with open(output_pdf, 'wb') as f:
                f.write(pdf_bytes if isinstance(pdf_bytes, (bytes, bytearray)) else bytes(pdf_bytes))

        if exit_code != 0 or not pdf_bytes:
            # Add a brief log tail to the error for convenience.
            tail = ''
            try:
                txt = (chosen_log or b'').decode('utf-8', errors='ignore').splitlines()
                tail = '\n\n--- build.log (tail) ---\n' + '\n'.join(txt[-60:]) + '\n--- end ---'
            except Exception:
                pass
            raise RuntimeError(f'pdflatex failed (exit {exit_code}). See {os.path.basename(output_log)} in {out_dir}{tail}')

        return output_pdf
