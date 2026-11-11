import json
from reports.html_report import render_html
from reports.pdf_report import render_pdf


def build_reports(result):
    data = result.to_dict()
    return {"json": json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
            "html": render_html(data), "pdf": render_pdf(data)}
