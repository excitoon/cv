import os
import re
import datetime
import typing
from jinja2 import Environment, FileSystemLoader

import backend


class Renderer(backend.BaseRenderer):
    def expand_intermediate(self) -> dict:
        # See example of intermediate format in `intermediate.example.yaml`. No schema, womp-womp.

        raw_all: dict[str, typing.Any] = getattr(self, 'data', {}) or {}
        raw: dict[str, typing.Any] = raw_all.get('data') if isinstance(raw_all.get('data'), dict) else raw_all

        # Resolve to strict two-letter language code (e.g., `en`, `ru`, `pl`).
        raw_lang = (getattr(self, 'language', None) or 'en')
        s = str(raw_lang).strip().lower().replace('_', '-')
        m = re.match('^([a-z]{2})', s)
        lang = m.group(1) if m else 'en'

        today = datetime.date.today()

        def _to_locale(l: str) -> str:
            m = {'en': 'en_US', 'ru': 'ru_RU', 'pl': 'pl_PL'}
            return m.get(l, 'en_US')

        def tr(v: typing.Any) -> typing.Any:
            if isinstance(v, dict):
                # Prefer exact two-letter language match.
                if lang in v:
                    return v[lang]
                if 'en' in v:
                    return v['en']
                for _, val in v.items():
                    return val
                return None
            return v

        def tr_list(items: typing.Any) -> list[str]:
            return [str(tr(x)) for x in (items or [])]

        def _parse_date(s: typing.Any) -> datetime.date | None:
            if s is None:
                return None
            s = str(s).strip()
            if not s or s.lower() in {'now', 'present', 'current', 'ongoing'}:
                return None
            try:
                if re.fullmatch('\\d{4}-\\d{2}', s):
                    y, m = s.split('-')
                    return datetime.date(int(y), int(m), 1)
                if re.fullmatch('\\d{4}', s):
                    return datetime.date(int(s), 1, 1)
                return datetime.date.fromisoformat(s)
            except Exception:
                return None

        def _fmt_ym(d: datetime.date | None) -> str | None:
            return f'{d.year:04d}-{d.month:02d}' if d else None

        def _months_between(a: datetime.date | None, b: datetime.date | None) -> int | None:
            if not a or not b:
                return None
            months = (b.year - a.year) * 12 + (b.month - a.month)
            return max(months - (1 if b.day < a.day else 0), 0)

        environment = dict(getattr(self, 'environment', {}) or {})

        # Person.
        p_raw = raw.get('person') or {}
        person = {
            'name': tr(p_raw.get('name')),
            'title': tr(p_raw.get('title')),
            'location': tr(p_raw.get('location')),
            'contacts': dict(p_raw.get('contacts') or {}),
            'summary': tr(p_raw.get('summary')),
        }

        highlights = tr_list(raw.get('highlights'))

        # Skills.
        skills_raw = raw.get('skills') or {}
        registry: dict[str, dict] = skills_raw.get('registry') or {}
        groups_raw: list[dict] = skills_raw.get('groups') or []
        skills: list[dict] = []
        for g in groups_raw:
            items_out: list[dict] = []
            for sid in (g.get('items') or []):
                meta = registry.get(str(sid)) or {}
                items_out.append({
                    'id': sid,
                    'name': tr(meta.get('name') or sid),
                    'level': meta.get('level'),
                    'highlight': False,
                })
            skills.append({
                'id': g.get('id'),
                'group': tr(g.get('name') or g.get('id')),
                'items': items_out,
            })

        # Spoken languages.
        languages_raw = raw.get('languages') or {}
        languages: list[dict] = []
        for _, lobj in languages_raw.items():
            languages.append({'name': tr(lobj.get('name')), 'level': lobj.get('level')})

        # Contributions registry.
        contrib_reg: dict[str, dict] = raw.get('contributions') or {}

        # Projects grouped by employer.
        projects_raw: dict[str, dict] = raw.get('projects') or {}
        # Optionally exclude some projects by ID based on configuration.
        exclude_projects: set[str] = set([str(x) for x in (getattr(self, 'exclude_projects', []) or [])])
        by_employer: dict[str, list[dict]] = {}

        # Accumulate per-skill usage across projects.
        skill_stats: dict[str, dict] = {}

        def _skill_items_from_ids(ids: list[str]) -> list[dict]:
            out: list[dict] = []
            for sid in (ids or []):
                meta = registry.get(str(sid)) or {}
                out.append({
                    'id': sid,
                    'name': tr(meta.get('name') or sid),
                    'level': meta.get('level'),
                    'highlight': False,
                })
            return out

        for pid, pr in (projects_raw or {}).items():
            if pid in exclude_projects:
                continue
            emp_key = pr.get('employer')
            if not emp_key:
                continue
            ps = _parse_date(pr.get('start'))
            pe_raw = _parse_date(pr.get('end'))
            ongoing = pr.get('end') in (None, '', 'now', 'present', 'current', 'ongoing') or pe_raw is None
            pe_for_duration = pe_raw or today
            pr_months = _months_between(ps, pe_for_duration) or 0

            entry = {
                'name': tr(pr.get('name')),
                'type': pr.get('type', 'employment'),
                'start': _fmt_ym(ps) if ps else None,
                'end': None if ongoing else _fmt_ym(pe_raw),
                'duration_months': _months_between(ps, pe_for_duration),
                'summary': tr(pr.get('summary')),
                'responsibilities': tr_list(pr.get('responsibilities')),
                'skills': _skill_items_from_ids(list(pr.get('skills') or [])),
                'links': dict(pr.get('links') or {}),
                'contributions': [],
            }
            # Accumulate skill usage for this project's skills.
            for sid in (pr.get('skills') or []):
                st = skill_stats.setdefault(str(sid), {'months': 0, 'first': None, 'last': None})
                st['months'] = int(st.get('months', 0)) + int(pr_months)
                if ps:
                    st['first'] = ps if (st['first'] is None or ps < st['first']) else st['first']
                end_for_last = pe_raw or today
                if end_for_last:
                    st['last'] = end_for_last if (st['last'] is None or end_for_last > st['last']) else st['last']

            for cid in (pr.get('contributions') or []):
                meta = contrib_reg.get(str(cid)) or {}
                entry['contributions'].append({
                    'repo': meta.get('repo'),
                    'link': meta.get('link'),
                    'note': tr(meta.get('note')),
                })
            by_employer.setdefault(emp_key, []).append(entry)

        # Employers -> experience.
        employers_raw: dict[str, dict] = raw.get('employers') or {}
        experience: list[dict] = []
        for ekey, emp in employers_raw.items():
            projects = by_employer.get(ekey, [])
            if not projects:
                continue

            start_dates = [_parse_date(p.get('start')) for p in projects if p.get('start')]
            end_dates_raw = [_parse_date(p.get('end')) for p in projects if p.get('end')]
            any_ongoing = any(p.get('end') in (None, '',) for p in projects)

            emp_start = min([d for d in start_dates if d]) if start_dates else None
            if any_ongoing or not end_dates_raw:
                emp_end = None
                end_for_duration = today
            else:
                filtered = [d for d in end_dates_raw if d]
                emp_end = max(filtered) if filtered else None
                end_for_duration = emp_end or today

            roles = emp.get('roles') or []
            role_title = None
            for r in roles:
                rt = tr(r.get('title'))
                if rt:
                    role_title = rt
                    break

            keywords: list[str] = []
            for p in projects:
                for s in (p.get('skills') or []):
                    nm = s.get('name')
                    if nm and nm not in keywords:
                        keywords.append(nm)

            experience.append({
                'employer': tr(emp.get('name')),
                'location': tr(emp.get('location')),
                'url': emp.get('url'),
                'role': role_title,
                'start': _fmt_ym(emp_start) if emp_start else None,
                'end': _fmt_ym(emp_end) if emp_end else None,
                'duration_months': _months_between(emp_start, end_for_duration),
                'keywords': keywords or None,
                'projects': projects,
            })

        def _sort_key(e: dict) -> tuple[int, int, int, int]:
            end_d = _parse_date(e.get('end')) or today
            start_d = _parse_date(e.get('start')) or end_d
            return (-end_d.year, -end_d.month, -start_d.year, -start_d.month)

        experience.sort(key=_sort_key)

        # Education.
        education_raw: dict[str, dict] = raw.get('education') or {}
        education: list[dict] = []
        for _, ed in education_raw.items():
            education.append({
                'institution': tr(ed.get('institution')),
                'degree': tr(ed.get('degree')),
                'field': tr(ed.get('field')),
                'start': ed.get('start'),
                'end': ed.get('end'),
                'location': tr(ed.get('location')),
            })

        education.sort(key=lambda ed: (-(ed.get('end') or 0), -(ed.get('start') or 0)))

        # Classes.
        classes: list[dict] = []
        for c in (raw.get('classes') or []):
            classes.append({
                'name': tr(c.get('name')),
                'provider': tr(c.get('provider')),
                'year': c.get('year'),
                'link': c.get('link'),
            })

        labels_in = getattr(self, 'labels', {}) or {}
        labels = {k: tr(v) for k, v in labels_in.items()}

        # Recommendations (translate fields, keep contacts raw).
        recommendations: list[dict] = []
        for r in (raw.get('recommendations') or []):
            recommendations.append({
                'name': tr(r.get('name')),
                'title': tr(r.get('title')),
                'relation': tr(r.get('relation')),
                'text': tr(r.get('text')),
                'contact': dict(r.get('contact') or {}),
            })

        # Annotate each skill item with usage data.
        for g in skills:
            for it in (g.get('items') or []):
                sid = str(it.get('id'))
                st = skill_stats.get(sid) or {}
                months = int(st.get('months', 0) or 0)
                years = round(months / 12.0, 1) if months else 0
                first_used = _fmt_ym(st.get('first')) if st.get('first') else None
                last_used = _fmt_ym(st.get('last')) if st.get('last') else None
                # Keep flat fields for backward compatibility, and also provide a nested map
                it['months'] = months
                it['years'] = years
                it['first_used'] = first_used
                it['last_used'] = last_used

        # Metrics
        earliest_start: datetime.date | None = None
        latest_end_for_duration: datetime.date | None = None
        total_projects = 0
        for e in experience:
            for p in (e.get('projects') or []):
                total_projects += 1
                ps = _parse_date(p.get('start'))
                pe = _parse_date(p.get('end'))
                if ps and (earliest_start is None or ps < earliest_start):
                    earliest_start = ps
                pe_span = pe or today
                if pe_span and (latest_end_for_duration is None or pe_span > latest_end_for_duration):
                    latest_end_for_duration = pe_span
        months_total = _months_between(earliest_start, latest_end_for_duration) or 0
        experience_years = max(int(round(months_total / 12.0)), 0)

        return {
            'version': 1,
            'generated_at': today.isoformat(),
            'lang': lang,
            'locale': _to_locale(lang),
            'environment': environment,
            'person': person,
            'highlights': highlights,
            'skills': skills,
            'languages': languages,
            'experience': experience,
            'education': education,
            'classes': classes,
            'recommendations': recommendations,
            'awards': tr_list(raw.get('awards')),
            'certifications': tr_list(raw.get('certifications')),
            'publications': tr_list(raw.get('publications')),
            'talks': tr_list(raw.get('talks')),
            'interests': tr_list(raw.get('interests')),
            'metrics': {'experience_years': experience_years, 'companies': len(experience), 'projects': total_projects},
            'labels': labels,
        }

    def render(self):
        intermediate = self.expand_intermediate()

        root_dir = getattr(self, 'root_dir', None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_dir = os.path.join(root_dir, 'template')
        template_file = os.path.join(template_dir, self.template)
        if not os.path.exists(template_file):
            raise FileNotFoundError(f'Template not found: {template_file}.')

        out_dir = getattr(self, 'out_dir', os.path.join(root_dir, 'out'))
        os.makedirs(out_dir, exist_ok=True)

        def _slugify(s: str) -> str:
            s = str(s)
            return re.sub('-{2,}', '-', ''.join(ch.lower() if (ch.isalnum() and ch.isascii()) else '-' for ch in s)).strip('-') or 'cv'

        basename = getattr(self, 'basename', None) or (intermediate.get('person') or {}).get('name') or 'cv'
        base_slug = _slugify(basename)
        cfg_hash = getattr(self, 'config_hash', None)
        cfg_hash_short = (str(cfg_hash)[:8]) if cfg_hash else None
        lang = getattr(self, 'language', None) or 'en'
        lang_token = str(lang).replace('_', '-').split('-')[0].lower() or 'en'
        today = datetime.date.today()
        if cfg_hash_short:
            stem = f'{base_slug}-{cfg_hash_short}-{lang_token}-{today.year:04d}-{today.month:02d}-{today.day:02d}'
        else:
            stem = f'{base_slug}-{lang_token}-{today.year:04d}-{today.month:02d}-{today.day:02d}'
        output_pdf = os.path.join(out_dir, stem + '.pdf')
        output_log = os.path.join(out_dir, stem + '.log')
        output_tex = os.path.join(out_dir, stem + '.tex')

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
            s = s.replace('~', r'\textasciitilde{}').replace('^', r'\textasciicircum{}')
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
