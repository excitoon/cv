import os

from jinja2 import Environment, FileSystemLoader, StrictUndefined

import backend


class Renderer(backend.BaseRenderer):
    def _render_impl(self, intermediate: dict) -> str:
        template_dir = os.path.join(self.root_dir, 'template')
        template_file = os.path.join(template_dir, self.template)
        if not os.path.exists(template_file):
            raise FileNotFoundError(f'Template not found: {template_file}.')
 
        output_txt = os.path.join(self.out_dir, self.stem + '.txt')

        env = Environment(
            loader=FileSystemLoader(template_dir),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        # Simple passthrough filter for plaintext templates.
        def text_filter(val):
            if val is None:
                return ''
            s = str(val)
            # Normalize spaces/newlines a bit.
            return ' '.join(s.split())

        env.filters['text'] = text_filter

        template = env.get_template(os.path.basename(template_file))
        rendered = template.render(**intermediate)

        with open(output_txt, 'w', encoding='utf-8') as f:
            f.write(rendered)

        return output_txt
