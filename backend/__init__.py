import datetime
import os
import glob
import re
import shutil
import tempfile
import subprocess
import typing


class BaseRenderer:
    def __init__(
        self,
        data: dict,
        labels: dict,
        basename: str,
        language: str,
        template: str,
        dockerfile: str,
        environment: dict,
        out_dir: str,
        configuration: str,
        config_hash: str,
        exclude_projects: typing.Iterable[str],
        exclude_education: typing.Iterable[str],
        exclude_languages: typing.Iterable[str],
        exclude_skills: typing.Iterable[str],
        exclude_skill_groups: typing.Iterable[str],
        root_dir: str,
    ):
        self.data = data or {}
        self.labels = labels or {}
        self.basename = basename or 'cv'
        self.language = language or 'en'
        self.template = template
        self.dockerfile = dockerfile
        self.environment = dict(environment or {})
        self.out_dir = out_dir or 'build/'
        self.configuration = configuration
        self.config_hash = config_hash
        self.exclude_projects = [str(x) for x in (exclude_projects or [])]
        self.exclude_education = [str(x) for x in (exclude_education or [])]
        self.exclude_languages = [str(x) for x in (exclude_languages or [])]
        self.exclude_skills = [str(x) for x in (exclude_skills or [])]
        self.exclude_skill_groups = [str(x) for x in (exclude_skill_groups or [])]
        self.root_dir = root_dir

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
            """Ceiling month count.

            - Returns None if either date is missing.
            - If end is strictly before start (day-aware), returns 0.
            - Base diff uses (year, month).
            - Applies ceiling: add 1 month when end.day >= start.day, else no extra month.
            """
            if not a or not b:
                return None
            if b < a:
                return 0
            diff = (b.year - a.year) * 12 + (b.month - a.month)
            extra = 1 if b.day >= a.day else 0
            months = diff + extra
            return max(months, 0)

        environment = dict(getattr(self, 'environment', {}) or {})

        # Person: derive name/title/summary from data.person; derive location and phone strictly from environment.location via data.locations.
        p_raw = raw.get('person') or {}
        locations_map: dict[str, dict] = raw.get('locations') or {}
        env_location_key: str | None = (environment.get('location') if isinstance(environment, dict) else None)
        loc_obj: dict | None = (locations_map.get(str(env_location_key)) if env_location_key else None)

        # Compose localized location string strictly from locations registry; no fallback to person.location.
        if loc_obj:
            loc_name = tr(loc_obj.get('name'))
            loc_country = tr(loc_obj.get('country'))
            composed_location = (f"{loc_name}, {loc_country}" if (loc_name and loc_country) else (loc_name or loc_country))
        else:
            composed_location = None

        # Contacts: start from person.contacts excluding phone; phone is taken only from locations registry.
        contacts_in = dict(p_raw.get('contacts') or {})
        # Remove any user-provided phone from person.contacts to avoid legacy fallback.
        if 'phone' in contacts_in:
            contacts_in.pop('phone', None)
        # Inject phone from locations registry if available.
        if loc_obj and isinstance(loc_obj.get('phone'), (str, int)):
            contacts_in['phone'] = str(loc_obj.get('phone'))

        person = {
            'name': tr(p_raw.get('name')),
            'title': tr(p_raw.get('title')),
            'location': composed_location,
            'contacts': contacts_in,
            'summary': tr(p_raw.get('summary')),
        }

        # Highlights: prefer nested under person for new schema; fallback to top-level for backward compatibility.
        highlights = tr_list(p_raw.get('highlights') or raw.get('highlights'))

        # References registry. Prefer mapping id->object. Support legacy array (deprecated).
        references_in = raw.get('references') if raw.get('references') is not None else (raw.get('recommendations') or {})
        # Normalize to mapping: if array provided, items must include 'id'
        references_map: dict[str, dict] = {}
        if isinstance(references_in, dict):
            for rid, robj in references_in.items():
                references_map[str(rid)] = robj or {}
        elif isinstance(references_in, list):
            for item in references_in:
                if not isinstance(item, dict):
                    continue
                rid = item.get('id') or item.get('name')
                if not rid:
                    # Skip items without id in legacy array.
                    continue
                references_map[str(rid)] = item or {}
        else:
            references_map = {}

        # Skills.
        skills_raw = raw.get('skills') or {}
        registry: dict[str, dict] = skills_raw.get('registry') or {}
        groups_raw: list[dict] = skills_raw.get('groups') or []
        exclude_skill_groups: set[str] = set([str(x) for x in (getattr(self, 'exclude_skill_groups', []) or [])])
        exclude_skills: set[str] = set([str(x) for x in (getattr(self, 'exclude_skills', []) or [])])

        # Build a unified skill index from registry and any inline group-defined skills.
        skill_index: dict[str, dict] = {}
        for sid, meta in (registry or {}).items():
            sid_s = str(sid)
            skill_index[sid_s] = {
                'id': sid_s,
                'name': tr((meta or {}).get('name') or sid_s),
                'level': (meta or {}).get('level'),
            }

        # Pre-scan groups for inline 'skills' definitions to enrich/override the index.
        for g in groups_raw:
            # Skip entire group if excluded (by group name or id field if present).
            g_name_for_exclude = str(g.get('id') or g.get('name') or '').strip()
            if g_name_for_exclude and g_name_for_exclude in exclude_skill_groups:
                continue
            inline = g.get('skills') or {}
            # Mapping form only: { id: {name, level, highlight?}, ... }
            if isinstance(inline, dict):
                for sid_raw, sdef in (inline or {}).items():
                    sid = str(sid_raw).strip()
                    if not sid or sid in exclude_skills:
                        continue
                    sdef = sdef or {}
                    skill_index[sid] = {
                        'id': sid,
                        'name': (tr(sdef.get('name')) if sdef.get('name') is not None else (skill_index.get(sid) or {}).get('name') or tr(sid)),
                        'level': sdef.get('level') or (skill_index.get(sid) or {}).get('level'),
                    }

        skills: list[dict] = []
        filtered_groups_raw = []
        for g in groups_raw:
            g_name_for_exclude = str(g.get('id') or g.get('name') or '').strip()
            if g_name_for_exclude and g_name_for_exclude in exclude_skill_groups:
                continue
            filtered_groups_raw.append(g)

        for g in filtered_groups_raw:
            items_out: list[dict] = []
            if g.get('skills'):
                # Mapping from id -> meta is required.
                inline = g.get('skills')
                if isinstance(inline, dict):
                    for sid_raw, sdef in (inline or {}).items():
                        sid = str(sid_raw).strip()
                        if not sid or sid in exclude_skills:
                            continue
                        meta = (skill_index.get(sid) or {})
                        sdef = sdef or {}
                        items_out.append({
                            'id': sid,
                            'name': meta.get('name') or tr(sdef.get('name') or sid),
                            'level': sdef.get('level') or meta.get('level'),
                            'highlight': bool(sdef.get('highlight') or False),
                        })
                else:
                    raise TypeError("skills groups must be a mapping of id -> meta; legacy array form is no longer supported")
            else:
                # Backward-compatible format: references by ids in 'items'.
                for sid in (g.get('items') or []):
                    sid_s = str(sid)
                    if sid_s in exclude_skills:
                        continue
                    meta = (skill_index.get(sid_s) or {})
                    items_out.append({
                        'id': sid_s,
                        'name': meta.get('name') or tr(sid_s),
                        'level': meta.get('level'),
                        'highlight': False,
                    })

            skills.append({
                'group': tr(g.get('name') or g.get('id')),
                'items': items_out,
            })

        # Spoken languages.
        languages_raw = raw.get('languages') or {}
        languages: list[dict] = []
        exclude_languages: set[str] = set([str(x) for x in (getattr(self, 'exclude_languages', []) or [])])
        for lid, lobj in languages_raw.items():
            if str(lid) in exclude_languages:
                continue
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
                if str(sid) in exclude_skills:
                    continue
                meta = (skill_index.get(str(sid)) or {})
                out.append({
                    'id': sid,
                    'name': meta.get('name') or tr(sid),
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

            pr_type = str(pr.get('type', 'employment')).lower() if pr.get('type') else 'employment'
            entry = {
                'name': tr(pr.get('name')),
                'type': pr_type,
                'start': _fmt_ym(ps) if ps else None,
                'end': None if ongoing else _fmt_ym(pe_raw),
                'duration_months': _months_between(ps, pe_for_duration),
                'summary': tr(pr.get('summary')),
                'responsibilities': tr_list(pr.get('responsibilities')),
                'skills': _skill_items_from_ids(list(pr.get('skills') or [])),
                'links': dict(pr.get('links') or {}),
                'contributions': [],
                # Project-level references: list of ids.
                'references': [str(x) for x in (pr.get('references') or []) if x is not None],
            }
            # Accumulate skill usage for this project's skills.
            for sid in (pr.get('skills') or []):
                if str(sid) in exclude_skills:
                    continue
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

        # Compute which references are actually referenced by visible projects.
        referenced_ids: set[str] = set()
        for ekey, plist in by_employer.items():
            for p in plist:
                # If project was excluded earlier it won't be in by_employer.
                for rid in (p.get('references') or []):
                    referenced_ids.add(str(rid))

        # Build final references list: include only those referenced by at least one visible project.
        references: list[dict] = []
        for rid, robj in references_map.items():
            if referenced_ids and (rid not in referenced_ids):
                # Skip references not linked from any visible project.
                continue
            references.append({
                'id': rid,
                'name': tr((robj or {}).get('name')),
                'title': tr((robj or {}).get('title')),
                'relation': tr((robj or {}).get('relation')),
                'text': tr((robj or {}).get('text')),
                'contact': dict((robj or {}).get('contact') or {}),
            })

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

            # Employer duration in months for comparison with project durations.
            emp_duration_months = _months_between(emp_start, end_for_duration)

            # When project dates and duration match employer's, omit them on the project.
            for p in projects:
                try:
                    p_start = _parse_date(p.get('start')) if p.get('start') else None
                    p_end = _parse_date(p.get('end')) if p.get('end') else None
                    same_start = (p_start == emp_start)
                    same_end = (p_end == emp_end)
                    if same_start and same_end:
                        p['start'] = None
                        p['end'] = None
                        p['duration_months'] = None
                except Exception:
                    # Be resilient to unexpected types; leave values as-is if comparison fails.
                    pass

            # Roles can change over time. Support "edge dates only":
            # users may specify only the start month for each role change; we infer the end
            # as the month before the next role's start, or the employer's end/present.
            roles_raw = emp.get('roles') or []
            roles_seq: list[dict] = []
            # Capture original order to respect input if dates are missing.
            for idx, r in enumerate(roles_raw):
                rt = tr((r or {}).get('title'))
                if not rt:
                    continue
                sd = _parse_date(r.get('start')) if (isinstance(r, dict) and 'start' in r) else None
                ed = _parse_date(r.get('end')) if (isinstance(r, dict) and 'end' in r) else None
                roles_seq.append({'idx': idx, 'title': rt, 'start_d': sd, 'end_d': ed})

            def _prev_month(d: datetime.date) -> datetime.date:
                y = d.year
                m = d.month
                if m == 1:
                    return datetime.date(y - 1, 12, 1)
                return datetime.date(y, m - 1, 1)

            # Helper to compute next month
            def _next_month(d: datetime.date) -> datetime.date:
                y = d.year
                m = d.month
                if m == 12:
                    return datetime.date(y + 1, 1, 1)
                return datetime.date(y, m + 1, 1)

            # Forward pass: infer missing ends from next role starts or employer end.
            for i in range(len(roles_seq)):
                r = roles_seq[i]
                if r['end_d'] is None:
                    next_start: datetime.date | None = None
                    for j in range(i + 1, len(roles_seq)):
                        ns = roles_seq[j]['start_d']
                        if ns is not None:
                            next_start = ns
                            break
                    if next_start is not None:
                        roles_seq[i]['end_d'] = _prev_month(next_start)
                    else:
                        roles_seq[i]['end_d'] = emp_end  # May be None if ongoing.

            # Backward pass: infer missing starts from previous role ends or employer start.
            for i in range(len(roles_seq)):
                r = roles_seq[i]
                if r['start_d'] is None:
                    if i > 0 and roles_seq[i - 1]['end_d'] is not None:
                        roles_seq[i]['start_d'] = _next_month(roles_seq[i - 1]['end_d'])
                    else:
                        roles_seq[i]['start_d'] = emp_start

            roles_out: list[dict] = []
            for r in roles_seq:
                start_d = r['start_d']
                end_d = r['end_d']
                ongoing_role = (end_d is None)
                roles_out.append({
                    'title': r['title'],
                    'start': _fmt_ym(start_d) if start_d else None,
                    'end': _fmt_ym(end_d) if end_d else None,
                    'ongoing': ongoing_role,
                })

            # Choose representative role: the latest by start date (fallback: last listed)
            role_title = None
            if roles_out:
                latest = None
                for r in roles_out:
                    sd = _parse_date(r.get('start')) if r.get('start') else None
                    if latest is None:
                        latest = (sd, r)
                    else:
                        prev_sd = latest[0]
                        if sd and (prev_sd is None or sd > prev_sd):
                            latest = (sd, r)
                role_title = (latest[1]['title'] if latest else roles_out[-1]['title'])

            keywords: list[str] = []
            for p in projects:
                for s in (p.get('skills') or []):
                    nm = s.get('name')
                    if nm and nm not in keywords:
                        keywords.append(nm)

            emp_type = str(emp.get('type', 'employment')).lower() if emp.get('type') else 'employment'
            experience.append({
                'employer': tr(emp.get('name')),
                'location': tr(emp.get('location')),
                'url': emp.get('url'),
                'role': role_title,
                'type': emp_type,
                'start': _fmt_ym(emp_start) if emp_start else None,
                'end': _fmt_ym(emp_end) if emp_end else None,
                'duration_months': emp_duration_months,
                'keywords': keywords or None,
                'roles': roles_out or None,
                'projects': projects,
            })

        def _sort_key(e: dict) -> tuple[int, int, int, int]:
            end_d = _parse_date(e.get('end')) or today
            start_d = _parse_date(e.get('start')) or end_d
            return (-end_d.year, -end_d.month, -start_d.year, -start_d.month)

        experience.sort(key=_sort_key)

        # Education.
        education_raw: dict[str, dict] = raw.get('education') or {}
        exclude_edu: set[str] = set([str(x) for x in (getattr(self, 'exclude_education', []) or [])])
        education: list[dict] = []
        for eid, ed in education_raw.items():
            if str(eid) in exclude_edu:
                continue
            education.append({
                'institution': tr(ed.get('institution')),
                'degree': tr(ed.get('degree')),
                'field': tr(ed.get('field')),
                'start': ed.get('start'),
                'end': ed.get('end'),
                'location': tr(ed.get('location')),
            })

        def _edu_ord(v: typing.Any, invert: bool = True) -> int:
            """Convert year or YYYY-MM string/int to sortable numeric.

            YYYY-MM -> YYYY*100 + MM
            YYYY -> YYYY*100
            Other/None -> 0
            invert=True returns negative for descending sort.
            """
            if v is None:
                return 0
            if isinstance(v, int):
                base = v * 100
            else:
                sv = str(v).strip()
                if re.fullmatch(r"\d{4}-\d{2}", sv):
                    y, m = sv.split('-')
                    try:
                        base = int(y) * 100 + int(m)
                    except Exception:
                        base = 0
                elif re.fullmatch(r"\d{4}", sv):
                    try:
                        base = int(sv) * 100
                    except Exception:
                        base = 0
                else:
                    base = 0
            return -base if invert else base

        education.sort(key=lambda ed: (_edu_ord(ed.get('end')), _edu_ord(ed.get('start'))))

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

        # Metrics.
        earliest_start: datetime.date | None = None
        latest_end_for_duration: datetime.date | None = None
        total_projects = 0
        for e in experience:
            # Count projects as before.
            total_projects += len(e.get('projects') or [])
            # Use employer-level start/end to avoid shifts when project dates are omitted.
            es = _parse_date(e.get('start')) if e.get('start') else None
            ee = _parse_date(e.get('end')) if e.get('end') else None
            if es and (earliest_start is None or es < earliest_start):
                earliest_start = es
            ee_span = ee or today
            if ee_span and (latest_end_for_duration is None or ee_span > latest_end_for_duration):
                latest_end_for_duration = ee_span
        months_total = _months_between(earliest_start, latest_end_for_duration) or 0
        experience_years = max(int(round(months_total / 12.0)), 0)

        # Normalize certifications: accept either strings or objects with name/provider/year.
        certs_in = raw.get('certifications') or []
        certifications: list[dict] = []
        for c in certs_in:
            if isinstance(c, dict):
                certifications.append({
                    'name': tr(c.get('name')),
                    'provider': tr(c.get('provider')),
                    'year': c.get('year'),
                })
            else:
                certifications.append({'text': tr(c)})

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
            'references': references,
            'awards': tr_list(raw.get('awards')),
            'certifications': certifications,
            'publications': tr_list(raw.get('publications')),
            'talks': tr_list(raw.get('talks')),
            'interests': tr_list(raw.get('interests')),
            'metrics': {'experience_years': experience_years, 'companies': len(experience), 'projects': total_projects},
            'labels': labels,
        }

    def render(self) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        intermediate = self.expand_intermediate()
        return self._render_impl(intermediate)

    @staticmethod
    def _slugify(s: str) -> str:
        s = str(s)
        return re.sub('-{2,}', '-', ''.join(ch.lower() if (ch.isalnum() and ch.isascii()) else '-' for ch in s)).strip('-') or 'cv'

    @property
    def stem(self) -> str:
        base_slug = self._slugify(self.basename)
        cfg_hash_short = self.config_hash[:8]
        lang_token = str(self.language).replace('_', '-').split('-')[0].lower() or 'en'
        today = datetime.date.today()
        return f'{base_slug}-{cfg_hash_short}-{lang_token}-{today.year:04d}-{today.month:02d}-{today.day:02d}'

    def _render_impl(self, intermediate: dict) -> str:
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
        image_tag = f"{safe or 'cv'}:latest"

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
